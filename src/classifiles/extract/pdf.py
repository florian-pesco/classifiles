from __future__ import annotations

from pathlib import Path


def extract(path: Path, max_chars: int) -> tuple[str, dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    meta = {"pages": len(reader.pages)}
    info = reader.metadata or {}
    if info.get("/Title"):
        meta["title"] = str(info["/Title"])
    if info.get("/Author"):
        meta["author"] = str(info["/Author"])
    chunks, total = [], 0
    for page in reader.pages[:3]:
        t = page.extract_text() or ""
        chunks.append(t)
        total += len(t)
        if total >= max_chars:
            break
    text = " ".join(chunks)
    if not text.strip():
        meta["note"] = "scanned PDF without a text layer"
    return text, meta
