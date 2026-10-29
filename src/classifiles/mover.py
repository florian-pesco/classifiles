"""Safe file operations: never overwrite, only into known categories, always journaled.

Actions:
- move:   the file goes into the category folder
- copy:   a copy goes into the category folder; the original stays in the inbox
- delete: the file goes into a hidden trash folder next to it (`<inbox>/.classifiles-trash/`),
          which is a fast rename and undoable. Only empty_trash() removes files for good.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import normalize_subpath, split_existing
from .taxonomy import Category

TRASH_DIR = ".classifiles-trash"


class MoveError(Exception):
    pass


@dataclass
class PlannedMove:
    src: Path
    dst: Path
    action: str  # move | copy | delete
    created_dir: Path | None = None  # topmost folder that apply will create (None: all exist)


def plan_move(src: str | Path, category: Category, subpath: str = "", name: str | None = None) -> PlannedMove:
    """Where `src` goes. `subpath` is a folder inside the category ('' = the category folder);
    missing folders in it are created by execute() and removed again by revert() if empty.
    `name`: a new file name (None = keep it). Deleted files always keep their name."""
    src = Path(src)
    if not category.usable:
        raise MoveError(f"Category {category.name!r} is disabled or its folder is missing")
    if not src.is_file():
        raise MoveError(f"Source file is missing: {src}")
    if category.action == "delete":
        return PlannedMove(src, free_name(src.parent / TRASH_DIR / src.name), "delete")
    root = category.path
    if root is None or not root.is_dir():
        raise MoveError(f"Target folder does not exist: {root}")
    try:
        subpath = normalize_subpath(subpath)
    except ValueError as e:
        raise MoveError(str(e)) from None
    dest_dir = root / subpath if subpath else root
    if root.resolve() not in (dest_dir.resolve(), *dest_dir.resolve().parents):
        raise MoveError(f"Folder {subpath!r} is outside the category")
    existing, missing = split_existing(root, subpath)
    created = (root / existing / missing[0]) if missing else None
    if not missing and dest_dir.resolve() == src.parent.resolve():
        raise MoveError(f"File is already in {dest_dir}")
    target = name or src.name
    dst = dest_dir / target if missing else free_name(dest_dir / target, underscore=bool(name))
    return PlannedMove(src, dst, category.action, created)


def free_name(path: Path, underscore: bool = False) -> Path:
    """`file.pdf` -> `file (1).pdf` -> `file (2).pdf` ... until the name is unused. For suggested
    names (underscore=True): `Name_Type.pdf` -> `Name_Type_2.pdf` -> `Name_Type_3.pdf`."""
    if not path.exists():
        return path
    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{i + 1}{path.suffix}" if underscore
                                   else f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise MoveError(f"No free name for {path}")


def execute(m: PlannedMove) -> None:
    if m.dst.exists():
        raise MoveError(f"Destination already exists: {m.dst}")
    if m.created_dir is not None and m.created_dir.exists() and not m.created_dir.is_dir():
        raise MoveError(f"A file is in the way of the new folder: {m.created_dir}")
    m.dst.parent.mkdir(parents=True, exist_ok=True)
    if m.action == "copy":
        shutil.copy2(m.src, m.dst)  # keeps the modification time, used by revert()
    else:
        shutil.move(str(m.src), str(m.dst))


def remove_empty_dirs(start: Path, up_to: Path) -> None:
    """Remove `start` and its parents while they are empty, stopping after `up_to` (inclusive)."""
    current = start
    try:
        while up_to == current or up_to in current.parents:
            current.rmdir()  # fails (and stops) as soon as a folder is not empty
            if current == up_to:
                break
            current = current.parent
    except OSError:
        pass


def revert(src: Path, dst: Path, action: str, created_dir: Path | None = None) -> None:
    """Undo one journaled operation. Folders created for it are removed again if empty."""
    if not dst.exists():
        raise MoveError(f"File is no longer at {dst}")
    if action == "copy":
        # Only remove the copy if it is still identical in size and time to the original.
        if src.exists():
            a, b = src.stat(), dst.stat()
            if a.st_size != b.st_size or int(a.st_mtime) != int(b.st_mtime):
                raise MoveError(f"Copy was changed since; left in place: {dst}")
        else:
            raise MoveError(f"Original is gone, keeping the copy: {dst}")
        os.remove(dst)
        if created_dir is not None:
            remove_empty_dirs(dst.parent, created_dir)
        return
    if src.exists():
        raise MoveError(f"Something new is at the original place, not overwriting: {src}")
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dst), str(src))
    if created_dir is not None:
        remove_empty_dirs(dst.parent, created_dir)
    if action == "delete":
        try:
            dst.parent.rmdir()  # remove the trash folder once empty
        except OSError:
            pass


def purge(path: Path) -> None:
    """Permanently delete a file from a classifiles trash folder (and nothing else)."""
    if path.parent.name != TRASH_DIR:
        raise MoveError(f"Refusing to delete a file outside a trash folder: {path}")
    if path.exists():
        os.remove(path)
    try:
        path.parent.rmdir()  # remove the trash folder once empty
    except OSError:
        pass
