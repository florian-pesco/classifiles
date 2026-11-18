"""Search: find files in your folders and inboxes by name, folder, content, type, date and size.

The search index has two parts, both in SQLite:

- ``search_files``: every file in the searched locations (roots, manual category folders, inboxes)
  with a normalised name and folder (lower case, no accents, ``_ - .`` as spaces), kind, size and
  modification time. Listing is incremental: unchanged files are skipped. Searching by name is a
  substring match over this table (milliseconds for 100,000 files).
- ``search_text``: an FTS5 full-text index of the first ``TEXT_CHARS`` characters of documents
  (PDF text layer, Office, plain text). Read once per file version, never with OCR. Words match by
  prefix ("rechn" finds "Rechnung"); results are ranked by BM25 and come with a snippet.

Updates run in the background at low priority. Apply, undo and the Duplicates page keep the index
current for the files they move (``SearchIndex.track``); everything else is picked up by the next update.
Everything stays local.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from .duplicates import SKIP_DIRS
from .extract import extract
from .extract.base import kind_of
from .store import Store, now

TEXT_KINDS = ("pdf", "document", "spreadsheet", "presentation", "text")
TEXT_CHARS = 20_000           # characters of a document kept for searching
TEXT_MAX_BYTES = 50 * 2**20   # larger documents are found by name only
BATCH = 25                    # files per write; a short pause after each keeps the PC responsive
PAUSE_SECONDS = 0.02
MAX_HITS = 5_000              # per part (name / content); more is reported as "more than"
SKIP_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}

# Kind groups offered as filters (extract.base.KINDS -> what people call them).
GROUPS: dict[str, tuple[str, ...]] = {
    "documents": ("pdf", "document", "spreadsheet", "presentation", "text", "ebook"),
    "pictures": ("image",),
    "videos": ("video",),
    "audio": ("audio",),
    "archives": ("archive", "disk-image"),
    "installers": ("installer",),
    "code": ("code", "data"),
    "other": ("other", "font", "torrent"),
}

_SEPARATORS = re.compile(r"[\s_\-.+,;()\[\]{}]+")
HIT_START, HIT_END = "\x02", "\x03"  # marks around matched words in snippets


def normalize(s: str) -> str:
    """'Rechnung_März-2024.PDF' -> 'rechnung marz 2024 pdf' (for matching, not for display)."""
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(_SEPARATORS.split(s)).strip()


def terms_of(text: str) -> list[str]:
    return [t for t in normalize(text).split(" ") if t]


@dataclass
class Query:
    text: str = ""
    groups: list[str] = field(default_factory=list)  # kind groups (GROUPS keys); empty: all
    ext: str = ""                                    # e.g. "pdf" or ".pdf"
    after: float | None = None                       # modified at or after (epoch seconds)
    before: float | None = None                      # modified before
    min_size: int | None = None
    max_size: int | None = None
    location: str | None = None                      # one searched location; None: all
    match: str = "all"                               # all | name | content
    sort: str = "relevance"                          # relevance | newest | oldest | largest | name
    limit: int = 100

    def check(self) -> None:
        unknown = set(self.groups) - set(GROUPS)
        if unknown:
            raise ValueError(f"Unknown file types: {', '.join(sorted(unknown))}")
        if self.match not in ("all", "name", "content"):
            raise ValueError("match must be all, name or content")
        if self.sort not in ("relevance", "newest", "oldest", "largest", "name"):
            raise ValueError("sort must be relevance, newest, oldest, largest or name")
        if not 1 <= self.limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SearchIndex:
    def __init__(self, store: Store):
        self.store = store
        self.status: dict = {"state": "idle"}
        self._cancel = threading.Event()

    # --- updating ---------------------------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def counts(self) -> dict:
        files = self.store.one("SELECT COUNT(*) AS n FROM search_files")
        texts = self.store.one("SELECT COUNT(*) AS n FROM search_text")
        return {"files": files["n"] if files else 0, "texts": texts["n"] if texts else 0}

    def update(self, locations: list[str], read_text: bool = True) -> dict:
        """List the locations again (incremental) and read the text of new or changed documents.
        A location that is not reachable (e.g. an unplugged drive) keeps its entries."""
        self._cancel.clear()
        status: dict = {"state": "running", "phase": "listing", "started_at": now()}
        self.status = status
        try:
            gone = [r["location"] for r in self.store.query("SELECT DISTINCT location FROM search_files")
                    if r["location"] not in locations]
            for loc in gone:
                self._delete("location = ?", (loc,))
            for i, loc in enumerate(locations, 1):
                if self._cancel.is_set():
                    break
                if Path(loc).is_dir():
                    status["message"] = f"listing {Path(loc).name or loc} ({i}/{len(locations)})"
                    self._list(loc)
            if read_text and not self._cancel.is_set():
                self._read_texts(status)
            elif not read_text:
                self.store.execute("DELETE FROM search_text")
                self.store.execute("UPDATE search_files SET text_mtime = NULL")
            status.update(state="cancelled" if self._cancel.is_set() else "done", finished_at=now(),
                          message=None, **self.counts())
        except Exception as e:  # noqa: BLE001 - background update: recorded in the status
            status.update(state="failed", error=str(e)[:300])
        return status

    def _list(self, loc: str) -> None:
        existing = {r["relpath"]: r for r in self.store.query(
            "SELECT id, relpath, size, mtime FROM search_files WHERE location = ?", (loc,))}
        seen: set[str] = set()
        rows: list[tuple] = []
        for rel, size, mtime in walk(Path(loc), self._cancel):
            seen.add(rel)
            old = existing.get(rel)
            if old and old["size"] == size and abs(old["mtime"] - mtime) < 1:
                continue
            rows.append(_row(loc, rel, size, mtime))
            if len(rows) >= 1000:
                self._upsert(rows)
                rows = []
                time.sleep(PAUSE_SECONDS)
        self._upsert(rows)
        if not self._cancel.is_set():  # a cancelled listing hasn't seen everything: delete nothing
            ids = [(r["id"],) for rel, r in existing.items() if rel not in seen]
            self.store.executemany("DELETE FROM search_text WHERE rowid = ?", ids)
            self.store.executemany("DELETE FROM search_files WHERE id = ?", ids)

    def _upsert(self, rows: list[tuple]) -> None:
        self.store.executemany(
            "INSERT INTO search_files (location, relpath, name_norm, folder_norm, ext, kind, size, mtime) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (location, relpath) DO UPDATE SET "
            "name_norm = excluded.name_norm, folder_norm = excluded.folder_norm, ext = excluded.ext, "
            "kind = excluded.kind, size = excluded.size, mtime = excluded.mtime", rows)

    def _delete(self, where: str, params: tuple) -> None:
        self.store.execute(f"DELETE FROM search_text WHERE rowid IN (SELECT id FROM search_files WHERE {where})",
                           params)
        self.store.execute(f"DELETE FROM search_files WHERE {where}", params)

    def _read_texts(self, status: dict) -> None:
        marks = ",".join("?" * len(TEXT_KINDS))
        todo = self.store.query(
            f"SELECT id, location, relpath, mtime, size FROM search_files WHERE kind IN ({marks}) "
            "AND size > 0 AND size <= ? AND (text_mtime IS NULL OR text_mtime != mtime) ORDER BY mtime DESC",
            (*TEXT_KINDS, TEXT_MAX_BYTES))
        status.update(phase="reading", done=0, total=len(todo), message=None)
        started = time.monotonic()
        for start in range(0, len(todo), BATCH):
            if self._cancel.is_set():
                return
            texts, read = [], []
            for r in todo[start:start + BATCH]:
                try:
                    text = extract(Path(r["location"]) / r["relpath"], max_chars=TEXT_CHARS,
                                   ocr_pdf_pages=0, ocr_images=False).text
                except OSError:
                    continue  # gone since the listing
                if text:
                    texts.append((r["id"], text))
                read.append((r["mtime"], r["id"]))
            self.store.executemany("DELETE FROM search_text WHERE rowid = ?", [(i,) for _, i in read])
            self.store.executemany("INSERT INTO search_text (rowid, text) VALUES (?, ?)", texts)
            self.store.executemany("UPDATE search_files SET text_mtime = ? WHERE id = ?", read)
            done = min(start + BATCH, len(todo))
            rate = done / max(time.monotonic() - started, 1e-3)
            status.update(done=done, eta_seconds=round((len(todo) - done) / rate) if rate else None)
            time.sleep(PAUSE_SECONDS)

    # --- kept current by apply / undo / the Duplicates page ---------------------------------------

    def track(self, locations: list[str], src: Path | None, dst: Path | None) -> None:
        """A file moved from src to dst (either None: added or removed). A moved file keeps its text;
        files that leave the searched locations or go into a hidden folder (the trash) are dropped."""
        old = _locate(src, locations) if src else None
        new = _locate(dst, locations) if dst else None
        row = self.store.one("SELECT id FROM search_files WHERE location = ? AND relpath = ?", old) if old else None
        if new is None:
            if row:
                self._delete("id = ?", (row["id"],))
            return
        assert dst is not None
        try:
            st = dst.stat()
        except OSError:
            return
        values = _row(*new, st.st_size, st.st_mtime)
        if row:
            self.store.execute("UPDATE search_files SET location = ?, relpath = ?, name_norm = ?, folder_norm = ?, "
                               "ext = ?, kind = ?, size = ?, mtime = ? WHERE id = ?", (*values, row["id"]))
        else:
            self._upsert([values])

    # --- searching --------------------------------------------------------------------------------

    def search(self, q: Query) -> dict:
        """Matching files, best first: {"total", "more", "results", "took_ms"}."""
        q.check()
        started = time.monotonic()
        where, params = _filters(q)
        terms = terms_of(q.text)
        cols = "f.id, f.location, f.relpath, f.kind, f.ext, f.size, f.mtime, f.name_norm"
        hits: dict[int, dict] = {}
        more = False
        if not terms:
            order = {"oldest": "f.mtime ASC", "largest": "f.size DESC", "name": "f.name_norm ASC"}.get(q.sort,
                                                                                                    "f.mtime DESC")
            total = self.store.one(f"SELECT COUNT(*) AS n FROM search_files f WHERE {where}", tuple(params))
            rows = self.store.query(f"SELECT {cols} FROM search_files f WHERE {where} ORDER BY {order} LIMIT ?",
                                    (*params, q.limit))
            return {"total": total["n"] if total else 0, "more": False,
                    "results": [_result(r) for r in rows], "took_ms": _ms(started)}
        if q.match in ("all", "name"):
            cond = " AND ".join("(f.name_norm LIKE ? ESCAPE '\\' OR f.folder_norm LIKE ? ESCAPE '\\')" for _ in terms)
            like = [x for t in terms for x in (_like(t), _like(t))]
            rows = self.store.query(f"SELECT {cols} FROM search_files f WHERE {where} AND {cond} "
                                    "ORDER BY f.mtime DESC LIMIT ?", (*params, *like, MAX_HITS + 1))
            more |= len(rows) > MAX_HITS
            for r in rows[:MAX_HITS]:
                in_name = all(t in r["name_norm"] for t in terms)
                hits[r["id"]] = {**r, "in_name": in_name, "in_folder": not in_name, "in_content": False,
                                 "snippet": None, "rank": 0.0}
        if q.match in ("all", "content"):
            fts = " AND ".join('"' + t.replace('"', "") + '"*' for t in terms)
            try:
                rows = self.store.query(
                    f"SELECT {cols}, snippet(search_text, 0, ?, ?, '…', 14) AS snippet, bm25(search_text) AS rank "
                    f"FROM search_text JOIN search_files f ON f.id = search_text.rowid "
                    f"WHERE search_text MATCH ? AND {where} ORDER BY rank LIMIT ?",
                    (HIT_START, HIT_END, fts, *params, MAX_HITS + 1))
            except sqlite3.OperationalError:  # a query the full-text syntax can't take: no content hits
                rows = []
            more |= len(rows) > MAX_HITS
            for r in rows[:MAX_HITS]:
                h = hits.setdefault(r["id"], {**r, "in_name": False, "in_folder": False})
                h.update(in_content=True, snippet=r["snippet"], rank=r["rank"])
        ordered = sorted(hits.values(), key=_sort_key(q.sort))
        return {"total": len(hits), "more": more, "results": [_result(h) for h in ordered[:q.limit]],
                "took_ms": _ms(started)}


def _sort_key(sort: str) -> Callable[[dict], tuple]:
    if sort == "newest":
        return lambda h: (-h["mtime"],)
    if sort == "oldest":
        return lambda h: (h["mtime"],)
    if sort == "largest":
        return lambda h: (-h["size"],)
    if sort == "name":
        return lambda h: (h["name_norm"],)
    # relevance: every word in the file name first, then content (BM25), then folder-only matches
    return lambda h: (0 if h["in_name"] else 1 if h["in_content"] else 2, h.get("rank") or 0.0, -h["mtime"])


def _filters(q: Query) -> tuple[str, list]:
    where: list[str] = ["1 = 1"]
    params: list = []
    if q.groups:
        kinds = [k for g in q.groups for k in GROUPS[g]]
        where.append(f"f.kind IN ({','.join('?' * len(kinds))})")
        params += kinds
    if q.ext.strip():
        where.append("f.ext = ?")
        params.append("." + q.ext.strip().lower().lstrip("."))
    for column, op, value in (("mtime", ">=", q.after), ("mtime", "<", q.before),
                              ("size", ">=", q.min_size), ("size", "<=", q.max_size)):
        if value is not None:
            where.append(f"f.{column} {op} ?")
            params.append(value)
    if q.location:
        where.append("f.location = ?")
        params.append(q.location)
    return " AND ".join(where), params


def _result(r: dict) -> dict:
    rel = PurePosixPath(r["relpath"])
    loc = Path(r["location"])
    folder = PurePosixPath(loc.name or str(loc)) / rel.parent
    return {"id": r["id"], "name": rel.name, "folder": str(folder), "path": str(loc / rel), "location": r["location"],
            "kind": r["kind"], "ext": r["ext"], "size": r["size"],
            "modified": datetime.fromtimestamp(r["mtime"]).isoformat(timespec="minutes"),
            "in_name": r.get("in_name", False), "in_folder": r.get("in_folder", False),
            "in_content": r.get("in_content", False), "snippet": r.get("snippet")}


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _row(location: str, relpath: str, size: int, mtime: float) -> tuple:
    p = PurePosixPath(relpath)
    return (location, relpath, normalize(p.name), normalize(str(p.parent)) if str(p.parent) != "." else "",
            p.suffix.lower(), kind_of(Path(p.name)), size, mtime)


def _locate(path: Path, locations: list[str]) -> tuple[str, str] | None:
    """(location, relpath) of a path inside a searched location and not in a hidden folder."""
    for loc in locations:
        try:
            rel = path.relative_to(loc)
        except ValueError:
            continue
        if any(part.startswith(".") or part.lower() in SKIP_DIRS for part in rel.parts[:-1]):
            return None
        return loc, rel.as_posix()
    return None


def walk(root: Path, cancel: threading.Event) -> Iterator[tuple[str, int, float]]:
    """(relpath, size, mtime) of every file under root, without hidden and tool folders."""
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        if cancel.is_set():
            return
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in SKIP_DIRS]
        for name in filenames:
            if name.startswith(".") or name.lower() in SKIP_FILES:
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            yield os.path.relpath(full, root).replace(os.sep, "/"), st.st_size, st.st_mtime


def searched_locations(paths: list[str]) -> list[str]:
    """Distinct folders to search: a folder inside another one is covered by it."""
    out: list[str] = []
    for p in sorted({str(Path(x).expanduser()) for x in paths if x}, key=len):
        if not any(Path(p) == Path(o) or Path(o) in Path(p).parents for o in out):
            out.append(p)
    return out
