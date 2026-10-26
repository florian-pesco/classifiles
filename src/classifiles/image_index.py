"""Image similarity: compare inbox pictures with pictures already sorted into the categories.

The text index sees only an image's name and OCR text, so photos, screenshots and scans named
"IMG_….jpg" were guessed. Here each image gets a vector from a small local image model (the image
part of SigLIP2-base, 93M parameters, ~0.2-0.3 GB GPU memory in fp16), and an inbox image is
compared with up to `per_category` sorted images per category (nearest neighbours).

Bounded on purpose, like the text index:
- at most `per_category` images per category, sampled evenly over subfolders; huge files skipped
- JPEGs are decoded at reduced size (draft mode); small batches with pauses; low-priority thread
- incremental (unchanged files keep their vectors); cancellable
Everything runs locally; the model is downloaded once, then loaded offline.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from .examples import BATCH, PAUSE, category_key, sample_folder
from .extract import kind_of
from .store import Store, now
from .taxonomy import Category

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_WALK = 60_000


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


class ImageEmbedder:
    def __init__(self, model: str = "google/siglip2-base-patch16-224", gpu_memory_fraction: float = 0.25,
                 cpu_threads: int = 2, batch_size: int = 16):
        self.model_name = model
        self.gpu_memory_fraction = gpu_memory_fraction
        self.cpu_threads = cpu_threads
        self.batch_size = batch_size
        self._model = None
        self._proc = None
        self._device = "cpu"
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        import torch
        import transformers
        from transformers import AutoImageProcessor, SiglipVisionModel

        transformers.logging.set_verbosity_error()
        torch.set_num_threads(self.cpu_threads)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        if self._device == "cuda":
            torch.cuda.set_per_process_memory_fraction(self.gpu_memory_fraction)
        dtype = torch.float16 if self._device == "cuda" else torch.float32
        try:
            proc = AutoImageProcessor.from_pretrained(self.model_name, local_files_only=True)
            model = SiglipVisionModel.from_pretrained(self.model_name, dtype=dtype, local_files_only=True)
        except OSError:
            log.info("Downloading image model %s (one time)", self.model_name)
            proc = AutoImageProcessor.from_pretrained(self.model_name)
            model = SiglipVisionModel.from_pretrained(self.model_name, dtype=dtype)
        os.environ["HF_HUB_OFFLINE"] = "1"
        self._proc, self._model = proc, model.to(self._device).eval()

    @staticmethod
    def open_image(path: Path):
        """The image at reduced size (fast for big photos); None if it can't be read."""
        from PIL import Image

        try:
            im = Image.open(path)
            im.draft("RGB", (448, 448))  # JPEG: decode at 1/2..1/8 size directly
            im = im.convert("RGB")
            im.thumbnail((448, 448))
            return im
        except Exception:  # noqa: BLE001 - broken or unsupported images are skipped
            return None

    def encode_images(self, images: list) -> np.ndarray:
        """L2-normalised vectors for PIL images."""
        import torch

        with self._lock:
            if self._model is None:
                self._load()
            out = []
            for i in range(0, len(images), self.batch_size):
                batch = images[i:i + self.batch_size]
                with torch.inference_mode():
                    pixels = self._proc(images=batch, return_tensors="pt")["pixel_values"].to(self._device)
                    if self._device == "cuda":
                        pixels = pixels.half()
                    v = self._model(pixel_values=pixels).pooler_output.float()
                out.append(torch.nn.functional.normalize(v, dim=-1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 768), np.float32)

    def encode_paths(self, paths: list[Path]) -> tuple[np.ndarray, list[int]]:
        """Vectors for the readable images, and their positions in `paths`."""
        images, ok = [], []
        for i, p in enumerate(paths):
            im = self.open_image(p)
            if im is not None:
                images.append(im)
                ok.append(i)
        return (self.encode_images(images) if images else np.zeros((0, 768), np.float32)), ok

    def unload(self) -> None:
        with self._lock:
            self._model = self._proc = None


