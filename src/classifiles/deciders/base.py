"""Decider: the pluggable "System One" decision interface (Jev-style typed choice)."""

from __future__ import annotations

from typing import Protocol


class Decider(Protocol):
    name: str

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        """Return a probability for each option label (summing to ~1).

        ``options`` maps label -> short description of when to pick it.
        """
        ...
