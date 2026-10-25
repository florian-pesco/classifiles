from __future__ import annotations

import re
from pathlib import Path

_SCREENSHOT_NAME = re.compile(r"screen ?shot|bildschirmfoto|screenshot|snip|capture", re.I)
_SCREEN_SIZES = {(1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1440, 900), (2560, 1600),
                 (2880, 1800), (1280, 720), (1536, 864), (3440, 1440)}


def extract(path: Path, max_chars: int, ocr: bool = True) -> tuple[str, dict]:
    from PIL import Image

    meta: dict = {}
    if path.suffix.lower() == ".svg":
        return "", {"image": "vector graphic"}
    with Image.open(path) as im:
        w, h = im.size
        meta["dimensions"] = f"{w}x{h}"
        exif = im.getexif()
        camera = " ".join(str(exif.get(t, "")) for t in (271, 272)).strip()  # Make, Model
        if camera:
            meta["camera"] = camera
        if exif.get(306):  # DateTime
            meta["taken"] = str(exif.get(306))
    if _SCREENSHOT_NAME.search(path.name) or ((w, h) in _SCREEN_SIZES and "camera" not in meta):
        meta["looks_like"] = "screenshot"
    elif "camera" in meta:
        meta["looks_like"] = "photo"
    if not ocr:
        return "", meta
    from .ocr import ocr_image

    return ocr_image(str(path), max_chars), meta
