"""Applying decisions (move / copy / trash), duplicates' safety check, undo and the trash."""

from __future__ import annotations

import uuid
from pathlib import Path

from ..examples import DELETE_KEY, category_key, doc_text
from ..mover import MoveError, execute, plan_move, purge, revert
from ..store import now
from .base import ServiceBase
from .helpers import (
    _inbox_text,
    _learned_folder,
    _learned_key,
)


class ApplyingMixin(ServiceBase):
    def _plan(self, p: dict):
        try:
            m = plan_move(p["path"], self.category(p["final_target"]), p["subpath"] or "", p.get("new_name"))
        except (MoveError, ValueError) as e:
            return None, str(e)
        if p["source"] == "duplicate" and m.action == "delete":
            problem = self._duplicate_problem(p)
            if problem:
                return None, f"Not deleted: {problem}"
        return m, None

    def _duplicate_problem(self, p: dict) -> str | None:
        """Safety check right before a duplicate goes to the trash: the identical copy must still
        exist and still be identical. Returns a reason if not."""
        from ..duplicates import full_hash

        other = Path(p["duplicate_path"]) if p.get("duplicate_path") else None
        if other is None or not other.is_file():
            return f"the identical copy is gone ({p.get('duplicate_of') or 'unknown'})"
        try:
            if other.stat().st_size != Path(p["path"]).stat().st_size or full_hash(other) != full_hash(Path(p["path"])):
                return f"{p['duplicate_of']} has changed and is no longer identical"
        except OSError as e:
            return f"could not read the files ({e})"
        return None

    def duplicate_details(self, proposal_id: int) -> dict:
        """Facts behind a duplicate proposal, with both files re-checked now."""
        from datetime import datetime

        from ..duplicates import copy_marked, full_hash

        p = self.get_proposal(proposal_id)
        if not p.get("duplicate_path"):
            raise ValueError("This file is not marked as a duplicate")

        def side(path: Path, label: str) -> dict:
            info = {"label": label, "exists": path.is_file()}
            if info["exists"]:
                st = path.stat()
                info.update({"size": st.st_size,
                             "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                             "fingerprint": full_hash(path)})
            return info

        this = side(self.current_path(proposal_id), p["name"])
        other = side(Path(p["duplicate_path"]), p["duplicate_of"])
        identical = this["exists"] and other["exists"] and this["size"] == other["size"] \
            and this["fingerprint"] == other["fingerprint"]
        if p["duplicate_kind"] == "sorted":
            keep = "The other copy is already sorted in your folders, so the inbox copy is the one to remove."
        else:
            why = []
            if copy_marked(p["name"]) and not copy_marked(Path(p["duplicate_path"]).name):
                why.append("this name looks like a copy (e.g. \"(1)\", \"Kopie\")")
            if this.get("modified") and other.get("modified") and this["modified"] > other["modified"]:
                why.append("the other copy is older")
            keep = ("The other copy is kept because " + " and ".join(why) + ".") if why else \
                "The other copy is kept (shorter or earlier name)."
        return {"kind": p["duplicate_kind"], "this": this, "other": other, "identical_now": identical,
                "keep_reason": keep,
                "method": "Same size in bytes and the same BLAKE2b fingerprint (128-bit) of the entire content. "
                          "Names and dates don't matter."}

    def plan_apply(self, scan_id: int | None = None) -> list[dict]:
        """Dry run: what apply() would do for accepted proposals, including problems."""
        out = []
        for p in self.proposals(scan_id, "accepted"):
            m, err = self._plan(p)
            out.append({"proposal_id": p["id"], "src": p["path"], "dst": str(m.dst) if m else None,
                        "action": m.action if m else None, "category": p["final_target"],
                        "subpath": p["subpath"] or "",
                        "renamed": bool(m and m.action != "delete" and p.get("new_name")),
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
            if p["source"] != "duplicate":  # a duplicate isn't junk by its kind: don't learn it as Delete
                learned.append((key, rel, doc_text(_learned_folder(key, rel), p["name"], p["kind"],
                                                   p["evidence"].get("text", "")),
                                _inbox_text(p["name"], p["kind"], p["evidence"])))
            if m.action != "delete" and key != DELETE_KEY:
                self._catalog.add(key, rel, m.dst, p.get("content_hash"))
            if m.created_dir:
                self.invalidate()  # a new folder exists now: refresh the cached categories
        self._learn(learned)
        self._learn_images(moved)
        if learned and self.settings.trained_weight > 0:
            self.schedule_retrain()
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
            if m["action"] != "delete":
                self._catalog.remove(*forget[-1])
        self._forget(forget)
        if self._image_index is not None:
            self._image_index.remove([f for f in forget if f[0] != DELETE_KEY])
        if forget and self.settings.trained_weight > 0:
            self.schedule_retrain()
        return {"batch_id": batch_id, "restored": restored, "errors": errors}

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
