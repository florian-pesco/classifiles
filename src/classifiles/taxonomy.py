"""The taxonomy: the categories files can be sorted into.

Categories come from three sources:
- auto:    every immediate subfolder of a configured root
- manual:  any folder path listed in the config's `categories` that is not under a root
- delete:  the built-in "Delete" category (files go to a trash folder, see mover.py)

Per-category settings (name, description, enabled, action) are looked up by path.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DELETE_NAME = "Delete"
DEFAULT_DELETE_DESCRIPTION = "junk: installers, temporary or generated files, duplicates, partial downloads"


@dataclass
class Category:
    name: str  # unique label; what proposals and rules refer to
    path: Path | None  # None for the Delete category
    description: str = ""
    samples: list[str] = field(default_factory=list)  # filenames already in the folder
    action: str = "move"  # move | copy | delete
    enabled: bool = True
    source: str = "auto"  # auto | manual | delete
    root: str | None = None  # root it was discovered under (auto only)
    exists: bool = True
    suggest: bool = True  # may the model propose it? (rules and manual choice ignore this)

    @property
    def usable(self) -> bool:
        """Can files be put here (by rule, by hand, or by the model if `suggest`)?"""
        return self.enabled and self.exists

    def option_text(self) -> str:
        """Short description handed to the decision model as this option's criterion.

        Laya gives all options together ~192 tokens; when they overflow, each is cut to
        (192 - 16) / n tokens (~17 with 10 folders). So a written description is used alone,
        and sample filenames only stand in for a missing description."""
        if self.description:
            return self.description
        if self.samples:
            return f"files about {self.name}, e.g. " + ", ".join(self.samples)
        return f"files about {self.name}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path) if self.path else None
        d["usable"] = self.usable
        return d


def _key(p: str | Path) -> str:
    return os.path.normpath(os.path.expanduser(str(p)))


def build_categories(
    roots: list[dict],
    entries: list[dict],
    samples_per_folder: int = 3,
    exclude_paths: list[str | Path] | None = None,
    delete: dict | None = None,
) -> list[Category]:
    """All categories, including disabled and missing ones (the UI shows them; classification
    uses only `usable` ones). Paths in exclude_paths (inboxes) never become categories."""
    settings = {_key(e["path"]): e for e in entries}
    skip = {_key(p) for p in exclude_paths or []}
    out: list[Category] = []
    seen: set[str] = set()

    for root in roots:
        root_path = Path(_key(root["path"]))
        try:
            children = sorted((c for c in root_path.iterdir() if c.is_dir()), key=lambda p: p.name.lower())
        except OSError:  # root missing or unreadable
            continue
        for child in children:
            k = _key(child)
            if child.name.startswith(".") or k in skip or k in seen:
                continue
            seen.add(k)
            out.append(_category(child, settings.get(k, {}), "auto", str(root_path), samples_per_folder))

    for k, entry in settings.items():
        if k in seen or k in skip:
            continue
        seen.add(k)
        path = Path(k)
        out.append(_category(path, entry, "manual", None, samples_per_folder, exists=path.is_dir()))

    if delete is not None:
        out.append(Category(
            name=DELETE_NAME, path=None,
            description=delete.get("description") or DEFAULT_DELETE_DESCRIPTION,
            action="delete", enabled=delete.get("enabled", True), source="delete",
            suggest=delete.get("suggest", False),
        ))

    _make_names_unique(out)
    return out


def _category(path: Path, entry: dict, source: str, root: str | None, n_samples: int,
              exists: bool = True) -> Category:
    description = entry.get("description", "") or ""
    enabled = entry.get("enabled", True)
    return Category(
        name=entry.get("name") or path.name,
        path=path,
        description=description,
        # Samples only stand in for a missing description (see option_text).
        samples=[] if description or not enabled or not exists else _sample_names(path, n_samples),
        action=entry.get("action", "move"),
        enabled=enabled,
        source=source,
        root=root,
        exists=exists,
    )


def _make_names_unique(cats: list[Category]) -> None:
    """Two roots may both have an 'Uni' folder: label them 'Uni (RootA)' and 'Uni (RootB)'.
    The Delete category keeps its name; a folder called 'Delete' gets a suffix instead."""
    by_name: dict[str, list[Category]] = {}
    for c in cats:
        by_name.setdefault(c.name, []).append(c)
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        for c in group:
            if c.source == "delete":
                continue
            parent = Path(c.root).name if c.root else (c.path.parent.name if c.path else "")
            c.name = f"{name} ({parent})"
    taken: set[str] = set()
    for c in sorted(cats, key=lambda c: c.source != "delete"):  # Delete claims its name first
        base, i = c.name, 2
        while c.name in taken:
            c.name = f"{base} {i}"
            i += 1
        taken.add(c.name)


def load_taxonomy(
    root: str | Path,
    descriptions: dict[str, str] | None = None,
    exclude: list[str] | None = None,
    samples_per_folder: int = 3,
    exclude_paths: list[str | Path] | None = None,
) -> list[Category]:
    """Single-root convenience: usable folder categories under `root` (descriptions/exclude by name)."""
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Root does not exist: {root}")
    entries = [{"path": str(root / n), "description": d} for n, d in (descriptions or {}).items()]
    entries += [{"path": str(root / n), "enabled": False} for n in exclude or []]
    cats = build_categories([{"path": str(root)}], entries, samples_per_folder, exclude_paths)
    return [c for c in cats if c.usable]


def _sample_names(folder: Path, n: int, max_scan: int = 50) -> list[str]:
    """Up to n recently modified file names directly in the folder.

    Only looks at the first max_scan entries of the folder itself, never recursing: on Windows
    drives (/mnt/...) every file access from WSL is slow, and a recursive walk took ~27 s."""
    if n <= 0:
        return []
    entries = []
    try:
        with os.scandir(folder) as it:
            for i, e in enumerate(it):
                if i >= max_scan:
                    break
                if not e.name.startswith(".") and e.is_file():
                    entries.append((e.stat().st_mtime, e.name))
    except OSError:
        return []
    entries.sort(reverse=True)
    return [name[:60] for _, name in entries[:n]]