class ImageIndex:
    def __init__(self, store: Store, embedder: ImageEmbedder, per_category: int = 300, max_mb: int = 40,
                 k: int = 10, temperature: float = 0.02):
        self.store = store
        self.embedder = embedder
        self.per_category = per_category
        self.max_bytes = max_mb * 1024 * 1024
        self.k = k
        self.temperature = temperature
        self._data = None
        self._cancel = threading.Event()
        self.status: dict = {"state": "idle"}

    # --- query --------------------------------------------------------------------------

    def load(self) -> None:
        rows = self.store.query("SELECT id, category_key, relpath, vector FROM image_examples WHERE model = ?",
                                (self.embedder.model_name,))
        if not rows:
            self._data = (np.zeros(0, int), [], [], np.zeros((0, 1), np.float32))
            return
        ids = np.array([r["id"] for r in rows])
        keys = [r["category_key"] for r in rows]
        rels = [r["relpath"] for r in rows]
        matrix = np.stack([np.frombuffer(r["vector"], dtype=np.float16) for r in rows]).astype(np.float32)
        self._data = (ids, keys, rels, matrix)

    def _get(self):
        if self._data is None:
            self.load()
        return self._data

    @property
    def size(self) -> int:
        return len(self._get()[0])

    def counts(self) -> dict[str, int]:
        return {r["category_key"]: r["n"] for r in self.store.query(
            "SELECT category_key, COUNT(*) AS n FROM image_examples WHERE model = ? GROUP BY category_key",
            (self.embedder.model_name,))}

    def query(self, vec: np.ndarray, allowed: set[str], exclude_ids: set[int] | None = None):
        """(probability per category key, [(category_key, relpath, similarity, weight)] best first)."""
        ids, keys, rels, matrix = self._get()
        if not len(ids):
            return {}, []
        mask = np.array([k in allowed for k in keys])
        if exclude_ids:
            mask &= ~np.isin(ids, list(exclude_ids))
        idx = np.flatnonzero(mask)
        if not len(idx):
            return {}, []
        sims = matrix[idx] @ vec
        k = min(self.k, len(idx))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        weights = np.exp((sims[top] - sims[top[0]]) / self.temperature)
        scores: dict[str, float] = {}
        for t, w in zip(top, weights):
            scores[keys[idx[t]]] = scores.get(keys[idx[t]], 0.0) + float(w)
        total = sum(scores.values())
        neighbours = [(keys[idx[t]], rels[idx[t]], float(sims[t]), float(w)) for t, w in zip(top, weights)]
        return {key: s / total for key, s in scores.items()}, neighbours

    def encode_file(self, path: Path) -> np.ndarray | None:
        vecs, ok = self.embedder.encode_paths([path])
        return vecs[0] if ok else None

    # --- build --------------------------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def build(self, categories: list[Category]) -> dict:
        self._cancel.clear()
        model = self.embedder.model_name
        self.status = {"state": "running", "message": "images: starting", "done": 0, "total": 0, "added": 0,
                       "started_at": now()}
        try:
            self.store.execute("DELETE FROM image_examples WHERE model != ?", (model,))
            cats = [c for c in categories if c.usable and c.path is not None]
            for i, c in enumerate(cats, 1):
                if self._cancel.is_set():
                    break
                self.status = {**self.status, "message": f"images: {c.name} ({i}/{len(cats)})", "done": 0, "total": 0}
                self._index_category(c)
            self.load()
            state = "cancelled" if self._cancel.is_set() else "done"
            self.status = {**self.status, "state": state, "message": state, "finished_at": now()}
        except Exception as e:  # noqa: BLE001 - shown in the UI
            log.exception("image index build failed")
            self.status = {**self.status, "state": "failed", "error": str(e)[:500]}
        return self.status

    def _index_category(self, c: Category) -> None:
        key, root, model = category_key(c), c.path, self.embedder.model_name
        sample = [rel for rel in sample_folder(root, self.per_category * 4, MAX_WALK) if is_image(rel)]
        sample = sample[:self.per_category]
        existing = {r["relpath"]: r for r in self.store.query(
            "SELECT id, relpath, mtime FROM image_examples WHERE category_key = ? AND model = ?", (key, model))}
        todo, keep = [], set()
        for rel in sample:
            try:
                st = (root / rel).stat()
            except OSError:
                continue
            if st.st_size > self.max_bytes:
                continue
            keep.add(rel)
            row = existing.get(rel)
            if not (row and abs(row["mtime"] - st.st_mtime) < 1):
                todo.append((rel, st.st_mtime))
        stale = [r["id"] for rel, r in existing.items() if rel not in keep]
        self.store.executemany("DELETE FROM image_examples WHERE id = ?", [(i,) for i in stale])
        self.status = {**self.status, "total": len(todo)}
        for start in range(0, len(todo), BATCH):
            if self._cancel.is_set():
                return
            batch = todo[start:start + BATCH]
            vecs, ok = self.embedder.encode_paths([root / rel for rel, _ in batch])
            rows = [(key, batch[i][0], batch[i][1], model, v.astype(np.float16).tobytes()) for i, v in zip(ok, vecs)]
            self.store.executemany(
                "INSERT OR REPLACE INTO image_examples (category_key, relpath, mtime, model, vector) VALUES (?, ?, ?, ?, ?)",
                rows)
            self.status = {**self.status, "done": min(start + BATCH, len(todo)),
                           "added": self.status["added"] + len(rows)}
            time.sleep(PAUSE)

    # --- learning -----------------------------------------------------------------------

    def add(self, items: list[tuple[str, str, Path]]) -> None:
        """Learn applied images: [(category_key, relpath, path on disk)]."""
        items = [it for it in items if is_image(it[2])]
        if not items:
            return
        vecs, ok = self.embedder.encode_paths([p for _, _, p in items])
        rows = []
        for i, v in zip(ok, vecs):
            key, rel, path = items[i]
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = time.time()
            rows.append((key, rel, mtime, self.embedder.model_name, v.astype(np.float16).tobytes()))
        self.store.executemany(
            "INSERT OR REPLACE INTO image_examples (category_key, relpath, mtime, model, vector) VALUES (?, ?, ?, ?, ?)",
            rows)
        self.load()

    def remove(self, items: list[tuple[str, str]]) -> None:
        if items:
            self.store.executemany("DELETE FROM image_examples WHERE category_key = ? AND relpath = ?", items)
            self.load()


__all__ = ["ImageEmbedder", "ImageIndex", "is_image", "kind_of"]
