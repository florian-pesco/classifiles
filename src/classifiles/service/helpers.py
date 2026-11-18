"""Constants and small helpers shared by the service modules."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..examples import DELETE_KEY, inbox_text

log = logging.getLogger("classifiles.service")


# Partial downloads and OS junk are never classified.
SKIP_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload"}


SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


REVIEW_ACTIONS = {"accept", "reject", "reset"}


# Proposals joined with their active (not undone) journal entry, if any.
PROPOSALS_SQL = """
SELECT p.*, m.action AS applied_action, m.dst AS applied_dst
FROM proposals p
LEFT JOIN moves m ON m.proposal_id = p.id AND m.undone_at IS NULL
"""


def _learned_key(dst: str, action: str, cat_key: str | None = None) -> tuple[str, str]:
    """(category_key, relpath) under which an applied file is learned. Trashed files belong to
    Delete. Otherwise the relpath is the file's path inside its category folder. Moves recorded
    before category_key was journaled landed directly in the category folder (its parent)."""
    p = Path(dst)
    if action == "delete":
        return DELETE_KEY, p.name
    if cat_key and cat_key != DELETE_KEY:
        try:
            return cat_key, p.relative_to(cat_key).as_posix()
        except ValueError:
            pass
    return str(p.parent), p.name


def _inbox_text(name: str, kind: str, evidence: dict) -> str:
    """Inbox-style text of a decided file, from its stored evidence."""
    meta = evidence.get("meta") or {}
    return inbox_text(name, kind, evidence.get("text", ""), meta.get("looks_like"), meta.get("source"))


def _learned_folder(key: str, relpath: str) -> str:
    """Folder text for a learned example: category folder name plus the subfolder, if any."""
    if key == DELETE_KEY:
        return "Delete"
    sub = os.path.dirname(relpath)
    return f"{Path(key).name}/{sub}" if sub else Path(key).name


def _vec_bytes(v) -> bytes | None:
    import numpy as np

    return None if v is None else np.asarray(v, dtype=np.float16).tobytes()


def _vec(b):
    import numpy as np

    return None if b is None else np.frombuffer(b, dtype=np.float16).astype(np.float32)


def _without_vectors(p: dict) -> dict:
    """Proposals as handed to callers (UI/API): the stored vectors are internal binary data."""
    p.pop("qvec", None)
    p.pop("ivec", None)
    return p


def _quietly(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        log.exception("learning from applied files failed")
