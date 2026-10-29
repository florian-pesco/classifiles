from __future__ import annotations

import re
from pathlib import Path

_SCREENSHOT_NAME = re.compile(r"screen ?shot|bildschirmfoto|screenshot|snip|capture", re.I)
_SCREEN_SIZES = {(1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1440, 900), (2560, 1600),
                 (2880, 1800), (1280, 720), (1536, 864), (3440, 1440)}


def _gps(exif) -> list[float] | None:
    """[lat, lon] from the EXIF GPS block, or None."""
    try:
        g = exif.get_ifd(0x8825)
        if not g or 2 not in g or 4 not in g:
            return None

        def deg(v):
            d, m, s = (float(x) for x in v)
            return d + m / 60 + s / 3600

        lat, lon = deg(g[2]), deg(g[4])
        if g.get(1) in ("S", b"S"):
            lat = -lat
        if g.get(3) in ("W", b"W"):
            lon = -lon
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            return None
        return [round(lat, 5), round(lon, 5)]
    except Exception:  # noqa: BLE001 - malformed GPS blocks are common
        return None


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
        gps = _gps(exif)
        if gps:
            meta["gps"] = gps
    if _SCREENSHOT_NAME.search(path.name) or ((w, h) in _SCREEN_SIZES and "camera" not in meta):
        meta["looks_like"] = "screenshot"
    elif "camera" in meta:
        meta["looks_like"] = "photo"
    if not ocr:
        return "", meta
    from .ocr import ocr_image

    return ocr_image(str(path), max_chars), meta
