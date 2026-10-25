"""Measure classification quality. Read-only: nothing is moved, and results are aggregates only
(counts, rates, category names), never file names or contents.

Three measurements:
- held-out, split "file": hide individual already-sorted files from the index and classify them
  like inbox files. Optimistic: their sibling files stay in the index.
- held-out, split "folder": hide the file's whole folder (all siblings). Simulates a file from a
  topic you have not sorted yet; closer to real inbox files.
- real decisions: compare proposals with what you actually chose in Review.
"""

from __future__ import annotations

import os
import random
from collections import Counter, defaultdict
from typing import Callable

from .classifier import Classifier
from .deciders import make_decider
from .deciders.examples import ExampleDecider
from .examples import category_key
from .extract.base import Evidence
from .source import download_sources

CONFIDENCE_BUCKETS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9]
SPLITS = ("file", "folder")


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
                out.append({"id": r["id"], "path": path, "category": c.name, "key": category_key(c),
                            "folder": os.path.dirname(r["relpath"])})
                picked += 1
                if picked >= per_category:
                    break
    return out


def _sibling_ids(service, holdout: list[dict]) -> dict[int, set[int]]:
    """For split 'folder': per held-out file, the ids of all examples in the same folder."""
    by_folder: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in service.store.query("SELECT id, category_key, relpath FROM examples WHERE model = ?",
                                 (service.settings.embedding_model,)):
        by_folder[(r["category_key"], os.path.dirname(r["relpath"]))].add(r["id"])
    return {h["id"]: by_folder[(h["key"], h["folder"])] | {h["id"]} for h in holdout}


def evaluate(service, backends: list[str], per_category: int = 30, seed: int = 0,
             splits: tuple[str, ...] = SPLITS, progress: Callable[[str, int, int], None] | None = None) -> dict:
    service.check_ready()
    holdout = pick_holdout(service, per_category, seed)
    if not holdout:
        raise ValueError("Nothing to evaluate: build the example index first.")
    cats = service.categories()
    all_held = {h["id"] for h in holdout}
    siblings = _sibling_ids(service, holdout) if "folder" in splits else {}
    # read held-out files exactly like inbox files (OCR, download source)
    sources = download_sources([h["path"] for h in holdout]) if service.settings.use_download_source else {}
    evidence = []
    for i, h in enumerate(holdout, 1):
        if progress:
            progress("reading files", i, len(holdout))
        evidence.append(service.read_file(h["path"], sources.get(h["path"])))

    results = {}
    for backend in backends:
        if backend == "examples":
            clf = Classifier(cats, ExampleDecider(service.example_index()))
            runs = splits
        else:
            if service._index is not None:  # free GPU memory for the other model
                service._index.embedder.unload()
            clf = Classifier(cats, make_decider(backend, laya_model=service.settings.laya_model))
            runs = ("file",)  # other backends don't use the index, so the split makes no difference
        for split in runs:
            rows = []
            for i, (h, ev) in enumerate(zip(holdout, evidence), 1):
                if progress:
                    progress(f"classifying ({backend}, {split})", i, len(holdout))
                if backend == "examples":
                    hidden = all_held | (siblings.get(h["id"], set()) if split == "folder" else set())
                    clf.decider_kwargs = {"exclude_ids": hidden}
                c = clf.classify(ev)
                rows.append({"true": h["category"], "pred": c.target, "confidence": c.confidence,
                             "top3": [a["folder"] for a in c.alternatives[:3]]})
            label = backend if backend != "examples" else f"examples ({split} split)"
            results[label] = summarize(rows)
    return {"files": len(holdout), "per_category": per_category, "results": results}


def decided_proposals(service) -> list[dict]:
    """Proposals you decided in Review (accepted, overridden or applied), excluding rule hits."""
    return service.store.query("""
        SELECT p.id, p.evidence, p.target, p.final_target, p.confidence, p.alternatives, p.decided_via,
               s.backend, m.dst, m.action, m.category_key
        FROM proposals p
        JOIN scans s ON s.id = p.scan_id
        LEFT JOIN moves m ON m.proposal_id = p.id
        WHERE p.status IN ('accepted', 'moved') AND p.source != 'rule' AND p.final_target IS NOT NULL""")


