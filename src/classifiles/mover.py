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

from .taxonomy import Category

TRASH_DIR = ".classifiles-trash"


class MoveError(Exception):
    pass


@dataclass
class PlannedMove:
    src: Path
    dst: Path
    action: str  # move | copy | delete


def plan_move(src: str | Path, category: Category) -> PlannedMove:
    src = Path(src)
    if not category.usable:
        raise MoveError(f"Category {category.name!r} is disabled or its folder is missing")
    if not src.is_file():
        raise MoveError(f"Source file is missing: {src}")
    if category.action == "delete":
        return PlannedMove(src, free_name(src.parent / TRASH_DIR / src.name), "delete")
    dest_dir = category.path
    if dest_dir is None or not dest_dir.is_dir():
        raise MoveError(f"Target folder does not exist: {dest_dir}")
    if dest_dir.resolve() == src.parent.resolve():
        raise MoveError(f"File is already in {dest_dir}")
    return PlannedMove(src, free_name(dest_dir / src.name), category.action)


def free_name(path: Path) -> Path:
    """`file.pdf` -> `file (1).pdf` -> `file (2).pdf` ... until the name is unused."""
    if not path.exists():
        return path
    for i in range(1, 10_000):
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise MoveError(f"No free name for {path}")


def execute(m: PlannedMove) -> None:
    if m.dst.exists():
        raise MoveError(f"Destination already exists: {m.dst}")
    m.dst.parent.mkdir(parents=True, exist_ok=True)
    if m.action == "copy":
        shutil.copy2(m.src, m.dst)  # keeps the modification time, used by revert()
    else:
        shutil.move(str(m.src), str(m.dst))


def revert(src: Path, dst: Path, action: str) -> None:
    """Undo one journaled operation."""
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
        return
    if src.exists():
        raise MoveError(f"Something new is at the original place, not overwriting: {src}")
    src.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dst), str(src))
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
