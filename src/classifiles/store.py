"""SQLite persistence: scans, proposals (one per file) and the move journal."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    inbox TEXT NOT NULL,
    target_root TEXT NOT NULL,
    backend TEXT NOT NULL,
    threshold REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    phase TEXT,                               -- classify | second_opinion
    llm_total INTEGER NOT NULL DEFAULT 0,
    llm_done INTEGER NOT NULL DEFAULT 0,
    llm_error TEXT,
    max_files INTEGER,                        -- NULL = no limit
    only_new INTEGER NOT NULL DEFAULT 0,      -- 1 = skip files classified in earlier scans
    inbox_total INTEGER NOT NULL DEFAULT 0,   -- files in the inbox when the scan started
    total INTEGER NOT NULL DEFAULT 0,         -- files selected for this scan
    processed INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    evidence TEXT NOT NULL,             -- JSON
    target TEXT,                        -- model/rule proposal
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    alternatives TEXT NOT NULL,         -- JSON
    reason TEXT NOT NULL DEFAULT '',
    -- suggested (>= threshold) | needs_review | accepted | rejected | moved (= applied: moved, copied or trashed; see moves.action)
    status TEXT NOT NULL,
    final_target TEXT,                  -- what the user accepted (defaults to target)
    decided_via TEXT,                   -- confirm | override | bulk | reject (NULL: undecided or before tracking)
    neighbours TEXT,                    -- JSON: most similar examples [{category, dir, weight}] (example backend)
    suggested_subpath TEXT,             -- folder inside the category suggested by the model ('' = top level)
    subpath TEXT,                       -- folder inside the category that apply will use ('' = top level)
    new_folder TEXT,                    -- suggested NEW subpath (not created unless chosen as subpath)
    knn_confidence REAL,                -- example model's confidence before a second opinion
    llm_choice TEXT,                    -- category the language model chose (second opinion)
    llm_confidence REAL,                -- its probability for that choice
    duplicate_of TEXT,                  -- identical file: "Category/path" (sorted) or inbox file name
    duplicate_kind TEXT,                -- sorted | inbox
    duplicate_path TEXT,                -- absolute path of that identical file
    content_hash TEXT,                  -- full hash, if the file was hashed
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_scan ON proposals(scan_id, status);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    proposal_id INTEGER REFERENCES proposals(id),
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'move',  -- move | copy | delete
    category_key TEXT,                    -- category folder path (or '__delete__')
    created_dir TEXT,                     -- topmost folder created for this move (removed on undo if empty)
    moved_at TEXT NOT NULL,
    undone_at TEXT,
    purged_at TEXT                        -- delete only: trash emptied, no longer undoable
);
-- Every file in the category folders, for duplicate detection (see duplicates.py).
CREATE TABLE IF NOT EXISTS catalog (
    id INTEGER PRIMARY KEY,
    category_key TEXT NOT NULL,
    relpath TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    quick_hash TEXT,                      -- first 64 KB + size, computed on demand
    full_hash TEXT,                       -- whole file, computed on demand
    UNIQUE (category_key, relpath)
);
CREATE INDEX IF NOT EXISTS catalog_size ON catalog(size);
-- Already-sorted files used as examples by the example-based classifier (see examples.py).
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY,
    category_key TEXT NOT NULL,           -- category folder path, or '__delete__'
    relpath TEXT NOT NULL,                -- path inside the category folder
    mtime REAL NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT 'scan',  -- scan (indexed from disk) | applied (learned from you) | description
    has_content INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,
    vector BLOB NOT NULL,                 -- float16, L2-normalised
    UNIQUE (category_key, relpath, origin)
);
"""

# Columns added after the first release: (table, column, definition). Applied to older databases.
MIGRATIONS = [
    ("scans", "max_files", "INTEGER"),
    ("scans", "only_new", "INTEGER NOT NULL DEFAULT 0"),
    ("scans", "inbox_total", "INTEGER NOT NULL DEFAULT 0"),
    ("moves", "action", "TEXT NOT NULL DEFAULT 'move'"),
    ("moves", "purged_at", "TEXT"),
    ("proposals", "decided_via", "TEXT"),
    ("proposals", "neighbours", "TEXT"),
    ("proposals", "suggested_subpath", "TEXT"),
    ("proposals", "subpath", "TEXT"),
    ("proposals", "new_folder", "TEXT"),
    ("moves", "category_key", "TEXT"),
    ("moves", "created_dir", "TEXT"),
    ("proposals", "knn_confidence", "REAL"),
    ("proposals", "llm_choice", "TEXT"),
    ("proposals", "llm_confidence", "REAL"),
    ("scans", "phase", "TEXT"),
    ("scans", "llm_total", "INTEGER NOT NULL DEFAULT 0"),
    ("scans", "llm_done", "INTEGER NOT NULL DEFAULT 0"),
    ("scans", "llm_error", "TEXT"),
    ("proposals", "duplicate_of", "TEXT"),
    ("proposals", "duplicate_kind", "TEXT"),
    ("proposals", "duplicate_path", "TEXT"),
    ("proposals", "content_hash", "TEXT"),
]

JSON_FIELDS = ("evidence", "alternatives", "neighbours")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                existing = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self._db.commit()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def executemany(self, sql: str, rows: list[tuple] | list[dict]) -> None:
        with self._lock:
            self._db.executemany(sql, rows)
            self._db.commit()

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, values: dict) -> int:
        values = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k, v in values.items()}
        cols = ", ".join(values)
        marks = ", ".join(f":{k}" for k in values)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values).lastrowid

    def update(self, table: str, id: int, values: dict) -> None:
        values = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
                  for k, v in values.items()}
        sets = ", ".join(f"{k} = :{k}" for k in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = :id", {**values, "id": id})


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_FIELDS:
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d
