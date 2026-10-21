"""Classify one file: rules first, then a single decision over the categories."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

from .deciders import Decider
from .extract import Evidence
from .rules import Rule, first_match
from .taxonomy import Category

log = logging.getLogger(__name__)

QUESTION = "Which folder does this file belong in?"


@dataclass
class Classification:
    target: str | None  # category name; None if nothing could be proposed
    confidence: float
    source: str  # "rule" or the decider's name
    alternatives: list[dict] = field(default_factory=list)  # [{"folder", "probability"}], best first
    reason: str = ""
    neighbours: list[dict] = field(default_factory=list)  # example backend: [{category, dir, weight}]
    vector: object = None  # example backend: query vector (not stored)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("vector", None)
        return d


class Classifier:
    def __init__(self, categories: list[Category], decider: Decider | None, rules: list[Rule] | None = None):
        usable = [c for c in categories if c.usable]
        names = {c.name for c in usable}
        # The model only sees categories it may suggest; rules and manual choice can use any usable one.
        self.categories = [c for c in usable if c.suggest]
        self.decider = decider
        self.rules = []
        for r in rules or []:
            if r.folder in names:
                self.rules.append(r)
            else:
                log.warning("Skipping rule %s: category %r is unknown or disabled", r.describe(), r.folder)
        self._options = {c.name: c.option_text() for c in self.categories}
        self.decider_kwargs: dict = {}  # extra arguments for classify_evidence (evaluation uses exclude_ids)

    def classify(self, ev: Evidence) -> Classification:
        rule = first_match(self.rules, ev)
        if rule:
            return Classification(rule.folder, 1.0, "rule", reason=f"rule: {rule.describe()}")
        if not self.categories:
            return Classification(None, 0.0, "none", reason="no categories configured")
        if self.decider is None:
            return Classification(None, 0.0, "none", reason="no rule matched and no model configured")
        reason, extras = "", {}
        if hasattr(self.decider, "classify_evidence"):  # example-based: needs the evidence, not a prompt
            probs, reason, extras = self.decider.classify_evidence(ev, self.categories, **self.decider_kwargs)
        else:
            probs = self.decider.choose(ev.to_state(), QUESTION, self._options)
        ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        best, p = ranked[0]
        alts = [{"folder": f, "probability": round(q, 4)} for f, q in ranked[:4]]
        return Classification(best, round(p, 4), self.decider.name, alts, reason,
                              extras.get("neighbours", []), extras.get("vector"))
