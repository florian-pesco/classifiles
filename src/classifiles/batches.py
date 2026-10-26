"""Batch suggestions: groups of inbox files that are probably best reviewed together.

Four signals, all computed from what the scan stored (no file access, no model run):
- name pattern: the same name with the varying parts (numbers, dates, long IDs) taken out,
  e.g. "Scan_2025-03-01_0042.pdf" and "Scan_2025-04-12_0043.pdf" → "Scan_<date>_<n>.pdf"
- similar content: files whose text vectors are very close (cosine ≥ CONTENT_SIM)
- same proposal: undecided files the model confidently sends to the same category and folder
- same doubt: files below the threshold where the model hesitates between the same two categories

A file can be in several batches. Duplicates and applied files are left out; batches where every
file is decided already are dropped.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import PurePath

import numpy as np

MIN_SIZE = 3
CONTENT_SIM = 0.9
MAX_CONTENT_FILES = 3000  # similarity matrix is n²; beyond this, content clusters are skipped
MAX_BATCHES = 40
UNDECIDED = ("suggested", "needs_review")
WEIGHT = {"name": 1.0, "content": 1.0, "doubt": 0.9, "proposal": 0.6}  # ranking: patterns first

_DATE = re.compile(r"(?<!\d)(?:\d{4}[-_.]?\d{2}[-_.]?\d{2}|\d{2}[-_.]\d{2}[-_.]\d{4})(?!\d)")
_TIME = re.compile(r"(?<!\d)\d{2}[-_.:]\d{2}[-_.:]\d{2}(?!\d)")
_ID = re.compile(r"(?<![0-9a-z])(?=[0-9a-f-]*\d)(?=[0-9a-f-]*[a-f])[0-9a-f-]{8,}(?![0-9a-z])")
_NUM = re.compile(r"\d+")


def name_pattern(name: str, keep_case: bool = False) -> str:
    """The file name with its varying parts replaced: long hex IDs → <id>, dates → <date>,
    other numbers → <n>. Case and extension are normalised (unless keep_case, for display)."""
    p = PurePath(name)
    stem, ext = (p.stem, p.suffix) if keep_case else (p.stem.lower(), p.suffix.lower())
    stem = (_ID if not keep_case else re.compile(_ID.pattern, re.I)).sub("\0id\0", stem)
    stem = _DATE.sub("\0date\0", stem)
    stem = _TIME.sub("\0n\0", stem)
    stem = _NUM.sub("\0n\0", stem)
    stem = re.sub(r"\0(\w+)\0", r"<\1>", stem)
    stem = re.sub(r"(<n>[ _.-]?)+<n>", "<n>", stem)  # "1_2_3" → one number group
    return stem + ext


def _display(pattern: str) -> str:
    return pattern.replace("<date>", "‹date›").replace("<id>", "‹id›").replace("<n>", "‹n›")


def _where(p: dict) -> str:
    cat = p["final_target"] or p["target"] or "?"
    return f"{cat}/{p['subpath']}" if p.get("subpath") else cat


def _top_two(p: dict) -> tuple[str, str] | None:
    alts = p.get("alternatives") or []
    if len(alts) < 2:
        return None
    return tuple(sorted((alts[0]["folder"], alts[1]["folder"])))


def _batch(kind: str, key: str, title: str, why: str, members: list[dict]) -> dict:
    undecided = sum(m["status"] in UNDECIDED for m in members)
    dist = Counter(_where(m) for m in members)
    return {
        "id": f"{kind}:{key}", "kind": kind, "title": title, "why": why,
        "ids": [m["id"] for m in members], "size": len(members), "undecided": undecided,
        "distribution": [{"where": w, "count": n} for w, n in dist.most_common()],
        "uniform": len(dist) == 1,
    }


def find_batches(proposals: list[dict], vectors: dict[int, np.ndarray], threshold: float) -> list[dict]:
    """proposals: rows of one scan; vectors: text vector per proposal id (may be incomplete)."""
    pool = [p for p in proposals if p["source"] != "duplicate" and p["status"] != "moved"]
    out: list[dict] = []

    # 1. name patterns
    by_pattern: dict[str, list[dict]] = defaultdict(list)
    for p in pool:
        by_pattern[name_pattern(p["name"])].append(p)
    name_sets = []
    for pattern, members in by_pattern.items():
        if len(members) >= MIN_SIZE and "<" in pattern:
            shown = _display(name_pattern(members[0]["name"], keep_case=True))
            out.append(_batch("name", pattern, shown, f"{len(members)} files named like “{shown}”", members))
            name_sets.append({m["id"] for m in members})

    # 2. similar content (greedy: the file with most close neighbours takes them, repeat)
    cand = [p for p in pool if p["id"] in vectors]
    if MIN_SIZE <= len(cand) <= MAX_CONTENT_FILES:
        m = np.stack([vectors[p["id"]] for p in cand]).astype(np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        close = (m @ m.T) >= CONTENT_SIM
        free = np.ones(len(cand), bool)
        while True:
            counts = (close & free[None, :]).sum(axis=1) * free
            leader = int(np.argmax(counts))
            if counts[leader] < MIN_SIZE:
                break
            idx = np.flatnonzero(close[leader] & free)
            free[idx] = False
            members = [cand[i] for i in idx]
            ids = {p["id"] for p in members}
            if any(len(ids & s) >= 0.8 * len(ids) for s in name_sets):
                continue  # already a name batch
            sample = cand[leader]["name"]
            out.append(_batch("content", str(cand[leader]["id"]), f"Similar to “{sample}”",
                              f"{len(members)} files with very similar names/content", members))

    # 3. same confident proposal, 4. same doubt
    by_target: dict[str, list[dict]] = defaultdict(list)
    by_doubt: dict[tuple, list[dict]] = defaultdict(list)
    for p in pool:
        if p["status"] not in UNDECIDED or p["source"] == "rule" or not p["target"]:
            continue
        if p["confidence"] >= threshold:
            by_target[_where(p)].append(p)
        elif (pair := _top_two(p)) is not None:
            by_doubt[pair].append(p)
    for where, members in by_target.items():
        if len(members) >= MIN_SIZE:
            out.append(_batch("proposal", where, f"→ {where}",
                              f"{len(members)} files the model confidently puts into {where}", members))
    for (a, b), members in by_doubt.items():
        if len(members) >= MIN_SIZE:
            out.append(_batch("doubt", f"{a}|{b}", f"{a} or {b}?",
                              f"{len(members)} files where the model hesitates between {a} and {b}", members))

    out = [b for b in out if b["undecided"]]
    out.sort(key=lambda b: (-b["undecided"] * WEIGHT[b["kind"]], b["title"]))
    return out[:MAX_BATCHES]
