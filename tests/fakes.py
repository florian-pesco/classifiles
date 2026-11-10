"""Fake models for tests: deterministic, no download, no GPU, no network."""

import zlib

import numpy as np
from PIL import Image

DIM = 64


class FakeEmbedder:
    """Bag-of-words hashing: texts sharing words get similar vectors. No model, no network."""

    model_name = "fake"
    loaded = True

    def encode(self, texts, query=False):
        out = np.zeros((len(texts), DIM), np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace("/", " ").replace("_", " ").replace(".", " ").split():
                out[i, zlib.crc32(w.encode()) % DIM] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)

    def unload(self):
        pass


class FakeImageEmbedder:
    """Vector = normalised mean colour + a bit of structure: same-coloured pictures are similar."""

    model_name = "fake-image"
    loaded = True

    def encode_paths(self, paths):
        vecs, ok = [], []
        for i, p in enumerate(paths):
            try:
                with Image.open(p) as im:
                    a = np.asarray(im.convert("RGB").resize((8, 8)), dtype=np.float32).reshape(-1) / 255
            except (OSError, ValueError):  # not a picture
                continue
            v = np.concatenate([a, [1.0]])
            vecs.append(v / np.linalg.norm(v))
            ok.append(i)
        return (np.stack(vecs).astype(np.float32) if vecs else np.zeros((0, 193), np.float32)), ok

    def unload(self):
        pass


def picture(path, colour, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path
