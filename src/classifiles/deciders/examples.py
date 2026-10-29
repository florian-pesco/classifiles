"""Example-based decider: a file goes where the most similar already-sorted files are."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..examples import ExampleIndex, category_key, query_text
from ..extract import Evidence
from ..taxonomy import Category

EPS = 1e-4
DECIDED = "@decided:"  # relpath prefix of temporary examples (files decided in Review, not applied yet)


class ExampleDecider:
    name = "examples"

    def __init__(self, index: ExampleIndex, image_index=None, image_weight: float = 0.5, trained=None,
                 trained_weight: float = 0.0):
        self.index = index
        self.image_index = image_index  # optional: image similarity for pictures (image_index.py)
        self.image_weight = image_weight
        self.trained = trained  # optional: trained classifier (trained.py), mixed in with trained_weight
        self.trained_weight = trained_weight

    def classify_evidence(self, ev: Evidence, categories: list[Category], exclude_ids: set[int] | None = None,
                          image_exclude_ids: set[int] | None = None) -> tuple[dict[str, float], str, dict]:
        """Probability per category name, a human-readable reason (the most similar files), and
        extras: the neighbours (for the folder suggestion) and the text/picture vectors (kept so the
        proposal can be re-scored later without reading the file again)."""
        if self.index.size == 0:
            raise ValueError("The example index is empty. Build it first (Categories → What the model learned → Build index).")
        vec = self.index.embedder.encode([query_text(ev)], query=True)[0]
        ivec = self._image_vector(ev)
        probs, reason, nbs = self.score(vec, ivec, categories, exclude_ids, image_exclude_ids)
        return probs, reason, {"vector": vec, "image_vector": ivec, "neighbours": nbs}

    def candidates(self, vecs: list, ivecs: list, categories: list[Category]) -> list[tuple]:
        """The nearest stored examples (text, picture) for many queries at once, to be passed to
        score() as `base`. Used to re-score a whole scan quickly."""
        allowed = {category_key(c) for c in categories}
        text = self.index.candidates(np.stack(vecs), allowed) if vecs else []
        pics = [None] * len(ivecs)
        which = [i for i, v in enumerate(ivecs) if v is not None]
        if which and self.image_index is not None and self.image_index.size:
            for i, c in zip(which, self.image_index.candidates(np.stack([ivecs[i] for i in which]), allowed)):
                pics[i] = c
        return list(zip(text, pics))

    def score(self, vec, ivec, categories: list[Category], exclude_ids: set[int] | None = None,
              image_exclude_ids: set[int] | None = None, extra: list | None = None,
              image_extra: list | None = None, base: tuple | None = None) -> tuple[dict[str, float], str, list[dict]]:
        """Score from precomputed vectors. extra / image_extra: temporary examples
        [(category_key, "@decided:<name>/<subpath>", vector)] that compete with the stored ones.
        base: (text, picture) stored candidates from candidates(), to skip the index search.

        For pictures (ivec given and picture examples exist) the text result is combined with
        image similarity: P ∝ P_text^(1−w) · P_image^w."""
        by_key = {category_key(c): c for c in categories}
        text_base, image_base = base or (None, None)
        probs_by_key, neighbours = self.index.query(vec, set(by_key), exclude_ids, extra=extra, base=text_base)
        nbs = [self._neighbour(by_key, n.category_key, n.relpath, n.weight) for n in neighbours if n.relpath]
        similar = [f"{self._label(by_key, n.category_key, n.relpath)} ({n.similarity:.2f})" for n in neighbours[:3]]
        reason = "similar to: " + "; ".join(similar) if similar else ""

        if self.trained is not None and self.trained_weight > 0:
            from ..trained import blend

            p_tr = {k: p for k, p in self.trained.probs(vec).items() if k in by_key}
            if p_tr:
                total = sum(p_tr.values()) or 1.0
                p_tr = {k: p / total for k, p in p_tr.items()}
                probs_by_key = blend({k: probs_by_key.get(k, 0.0) for k in by_key}, p_tr, self.trained_weight)
                top = max(p_tr, key=p_tr.get)
                reason = f"trained model: {by_key[top].name} {p_tr[top]:.0%}" + ("; " + reason if reason else "")

        if ivec is not None and self.image_index is not None and (self.image_index.size or image_extra):
            p_img, img_nbs = self.image_index.query(ivec, set(by_key), image_exclude_ids, extra=image_extra,
                                                    base=image_base)
            if p_img:
                w = self.image_weight
                raw = {k: max(probs_by_key.get(k, 0.0), EPS) ** (1 - w) * max(p_img.get(k, 0.0), EPS) ** w
                       for k in by_key}
                total = sum(raw.values())
                probs_by_key = {k: v / total for k, v in raw.items()}
                looks = [f"{self._label(by_key, k, rel)} ({sim:.2f})" for k, rel, sim, _ in img_nbs[:3]]
                reason = "looks like: " + "; ".join(looks) + ("; " + reason if reason else "")
                # the most similar pictures also vote for the folder inside the category
                nbs = [self._neighbour(by_key, k, rel, wt * w * 2) for k, rel, _, wt in img_nbs] + nbs

        probs = {c.name: probs_by_key.get(k, 0.0) for k, c in by_key.items()}
        return probs, reason, nbs

    @staticmethod
    def _label(by_key, key, rel) -> str:
        name = by_key[key].name
        if not rel:
            return f"{name} (description)"
        if rel.startswith(DECIDED):
            return f"your decision: {rel[len(DECIDED):].split('/')[0]} → {name}"
        return f"{name}/{rel}"

    @staticmethod
    def _neighbour(by_key, key, rel, weight) -> dict:
        if rel.startswith(DECIDED):  # "@decided:<file name>/<subpath>"
            name, _, sub = rel[len(DECIDED):].partition("/")
            return {"category": by_key[key].name, "dir": sub, "file": name, "weight": round(weight, 4), "decided": True}
        return {"category": by_key[key].name, "dir": os.path.dirname(rel), "file": os.path.basename(rel),
                "weight": round(weight, 4)}

    def _image_vector(self, ev: Evidence):
        from ..image_index import is_image

        if self.image_index is None or ev.kind != "image" or not ev.path or not is_image(ev.path):
            return None
        if self.image_index.size == 0:
            return None
        return self.image_index.encode_file(Path(ev.path))

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        raise NotImplementedError("ExampleDecider works on Evidence; use classify_evidence()")
