"""Duplicates page: search a root for identical files, review them, keep one copy, trash the rest."""

from __future__ import annotations

import uuid
from pathlib import Path

from .. import background
from ..cleanup import (
    Copy,
    Group,
    candidate_rows,
    files_per_folder,
    find_groups,
    folder_pairs,
    hash_candidates,
    place_scores,
    suggest,
)
from ..duplicates import full_hash
from ..examples import category_key
from ..mover import TRASH_DIR, MoveError, PlannedMove, execute, free_name
from ..store import now
from ..taxonomy import Category
from .base import ServiceBase
from .helpers import log


class CleanupMixin(ServiceBase):
    # --- scope ----------------------------------------------------------------------------------

    def cleanup_categories(self, root: str | None = None) -> list[Category]:
        """Usable folder categories inside `root` (None: all of them, manual folders included)."""
        cats = [c for c in self.categories() if c.usable and c.path is not None and c.source != "delete"]
        if root is None:
            return cats
        base = Path(root).expanduser()
        if root not in [r["path"] for r in self.settings.roots]:
            raise ValueError(f"Not a configured root: {root}")
        return [c for c in cats if c.path is not None and base in (c.path, *c.path.parents)]

    def _names(self, root: str | None) -> dict[str, str]:
        return {category_key(c): c.name for c in self.cleanup_categories(root)}

    # --- searching ------------------------------------------------------------------------------

    def start_duplicate_search(self, root: str | None = None) -> dict:
        """Search `root` for identical files in the background (low priority, cancellable)."""
        if self._cleanup_status.get("state") == "running":
            raise ValueError("A duplicate search is already running")
        missing = [r for r in self.unreachable_roots() if root in (None, r)]
        if missing:
            raise ValueError(f"Not reachable: {', '.join(missing)}. Is the drive connected?")
        self._cleanup_status = {"state": "running", "root": root, "phase": "listing", "started_at": now()}
        background.run_in_background(self.search_duplicates, root, name="classifiles-duplicates")
        return self._cleanup_status

    def search_duplicates(self, root: str | None = None) -> dict:
        """The search itself (blocking; the CLI calls it directly): list the files again, then hash
        only what may be a duplicate. Progress is in duplicate_search_status()."""
        self._cleanup_cancel.clear()
        status: dict = {"state": "running", "root": root, "phase": "listing", "started_at": now()}
        self._cleanup_status = status
        try:
            cats = self.cleanup_categories(root)
            self.categories(refresh=True)
            self._catalog.refresh(cats, lambda m: status.update(message=m), self._cleanup_cancel)
            rows = candidate_rows(self.store, [category_key(c) for c in cats])
            hash_candidates(self._catalog, rows, status.update, self._cleanup_cancel)
            cancelled = self._cleanup_cancel.is_set()
            groups = [] if cancelled else find_groups(self.store, self._names(root))
            status.update(state="cancelled" if cancelled else "done", finished_at=now(), message=None,
                          groups=len(groups), copies=sum(len(g.copies) - 1 for g in groups),
                          bytes=sum(g.wasted for g in groups))
        except Exception as e:  # noqa: BLE001 - background search: recorded in the status
            log.exception("duplicate search failed")
            status.update(state="failed", error=str(e)[:300])
        return status

    def cancel_duplicate_search(self) -> dict:
        self._cleanup_cancel.set()
        return self._cleanup_status

    def duplicate_search_status(self) -> dict:
        return self._cleanup_status

    # --- reviewing ------------------------------------------------------------------------------

    def _ignored(self) -> set[str]:
        return {r["full_hash"] for r in self.store.query("SELECT full_hash FROM duplicate_ignored")}

    def duplicate_groups(self, root: str | None = None, ignored: bool = False) -> list[Group]:
        """Identical files found so far (from earlier searches too), with a suggestion each.
        ignored=True: only the groups you chose to keep as they are."""
        skip = self._ignored()
        groups = [g for g in find_groups(self.store, self._names(root)) if (g.hash in skip) == ignored]
        return self._suggest(groups)

    def _suggest(self, groups: list[Group]) -> list[Group]:
        if self.settings.backend == "examples" and groups:
            index = self.example_index()
            if index.size:
                place_scores(index, groups, {category_key(c) for c in self.categories() if c.usable and c.path})
        for g in groups:
            suggest(g)
        return groups

    def duplicate_overview(self, root: str | None = None) -> dict:
        """Everything the Duplicates page shows: groups, folder pairs and totals."""
        names = self._names(root)
        skip = self._ignored()
        found = find_groups(self.store, names)
        groups = self._suggest([g for g in found if g.hash not in skip])
        return {
            "status": self._cleanup_status,
            "summary": {"groups": len(groups), "copies": sum(len(g.copies) - 1 for g in groups),
                        "bytes": sum(g.wasted for g in groups), "clear": sum(g.clear for g in groups),
                        "ignored": sum(g.hash in skip for g in found)},
            "groups": [group_payload(g) for g in groups],
            "pairs": folder_pairs(groups, files_per_folder(self.store, list(names))),
        }

    def duplicate_copy(self, catalog_id: int) -> Path:
        """Path of a catalogued file (for opening it), only within the usable categories."""
        row = self.store.one("SELECT category_key, relpath FROM catalog WHERE id = ?", (catalog_id,))
        if row is None or row["category_key"] not in self._names(None):
            raise KeyError(f"Unknown file: {catalog_id}")
        return Path(row["category_key"]) / row["relpath"]

    def ignore_duplicates(self, hashes: list[str], ignored: bool = True) -> dict:
        """Keep these groups as they are (they no longer show up), or show them again."""
        if ignored:
            self.store.executemany("INSERT OR IGNORE INTO duplicate_ignored (full_hash, created_at) VALUES (?, ?)",
                                   [(h, now()) for h in hashes])
        else:
            self.store.executemany("DELETE FROM duplicate_ignored WHERE full_hash = ?", [(h,) for h in hashes])
        return {"ok": True}

    # --- deciding -------------------------------------------------------------------------------

    def resolve_duplicates(self, decisions: list[dict], root: str | None = None) -> dict:
        """decisions: [{"hash", "keep": [catalog ids]}]. Every other copy goes to the trash next to it,
        in one undoable batch. Each copy is checked again right before (still there, still identical)."""
        groups = {g.hash: g for g in find_groups(self.store, self._names(root))}
        batch_id = uuid.uuid4().hex[:12]
        trashed, errors = [], []
        for d in decisions:
            g = groups.get(d["hash"])
            keep = set(d.get("keep") or [])
            if g is None:
                errors.append({"hash": d["hash"], "error": "These files are no longer identical or gone; search again"})
                continue
            keepers = [c for c in g.copies if c.id in keep]
            if not keepers:
                errors.append({"hash": g.hash, "error": "Choose at least one copy to keep"})
                continue
            if not any(_still_identical(self, c, g.hash) for c in keepers):
                errors.append({"hash": g.hash, "error": f"{keepers[0].label} has changed or is gone; nothing trashed"})
                continue
            for c in g.copies:
                if c.id in keep:
                    continue
                err = self._trash_copy(c, g.hash, batch_id)
                if err:
                    errors.append({"hash": g.hash, "error": f"{c.label}: {err}"})
                else:
                    trashed.append({"id": c.id, "label": c.label, "size": c.size})
        return {"batch_id": batch_id if trashed else None, "trashed": len(trashed),
                "bytes": sum(t["size"] for t in trashed), "errors": errors}

    def resolve_suggested_duplicates(self, root: str | None = None, only_clear: bool = True) -> dict:
        """Keep the suggested copy of every group (by default only where the suggestion is clear)."""
        decisions = [{"hash": g.hash, "keep": [g.keep]} for g in self.duplicate_groups(root)
                     if g.keep is not None and (g.clear or not only_clear)]
        return self.resolve_duplicates(decisions, root)

    def resolve_duplicate_folders(self, keep_folder: str, drop_folder: str, root: str | None = None) -> dict:
        """For every file in both folders: keep the copy in `keep_folder`, trash the one in `drop_folder`.
        Copies in other folders stay."""
        decisions = []
        for g in self.duplicate_groups(root):
            folders = {c.folder for c in g.copies}
            if keep_folder in folders and drop_folder in folders:
                decisions.append({"hash": g.hash, "keep": [c.id for c in g.copies if c.folder != drop_folder]})
        if not decisions:
            raise ValueError("These folders share no identical files (any more)")
        return self.resolve_duplicates(decisions, root)

    def _trash_copy(self, c: Copy, digest: str, batch_id: str) -> str | None:
        """Move one copy into the trash folder next to it and journal it (undo restores it)."""
        if not _still_identical(self, c, digest):
            return "changed or gone since the search; left in place"
        src = c.path
        m = PlannedMove(src, free_name(src.parent / TRASH_DIR / src.name), "delete")
        try:
            execute(m)
        except (MoveError, OSError) as e:
            return str(e)
        # category_key = the category it was in: undo puts it back into the catalog (see applying.undo)
        self.store.insert("moves", {"batch_id": batch_id, "proposal_id": None, "src": str(m.src), "dst": str(m.dst),
                                    "action": "delete", "category_key": c.key, "moved_at": now()})
        self._catalog.remove(c.key, c.relpath)
        self._search_track(m.src, m.dst)
        return None


def _still_identical(svc: ServiceBase, c: Copy, digest: str) -> bool:
    """The copy is still there and still has this content. Unchanged size and time: trust the cached
    hash (as everywhere in the catalog); otherwise read it again."""
    try:
        st = c.path.stat()
    except OSError:
        return False
    if st.st_size == c.size and abs(st.st_mtime - c.mtime) < 1:
        return True
    try:
        return st.st_size == c.size and full_hash(c.path) == digest
    except OSError:
        return False


def group_payload(g: Group) -> dict:
    return {"hash": g.hash, "size": g.size, "wasted": g.wasted, "spread": g.spread, "keep": g.keep,
            "reason": g.reason, "clear": g.clear,
            "copies": [{"id": c.id, "category": c.category, "relpath": c.relpath, "label": c.label,
                        "folder": c.folder, "mtime": c.mtime, "fit": round(g.place[c.id], 3) if c.id in g.place
                        else None} for c in g.copies]}

