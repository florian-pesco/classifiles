"""Example index: learn what belongs in each category from the files already sorted there.

Each already-sorted file becomes an example: a short text (folder path inside the category, file
name, type and, for some documents, a content snippet) embedded into a vector. A new file is
classified by its nearest examples (see ExampleIndex.query).

Resource use is bounded on purpose:
- at most `per_category` files per category, sampled evenly across subfolders
- content is read for at most `content_per_category` documents per category (images: never)
- building runs in a background thread at low CPU priority, in small batches with pauses,
  is cancellable, and is incremental (unchanged files are not re-embedded)
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
import zlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .embedder import Embedder
from .extract import Evidence, extract, kind_of
from .store import Store, now
from .taxonomy import Category

log = logging.getLogger(__name__)

DELETE_KEY = "__delete__"
CONTENT_KINDS = ("pdf", "document", "presentation", "spreadsheet", "text", "data", "code")  # by priority
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".idea", ".vscode", ".gradle",
             "build", "dist", "target", "bin", "obj", ".cache", ".classifiles-trash", "site-packages",
             ".next", ".nuxt", ".tox", ".mypy_cache", ".pytest_cache"}
SKIP_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}
MAX_WALK = 60_000  # stop listing a category after this many files
BATCH = 32
PAUSE = 0.05  # seconds between batches, keeps the machine responsive
SNIPPET_CHARS = 400


def category_key(c: Category) -> str:
    return str(c.path) if c.path else DELETE_KEY


def doc_text(folder: str, name: str, kind: str, content: str = "") -> str:
    """Text of an example file (the 'document' side)."""
    t = f"folder: {folder}\nfile: {name}\ntype: {kind}"
    return t + (f"\ncontent: {content[:SNIPPET_CHARS]}" if content else "")


def query_text(ev: Evidence) -> str:
    """Text of a file to classify (the 'query' side): no folder, it has none yet."""
    t = f"file: {ev.name}\ntype: {ev.kind}"
    looks = ev.meta.get("looks_like")
    if looks:
        t += f" ({looks})"
    return t + (f"\ncontent: {ev.text[:SNIPPET_CHARS]}" if ev.text else "")


def description_text(c: Category) -> str:
    return f"folder: {c.path.name if c.path else c.name}" + (f"\ndescription: {c.description}" if c.description else "")


@dataclass
class Neighbour:
    category_key: str
    relpath: str
    similarity: float
    weight: float = 1.0  # vote weight relative to the best match (1.0 = best)


def sample_folder(root: Path, cap: int, max_walk: int = MAX_WALK) -> list[str]:
    """Up to `cap` relative file paths, spread evenly across all subfolders (round-robin), so one
    huge photo folder does not crowd out everything else. Skips hidden, build and VCS folders."""
    per_dir: deque = deque()
    walked = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS)
        files = [f for f in filenames if not f.startswith(".") and f.lower() not in SKIP_FILES]
        if files:
            files.sort()
            random.Random(zlib.crc32(dirpath.encode())).shuffle(files)  # deterministic per folder
            rel_dir = os.path.relpath(dirpath, root)
            per_dir.append(deque(os.path.normpath(os.path.join(rel_dir, f)) for f in files))
        walked += len(files)
        if walked >= max_walk:
            break
    out: list[str] = []
    while per_dir and len(out) < cap:
        files = per_dir.popleft()
        out.append(files.popleft())
        if files:
            per_dir.append(files)
    return out


class ExampleIndex:
    def __init__(self, store: Store, embedder: Embedder, per_category: int = 3000,
                 content_per_category: int = 300, k: int = 20, temperature: float = 0.05):
        self.store = store
        self.embedder = embedder
        self.per_category = per_category
        self.content_per_category = content_per_category
        self.k = k
        self.temperature = temperature
        self._data = None  # (ids, key_codes, keys, relpaths, matrix)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.status: dict = {"state": "idle"}

    # --- loading & querying -----------------------------------------------------------

    def load(self) -> None:
        rows = self.store.query("SELECT id, category_key, relpath, vector FROM examples WHERE model = ?",
                                (self.embedder.model_name,))
        if not rows:
            self._data = (np.zeros(0, int), np.zeros(0, int), [], [], np.zeros((0, 1), np.float32))
            return
        keys = sorted({r["category_key"] for r in rows})
        code = {k: i for i, k in enumerate(keys)}
        ids = np.array([r["id"] for r in rows])
        key_codes = np.array([code[r["category_key"]] for r in rows])
        relpaths = [r["relpath"] for r in rows]
        matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float16) for r in rows]).astype(np.float32)
        self._data = (ids, key_codes, keys, relpaths, matrix)  # swapped in one assignment

    def _get(self):
        if self._data is None:
            self.load()
        return self._data

    @property
    def size(self) -> int:
        return len(self._get()[0])

    def counts(self) -> dict[str, dict[str, int]]:
        """{category_key: {origin: n}} for the current model."""
        out: dict[str, dict[str, int]] = {}
        for r in self.store.query("SELECT category_key, origin, COUNT(*) AS n FROM examples WHERE model = ? "
                                  "GROUP BY category_key, origin", (self.embedder.model_name,)):
            out.setdefault(r["category_key"], {})[r["origin"]] = r["n"]
        return out

    def query(self, vec: np.ndarray, allowed: set[str], exclude_ids: set[int] | None = None,
              k: int | None = None) -> tuple[dict[str, float], list[Neighbour]]:
        """Probability per allowed category key from the k nearest examples, plus the nearest ones."""
        ids, key_codes, keys, relpaths, matrix = self._get()
        if not len(ids):
            return {}, []
        allowed_codes = [i for i, key in enumerate(keys) if key in allowed]
        mask = np.isin(key_codes, allowed_codes)
        if exclude_ids:
            mask &= ~np.isin(ids, list(exclude_ids))
        idx = np.flatnonzero(mask)
        if not len(idx):
            return {}, []
        sims = matrix[idx] @ vec
        k = min(k or self.k, len(idx))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        weights = np.exp((sims[top] - sims[top[0]]) / self.temperature)
        scores: dict[str, float] = {}
        for t, w in zip(top, weights):
            key = keys[key_codes[idx[t]]]
            scores[key] = scores.get(key, 0.0) + float(w)
        total = sum(scores.values())
        probs = {key: s / total for key, s in scores.items()}
        neighbours = [Neighbour(keys[key_codes[idx[t]]], relpaths[idx[t]], float(sims[t]), float(w))
                      for t, w in zip(top, weights)]  # all k, best first
        return probs, neighbours

    # --- building -----------------------------------------------------------------------

    @property
    def building(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_build(self, categories: list[Category], on_done: Callable[[], None] | None = None) -> None:
        if self.building:
            raise ValueError("The example index is already being built")
        self._cancel.clear()

        def run():
            try:
                os.nice(10)  # Linux: lowers this thread only
            except OSError:
                pass
            self.build(categories)
            if on_done:
                on_done()

        self._thread = threading.Thread(target=run, daemon=True, name="classifiles-index")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def _progress(self, **kw) -> None:
        self.status = {**self.status, **kw}

    def build(self, categories: list[Category]) -> dict:
        """Index all usable categories (incremental). Returns the final status."""
        model = self.embedder.model_name
        self.status = {"state": "running", "started_at": now(), "message": "starting", "done": 0, "total": 0,
                       "added": 0, "reused": 0, "removed": 0, "category": None, "error": None}
        try:
            self.store.execute("DELETE FROM examples WHERE model != ?", (model,))  # vectors of another model
            cats = [c for c in categories if c.usable]
            self._index_descriptions(cats)
            for i, c in enumerate(cats):
                if self._cancel.is_set():
                    break
                if c.path is None:
                    continue
                self._progress(category=c.name, message=f"listing {c.name} ({i + 1}/{len(cats)})", done=0, total=0)
                self._index_category(c)
            self.load()
            state = "cancelled" if self._cancel.is_set() else "done"
            self._progress(state=state, message=state, finished_at=now(), category=None)
        except Exception as e:  # noqa: BLE001 - surfaced in the UI
            log.exception("index build failed")
            self._progress(state="failed", error=str(e)[:500], finished_at=now())
        return self.status

    def _index_descriptions(self, cats: list[Category]) -> None:
        """One pseudo-example per category from its name/description, so every category is reachable
        even with an empty folder."""
        texts = [description_text(c) for c in cats]
        vecs = self.embedder.encode(texts)
        self.store.execute("DELETE FROM examples WHERE origin = 'description'")
        self.store.executemany(
            "INSERT OR REPLACE INTO examples (category_key, relpath, mtime, origin, has_content, model, vector) "
            "VALUES (?, '', 0, 'description', 0, ?, ?)",
            [(category_key(c), self.embedder.model_name, v.astype(np.float16).tobytes()) for c, v in zip(cats, vecs)])

    def _index_category(self, c: Category) -> None:
        key, model, root = category_key(c), self.embedder.model_name, c.path
        sample = sample_folder(root, self.per_category)
        existing = {r["relpath"]: r for r in self.store.query(
            "SELECT id, relpath, mtime, has_content FROM examples WHERE category_key = ? AND origin = 'scan' "
            "AND model = ?", (key, model))}

        # stat the sampled files (cheap compared to reading), find new/changed ones
        todo: list[tuple[str, float]] = []
        keep = set()
        for rel in sample:
            if self._cancel.is_set():
                return
            try:
                mtime = (root / rel).stat().st_mtime
            except OSError:
                continue
            keep.add(rel)
            row = existing.get(rel)
            if row and abs(row["mtime"] - mtime) < 1:
                continue
            todo.append((rel, mtime))
        stale = [r["id"] for rel, r in existing.items() if rel not in keep]
        if stale:
            self.store.executemany("DELETE FROM examples WHERE id = ?", [(i,) for i in stale])
        self._progress(reused=self.status["reused"] + len(keep) - len(todo),
                       removed=self.status["removed"] + len(stale))

        # read content for a capped number of documents, most useful kinds first
        with_content = sum(1 for rel in keep if existing.get(rel, {}).get("has_content")
                           and rel not in {t for t, _ in todo})
        budget = max(0, self.content_per_category - with_content)
        rank = {k: i for i, k in enumerate(CONTENT_KINDS)}
        readable = sorted((t for t in todo if kind_of(Path(t[0])) in rank), key=lambda t: rank[kind_of(Path(t[0]))])
        read_set = {rel for rel, _ in readable[:budget]}

        self._progress(message=f"indexing {c.name}", done=0, total=len(todo))
        for start in range(0, len(todo), BATCH):
            if self._cancel.is_set():
                return
            batch = todo[start:start + BATCH]
            texts, flags = [], []
            for rel, _ in batch:
                p = Path(rel)
                content = ""
                if rel in read_set:
                    try:
                        content = extract(root / rel, SNIPPET_CHARS).text
                    except OSError:
                        content = ""
                folder = os.path.join(root.name, str(p.parent)) if str(p.parent) != "." else root.name
                texts.append(doc_text(folder, p.name, kind_of(p), content))
                flags.append(1 if content else 0)
            vecs = self.embedder.encode(texts)
            self.store.executemany(
                "INSERT OR REPLACE INTO examples (category_key, relpath, mtime, origin, has_content, model, vector) "
                "VALUES (?, ?, ?, 'scan', ?, ?, ?)",
                [(key, rel, mtime, f, model, v.astype(np.float16).tobytes())
                 for (rel, mtime), f, v in zip(batch, flags, vecs)])
            self._progress(done=min(start + BATCH, len(todo)), added=self.status["added"] + len(batch))
            time.sleep(PAUSE)

    # --- learning from applied files ----------------------------------------------------

    def add(self, items: list[tuple[str, str, str]]) -> None:
        """Add learned examples: [(category_key, relpath, text)] (origin 'applied')."""
        if not items:
            return
        vecs = self.embedder.encode([t for _, _, t in items])
        self.store.executemany(
            "INSERT OR REPLACE INTO examples (category_key, relpath, mtime, origin, has_content, model, vector) "
            "VALUES (?, ?, ?, 'applied', 0, ?, ?)",
            [(k, rel, time.time(), self.embedder.model_name, v.astype(np.float16).tobytes())
             for (k, rel, _), v in zip(items, vecs)])
        self.load()

    def remove(self, items: list[tuple[str, str]]) -> None:
        """Forget learned examples [(category_key, relpath)] (e.g. after undo)."""
        if not items:
            return
        self.store.executemany("DELETE FROM examples WHERE category_key = ? AND relpath = ? AND origin = 'applied'",
                               items)
        self.load()
