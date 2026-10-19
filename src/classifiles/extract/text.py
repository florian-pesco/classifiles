from __future__ import annotations

from pathlib import Path


def extract(path: Path, max_chars: int) -> tuple[str, dict]:
    with open(path, "rb") as f:
        raw = f.read(max_chars * 4)
    if b"\x00" in raw[:1024]:
        return "", {"note": "binary content"}
    return raw.decode("utf-8", errors="ignore")[:max_chars], {}
