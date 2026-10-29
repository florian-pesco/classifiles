"""Videos: recording date and place from the MP4/MOV header, and a frame for picture recognition.

The header is read with a small box parser (no external tools): `moov/mvhd` holds the creation
time (seconds since 1904, UTC), `©xyz` (phones, ISO 6709 like "+48.1372+011.5756/") the place.
Other containers (mkv, avi, …) only get the frame.
"""

from __future__ import annotations

import re
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_FRAME_BYTES = 500 * 1024 * 1024  # videos above this size are not opened for a frame
_EPOCH_1904 = datetime(1904, 1, 1, tzinfo=UTC)
_ISO6709 = re.compile(rb"([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)")
_CONTAINERS = {b"moov", b"udta", b"trak", b"mdia", b"meta"}


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, payload end) for the boxes in data[start:end]."""
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        header = 8
        if size == 1 and i + 16 <= end:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            header = 16
        elif size == 0:
            size = end - i
        if size < header:
            return
        yield kind, i + header, min(i + size, end)
        i += size


def _moov(path: Path, limit: int = 64 * 1024 * 1024) -> bytes | None:
    """The moov box (metadata) of an MP4/MOV file, without reading the media data."""
    with open(path, "rb") as f:
        pos = 0
        while True:
            head = f.read(16)
            if len(head) < 8:
                return None
            size, kind = struct.unpack(">I4s", head[:8])
            header = 8
            if size == 1:
                size, header = struct.unpack(">Q", head[8:16])[0], 16
            if kind == b"moov":
                if size > limit:
                    return None
                f.seek(pos)
                return f.read(size)
            if size < header:
                return None
            pos += size
            f.seek(pos)


def header_meta(path: Path) -> dict:
    """{"taken": "YYYY-MM-DD HH:MM:SS", "gps": [lat, lon]} from an MP4/MOV header, as far as present."""
    if path.suffix.lower() not in (".mp4", ".mov", ".m4v", ".3gp"):
        return {}
    data = _moov(path)
    if not data:
        return {}
    meta: dict = {}

    def walk(start, end):
        for kind, a, b in _boxes(data, start, end):
            if kind == b"mvhd" and "taken" not in meta:
                version = data[a]
                secs = struct.unpack(">Q", data[a + 4:a + 12])[0] if version == 1 else \
                    struct.unpack(">I", data[a + 4:a + 8])[0]
                if secs > 0:
                    dt = _EPOCH_1904 + timedelta(seconds=secs)
                    if 1990 <= dt.year <= datetime.now().year + 1:
                        meta["taken"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            elif kind == b"\xa9xyz" and "gps" not in meta:
                m = _ISO6709.search(data[a:b])
                if m:
                    meta["gps"] = [round(float(m.group(1)), 5), round(float(m.group(2)), 5)]
            elif kind in _CONTAINERS:
                walk(a + (4 if kind == b"meta" else 0), b)  # meta is a "full box" with 4 flag bytes

    walk(8, len(data))
    if "gps" not in meta:  # Apple stores it under a keys/ilst pair: search the text form directly
        i = data.find(b"com.apple.quicktime.location.ISO6709")
        if i >= 0:
            m = _ISO6709.search(data, i + 36)
            if m:
                meta["gps"] = [round(float(m.group(1)), 5), round(float(m.group(2)), 5)]
    return meta


def extract(path: Path, max_chars: int) -> tuple[str, dict]:
    return "", header_meta(path)


def middle_frame(path: Path):
    """A PIL image of the frame in the middle of the video, or None (unreadable or too big)."""
    try:
        if path.stat().st_size > MAX_FRAME_BYTES:
            return None
        import cv2
        from PIL import Image

        cap = cv2.VideoCapture(str(path))
        try:
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if n > 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, n // 2)
            ok, frame = cap.read()
        finally:
            cap.release()
        if not ok or frame is None:
            return None
        im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        im.thumbnail((448, 448))
        return im
    except Exception:  # noqa: BLE001 - a broken video must never stop a scan
        return None
