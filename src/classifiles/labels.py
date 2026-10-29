"""What a picture shows, as one German word from a fixed list (zero-shot, SigLIP2).

The picture model already gives every inbox picture a vector (image_index.py). SigLIP also has a
text side in the same space: "a photo of a beach" lands close to beach photos. The label prompts
are embedded once with the checkpoint's text tower (CPU, then released) and cached in the database
together with the model's logit scale and bias. Labelling a picture is then one small matrix product.

A label is used only when it clearly wins: its share among all labels (softmax) must be high and it
must beat the "nothing special" prompts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading

import numpy as np

from .store import Store, now

log = logging.getLogger(__name__)

# German word, English prompt. Order is irrelevant; one word per picture.
LABELS: list[tuple[str, str]] = [
    ("Strand", "a photo of a beach"), ("Berge", "a photo of mountains"), ("See", "a photo of a lake"),
    ("Meer", "a photo of the sea"), ("Wald", "a photo of a forest"), ("Schnee", "a photo of snow"),
    ("Sonnenuntergang", "a photo of a sunset"), ("Blumen", "a photo of flowers"), ("Garten", "a photo of a garden"),
    ("Natur", "a photo of a landscape"), ("Stadt", "a photo of a city"), ("Gebaeude", "a photo of a building"),
    ("Kirche", "a photo of a church"), ("Innenraum", "a photo of a room interior"), ("Kueche", "a photo of a kitchen"),
    ("Essen", "a photo of food on a plate"), ("Getraenke", "a photo of drinks"), ("Kuchen", "a photo of a cake"),
    ("Personen", "a photo of people"), ("Gruppenfoto", "a group photo of many people"), ("Selfie", "a selfie"),
    ("Portraet", "a portrait photo of a person"), ("Baby", "a photo of a baby"), ("Kinder", "a photo of children"),
    ("Hund", "a photo of a dog"), ("Katze", "a photo of a cat"), ("Pferd", "a photo of a horse"),
    ("Tiere", "a photo of animals"), ("Vogel", "a photo of a bird"), ("Auto", "a photo of a car"),
    ("Fahrrad", "a photo of a bicycle"), ("Zug", "a photo of a train"), ("Flugzeug", "a photo of an airplane"),
    ("Boot", "a photo of a boat"), ("Konzert", "a photo of a concert"), ("Party", "a photo of a party"),
    ("Geburtstag", "a photo of a birthday party with a cake"), ("Hochzeit", "a photo of a wedding"),
    ("Weihnachten", "a photo of christmas decorations"), ("Feuerwerk", "a photo of fireworks"),
    ("Sport", "a photo of people doing sports"), ("Fussball", "a photo of a football match"),
    ("Wandern", "a photo of hiking"), ("Ski", "a photo of skiing"), ("Dokument", "a photo of a paper document"),
    ("Quittung", "a photo of a receipt"), ("Tafel", "a photo of a blackboard"),
    ("Whiteboard", "a photo of a whiteboard with writing"), ("Praesentation", "a photo of presentation slides"),
    ("Karte", "a map"), ("Meme", "a meme with text"), ("Kunst", "a painting"), ("Museum", "a photo of a museum"),
    ("Zeichnung", "a drawing"), ("Computer", "a photo of a computer screen"), ("Handy", "a photo of a phone"),
    ("Pflanzen", "a photo of plants"), ("Himmel", "a photo of the sky"), ("Nacht", "a photo taken at night"),
]
# "nothing special": a picture closest to these gets no label
NULL_PROMPTS = ["a photo", "an image", "a blurry photo", "a close-up photo of an object"]
MIN_SHARE = 0.45  # softmax share of the best label among labels + null prompts


def _version(model: str) -> str:
    return hashlib.sha1(json.dumps([model, LABELS, NULL_PROMPTS]).encode()).hexdigest()[:12]


class Labeller:
    """Scores picture vectors against the label prompts. The text side is computed once (or
    loaded from the database); `encode_texts` is only called on a cache miss."""

    def __init__(self, store: Store, model: str, encode_texts=None, threads: int = 2):
        self.store = store
        self.model = model
        self.threads = threads
        self._encode_texts = encode_texts or self._siglip_texts
        self._data = None  # (text matrix, scale, bias)
        self._lock = threading.Lock()

    def _siglip_texts(self, prompts: list[str]) -> tuple[np.ndarray, float, float]:
        """Embed prompts with the checkpoint's text tower on the CPU, then free it."""
        import torch
        from transformers import AutoTokenizer, SiglipModel

        torch.set_num_threads(self.threads)
        try:
            tok = AutoTokenizer.from_pretrained(self.model, local_files_only=True)
        except OSError:
            log.info("Downloading the tokenizer of %s (one time)", self.model)
            tok = AutoTokenizer.from_pretrained(self.model)
        m = SiglipModel.from_pretrained(self.model, dtype=torch.float32, local_files_only=True).eval()
        with torch.inference_mode():
            t = tok(prompts, padding="max_length", max_length=64, return_tensors="pt")
            out = m.get_text_features(**t)
            feats = out if torch.is_tensor(out) else out.pooler_output
            feats = torch.nn.functional.normalize(feats, dim=-1).numpy().astype(np.float32)
        scale, bias = float(m.logit_scale.exp()), float(m.logit_bias)
        del m
        return feats, scale, bias

    def _get(self):
        if self._data is not None:
            return self._data
        with self._lock:
            if self._data is None:
                version = _version(self.model)
                row = self.store.one("SELECT * FROM label_features WHERE version = ?", (version,))
                if row:
                    n = len(LABELS) + len(NULL_PROMPTS)
                    feats = np.frombuffer(row["vectors"], dtype=np.float32).reshape(n, -1)
                    self._data = (feats, row["scale"], row["bias"])
                else:
                    feats, scale, bias = self._encode_texts([p for _, p in LABELS] + NULL_PROMPTS)
                    self.store.execute("DELETE FROM label_features")
                    self.store.insert("label_features", {"version": version, "model": self.model,
                                                         "vectors": feats.astype(np.float32).tobytes(),
                                                         "scale": scale, "bias": bias, "created_at": now()})
                    self._data = (feats, scale, bias)
        return self._data

    def label(self, vec) -> tuple[str | None, float]:
        """(German word, share) for a picture vector; (None, share) if nothing clearly wins."""
        if vec is None:
            return None, 0.0
        feats, scale, _ = self._get()
        v = np.asarray(vec, dtype=np.float32)
        v = v / (np.linalg.norm(v) or 1.0)
        z = feats @ v * scale
        z -= z.max()
        p = np.exp(z) / np.exp(z).sum()
        best = int(np.argmax(p))
        if best >= len(LABELS) or p[best] < MIN_SHARE:
            return None, float(p[best])
        return LABELS[best][0], float(p[best])
