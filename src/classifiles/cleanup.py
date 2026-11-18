"""Duplicates inside the sorted folders: find every set of identical files under a root, suggest the
copy to keep (the one in its "real place"), and trash the others (undoable). The Duplicates page.

Finding them (a *sweep*) reuses the duplicate catalog (duplicates.py): every file with its size and
cached hashes. Only files whose size occurs more than once are read, in two steps:

    same size  ->  same quick hash (first 64 KB)  ->  same full hash (whole file)

Hashes are cached in the catalog and stay valid while a file's size and modification time don't
change, so a second sweep only reads new or changed files.

Which copy to keep, in this order:
1. names that look like copies ("x (1).pdf", "Kopie von x") lose;
2. the copy whose folder fits the file best: the files most similar to it (example index, no
   model needed: stored vectors) vote for categories and folders;
3. the oldest copy, then the shortest path.
A suggestion is *clear* when one of the first two rules alone decides it.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np

from .duplicates import QUICK_BYTES, Catalog, copy_marked, full_hash, quick_hash
from .examples import ExampleIndex, vote
from .store import Store

PAUSE_EVERY = 200      # files; a short pause keeps the disk usable for other programs
PAUSE_SECONDS = 0.02
CLEAR_MARGIN = 0.25    # how much better the best place must fit than the next copy's place
MAX_PAIR_DIRS = 8      # groups spread over more folders don't count towards folder pairs


@dataclass
class Copy:
    id: int                 # catalog id
    key: str                # category folder (absolute)
    category: str           # category name
    relpath: str            # path inside the category folder
    size: int
    mtime: float

    @property
    def path(self) -> Path:
        return Path(self.key) / self.relpath

    @property
    def folder(self) -> str:
        """Absolute folder of the copy."""
        return str(self.path.parent)

    @property
    def label(self) -> str:
        return f"{self.category}/{self.relpath}"


@dataclass
class Group:
    hash: str
    size: int
    copies: list[Copy]
    keep: int | None = None            # catalog id of the suggested copy to keep
    reason: str = ""
    clear: bool = False
    place: dict[int, float] = field(default_factory=dict)  # copy id -> how well its folder fits (0..1.5)

    @property
    def spread(self) -> str:
        """folder: all copies in one folder; category: one category; categories: several."""
        if len({c.folder for c in self.copies}) == 1:
            return "folder"
        return "category" if len({c.key for c in self.copies}) == 1 else "categories"

    @property
    def wasted(self) -> int:
        return self.size * (len(self.copies) - 1)


# --- finding ---------------------------------------------------------------------------------

def _in(keys: list[str]) -> str:
    return ",".join("?" * len(keys))


def candidate_rows(store: Store, keys: list[str]) -> list[dict]:
    """Catalog rows of these categories whose size occurs more than once (the only possible duplicates)."""
    if not keys:
        return []
    return store.query(f"""
        SELECT id, category_key, relpath, size, quick_hash, full_hash FROM catalog
        WHERE category_key IN ({_in(keys)}) AND size > 0 AND size IN (
            SELECT size FROM catalog WHERE category_key IN ({_in(keys)}) AND size > 0
            GROUP BY size HAVING COUNT(*) > 1)""", (*keys, *keys))


def hash_candidates(catalog: Catalog, rows: list[dict], progress: Callable[[dict], None],
                    cancel: threading.Event) -> None:
    """Compute the missing quick and full hashes of these rows, only where still needed, and cache them."""
    def run(phase: str, todo: list[dict], column: str, nbytes: Callable[[dict], int],
            compute: Callable[[Path, int], str]) -> None:
        total = sum(nbytes(r) for r in todo)
        done_bytes, started, pending = 0, time.monotonic(), []
        for i, r in enumerate(todo, 1):
            if cancel.is_set():
                break
            try:
                r[column] = compute(Path(r["category_key"]) / r["relpath"], r["size"])
                pending.append((r[column], r["id"]))
            except OSError:
                pass  # gone or unreadable: simply not a duplicate
            done_bytes += nbytes(r)
            if i % PAUSE_EVERY == 0 or i == len(todo):
                catalog.store.executemany(f"UPDATE catalog SET {column} = ? WHERE id = ?", pending)
                pending = []
                rate = done_bytes / max(time.monotonic() - started, 1e-3)
                progress({"phase": phase, "done": i, "total": len(todo), "bytes_done": done_bytes,
                          "bytes_total": total, "eta_seconds": round((total - done_bytes) / rate) if rate else None})
                time.sleep(PAUSE_SECONDS)
        if pending:
            catalog.store.executemany(f"UPDATE catalog SET {column} = ? WHERE id = ?", pending)

    by_size: dict[int, list[dict]] = {}
    for r in rows:
        by_size.setdefault(r["size"], []).append(r)
    todo = [r for group in by_size.values() for r in group if r["quick_hash"] is None]
    run("comparing", todo, "quick_hash", lambda r: min(r["size"], QUICK_BYTES), lambda p, size: quick_hash(p, size))
    if cancel.is_set():
        return
    by_quick: dict[tuple, list[dict]] = {}
    for r in rows:
        if r["quick_hash"]:
            by_quick.setdefault((r["size"], r["quick_hash"]), []).append(r)
    # Small files were read completely by the quick hash already, but are still hashed the same way
    # as large ones, so every group is proven by a full hash.
    todo = [r for group in by_quick.values() if len(group) > 1 for r in group if r["full_hash"] is None]
    todo.sort(key=lambda r: r["size"])  # many small files first: most groups are found early
    run("verifying", todo, "full_hash", lambda r: r["size"], lambda p, _size: full_hash(p))


def find_groups(store: Store, names: dict[str, str]) -> list[Group]:
    """Every set of identical files in these categories (names: category key -> name), largest waste first."""
    keys = list(names)
    if not keys:
        return []
    rows = store.query(f"""
        SELECT id, category_key, relpath, size, mtime, full_hash FROM catalog
        WHERE category_key IN ({_in(keys)}) AND full_hash IN (
            SELECT full_hash FROM catalog WHERE category_key IN ({_in(keys)}) AND full_hash IS NOT NULL
            GROUP BY full_hash HAVING COUNT(*) > 1)""", (*keys, *keys))
    by_hash: dict[str, list[Copy]] = {}
    for r in rows:
        by_hash.setdefault(r["full_hash"], []).append(
            Copy(r["id"], r["category_key"], names[r["category_key"]], r["relpath"], r["size"], r["mtime"]))
    groups = [Group(h, copies[0].size, sorted(copies, key=lambda c: c.label)) for h, copies in by_hash.items()]
    return sorted(groups, key=lambda g: (-g.wasted, g.copies[0].label))


# --- which copy to keep --------------------------------------------------------------------------

def place_scores(index: ExampleIndex, groups: list[Group], allowed: set[str]) -> None:
    """How well each copy's place fits the file, from the most similar *other* sorted files (stored
    vectors, no model). Sets group.place. Groups whose files are not in the index get none."""
    model = index.embedder.model_name
    wanted = {(c.key, c.relpath) for g in groups for c in g.copies}
    if not wanted:
        return
    found: dict[tuple[str, str], tuple[int, bytes]] = {}
    for r in index.store.query("SELECT id, category_key, relpath, qvector FROM examples "
                               "WHERE model = ? AND origin = 'scan' AND qvector IS NOT NULL", (model,)):
        k = (r["category_key"], r["relpath"])
        if k in wanted:
            found[k] = (r["id"], r["qvector"])
    if not found:
        return
    exclude = {i for i, _ in found.values()}  # duplicated files don't vote: their places are in question
    asked, vecs = [], []
    for g in groups:
        own = [np.frombuffer(found[(c.key, c.relpath)][1], dtype=np.float16).astype(np.float32)
               for c in g.copies if (c.key, c.relpath) in found]
        if own:
            v = np.mean(own, axis=0)
            asked.append(g)
            vecs.append(v / (np.linalg.norm(v) or 1.0))
    if not asked:
        return
    for g, cand in zip(asked, index.candidates(np.stack(vecs), allowed, exclude), strict=True):
        probs, neighbours = vote(*cand, index.k, index.temperature)
        total = sum(n.weight for n in neighbours) or 1.0
        for c in g.copies:
            here = str(PurePosixPath(c.relpath).parent)
            same_folder = sum(n.weight for n in neighbours
                              if n.category_key == c.key and str(PurePosixPath(n.relpath).parent) == here)
            g.place[c.id] = probs.get(c.key, 0.0) + 0.5 * same_folder / total


def suggest(g: Group) -> None:
    """Choose the copy to keep and say why (see the module docstring)."""
    marked = {c.id: copy_marked(c.relpath.rsplit("/", 1)[-1]) for c in g.copies}
    originals = [c for c in g.copies if not marked[c.id]] or g.copies
    oldest = min(originals, key=lambda c: (c.mtime, len(c.relpath), c.label))
    if len(originals) == 1 < len(g.copies):
        g.keep, g.clear = originals[0].id, True
        g.reason = "The other names look like copies (\"(1)\", \"Kopie\", \"copy of\")."
        return
    by_fit = sorted(originals, key=lambda c: -g.place.get(c.id, 0.0))
    if g.place and len(by_fit) > 1 and g.place.get(by_fit[0].id, 0.0) - g.place.get(by_fit[1].id, 0.0) >= CLEAR_MARGIN:
        best = by_fit[0]
        where = "folder" if g.place[best.id] > 1.0 else "category"
        g.keep, g.clear = best.id, True
        g.reason = f"Its {where} fits best: the most similar sorted files are there."
        return
    g.keep, g.clear = oldest.id, False
    g.reason = ("All copies are in the same folder; the oldest one is suggested." if g.spread == "folder"
                else "No place fits clearly better; the oldest copy is suggested.")


# --- whole folders --------------------------------------------------------------------------------

def folder_pairs(groups: list[Group], files_in: dict[str, int], limit: int = 100) -> list[dict]:
    """Pairs of folders that share identical files, most shared bytes first, so a duplicated folder
    (e.g. an old backup copy) can be decided once. files_in: folder -> files directly in it."""
    pairs: dict[tuple[str, str], dict] = {}
    labels: dict[str, str] = {}
    for g in groups:
        folders = sorted({c.folder for c in g.copies})
        if not 1 < len(folders) <= MAX_PAIR_DIRS:
            continue
        for c in g.copies:
            labels.setdefault(c.folder, str(PurePosixPath(c.label).parent))
        for i, a in enumerate(folders):
            for b in folders[i + 1:]:
                p = pairs.setdefault((a, b), {"a": a, "b": b, "files": 0, "bytes": 0})
                p["files"] += 1
                p["bytes"] += g.size
    out = sorted(pairs.values(), key=lambda p: (-p["bytes"], -p["files"]))[:limit]
    for p in out:
        p.update(a_label=labels[p["a"]], b_label=labels[p["b"]],
                 a_files=files_in.get(p["a"], 0), b_files=files_in.get(p["b"], 0))
    return out


def files_per_folder(store: Store, keys: list[str]) -> dict[str, int]:
    """Number of files directly in each folder of these categories (from the catalog)."""
    counts: dict[str, int] = {}
    if keys:
        for r in store.query(f"SELECT category_key, relpath FROM catalog WHERE category_key IN ({_in(keys)})",
                             tuple(keys)):
            folder = os.path.dirname(os.path.join(r["category_key"], r["relpath"]))
            counts[folder] = counts.get(folder, 0) + 1
    return counts
