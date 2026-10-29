"""Nearest town for GPS coordinates, offline.

Uses the GeoNames list of towns with at least 15,000 inhabitants (`cities15000`, CC BY 4.0,
https://www.geonames.org), downloaded once (~3 MB) to ~/.cache/classifiles/ and turned into a
small numpy file. Coordinates never leave the PC: the lookup is a local nearest-neighbour search.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import zipfile
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

URL = "https://download.geonames.org/export/dump/cities15000.zip"
CACHE = Path(os.environ.get("CLASSIFILES_CACHE", Path.home() / ".cache" / "classifiles"))
MAX_KM = 30.0


class Places:
    def __init__(self, cache_dir: Path = CACHE, url: str = URL):
        self.file = cache_dir / "cities15000.npz"
        self.url = url
        self._data = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def _load(self):
        if self._data is not None:
            return self._data
        with self._lock:
            if self._data is None:
                if not self.file.exists():
                    self._download()
                z = np.load(self.file, allow_pickle=False)
                self._data = (np.radians(z["lat"]), np.radians(z["lon"]), list(z["name"]))
        return self._data

    def _download(self) -> None:
        import urllib.request

        log.info("Downloading the town list (GeoNames cities15000, one time)")
        with urllib.request.urlopen(self.url, timeout=60) as r:
            raw = r.read()
        names, lat, lon = [], [], []
        with zipfile.ZipFile(io.BytesIO(raw)) as z, z.open("cities15000.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                cols = line.rstrip("\n").split("\t")
                if len(cols) > 5:
                    names.append(cols[1])  # name (local spelling); cols[2] is ASCII
                    lat.append(float(cols[4]))
                    lon.append(float(cols[5]))
        self.file.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.file, name=np.array(names), lat=np.array(lat, np.float32),
                            lon=np.array(lon, np.float32))

    def nearest(self, lat: float, lon: float, max_km: float = MAX_KM) -> str | None:
        """Name of the nearest town within max_km, or None (also if the list can't be loaded)."""
        try:
            la, lo, names = self._load()
        except Exception as e:  # noqa: BLE001 - offline or blocked: names just get no place
            self.error = str(e)[:200]
            return None
        p, q = np.radians(lat), np.radians(lon)
        a = np.sin((la - p) / 2) ** 2 + np.cos(p) * np.cos(la) * np.sin((lo - q) / 2) ** 2
        km = 6371.0 * 2 * np.arcsin(np.sqrt(np.minimum(a, 1.0)))
        i = int(np.argmin(km))
        return str(names[i]) if km[i] <= max_km else None
