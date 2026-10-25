"""Exact duplicate detection for inbox files: against the sorted folders and within the inbox.

A file is a duplicate when its content is byte-for-byte identical to another file. To avoid
reading everything, files are compared in three steps, each only for the files still in question:

    same size  ->  same quick hash (first 64 KB + size)  ->  same full hash (whole file, BLAKE2b)

The *catalog* lists every file in the category folders with its size (built in the background with
the example index, kept up to date on apply/undo). Hashes of catalog files are computed only when an
inbox file of the same size turns up, and cached. Everything stays local.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .store import Store, now
from .taxonomy import Category

log = logging.getLogger(__name__)

QUICK_BYTES = 64 * 1024
CHUNK = 1024 * 1024
MIN_SIZE = 1  # empty files are never duplicates of anything meaningful
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".gradle",
             ".cache", ".classifiles-trash", "site-packages", ".tox", ".mypy_cache", ".pytest_cache"}
_COPY_MARK = re.compile(r"(\s*\(\d+\)|\s*-\s*(kopie|copy)(\s*\(\d+\))?|^(kopie von|copy of)\s+|[_\s-]copy\d*)",
                        re.IGNORECASE)


def quick_hash(path: Path, size: int) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        h.update(f.read(QUICK_BYTES))
    h.update(str(size).encode())
    return h.hexdigest()


def full_hash(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def copy_marked(name: str) -> bool:
    """'report (1).pdf', 'report - Kopie.pdf', 'Copy of report.pdf' look like copies."""
    return bool(_COPY_MARK.search(Path(name).stem))


def original_first(paths: list[Path]) -> list[Path]:
    """Order identical inbox files so the one to keep comes first: no copy marker, then oldest,
    then shortest name."""
    def key(p: Path):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = float("inf")
        return (copy_marked(p.name), mtime, len(p.name), p.name)
    return sorted(paths, key=key)


@dataclass
class Duplicate:
    of: str                     # what it duplicates, for display: "Studies/Lectures/x.pdf" or "x.pdf" (inbox)
    kind: str                   # "sorted" | "inbox"
    path: str                   # absolute path of that file
    content_hash: str = ""


@dataclass
class _Entry:
    path: Path
    size: int
    quick: str | None = None
    full: str | None = None
    catalog_id: int | None = None
    label: str = ""
    extra: dict = field(default_factory=dict)


class Catalog:
    """All files in the category folders with size (+ cached hashes), in SQLite table `catalog`."""

    def __init__(self, store: Store):
        self.store = store
        self.status: dict = {"state": "idle"}
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def count(self) -> int:
        return self.store.one("SELECT COUNT(*) AS n FROM catalog")["n"]

    def build(self, categories: list[Category]) -> dict:
        """List every file of every usable folder category (incremental: unchanged files keep their
        cached hashes). Runs in the index thread at low priority."""
        self._cancel.clear()
        self.status = {"state": "running", "message": "listing files for duplicate detection", "done": 0,
                       "started_at": now()}
        try:
            cats = [c for c in categories if c.usable and c.path is not None]
            keys = set()
            for i, c in enumerate(cats, 1):
                if self._cancel.is_set():
                    break
                key = str(c.path)
                keys.add(key)
                self.status = {**self.status, "message": f"listing {c.name} ({i}/{len(cats)})"}
                self._catalog_category(key, c.path)
            if not self._cancel.is_set():  # categories that are gone or disabled
                stale = [r["category_key"] for r in self.store.query("SELECT DISTINCT category_key FROM catalog")
                         if r["category_key"] not in keys]
                self.store.executemany("DELETE FROM catalog WHERE category_key = ?", [(k,) for k in stale])
            state = "cancelled" if self._cancel.is_set() else "done"
            self.status = {**self.status, "state": state, "message": state, "finished_at": now(),
                           "files": self.count()}
        except Exception as e:  # noqa: BLE001 - shown in the UI
            log.exception("catalog build failed")
            self.status = {**self.status, "state": "failed", "error": str(e)[:500]}
        return self.status

    def _catalog_category(self, key: str, root: Path) -> None:
        existing = {r["relpath"]: r for r in self.store.query(
            "SELECT id, relpath, size, mtime FROM catalog WHERE category_key = ?", (key,))}
        seen, upserts = set(), []
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            if self._cancel.is_set():
                return
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS]
            for name in filenames:
                if name.startswith("."):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, root)
                seen.add(rel)
                row = existing.get(rel)
                if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1:
                    continue
                upserts.append((key, rel, st.st_size, st.st_mtime))
                if len(upserts) >= 1000:
                    self._upsert(upserts)
                    self.status = {**self.status, "done": self.status.get("done", 0) + len(upserts)}
                    upserts = []
                    time.sleep(0.01)
        self._upsert(upserts)
        gone = [r["id"] for rel, r in existing.items() if rel not in seen]
        self.store.executemany("DELETE FROM catalog WHERE id = ?", [(i,) for i in gone])

    def _upsert(self, rows: list[tuple]) -> None:
        # a changed file loses its cached hashes
        self.store.executemany(
            "INSERT INTO catalog (category_key, relpath, size, mtime) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (category_key, relpath) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, "
            "quick_hash = NULL, full_hash = NULL", rows)

    # --- kept current by apply / undo -------------------------------------------------------

    def add(self, key: str, relpath: str, path: Path, full: str | None = None) -> None:
        try:
            st = path.stat()
        except OSError:
            return
        self.store.execute(
            "INSERT INTO catalog (category_key, relpath, size, mtime, full_hash) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (category_key, relpath) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, "
            "quick_hash = NULL, full_hash = excluded.full_hash", (key, relpath, st.st_size, st.st_mtime, full))

    def remove(self, key: str, relpath: str) -> None:
        self.store.execute("DELETE FROM catalog WHERE category_key = ? AND relpath = ?", (key, relpath))

    def same_size(self, sizes: set[int]) -> list[dict]:
        if not sizes:
            return []
        out = []
        sizes_list = sorted(sizes)
        for i in range(0, len(sizes_list), 500):
            part = sizes_list[i:i + 500]
            marks = ",".join("?" * len(part))
            out += self.store.query(f"SELECT * FROM catalog WHERE size IN ({marks})", tuple(part))
        return out

    def cache_hashes(self, catalog_id: int, quick: str | None, full: str | None) -> None:
        self.store.execute("UPDATE catalog SET quick_hash = COALESCE(?, quick_hash), "
                           "full_hash = COALESCE(?, full_hash) WHERE id = ?", (quick, full, catalog_id))


def find_duplicates(inbox_files: list[Path], catalog: Catalog | None, labels: dict[str, str],
                    max_candidates: int = 50) -> tuple[dict[Path, Duplicate], dict[Path, str]]:
    """Duplicates among inbox files and against the catalog.

    labels: category_key -> category name (for display). Returns ({inbox path: Duplicate},
    {inbox path: full hash} for every inbox file that was hashed)."""
    inbox: list[_Entry] = []
    for p in inbox_files:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size >= MIN_SIZE:
            inbox.append(_Entry(p, size, label=p.name))
    by_size: dict[int, list[_Entry]] = {}
    for e in inbox:
        by_size.setdefault(e.size, []).append(e)

    sorted_entries: dict[int, list[_Entry]] = {}
    if catalog is not None:
        for r in catalog.same_size(set(by_size)):
            key = r["category_key"]
            name = labels.get(key)
            if name is None:
                continue  # category disabled or gone
            sorted_entries.setdefault(r["size"], []).append(_Entry(
                Path(key) / r["relpath"], r["size"], r["quick_hash"], r["full_hash"], r["id"],
                f"{name}/{Path(r['relpath']).as_posix()}"))

    hashes: dict[Path, str] = {}
    result: dict[Path, Duplicate] = {}

    def quick(e: _Entry) -> str | None:
        if e.quick is None:
            try:
                e.quick = quick_hash(e.path, e.size)
            except OSError:
                return None
            if e.catalog_id is not None:
                catalog.cache_hashes(e.catalog_id, e.quick, None)
        return e.quick

    def full(e: _Entry) -> str | None:
        if e.full is None:
            try:
                e.full = full_hash(e.path)
            except OSError:
                return None
            if e.catalog_id is not None:
                catalog.cache_hashes(e.catalog_id, None, e.full)
        return e.full

    for size, group in by_size.items():
        others = sorted_entries.get(size, [])[:max_candidates]
        if len(group) < 2 and not others:
            continue  # unique size: certainly no duplicate, nothing is read
        everyone = group + others
        by_quick: dict[str, list[_Entry]] = {}
        for e in everyone:
            q = quick(e)
            if q:
                by_quick.setdefault(q, []).append(e)
        for same in by_quick.values():
            if len(same) < 2 or not any(e.catalog_id is None for e in same):
                continue
            by_full: dict[str, list[_Entry]] = {}
            for e in same:
                f = full(e)
                if f:
                    by_full.setdefault(f, []).append(e)
            for digest, identical in by_full.items():
                inbox_copies = [e for e in identical if e.catalog_id is None]
                sorted_copies = [e for e in identical if e.catalog_id is not None]
                for e in inbox_copies:
                    hashes[e.path] = digest
                if not inbox_copies:
                    continue
                if sorted_copies:  # already sorted: every inbox copy is a duplicate
                    ref = sorted_copies[0]
                    for e in inbox_copies:
                        result[e.path] = Duplicate(ref.label, "sorted", str(ref.path), digest)
                elif len(inbox_copies) > 1:  # only in the inbox: keep the "original"
                    keep, *copies = original_first([e.path for e in inbox_copies])
                    for p in copies:
                        result[p] = Duplicate(keep.name, "inbox", str(keep), digest)
    return result, hashes