def evaluate_decisions(service, rerun: bool = True) -> dict:
    """How right were proposals, measured against your actual decisions.

    recorded: what the model said at scan time (per backend).
    current:  the current example model re-run on the same files (from the stored evidence, so the
              files need not be read again). Applied files are hidden from the index so the
              model cannot see its own answer."""
    rows = decided_proposals(service)
    out: dict = {"decisions": len(rows), "decided_via": dict(Counter(r["decided_via"] or "unknown" for r in rows))}
    recorded = defaultdict(list)
    for r in rows:
        recorded[r["backend"]].append({"true": r["final_target"], "pred": r["target"], "confidence": r["confidence"],
                                       "top3": [a["folder"] for a in r["alternatives"][:3]],
                                       "via": r["decided_via"] or "unknown"})
    out["recorded"] = {b: summarize(v, by_via=True) for b, v in recorded.items()}

    if rerun and rows:
        service.check_ready()
        # hide each applied file from the index, both as scanned example (by its new path) and as
        # learned example (by its learned key; trashed files are learned under Delete)
        from .service import _learned_key

        examples = service.store.query("SELECT id, category_key, relpath, origin FROM examples WHERE model = ?",
                                       (service.settings.embedding_model,))
        by_path = {os.path.normpath(os.path.join(e["category_key"], e["relpath"])): e["id"]
                   for e in examples if e["origin"] == "scan"}
        by_learned = {(e["category_key"], e["relpath"]): e["id"] for e in examples if e["origin"] == "applied"}
        hide = set()
        for r in rows:
            if not r["dst"]:
                continue
            if os.path.normpath(r["dst"]) in by_path:
                hide.add(by_path[os.path.normpath(r["dst"])])
            learned = by_learned.get(_learned_key(r["dst"], r["action"], r["category_key"]))
            if learned:
                hide.add(learned)
        clf = Classifier(service.categories(), ExampleDecider(service.example_index()))
        clf.decider_kwargs = {"exclude_ids": hide}
        fields = Evidence.__dataclass_fields__
        # decisions scanned before the download source existed: look it up at the file's new place
        # (the Windows note moves with the file within a drive)
        sources = {}
        if service.settings.use_download_source:
            from pathlib import Path

            moved = [Path(r["dst"]) for r in rows if r["dst"] and r["action"] != "delete"]
            sources = {str(p): d for p, d in download_sources(moved).items()}
        current = []
        for r in rows:
            ev = Evidence(**{k: v for k, v in r["evidence"].items() if k in fields})
            if r["dst"] and str(r["dst"]) in sources and not ev.meta.get("source"):
                ev.meta = {**ev.meta, "source": sources[str(r["dst"])]}
            c = clf.classify(ev)
            current.append({"true": r["final_target"], "pred": c.target, "confidence": c.confidence,
                            "top3": [a["folder"] for a in c.alternatives[:3]], "via": r["decided_via"] or "unknown"})
        out["current"] = summarize(current, by_via=True)
    return out


def summarize(rows: list[dict], by_via: bool = False) -> dict:
    """Aggregate metrics only: no file names."""
    n = len(rows)
    if not n:
        return {"files": 0}
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
    out = {
        "files": n,
        "accuracy": round(sum(correct) / n, 3),
        "top3": round(sum(r["true"] in r["top3"] for r in rows) / n, 3),
        "mean_confidence_right": round(sum(ok_conf) / len(ok_conf), 3) if ok_conf else None,
        "mean_confidence_wrong": round(sum(bad_conf) / len(bad_conf), 3) if bad_conf else None,
        "per_category": {k: {"files": len(v), "recall": round(sum(v) / len(v), 3)} for k, v in sorted(by_true.items())},
        "confusions": Counter(f"{r['true']} → {r['pred']}" for r, ok in zip(rows, correct) if not ok).most_common(8),
        "calibration": buckets,
        "thresholds": thresholds,
    }
    if by_via:
        via = defaultdict(list)
        for r, ok in zip(rows, correct):
            via[r["via"]].append(ok)
        out["by_decided_via"] = {k: {"files": len(v), "accuracy": round(sum(v) / len(v), 3)} for k, v in sorted(via.items())}
    return out


# --- second opinion --------------------------------------------------------------------

WEIGHTS = (0.0, 0.3, 0.5, 0.7, 1.0)


def _cases_heldout(service, per_category: int, seed: int, split: str) -> list[dict]:
    holdout = pick_holdout(service, per_category, seed)
    siblings = _sibling_ids(service, holdout) if split == "folder" else {}
    held = {h["id"] for h in holdout}
    sources = download_sources([h["path"] for h in holdout]) if service.settings.use_download_source else {}
    return [{"ev": service.read_file(h["path"], sources.get(h["path"])), "true": h["category"],
             "exclude": held | siblings.get(h["id"], set())} for h in holdout]


def _cases_decisions(service) -> list[dict]:
    from .service import _learned_key

    rows = decided_proposals(service)
    examples = service.store.query("SELECT id, category_key, relpath, origin FROM examples WHERE model = ?",
                                   (service.settings.embedding_model,))
    by_path = {os.path.normpath(os.path.join(e["category_key"], e["relpath"])): e["id"]
               for e in examples if e["origin"] == "scan"}
    by_learned = {(e["category_key"], e["relpath"]): e["id"] for e in examples if e["origin"] == "applied"}
    hide = set()
    for r in rows:
        if r["dst"]:
            if os.path.normpath(r["dst"]) in by_path:
                hide.add(by_path[os.path.normpath(r["dst"])])
            learned = by_learned.get(_learned_key(r["dst"], r["action"], r["category_key"]))
            if learned:
                hide.add(learned)
    fields = Evidence.__dataclass_fields__
    return [{"ev": Evidence(**{k: v for k, v in r["evidence"].items() if k in fields}), "true": r["final_target"],
             "exclude": hide} for r in rows]


