"""Laya backend: open, non-autoregressive decision model with calibrated probabilities (Apache-2.0)."""

from __future__ import annotations

import os
import threading

# Laya calibrates choices with 11+ options at temperature ~0.1 (see rl_agent_config.json), which
# makes them wildly overconfident (an uninformative file scored 0.61 with 10 folders, 0.98 with 11).
# So no single call ever gets more than 10 options: larger sets go through Laya's elimination
# tournament in groups of at most 10.
TOURNAMENT_ABOVE = 10


class LayaDecider:
    name = "laya"

    def __init__(self, model: str = "convaiinnovations/laya", device: str | None = None):
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import laya

        self._laya = laya
        self._agent = laya.load(model, device=device) if device else laya.load(model)
        self._lock = threading.Lock()  # the agent is not documented as thread-safe

    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]:
        if len(options) == 1:
            return {next(iter(options)): 1.0}
        questions = {"q": {"type": "choice", "instructions": question, "criteria": options}}
        with self._lock:
            if len(options) > TOURNAMENT_ABOVE:
                result = self._laya.predict_tournament(
                    self._agent, state, questions, group_size=TOURNAMENT_ABOVE
                )
            else:
                result = self._agent.predict(state, questions)
        probs = result["answers"]["q"]["probabilities"]
        # After a tournament only finalists carry probabilities; eliminated options get 0.
        return {label: float(probs.get(label, 0.0)) for label in options}
