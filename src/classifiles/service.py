"""ClassifilesService: the public facade. UIs (CLI, web API, a future LifeOS) only talk to this."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Callable

from .classifier import Classifier
from .config import ACTIONS, Settings
from .deciders import Decider, make_decider
from .deciders.examples import ExampleDecider
from .embedder import Embedder
from .examples import DELETE_KEY, ExampleIndex, category_key, doc_text
from .extract import extract
from .mover import MoveError, execute, plan_move, purge, revert
from .paths import normalize_subpath, numbered_sibling, suggest_folder_name, suggest_subpath
from .rules import load_rules
from .store import Store, now
from .taxonomy import Category, build_categories

log = logging.getLogger(__name__)

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


class ClassifilesService:
    def __init__(self, settings: Settings | None = None, decider: Decider | None = None, store: Store | None = None):
        self.settings = settings or Settings.load()
        self._decider = decider
        self._categories: list[Category] | None = None
        self._index: ExampleIndex | None = None
        self.store = store or Store(self.settings.db_path)

    # --- categories, rules & model -----------------------------------------------------------

    def categories(self, refresh: bool = False, also_exclude: str | None = None) -> list[Category]:
        """All categories, including disabled/missing ones and the Delete category.

        Cached, because listing folders on a Windows drive is slow; refreshed at the start of
        every scan and whenever settings change (invalidate())."""
        if self._categories is None or refresh:
            s = self.settings
            excluded = [*s.inboxes, *([also_exclude] if also_exclude else [])]
            self._categories = build_categories(s.roots, s.categories, s.samples_per_folder, excluded, s.delete)
        return self._categories

    def unreachable_roots(self) -> list[str]:
        """Configured roots that cannot be read right now (e.g. an unplugged or unmounted drive)."""
        return [r["path"] for r in self.settings.roots if not Path(r["path"]).expanduser().is_dir()]

    def check_ready(self) -> None:
        """Refuse to scan or index when category folders are missing: otherwise every file would be
        proposed for whatever is left (e.g. Delete) with high confidence."""
        missing = self.unreachable_roots()
        if missing:
            raise ValueError(f"Category root not reachable: {', '.join(missing)}. "
                             "Is the drive connected and mounted in WSL?")
        if not any(c.usable and c.path for c in self.categories(refresh=True)):
            raise ValueError("No category folders available. Add a root or a manual category first.")

    def category(self, name: str) -> Category:
        """A usable category by name (raises ValueError otherwise)."""
        for c in self.categories():
            if c.name == name:
                if not c.usable:
                    raise ValueError(f"Category {name!r} is disabled or its folder is missing")
                return c
        raise ValueError(f"Unknown category: {name!r}")

    def invalidate(self) -> None:
        """Forget cached categories (and the model, if the backend changed) after a settings change."""
        self._categories = None

    def set_categories(self, roots: list[dict], categories: list[dict], delete: dict | None = None) -> list[Category]:
        """Replace roots, per-category settings and Delete settings (validated). Caller persists."""
        candidate = Settings.from_dict({**self.settings.to_dict(), "roots": roots, "categories": categories,
                                        **({"delete": delete} if delete is not None else {})})
        for c in candidate.categories:
            if c.get("action", "move") not in ACTIONS:
                raise ValueError(f"action must be one of {ACTIONS}")
        self.settings.roots, self.settings.categories = candidate.roots, candidate.categories
        self.settings.delete = candidate.delete
        self.invalidate()
        return self.categories()

    def set_rules(self, rules: list[dict]) -> list[dict]:
        """Validate and replace the rules. Caller persists."""
        parsed = load_rules(rules)
        names = {c.name for c in self.categories() if c.usable}
        for r in parsed:
            if r.folder not in names:
                raise ValueError(f"Rule '{r.describe()}' targets unknown or disabled category {r.folder!r}")
        self.settings.rules = rules
        return rules

    def decider(self) -> Decider:
        if self._decider is None:
            if self.settings.backend == "examples":
                self._decider = ExampleDecider(self.example_index())
            else:
                self._decider = make_decider(self.settings.backend, laya_model=self.settings.laya_model)
        return self._decider

    def reset_decider(self) -> None:
        self._decider = None

    # --- example index ------------------------------------------------------------------

    def example_index(self) -> ExampleIndex:
        """The example index (created lazily; the embedding model loads only when first used)."""
        s = self.settings
        if self._index is None or self._index.embedder.model_name != s.embedding_model:
            if self._index is not None and self._index.building:
                raise ValueError("Wait for the running index build to finish (or cancel it)")
            embedder = Embedder(s.embedding_model, s.gpu_memory_fraction, s.cpu_threads)
            self._index = ExampleIndex(self.store, embedder, s.examples_per_category, s.content_per_category)
        self._index.per_category = s.examples_per_category
        self._index.content_per_category = s.content_per_category
        return self._index

    def start_index_build(self) -> dict:
        """Build/update the example index in the background (low priority, cancellable)."""
        self.check_ready()
        cats = self.categories(refresh=True)
        index = self.example_index()
        index.start_build(cats)
        return self.index_status()

    def cancel_index_build(self) -> dict:
        if self._index:
            self._index.cancel()
        return self.index_status()

    def index_status(self) -> dict:
        """Build progress and example counts per category name."""
        index = self.example_index()
        counts = index.counts()
        per_category = []
        for c in self.categories():
            n = counts.get(category_key(c), {})
            per_category.append({"name": c.name, "usable": c.usable, "examples": n.get("scan", 0),
                                 "learned": n.get("applied", 0)})
        return {"status": index.status, "model": index.embedder.model_name,
                "model_loaded": index.embedder.loaded, "total": sum(sum(v.values()) for v in counts.values()),
                "categories": per_category, "unlearned": len(self.unlearned_decisions())}

    def _learn(self, items: list[tuple[str, str, str]]) -> None:
        """Add applied files to the example index in the background. Happens with any model as long
        as an index exists, so no decision is lost when switching models later."""
        if not items:
            return
        index = self.example_index()
        if self.settings.backend != "examples" and index.size == 0:
            return  # no index yet: unlearned decisions can be backfilled later (learn_past_decisions)
        threading.Thread(target=lambda: _quietly(index.add, items), daemon=True).start()

    def _forget(self, items: list[tuple[str, str]]) -> None:
        if items:
            self.example_index().remove(items)

    def unlearned_decisions(self) -> list[tuple[str, str, str]]:
        """Applied files (not undone) that are not in the index as learned examples yet, as
        (category_key, relpath, text) built from the stored evidence. No file access needed."""
        rows = self.store.query("""
            SELECT m.dst, m.action, m.category_key, p.name, p.kind, p.evidence FROM moves m
            JOIN proposals p ON p.id = m.proposal_id
            WHERE m.undone_at IS NULL""")
        learned = {(r["category_key"], r["relpath"]) for r in self.store.query(
            "SELECT category_key, relpath FROM examples WHERE origin = 'applied' AND model = ?",
            (self.settings.embedding_model,))}
        out = []
        for r in rows:
            key, rel = _learned_key(r["dst"], r["action"], r["category_key"])
            if (key, rel) in learned:
                continue
            out.append((key, rel, doc_text(_learned_folder(key, rel), r["name"], r["kind"],
                                           r["evidence"].get("text", ""))))
        return out

    def learn_past_decisions(self) -> dict:
        """Backfill: learn every applied file that was applied without learning (e.g. while another
        model was selected). Uses the stored evidence only; runs in seconds."""
        items = self.unlearned_decisions()
        self.example_index().add(items)
        by = {}
        for key, _, _ in items:
            kind = "delete" if key == DELETE_KEY else "move/copy"
            by[kind] = by.get(kind, 0) + 1
        return {"learned": len(items), "by_action": by}

    def classifier(self, inbox: str | None = None) -> Classifier:
        cats = self.categories(refresh=True, also_exclude=inbox)
        return Classifier(cats, self.decider(), load_rules(self.settings.rules))

    # --- scanning -----------------------------------------------------------------------

    def list_inbox(self, inbox: str | Path) -> list[Path]:
        """Classifiable files directly in the inbox, newest first."""
        inbox = Path(inbox).expanduser()
        if not inbox.is_dir():
            raise FileNotFoundError(f"Inbox does not exist: {inbox}")
        files = [
            p for p in inbox.iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.suffix.lower() not in SKIP_SUFFIXES and p.name.lower() not in SKIP_NAMES
        ]
        files.sort(key=lambda p: (-p.stat().st_mtime, p.name))
        return files

    def select_files(self, inbox: str | Path, max_files: int | None = None, only_new: bool = False) -> list[Path]:
        """The files a scan will classify: newest first, optionally skipping already-classified
        files (any earlier proposal for the same path), capped at max_files."""
        files = self.list_inbox(inbox)
        if only_new:
            seen = {r["path"] for r in self.store.query("SELECT DISTINCT path FROM proposals")}
            files = [p for p in files if str(p) not in seen]
        if max_files is not None:
            if max_files < 1:
                raise ValueError("max_files must be at least 1")
            files = files[:max_files]
        return files

    def start_scan(self, inbox: str | Path, max_files: int | None = None, only_new: bool = False) -> int:
        """Create a scan record; call run_scan(id) to classify (possibly in a background thread).

        max_files defaults to settings.max_files; pass 0 to scan all files regardless."""
        inbox = str(Path(inbox).expanduser().resolve())
        self.check_ready()
        if max_files is None:
            max_files = self.settings.max_files
        max_files = max_files or None
        files = self.select_files(inbox, max_files, only_new)
        return self.store.insert("scans", {
            "inbox": inbox, "target_root": ", ".join(r["path"] for r in self.settings.roots),
            "backend": self.settings.backend, "threshold": self.settings.threshold,
            "status": "running", "max_files": max_files, "only_new": int(only_new),
            "inbox_total": len(self.list_inbox(inbox)), "total": len(files), "processed": 0,
            "created_at": now(),
        })

    def run_scan(self, scan_id: int, on_progress: Callable[[int, int, dict], None] | None = None) -> list[dict]:
        scan = self.get_scan(scan_id)
        try:
            clf = self.classifier(scan["inbox"])
            files = self.select_files(scan["inbox"], scan["max_files"], bool(scan["only_new"]))
            self.store.update("scans", scan_id, {"total": len(files)})
            out, candidates = [], []
            for i, path in enumerate(files, 1):
                try:
                    proposal, candidate = self._classify_file(scan_id, clf, path, scan["threshold"])
                except FileNotFoundError:  # moved or deleted since the scan started
                    proposal = None
                else:
                    out.append(proposal)
                    if candidate:
                        candidates.append(candidate)
                self.store.update("scans", scan_id, {"processed": i})
                if on_progress and proposal:
                    on_progress(i, len(files), proposal)
            if candidates:
                self._suggest_new_folders(candidates)
                out = [self.get_proposal(p["id"]) for p in out]
            self.store.update("scans", scan_id, {"status": "done"})
            return out
        except Exception as e:
            self.store.update("scans", scan_id, {"status": "failed", "error": str(e)[:500]})
            raise

    def scan(self, inbox: str | Path, on_progress=None, max_files: int | None = None,
             only_new: bool = False) -> tuple[int, list[dict]]:
        scan_id = self.start_scan(inbox, max_files, only_new)
        return scan_id, self.run_scan(scan_id, on_progress)

    def _classify_file(self, scan_id: int, clf: Classifier, path: Path, threshold: float) -> tuple[dict, dict | None]:
        """Classify and store one file. Returns the proposal and, if a new subfolder might fit,
        a candidate for _suggest_new_folders()."""
        ev = extract(path, self.settings.max_text_chars)
        c = clf.classify(ev)
        status = "suggested" if c.target and c.confidence >= threshold else "needs_review"
        subpath, container = suggest_subpath(c.neighbours, c.target) if c.target else ("", False)
        new_folder = None
        sibling = numbered_sibling(subpath, ev.name)
        if sibling:
            cat_path = self.category(c.target).path
            if cat_path is not None and (cat_path / sibling).is_dir():
                subpath = sibling  # e.g. "BWL 2" exists already: that's the folder
            elif cat_path is not None:
                new_folder = sibling  # e.g. "BWL 2" doesn't exist yet: propose it ...
                subpath = sibling.rpartition("/")[0]  # ... and default to the shared parent folder
            container = False
        pid = self.store.insert("proposals", {
            "scan_id": scan_id, "path": str(path), "name": ev.name, "kind": ev.kind, "size": ev.size,
            "evidence": ev.to_dict(), "target": c.target, "confidence": c.confidence, "source": c.source,
            "alternatives": c.alternatives, "reason": c.reason, "status": status,
            "final_target": c.target, "neighbours": c.neighbours, "suggested_subpath": subpath,
            "subpath": subpath, "new_folder": new_folder, "updated_at": now(),
        })
        candidate = None
        if container and c.vector is not None:
            candidate = {"id": pid, "category": c.target, "parent": subpath, "name": ev.name, "vector": c.vector}
        return self.get_proposal(pid), candidate

    def _suggest_new_folders(self, candidates: list[dict], min_similarity: float = 0.8) -> None:
        """Propose a new subfolder for files whose similar files are spread over many subfolders.
        Similar inbox files of the same scan (same category and parent folder) are grouped and get
        one shared name from their common words. If that folder already exists, it is suggested
        as a normal subfolder instead."""
        import numpy as np

        groups: dict[tuple[str, str], list[dict]] = {}
        for cand in candidates:
            groups.setdefault((cand["category"], cand["parent"]), []).append(cand)
        for (category, parent), members in groups.items():
            try:
                cat_path = self.category(category).path
            except ValueError:
                continue
            if cat_path is None:
                continue
            clusters: list[list[dict]] = []
            for m in members:  # greedy: join the first cluster whose seed is similar enough
                for cl in clusters:
                    if float(np.dot(cl[0]["vector"], m["vector"])) >= min_similarity:
                        cl.append(m)
                        break
                else:
                    clusters.append([m])
            for cl in clusters:
                name = suggest_folder_name([m["name"] for m in cl])
                if not name:
                    continue
                sub = f"{parent}/{name}" if parent else name
                if (cat_path / sub).is_dir():  # exists already: just suggest it
                    for m in cl:
                        self.store.update("proposals", m["id"], {"suggested_subpath": sub, "subpath": sub})
                else:
                    for m in cl:
                        self.store.update("proposals", m["id"], {"new_folder": sub})

    def get_scan(self, scan_id: int) -> dict:
        scan = self.store.one("SELECT * FROM scans WHERE id = ?", (scan_id,))
        if not scan:
            raise KeyError(f"No scan {scan_id}")
        return scan

    def list_scans(self, limit: int = 50) -> list[dict]:
        """Scans, newest first, with counts per proposal status."""
        scans = self.store.query("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))
        counts = self.store.query("SELECT scan_id, status, COUNT(*) AS n FROM proposals GROUP BY scan_id, status")
        by_scan: dict[int, dict] = {}
        for r in counts:
            by_scan.setdefault(r["scan_id"], {})[r["status"]] = r["n"]
        for s in scans:
            s["counts"] = by_scan.get(s["id"], {})
        return scans

    def latest_scan_id(self) -> int | None:
        row = self.store.one("SELECT id FROM scans ORDER BY id DESC LIMIT 1")
        return row["id"] if row else None

    # --- review -------------------------------------------------------------------------

    def get_proposal(self, proposal_id: int) -> dict:
        p = self.store.one(PROPOSALS_SQL + " WHERE p.id = ?", (proposal_id,))
        if not p:
            raise KeyError(f"No proposal {proposal_id}")
        return p

    def proposals(self, scan_id: int | None = None, status: str | None = None) -> list[dict]:
        scan_id = scan_id or self.latest_scan_id()
        sql, params = PROPOSALS_SQL + " WHERE p.scan_id = ?", [scan_id]
        if status:
            sql += " AND p.status = ?"
            params.append(status)
        return self.store.query(sql + " ORDER BY p.confidence DESC, p.name", tuple(params))

    def current_path(self, proposal_id: int) -> Path:
        """Where the proposal's file is now: its applied destination, else its original path."""
        p = self.get_proposal(proposal_id)
        if p["applied_dst"] and p["applied_action"] != "copy":
            return Path(p["applied_dst"])
        return Path(p["path"])

    def review(self, proposal_id: int, action: str, folder: str | None = None, subpath: str | None = None) -> dict:
        """accept (optionally choosing the category and/or the folder inside it), reject (leave the
        file where it is), or reset.

        subpath: folder inside the category ('' = category top level). Missing folders in it are
        created on apply, so passing one is the confirmation for a new folder. If the category
        changes and no subpath is given, the suggestion is recomputed for the new category."""
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"action must be one of {sorted(REVIEW_ACTIONS)}")
        p = self.get_proposal(proposal_id)
        if p["status"] == "moved":
            raise ValueError("Already applied; undo it first")
        if action == "accept":
            target = folder or p["final_target"] or p["target"]
            if not target:
                raise ValueError("Choose a category first")
            cat = self.category(target)  # validates
            values = {"status": "accepted", "final_target": target,
                      "decided_via": "confirm" if target == p["target"] else "override"}
            if cat.action == "delete":
                values["subpath"] = ""
            elif subpath is not None:
                values["subpath"] = normalize_subpath(subpath)
            elif target != (p["final_target"] or p["target"]):
                values["subpath"] = suggest_subpath(p["neighbours"] or [], target)[0]
                values["new_folder"] = None
        elif action == "reject":
            values = {"status": "rejected", "decided_via": "reject"}
        else:
            values = {"status": "suggested" if p["target"] and p["confidence"] >= self.settings.threshold
                      else "needs_review", "final_target": p["target"], "decided_via": None,
                      "subpath": p["suggested_subpath"] or ""}
        self.store.update("proposals", proposal_id, {**values, "updated_at": now()})
        return self.get_proposal(proposal_id)

    def accept_suggested(self, scan_id: int | None = None, top_level: bool = False) -> int:
        """Accept every proposal at or above the threshold, into its suggested folder (or, with
        top_level, directly into the category folder). Returns how many were accepted."""
        scan_id = scan_id or self.latest_scan_id()
        extra = ", subpath = ''" if top_level else ""
        cur = self.store.execute(
            f"UPDATE proposals SET status = 'accepted', decided_via = 'bulk'{extra}, updated_at = ? "
            "WHERE scan_id = ? AND status = 'suggested'",
            (now(), scan_id),
        )
        return cur.rowcount

    # --- applying (move / copy / delete) ------------------------------------------------

    def _plan(self, p: dict):
        try:
            return plan_move(p["path"], self.category(p["final_target"]), p["subpath"] or ""), None
        except (MoveError, ValueError) as e:
            return None, str(e)

    def plan_apply(self, scan_id: int | None = None) -> list[dict]:
        """Dry run: what apply() would do for accepted proposals, including problems."""
        out = []
        for p in self.proposals(scan_id, "accepted"):
            m, err = self._plan(p)
            out.append({"proposal_id": p["id"], "src": p["path"], "dst": str(m.dst) if m else None,
                        "action": m.action if m else None, "category": p["final_target"],
                        "subpath": p["subpath"] or "",
                        "creates": str(m.created_dir) if m and m.created_dir else None, "error": err})
        return out

    def apply(self, scan_id: int | None = None) -> dict:
        """Apply every accepted proposal. Returns {"batch_id", "moved": [...], "errors": [...]}."""
        batch_id = uuid.uuid4().hex[:12]
        moved, errors, learned = [], [], []
        for p in self.proposals(scan_id, "accepted"):
            m, err = self._plan(p)
            if m:
                try:
                    execute(m)
                except (MoveError, OSError) as e:
                    err = str(e)
            if err:
                errors.append({"proposal_id": p["id"], "src": p["path"], "error": err})
                continue
            cat_key = category_key(self.category(p["final_target"]))
            self.store.insert("moves", {"batch_id": batch_id, "proposal_id": p["id"], "src": str(m.src),
                                        "dst": str(m.dst), "action": m.action, "category_key": cat_key,
                                        "created_dir": str(m.created_dir) if m.created_dir else None,
                                        "moved_at": now()})
            self.store.update("proposals", p["id"], {"status": "moved", "updated_at": now()})
            moved.append({"proposal_id": p["id"], "src": str(m.src), "dst": str(m.dst), "action": m.action,
                          "created_dir": str(m.created_dir) if m.created_dir else None})
            key, rel = _learned_key(str(m.dst), m.action, cat_key)
            learned.append((key, rel, doc_text(_learned_folder(key, rel), p["name"], p["kind"],
                                               p["evidence"].get("text", ""))))
            if m.created_dir:
                self.invalidate()  # a new folder exists now: refresh the cached categories
        self._learn(learned)
        return {"batch_id": batch_id if moved else None, "moved": moved, "errors": errors}

    def moves(self, limit: int = 500) -> list[dict]:
        return self.store.query("SELECT * FROM moves ORDER BY id DESC LIMIT ?", (limit,))

    def batches(self, limit: int = 50) -> list[dict]:
        """Applied batches, newest first: when, how many, by action, and whether undoable."""
        return self.store.query("""
            SELECT batch_id, MIN(moved_at) AS moved_at, COUNT(*) AS total,
                   SUM(action = 'move') AS moved, SUM(action = 'copy') AS copied,
                   SUM(action = 'delete') AS trashed,
                   SUM(undone_at IS NULL AND purged_at IS NULL) AS undoable
            FROM moves GROUP BY batch_id ORDER BY MIN(id) DESC LIMIT ?""", (limit,))

    def undo(self, batch_id: str | None = None) -> dict:
        """Revert a batch (default: the most recent batch with something left to undo)."""
        if batch_id is None:
            row = self.store.one("SELECT batch_id FROM moves WHERE undone_at IS NULL AND purged_at IS NULL "
                                 "ORDER BY id DESC LIMIT 1")
            if not row:
                return {"batch_id": None, "restored": [], "errors": []}
            batch_id = row["batch_id"]
        restored, errors, forget = [], [], []
        rows = self.store.query("SELECT * FROM moves WHERE batch_id = ? AND undone_at IS NULL "
                                "AND purged_at IS NULL ORDER BY id DESC", (batch_id,))
        for m in rows:
            try:
                revert(Path(m["src"]), Path(m["dst"]), m["action"],
                       Path(m["created_dir"]) if m["created_dir"] else None)
            except (MoveError, OSError) as e:
                errors.append({"move_id": m["id"], "error": str(e)})
                continue
            self.store.update("moves", m["id"], {"undone_at": now()})
            if m["proposal_id"]:
                self.store.update("proposals", m["proposal_id"], {"status": "accepted", "updated_at": now()})
            restored.append({"move_id": m["id"], "path": m["src"]})
            forget.append(_learned_key(m["dst"], m["action"], m["category_key"]))
        self._forget(forget)
        return {"batch_id": batch_id, "restored": restored, "errors": errors}

    # --- trash --------------------------------------------------------------------------

    def trash(self) -> list[dict]:
        """Files in the trash (deleted via classifiles, not undone, not purged)."""
        rows = self.store.query("SELECT * FROM moves WHERE action = 'delete' AND undone_at IS NULL "
                                "AND purged_at IS NULL ORDER BY id DESC")
        for r in rows:
            p = Path(r["dst"])
            r["size"] = p.stat().st_size if p.exists() else None
        return rows

    def empty_trash(self) -> dict:
        """Permanently delete every file in the trash. Not undoable."""
        purged, errors = 0, []
        for r in self.trash():
            try:
                purge(Path(r["dst"]))
            except (MoveError, OSError) as e:
                errors.append({"move_id": r["id"], "error": str(e)})
                continue
            self.store.update("moves", r["id"], {"purged_at": now()})
            purged += 1
        return {"purged": purged, "errors": errors}


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


def _learned_folder(key: str, relpath: str) -> str:
    """Folder text for a learned example: category folder name plus the subfolder, if any."""
    if key == DELETE_KEY:
        return "Delete"
    sub = os.path.dirname(relpath)
    return f"{Path(key).name}/{sub}" if sub else Path(key).name


def _quietly(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - learning is best effort, never breaks apply
        log.exception("learning from applied files failed")
