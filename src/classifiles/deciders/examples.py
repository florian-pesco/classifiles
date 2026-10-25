"""Example-based decider: a file goes where the most similar already-sorted files are."""

from __future__ import annotations

import os

from ..examples import ExampleIndex, category_key, query_text
from ..extract import Evidence
from ..taxonomy import Category


class ExampleDecider:
    name = "examples"

    def __init__(self, index: ExampleIndex):
        self.index = index

    def classify_evidence(self, ev: Evidence, categories: list[Category],
                          exclude_ids: set[int] | None = None) -> tuple[dict[str, float], str, dict]:
        """Probability per category name, a human-readable reason (the most similar files), and
        extras for the subfolder suggestion: the neighbours (category name, folder inside it,
        weight) and the query vector."""
        if self.index.size == 0:
            raise ValueError("The example index is empty. Build it first (Categories → Example index).")
        by_key = {category_key(c): c for c in categories}
        vec = self.index.embedder.encode([query_text(ev)], query=True)[0]
        probs_by_key, neighbours = self.index.query(vec, set(by_key), exclude_ids)
        probs = {c.name: probs_by_key.get(k, 0.0) for k, c in by_key.items()}
        extras = {"vector": vec,
                  "neighbours": [{"category": by_key[n.category_key].name, "dir": os.path.dirname(n.relpath),
                                  "file": os.path.basename(n.relpath), "weight": round(n.weight, 4)}
                                 for n in neighbours if n.relpath]}
        similar = []
        for n in neighbours[:3]:
            c = by_key[n.category_key]
            label = f"{c.name}/{n.relpath}" if n.relpath else f"{c.name} (description)"
            similar.append(f"{label} ({n.similarity:.2f})")
        return probs, ("similar to: " + "; ".join(similar) if similar else ""), extras

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        raise NotImplementedError("ExampleDecider works on Evidence; use classify_evidence()")
