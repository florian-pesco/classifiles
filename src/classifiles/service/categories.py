"""Categories (roots, manual folders, Delete), rules, and the guard against unreachable drives."""

from __future__ import annotations

from pathlib import Path

from ..config import ACTIONS, Settings
from ..rules import load_rules
from ..taxonomy import Category, build_categories
from .base import ServiceBase


class CategoriesMixin(ServiceBase):
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
