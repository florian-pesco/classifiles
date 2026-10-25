"""Settings for classifiles, loaded from a YAML file (default: ~/.config/classifiles/config.yaml).

The web UI edits this file for you; editing by hand is optional. Format:

    inboxes: [/mnt/d/Archive/Inbox]     # folders to clean up
    roots:                                        # every subfolder of a root becomes a category
      - path: /mnt/d/Archive
    categories:                                   # per-category settings, keyed by path
      - path: /mnt/d/Archive/Personal
        description: personal documents, ID, contracts
      - path: /mnt/d/Archive/Backups
        enabled: false                            # never propose this folder
      - path: /mnt/c/Users/me/OneDrive/Steuer     # not under a root: a manual category
        name: Steuer
        description: tax documents
        action: copy                              # copy instead of move
    delete:                                       # built-in "Delete" category (to trash)
      enabled: true
      suggest: false                              # let the model propose it?
    rules:
      - {kind: installer, folder: Delete}
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("CLASSIFILES_CONFIG_DIR", Path.home() / ".config" / "classifiles"))
DATA_DIR = Path(os.environ.get("CLASSIFILES_DATA_DIR", Path.home() / ".local" / "share" / "classifiles"))
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"

# Keys of the first config format, converted on load.
LEGACY_KEYS = {"target_root", "folder_descriptions", "exclude_folders"}

CATEGORY_KEYS = {"path", "name", "description", "enabled", "action"}
ACTIONS = ("move", "copy")


@dataclass
class Settings:
    # Folders that collect unsorted files (e.g. Downloads).
    inboxes: list[str] = field(default_factory=list)
    # Folders whose immediate subfolders become categories: [{"path": "/mnt/d/Archive"}].
    roots: list[dict] = field(default_factory=list)
    # Per-category settings keyed by path: name, description, enabled, action (move|copy).
    # An entry whose path is not a subfolder of a root is a manual category.
    categories: list[dict] = field(default_factory=list)
    # Built-in "Delete" category: files go to a trash folder next to the inbox (undoable) until
    # the trash is emptied. suggest: may the model propose it (rules/manual choice always can).
    delete: dict = field(default_factory=lambda: {"enabled": True, "suggest": False, "description": ""})
    # Deterministic rules, checked before the model; see classifiles.rules.
    rules: list[dict] = field(default_factory=list)
    # Proposals at or above this confidence are "suggested"; below it they need review.
    threshold: float = 0.7
    # Default cap on files per scan (newest first); None = all files in the inbox.
    max_files: int | None = None
    # Decision backend: "examples" (learns from already-sorted files), "laya" (decides from
    # descriptions) or "keyword" (no model, for testing).
    backend: str = "examples"
    # "convaiinnovations/laya" (English) or "ml" (multilingual, better for German).
    laya_model: str = "convaiinnovations/laya"
    # Example index (backend "examples"); all local, bounded resource use.
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    examples_per_category: int = 3000   # files sampled per category, spread over subfolders
    content_per_category: int = 300     # documents per category whose text is read (slow on /mnt/...)
    gpu_memory_fraction: float = 0.25   # cap on this process's share of GPU memory
    cpu_threads: int = 2
    # Sample filenames shown to the model for folders WITHOUT a description.
    samples_per_folder: int = 3
    max_text_chars: int = 1500
    # Reading inbox files (never applied while indexing):
    ocr_pdf_pages: int = 2              # scanned PDFs without text: read this many first pages by OCR (0 = off)
    ocr_images: bool = True             # read text in images by OCR
    use_download_source: bool = True    # Windows download note -> website domain (local, domain only)
    # Second opinion from a local language model for unsure files (examples backend only).
    second_opinion: bool = False
    llm_model: str = "Qwen/Qwen3.5-2B"
    llm_max_files: int = 100            # per scan
    llm_gpu_fraction: float = 0.7       # GPU share of the separate model process
    llm_weight: float = 0.5             # 0 = examples only, 1 = language model only
    llm_rotations: int = 3              # ask with rotated option orders and average (removes letter bias)
    db_path: str = str(DATA_DIR / "classifiles.db")

    def __post_init__(self):
        self.roots = [{"path": r} if isinstance(r, str) else dict(r) for r in self.roots]
        for c in self.categories:
            unknown = set(c) - CATEGORY_KEYS
            if unknown or "path" not in c:
                raise ValueError(f"Category entries need 'path' and only {sorted(CATEGORY_KEYS)}: {c}")
            if c.get("action", "move") not in ACTIONS:
                raise ValueError(f"Category action must be one of {ACTIONS}: {c}")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        if path is not None and not Path(path).exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()  # no personal config yet
        return cls.from_dict(yaml.safe_load(path.read_text()) or {}, source=str(path))

    @classmethod
    def from_dict(cls, data: dict, source: str = "config") -> "Settings":
        data = _upgrade_legacy(dict(data))
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys in {source}: {sorted(unknown)}")
        return cls(**data)

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True))
        return path

    def to_dict(self) -> dict:
        return asdict(self)


def _upgrade_legacy(data: dict) -> dict:
    """Convert the first format (one target_root + descriptions by folder name) to roots/categories."""
    if not LEGACY_KEYS & set(data):
        return data
    root = data.pop("target_root", None)
    descriptions = data.pop("folder_descriptions", None) or {}
    excluded = data.pop("exclude_folders", None) or []
    if root:
        data.setdefault("roots", []).append({"path": root})
        cats = data.setdefault("categories", [])
        for name, desc in descriptions.items():
            cats.append({"path": str(Path(root) / name), "description": desc})
        for name in excluded:
            cats.append({"path": str(Path(root) / name), "enabled": False})
    return data
