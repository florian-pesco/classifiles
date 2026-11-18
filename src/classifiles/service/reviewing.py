"""Reading and deciding proposals: review, bulk review, batches, learning from decisions in a scan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..deciders.examples import DECIDED, ExampleDecider
from ..examples import category_key
from ..paths import normalize_subpath, suggest_subpath
from ..store import now
from .base import ServiceBase
from .helpers import (
    PROPOSALS_SQL,
    REVIEW_ACTIONS,
    _vec,
    _without_vectors,
)


class ReviewingMixin(ServiceBase):
    def get_proposal(self, proposal_id: int) -> dict:
        p = self.store.one(PROPOSALS_SQL + " WHERE p.id = ?", (proposal_id,))
        if not p:
            raise KeyError(f"No proposal {proposal_id}")
        return _without_vectors(p)

    def proposals(self, scan_id: int | None = None, status: str | None = None) -> list[dict]:
        scan_id = scan_id or self.latest_scan_id()
        sql = PROPOSALS_SQL + " WHERE p.scan_id = ?"
        params: list[Any] = [scan_id]
        if status:
            sql += " AND p.status = ?"
            params.append(status)
        rows = self.store.query(sql + " ORDER BY p.confidence DESC, p.name", tuple(params))
        return [_without_vectors(p) for p in rows]

    def current_path(self, proposal_id: int) -> Path:
        """Where the proposal's file is now: its applied destination, else its original path."""
        p = self.get_proposal(proposal_id)
        if p["applied_dst"] and p["applied_action"] != "copy":
            return Path(p["applied_dst"])
        return Path(p["path"])

    def review(self, proposal_id: int, action: str, folder: str | None = None, subpath: str | None = None,
               name: str | None = None, use_suggested_name: bool | None = None) -> dict:
        """accept (optionally choosing the category and/or the folder inside it), reject (leave the
        file where it is), or reset.

        subpath: folder inside the category ('' = category top level). Missing folders in it are
        created on apply, so passing one is the confirmation for a new folder. If the category
        changes and no subpath is given, the suggestion is recomputed for the new category.
        name: a new file name ('' = keep the original); use_suggested_name: True/False to use the
        suggested name or keep the original. The suggestion follows category and folder changes."""
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
            sub = values.get("subpath", p["subpath"])
            if cat.action == "delete" or p["source"] == "duplicate":
                values.update({"suggested_name": None, "new_name": None, "name_reason": None})
            else:
                if target != p["final_target"] or sub != p["subpath"]:
                    values.update(self._renamed(p, target, sub))
                if use_suggested_name is not None:
                    suggested = values.get("suggested_name", p["suggested_name"])
                    values["new_name"] = suggested if use_suggested_name else None
                if name is not None:
                    values["new_name"] = self._typed_name(name, p["name"])
        elif action == "reject":
            values = {"status": "rejected", "decided_via": "reject"}
        else:
            values = {"status": "suggested" if p["target"] and p["confidence"] >= self.settings.threshold
                      else "needs_review", "final_target": p["target"], "decided_via": None,
                      "subpath": p["suggested_subpath"] or ""}
            if p["source"] != "duplicate":
                values.update(self._renamed({**p, "new_name": None}, p["target"], p["suggested_subpath"] or ""))
                values["new_name"] = values["suggested_name"] if self.settings.use_suggested_names else None
        self.store.update("proposals", proposal_id, {**values, "updated_at": now()})
        return self.get_proposal(proposal_id)

    def bulk_review(self, ids: list[int], action: str, folder: str | None = None,
                    subpath: str | None = None, use_suggested_name: bool | None = None) -> dict:
        """Apply one review action to several proposals. Returns {"updated": [...], "errors": [...]}."""
        updated, errors = [], []
        for pid in ids:
            try:
                updated.append(self.review(pid, action, folder, subpath, use_suggested_name=use_suggested_name))
            except (KeyError, ValueError) as e:
                errors.append({"proposal_id": pid, "error": str(e.args[0] if e.args else e)})
        return {"updated": updated, "errors": errors}

    def suggest_batches(self, scan_id: int | None = None) -> list[dict]:
        """Suggested batches of files to review together (batches.py)."""
        from ..batches import find_batches

        scan_id = scan_id or self.latest_scan_id()
        if not scan_id:
            return []
        rows = self.store.query(PROPOSALS_SQL + " WHERE p.scan_id = ?", (scan_id,))
        vectors = {r["id"]: _vec(r["qvec"]) for r in rows if r["qvec"] is not None}
        return find_batches([_without_vectors(r) for r in rows], vectors, self.get_scan(scan_id)["threshold"])

    def relearn(self, scan_id: int) -> list[dict]:
        """Learn from the decisions made so far in this scan, right away.

        Every accepted (or applied) file of the scan becomes a temporary example of the category and
        folder the user chose, using the vectors stored at scan time. All still-undecided proposals of
        the example model are then re-scored against the stored index plus these examples. It is
        computed from scratch each time, so taking a decision back also takes its effect back.
        Nothing is read from disk and no model runs. Rules, duplicates and decided files are never
        touched. Returns the proposals whose category, folder or confidence changed."""
        if not self.settings.relearn_in_scan or self.settings.backend != "examples":
            return []
        rows = self.store.query("SELECT * FROM proposals WHERE scan_id = ?", (scan_id,))
        cats = self.categories()
        key_of = {c.name: category_key(c) for c in cats}
        decided = [r for r in rows if r["status"] in ("accepted", "moved") and r["source"] not in ("rule", "duplicate")
                   and r["final_target"] in key_of and r["qvec"] is not None]
        extra = [(key_of[r["final_target"]], f"{DECIDED}{r['name']}/{r['subpath'] or ''}", _vec(r["qvec"]))
                 for r in decided]
        image_extra = [(key_of[r["final_target"]], f"{DECIDED}{r['name']}/{r['subpath'] or ''}", _vec(r["ivec"]))
                       for r in decided if r["ivec"] is not None]
        todo = [r for r in rows if r["status"] in ("suggested", "needs_review") and r["source"] == "examples"
                and r["qvec"] is not None]
        if not todo:
            return []
        decider = self.decider()
        if not isinstance(decider, ExampleDecider):
            return []
        model_cats = [c for c in cats if c.usable and c.suggest]
        threshold = self.get_scan(scan_id)["threshold"]
        bases = self._stored_candidates(scan_id, decider, model_cats, todo)
        changed = []
        for r in todo:
            qv, iv = _vec(r["qvec"]), _vec(r["ivec"])
            probs, reason, nbs = decider.score(qv, iv, model_cats, extra=extra, image_extra=image_extra,
                                               base=bases[r["id"]])
            if not probs:
                continue
            ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
            target, conf = ranked[0][0], round(ranked[0][1], 4)
            # the model's own answer, without the user's decisions
            base = decider.score(qv, iv, model_cats, base=bases[r["id"]])[0] if extra else probs
            base_target = max(base, key=base.__getitem__) if base else None
            relearned = int(bool(extra) and (base_target != target or abs(base.get(target, 0) - conf) > 0.05))
            if target == r["target"] and abs(conf - r["confidence"]) < 0.005 and relearned == r["relearned"]:
                continue
            subpath, new_folder, _ = self._folder_for(target, nbs, r["name"])
            self.store.update("proposals", r["id"], {
                **self._renamed(r, target, subpath),
                "target": target, "final_target": target, "confidence": conf,
                "alternatives": [{"folder": f, "probability": round(q, 4)} for f, q in ranked[:4]],
                "status": "suggested" if conf >= threshold else "needs_review",
                "reason": reason, "neighbours": nbs, "suggested_subpath": subpath, "subpath": subpath,
                "new_folder": new_folder, "relearned": relearned, "updated_at": now(),
            })
            changed.append(self.get_proposal(r["id"]))
        return changed

    def _stored_candidates(self, scan_id: int, decider: ExampleDecider, categories: list, todo: list[dict]) -> dict:
        """Nearest stored examples per proposal, searched once (in one batch) and kept while the
        index and categories stay the same: later decisions then only compare against the
        decided files, which is quick even for big scans."""
        sig = (scan_id, self.settings.embedding_model, decider.index.size,
               decider.image_index.size if decider.image_index is not None else 0,
               tuple(sorted(category_key(c) for c in categories)))
        cache = self._relearn_cache if self._relearn_cache[0] == sig else (sig, {})
        missing = [r for r in todo if r["id"] not in cache[1]]
        if missing:
            found = decider.candidates([_vec(r["qvec"]) for r in missing],
                                       [_vec(r["ivec"]) for r in missing],
                                       categories)
            cache[1].update({r["id"]: c for r, c in zip(missing, found)})
        self._relearn_cache = cache
        return cache[1]

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
