"""Keyword-overlap decider: no model, deterministic. For tests and machines without a GPU."""

from __future__ import annotations

import math
import re

_WORD = re.compile(r"[a-zA-ZäöüÄÖÜß]{3,}")


def _words(s: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(s)}


class KeywordDecider:
    name = "keyword"

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        state_words = _words(state)
        scores = {label: len(state_words & _words(f"{label} {desc}")) for label, desc in options.items()}
        exp = {label: math.exp(2.0 * s) for label, s in scores.items()}
        total = sum(exp.values())
        return {label: v / total for label, v in exp.items()}
