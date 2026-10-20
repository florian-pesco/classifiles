"""Measure classification quality on files you already sorted.

A sample of indexed files is held out: they are hidden from the example index and classified as if
they were new inbox files (only name, type and content, never their folder). Their real category is
known, so accuracy and calibration can be measured instead of guessed. Read-only: nothing is moved.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from .classifier import Classifier
from .deciders import make_decider
from .deciders.examples import ExampleDecider
from .examples import category_key
from .extract import extract

CONFIDENCE_BUCKETS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]


def pick_holdout(service, per_category: int, seed: int = 0) -> list[dict]:
    """Up to per_category indexed files per model-eligible folder category, that still exist."""
    cats = [c for c in service.categories(refresh=True) if c.usable and c.suggest and c.path]
    rng = random.Random(seed)
    out = []
    for c in cats:
        rows = service.store.query("SELECT id, relpath FROM examples WHERE category_key = ? AND origin = 'scan' "
                                   "AND model = ?", (category_key(c), service.settings.embedding_model))
        rng.shuffle(rows)
        picked = 0
        for r in rows:
            path = c.path / r["relpath"]
            if path.is_file():
                out.append({"id": r["id"], "path": path, "category": c.name})
                picked += 1
                if picked >= per_category:
                    break
    return out


def evaluate(service, backends: list[str], per_category: int = 30, seed: int = 0,
             progress: Callable[[str, int, int], None] | None = None) -> dict:
    holdout = pick_holdout(service, per_category, seed)
    if not holdout:
        raise ValueError("Nothing to evaluate: build the example index first.")
    cats = service.categories()
    excluded = {h["id"] for h in holdout}
    evidence = []
    for i, h in enumerate(holdout, 1):
        if progress:
            progress("reading files", i, len(holdout))
        evidence.append(extract(h["path"], service.settings.max_text_chars))

    results = {}
    for backend in backends:
        if backend == "examples":
            index = service.example_index()
            clf = Classifier(cats, ExampleDecider(index))
            clf.decider_kwargs = {"exclude_ids": excluded}
        else:
            if service._index is not None:  # free GPU memory for the other model
                service._index.embedder.unload()
            clf = Classifier(cats, make_decider(backend, laya_model=service.settings.laya_model))
        rows = []
        for i, (h, ev) in enumerate(zip(holdout, evidence), 1):
            if progress:
                progress(f"classifying ({backend})", i, len(holdout))
            c = clf.classify(ev)
            top3 = [a["folder"] for a in c.alternatives[:3]]
            rows.append({"name": h["path"].name, "true": h["category"], "pred": c.target,
                         "confidence": c.confidence, "top3": top3})
        results[backend] = summarize(rows)
    return {"files": len(holdout), "per_category": per_category, "results": results}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    correct = [r["pred"] == r["true"] for r in rows]
    by_true = defaultdict(list)
    for r, ok in zip(rows, correct):
        by_true[r["true"]].append(ok)
    buckets = []
    for lo, hi in CONFIDENCE_BUCKETS:
        sel = [ok for r, ok in zip(rows, correct) if lo <= r["confidence"] < hi]
        buckets.append({"range": f"{lo:.1f}–{min(hi, 1):.1f}", "files": len(sel),
                        "accuracy": round(sum(sel) / len(sel), 3) if sel else None})
    thresholds = []
    for t in THRESHOLDS:
        sel = [ok for r, ok in zip(rows, correct) if r["confidence"] >= t]
        thresholds.append({"threshold": t, "suggested": round(len(sel) / n, 3),
                           "accuracy": round(sum(sel) / len(sel), 3) if sel else None})
    ok_conf = [r["confidence"] for r, ok in zip(rows, correct) if ok]
    bad_conf = [r["confidence"] for r, ok in zip(rows, correct) if not ok]
    return {
        "accuracy": round(sum(correct) / n, 3),
        "top3": round(sum(r["true"] in r["top3"] for r in rows) / n, 3),
        "mean_confidence_right": round(sum(ok_conf) / len(ok_conf), 3) if ok_conf else None,
        "mean_confidence_wrong": round(sum(bad_conf) / len(bad_conf), 3) if bad_conf else None,
        "per_category": {k: {"files": len(v), "recall": round(sum(v) / len(v), 3)} for k, v in sorted(by_true.items())},
        "confusions": Counter(f"{r['true']} → {r['pred']}" for r, ok in zip(rows, correct) if not ok).most_common(8),
        "calibration": buckets,
        "thresholds": thresholds,
        "mistakes": [r for r, ok in zip(rows, correct) if not ok][:15],
    }