def evaluate_second_opinion(service, source: str = "folder", per_category: int = 40, seed: int = 0,
                            weights: tuple[float, ...] = WEIGHTS,
                            progress: Callable[[str, int, int], None] | None = None) -> dict:
    """Example model first; the language model asked about the files a scan would send to it
    (below the threshold, usable evidence); then the blend compared for several weights.
    source: "folder" / "file" (held-out split) or "decisions". Aggregates only."""
    from . import llm

    service.check_ready()
    threshold = service.settings.threshold
    cases = _cases_decisions(service) if source == "decisions" else _cases_heldout(service, per_category, seed, source)
    clf = Classifier(service.categories(), ExampleDecider(service.example_index()))
    for i, c in enumerate(cases, 1):
        if progress:
            progress("example model", i, len(cases))
        clf.decider_kwargs = {"exclude_ids": c["exclude"]}
        r = clf.classify(c["ev"])
        c.update(pred=r.target, conf=r.confidence, alts=r.alternatives, neighbours=r.neighbours, reason=r.reason)
    gated = [c for c in cases if c["conf"] < threshold and len(llm.candidates(c["alts"])) >= 2
             and llm.has_evidence(c["ev"])][:service.settings.llm_max_files]
    if service._index is not None:
        service._index.embedder.unload()
    import time

    started = time.time()
    with llm.LLMClient(service.settings.llm_model, service.settings.llm_gpu_fraction, service.settings.cpu_threads) as client:
        for i, c in enumerate(gated, 1):
            if progress:
                progress("second opinion", i, len(gated))
            p = {"alternatives": c["alts"], "neighbours": c["neighbours"], "_ev": c["ev"]}
            c["p_llm"] = service.ask_second_opinion(client, p)["p_llm"]
        model_gb = client.info.get("gpu_gb")
    seconds = (time.time() - started) / max(len(gated), 1)

    g_cases = [c for c in cases if "p_llm" in c]
    agree = [c for c in g_cases if max(c["p_llm"], key=c["p_llm"].get) == c["pred"]]
    disagree = [c for c in g_cases if max(c["p_llm"], key=c["p_llm"].get) != c["pred"]]
    agreement = {
        "agree": len(agree),
        "agree_right": sum(c["pred"] == c["true"] for c in agree),
        "disagree": len(disagree),
        "disagree_example_right": sum(c["pred"] == c["true"] for c in disagree),
        "disagree_llm_right": sum(max(c["p_llm"], key=c["p_llm"].get) == c["true"] for c in disagree),
    }
    by_weight = {}
    for w in weights:
        for mode in ("blend", "confirm"):
            rows = []
            for c in cases:
                pred, conf = c["pred"], c["conf"]
                if "p_llm" in c:
                    combined = llm.combine({a["folder"]: a["probability"] for a in c["alts"]}, c["p_llm"], w)
                    if mode == "blend":
                        pred = max(combined, key=combined.get)
                        conf = combined[pred]
                    elif max(c["p_llm"], key=c["p_llm"].get) == c["pred"]:  # confirm: only when they agree
                        conf = max(conf, combined[c["pred"]])
                rows.append({"true": c["true"], "pred": pred, "confidence": conf, "gated": "p_llm" in c})
            by_weight[f"{mode} w={w}"] = _so_metrics(rows, threshold)
    return {"source": source, "files": len(cases), "gated": len(gated), "threshold": threshold,
            "model": service.settings.llm_model, "seconds_per_file": round(seconds, 2), "model_gpu_gb": model_gb,
            "agreement": agreement, "by_weight": by_weight}


def _so_metrics(rows: list[dict], threshold: float) -> dict:
    """Overall accuracy, and for the files that got a second opinion: accuracy, how many now pass
    the threshold and how many of those are right, calibration."""
    g = [r for r in rows if r["gated"]]
    passed = [r for r in g if r["confidence"] >= threshold]
    return {
        "overall_accuracy": round(sum(r["pred"] == r["true"] for r in rows) / len(rows), 3),
        "gated_accuracy": round(sum(r["pred"] == r["true"] for r in g) / len(g), 3) if g else None,
        "gated_passing": len(passed),
        "passing_accuracy": round(sum(r["pred"] == r["true"] for r in passed) / len(passed), 3) if passed else None,
        "calibration": summarize([{**r, "top3": []} for r in g])["calibration"] if g else [],
    }
