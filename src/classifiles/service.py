"""ClassifilesService: the public facade. UIs (CLI, web API, a future LifeOS) only talk to this."""

from __future__ import annotations

import logging
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
                "categories": per_category}

    def _learn(self, items: list[tuple[str, str, str]]) -> None:
        """Add applied files to the example index in the background (examples backend only)."""
        if self.settings.backend != "examples" or not items:
            return
        index = self.example_index()
        threading.Thread(target=lambda: _quietly(index.add, items), daemon=True).start()

    def _forget(self, items: list[tuple[str, str]]) -> None:
        if self._index is not None and items:
            self._index.remove(items)

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
            out = []
            for i, path in enumerate(files, 1):
                try:
                    proposal = self._classify_file(scan_id, clf, path, scan["threshold"])
                except FileNotFoundError:  # moved or deleted since the scan started
                    proposal = None
                else:
                    out.append(proposal)
                self.store.update("scans", scan_id, {"processed": i})
                if on_progress and proposal:
                    on_progress(i, len(files), proposal)
            self.store.update("scans", scan_id, {"status": "done"})
            return out
        except Exception as e:
            self.store.update("scans", scan_id, {"status": "failed", "error": str(e)[:500]})
            raise

    def scan(self, inbox: str | Path, on_progress=None, max_files: int | None = None,
             only_new: bool = False) -> tuple[int, list[dict]]:
        scan_id = self.start_scan(inbox, max_files, only_new)
        return scan_id, self.run_scan(scan_id, on_progress)

    def _classify_file(self, scan_id: int, clf: Classifier, path: Path, threshold: float) -> dict:
        ev = extract(path, self.settings.max_text_chars)
        c = clf.classify(ev)
        status = "suggested" if c.target and c.confidence >= threshold else "needs_review"
        pid = self.store.insert("proposals", {
            "scan_id": scan_id, "path": str(path), "name": ev.name, "kind": ev.kind, "size": ev.size,
            "evidence": ev.to_dict(), "target": c.target, "confidence": c.confidence, "source": c.source,
            "alternatives": c.alternatives, "reason": c.reason, "status": status,
            "final_target": c.target, "updated_at": now(),
        })
        return self.get_proposal(pid)

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

    def review(self, proposal_id: int, action: str, folder: str | None = None) -> dict:
        """accept (optionally choosing the category), reject (leave the file where it is), or reset."""
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"action must be one of {sorted(REVIEW_ACTIONS)}")
        p = self.get_proposal(proposal_id)
        if p["status"] == "moved":
            raise ValueError("Already applied; undo it first")
        if action == "accept":
            target = folder or p["final_target"] or p["target"]
            if not target:
                raise ValueError("Choose a category first")
            self.category(target)  # validates
            values = {"status": "accepted", "final_target": target}
        elif action == "reject":
            values = {"status": "rejected"}
        else:
            values = {"status": "suggested" if p["target"] and p["confidence"] >= self.settings.threshold
                      else "needs_review", "final_target": p["target"]}
        self.store.update("proposals", proposal_id, {**values, "updated_at": now()})
        return self.get_proposal(proposal_id)

    def accept_suggested(self, scan_id: int | None = None) -> int:
        """Accept every proposal at or above the threshold. Returns how many were accepted."""
        scan_id = scan_id or self.latest_scan_id()
        cur = self.store.execute(
            "UPDATE proposals SET status = 'accepted', updated_at = ? WHERE scan_id = ? AND status = 'suggested'",
            (now(), scan_id),
        )
        return cur.rowcount

    # --- applying (move / copy / delete) ------------------------------------------------

    def _plan(self, p: dict):
        try:
            return plan_move(p["path"], self.category(p["final_target"])), None
        except (MoveError, ValueError) as e:
            return None, str(e)

    def plan_apply(self, scan_id: int | None = None) -> list[dict]:
        """Dry run: what apply() would do for accepted proposals, including problems."""
        out = []
        for p in self.proposals(scan_id, "accepted"):
            m, err = self._plan(p)
            out.append({"proposal_id": p["id"], "src": p["path"], "dst": str(m.dst) if m else None,
                        "action": m.action if m else None, "category": p["final_target"], "error": err})
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
            self.store.insert("moves", {"batch_id": batch_id, "proposal_id": p["id"], "src": str(m.src),
                                        "dst": str(m.dst), "action": m.action, "moved_at": now()})
            self.store.update("proposals", p["id"], {"status": "moved", "updated_at": now()})
            moved.append({"proposal_id": p["id"], "src": str(m.src), "dst": str(m.dst), "action": m.action})
            key, rel = _learned_key(str(m.dst), m.action)
            folder = Path(key).name if key != DELETE_KEY else "Delete"
            learned.append((key, rel, doc_text(folder, p["name"], p["kind"], p["evidence"].get("text", ""))))
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
                revert(Path(m["src"]), Path(m["dst"]), m["action"])
            except (MoveError, OSError) as e:
                errors.append({"move_id": m["id"], "error": str(e)})
                continue
            self.store.update("moves", m["id"], {"undone_at": now()})
            if m["proposal_id"]:
                self.store.update("proposals", m["proposal_id"], {"status": "accepted", "updated_at": now()})
            restored.append({"move_id": m["id"], "path": m["src"]})
            forget.append(_learned_key(m["dst"], m["action"]))
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


def _learned_key(dst: str, action: str) -> tuple[str, str]:
    """(category_key, relpath) under which an applied file is learned. Files land directly in the
    category folder, so its parent is the category key; trashed files belong to Delete."""
    p = Path(dst)
    return (DELETE_KEY, p.name) if action == "delete" else (str(p.parent), p.name)


def _quietly(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - learning is best effort, never breaks apply
        log.exception("learning from applied files failed")
