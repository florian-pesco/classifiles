from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

OCR_DPI = 150
OCR_MAX_SIDE = 2000  # pixels; very large pages (posters, plans) are scaled down


def extract(path: Path, max_chars: int, ocr_pages: int = 0) -> tuple[str, dict]:
    """Text of the first pages. If the PDF has no text layer (a scan) and ocr_pages > 0, the first
    ocr_pages pages are rendered and read with local OCR instead."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    meta = {"pages": len(reader.pages)}
    info = reader.metadata or {}
    if info.get("/Title"):
        meta["title"] = str(info["/Title"])
    if info.get("/Author"):
        meta["author"] = str(info["/Author"])
    try:
        created = reader.metadata.creation_date if reader.metadata else None
    except Exception:  # noqa: BLE001 - malformed date strings are common
        created = None
    if created and 1990 <= created.year:
        meta["created"] = created.date().isoformat()
    chunks, total = [], 0
    for page in reader.pages[:3]:
        t = page.extract_text() or ""
        chunks.append(t)
        total += len(t)
        if total >= max_chars:
            break
    text = " ".join(chunks)
    if text.strip():
        return text, meta
    if ocr_pages > 0:
        ocr_text = _ocr_pages(path, ocr_pages, max_chars)
        if ocr_text.strip():
            n = min(ocr_pages, meta["pages"])
            meta["note"] = "text read by OCR (first page)" if n == 1 else f"text read by OCR (first {n} pages)"
            return ocr_text, meta
    meta["note"] = "scanned PDF without a text layer"
    return text, meta


def _ocr_pages(path: Path, pages: int, max_chars: int) -> str:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ""
    from .ocr import ocr_image

    out, total = [], 0
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as e:  # noqa: BLE001 - damaged PDFs are judged by name
        log.debug("cannot render %s: %s", path, e)
        return ""
    try:
        for i in range(min(pages, len(doc))):
            page = doc[i]
            w, h = page.get_size()  # points (1/72 inch)
            scale = min(OCR_DPI / 72, OCR_MAX_SIDE / max(w, h, 1))
            image = page.render(scale=scale).to_pil()
            text = ocr_image(image, max_chars - total)
            out.append(text)
            total += len(text)
            if total >= max_chars:
                break
    finally:
        doc.close()
    return " ".join(out)
