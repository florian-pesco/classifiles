"""Example-based decider: a file goes where the most similar already-sorted files are."""

from __future__ import annotations

import os
from pathlib import Path

from ..examples import ExampleIndex, category_key, query_text
from ..extract import Evidence
from ..taxonomy import Category

EPS = 1e-4


class ExampleDecider:
    name = "examples"

    def __init__(self, index: ExampleIndex, image_index=None, image_weight: float = 0.5):
        self.index = index
        self.image_index = image_index  # optional: image similarity for pictures (image_index.py)
        self.image_weight = image_weight

    def classify_evidence(self, ev: Evidence, categories: list[Category], exclude_ids: set[int] | None = None,
                          image_exclude_ids: set[int] | None = None) -> tuple[dict[str, float], str, dict]:
        """Probability per category name, a human-readable reason (the most similar files), and
        extras for the subfolder suggestion: the neighbours (category name, folder inside it,
        weight) and the query vector.

        For pictures, the text result is combined with image similarity (if the image index has
        examples): P ∝ P_text^(1−w) · P_image^w."""
        if self.index.size == 0:
            raise ValueError("The example index is empty. Build it first (Categories → Example index).")
        by_key = {category_key(c): c for c in categories}
        vec = self.index.embedder.encode([query_text(ev)], query=True)[0]
        probs_by_key, neighbours = self.index.query(vec, set(by_key), exclude_ids)
        nbs = [{"category": by_key[n.category_key].name, "dir": os.path.dirname(n.relpath),
                "file": os.path.basename(n.relpath), "weight": round(n.weight, 4)} for n in neighbours if n.relpath]
        similar = []
        for n in neighbours[:3]:
            c = by_key[n.category_key]
            label = f"{c.name}/{n.relpath}" if n.relpath else f"{c.name} (description)"
            similar.append(f"{label} ({n.similarity:.2f})")
        reason = "similar to: " + "; ".join(similar) if similar else ""

        image = self._image_opinion(ev, set(by_key), image_exclude_ids)
        if image is not None:
            p_img, img_nbs = image
            w = self.image_weight
            raw = {k: max(probs_by_key.get(k, 0.0), EPS) ** (1 - w) * max(p_img.get(k, 0.0), EPS) ** w for k in by_key}
            total = sum(raw.values())
            probs_by_key = {k: v / total for k, v in raw.items()}
            looks = [f"{by_key[k].name}/{rel} ({sim:.2f})" for k, rel, sim, _ in img_nbs[:3]]
            reason = "looks like: " + "; ".join(looks) + ("; " + reason if reason else "")
            # the most similar pictures also vote for the folder inside the category
            nbs = [{"category": by_key[k].name, "dir": os.path.dirname(rel), "file": os.path.basename(rel),
                    "weight": round(wt * w * 2, 4)} for k, rel, _, wt in img_nbs] + nbs

        probs = {c.name: probs_by_key.get(k, 0.0) for k, c in by_key.items()}
        return probs, reason, {"vector": vec, "neighbours": nbs}

    def _image_opinion(self, ev: Evidence, allowed: set[str], exclude_ids: set[int] | None):
        from ..image_index import is_image

        if self.image_index is None or ev.kind != "image" or not ev.path or not is_image(ev.path):
            return None
        if self.image_index.size == 0:
            return None
        vec = self.image_index.encode_file(Path(ev.path))
        if vec is None:
            return None
        p_img, nbs = self.image_index.query(vec, allowed, exclude_ids)
        return (p_img, nbs) if p_img else None

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        raise NotImplementedError("ExampleDecider works on Evidence; use classify_evidence()")
