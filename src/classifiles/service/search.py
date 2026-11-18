"""Search page: keep the search index of roots, manual folders and inboxes current, and query it."""

from __future__ import annotations

from pathlib import Path

from .. import background
from ..search import Query, SearchIndex, searched_locations
from .base import ServiceBase
from .helpers import log


class SearchMixin(ServiceBase):
    def search_index(self) -> SearchIndex:
        return self._search

    def search_locations(self) -> list[str]:
        """Where Search looks: every root, manual category folder and inbox (nested ones once)."""
        s = self.settings
        manual = [c["path"] for c in s.categories if c.get("path") and not any(
            Path(c["path"]).expanduser() == Path(r["path"]) or Path(r["path"]) in Path(c["path"]).expanduser().parents
            for r in s.roots)]
        return searched_locations([*(r["path"] for r in s.roots), *manual, *s.inboxes])

    def start_search_update(self) -> dict:
        """Update the search index in the background (low priority, cancellable)."""
        if self._search.status.get("state") == "running":
            return self.search_status()
        self._search.status = {"state": "running", "phase": "listing"}
        background.run_in_background(self.update_search_index, name="classifiles-search")
        return self.search_status()

    def update_search_index(self) -> dict:
        """The update itself (blocking; the CLI calls it directly)."""
        return self._search.update(self.search_locations(), self.settings.search_content)

    def cancel_search_update(self) -> dict:
        self._search.cancel()
        return self.search_status()

    def search_status(self) -> dict:
        return {**self._search.status, **self._search.counts(), "locations": self.search_locations(),
                "content": self.settings.search_content}

    def search(self, query: Query | dict) -> dict:
        """Find files; see search.Query for the filters."""
        q = query if isinstance(query, Query) else Query(**query)
        if q.location is not None and q.location not in self.search_locations():
            raise ValueError(f"Not a searched location: {q.location}")
        return self._search.search(q)

    def search_result_path(self, file_id: int) -> Path:
        """Path of a search result (for opening it), only inside the searched locations."""
        row = self.store.one("SELECT location, relpath FROM search_files WHERE id = ?", (file_id,))
        if row is None or row["location"] not in self.search_locations():
            raise KeyError(f"Unknown file: {file_id}")
        return Path(row["location"]) / row["relpath"]

    def _search_track(self, src: str | Path | None, dst: str | Path | None) -> None:
        """Keep the search index current when classifiles moves a file (never fails the move)."""
        try:
            self._search.track(self.search_locations(), Path(src) if src else None, Path(dst) if dst else None)
        except Exception:  # noqa: BLE001 - the index is a convenience; the next update repairs it
            log.exception("updating the search index after a move failed")
