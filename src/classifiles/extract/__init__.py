"""Turn a file into Evidence: metadata plus a short text snippet the decision model can read."""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from pathlib import Path

from .base import Evidence, kind_of
from . import archive, image, office, pdf, text

log = logging.getLogger(__name__)

# kind -> extractor(path, max_chars) -> (text, meta)
_EXTRACTORS = {
    "pdf": pdf.extract,
    "document": office.extract,
    "spreadsheet": office.extract,
    "presentation": office.extract,
    "text": text.extract,
    "code": text.extract,
    "data": text.extract,
    "image": image.extract,
    "archive": archive.extract,
}


def extract(path: str | Path, max_chars: int = 1500) -> Evidence:
    path = Path(path)
    st = path.stat()
    kind = kind_of(path)
    ev = Evidence(
        path=str(path),
        name=path.name,
        ext=path.suffix.lower(),
        kind=kind,
        mime=mimetypes.guess_type(path.name)[0] or "",
        size=st.st_size,
        modified=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    )
    fn = _EXTRACTORS.get(kind)
    if fn:
        try:
            ev.text, ev.meta = fn(path, max_chars)
        except Exception as e:  # a broken file must never stop a scan
            log.debug("extract failed for %s: %s", path, e)
            ev.meta["extract_error"] = str(e)[:200]
    ev.text = " ".join(ev.text.split())[:max_chars]
    return ev


__all__ = ["Evidence", "extract", "kind_of"]
