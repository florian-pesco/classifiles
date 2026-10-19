from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path


def extract(path: Path, max_chars: int) -> tuple[str, dict]:
    names: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as t:
            names = t.getnames()
    if not names:
        return "", {}
    return "Contains: " + ", ".join(names[:40]), {"entries": len(names)}
