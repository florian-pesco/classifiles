"""Local OCR (text recognition in images) with RapidOCR. Runs on the CPU, nothing leaves the PC."""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def engine():
    """The OCR engine, or None if the `ocr` extra isn't installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    return RapidOCR()


def ocr_image(image, max_chars: int) -> str:
    """Text in an image (file path, PIL image or numpy array); '' if OCR is unavailable or fails."""
    eng = engine()
    if eng is None:
        return ""
    try:
        if not isinstance(image, str):
            import numpy as np

            image = np.asarray(image.convert("RGB")) if hasattr(image, "convert") else image
        result, _ = eng(image)
    except Exception as e:  # noqa: BLE001 - OCR is best effort
        log.debug("OCR failed: %s", e)
        return ""
    return " ".join(r[1] for r in (result or []))[:max_chars]
