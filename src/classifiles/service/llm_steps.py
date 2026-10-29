"""The optional language-model steps of a scan (second opinion, file-name help), in one model session."""

from __future__ import annotations

from .. import llm
from ..store import now
from .base import ServiceBase


class LlmStepsMixin(ServiceBase):
    def second_opinion_candidates(self, proposals: list[dict], threshold: float) -> list[dict]:
        """Proposals that get a second opinion: example-model proposals below the threshold with
        usable evidence and at least two candidate categories; at most llm_max_files."""
        from ..extract.base import Evidence

        out = []
        for p in sorted(proposals, key=lambda p: p["confidence"]):
            if p["source"] != "examples" or p["confidence"] >= threshold or p["status"] not in ("needs_review",):
                continue
            ev = Evidence.from_dict(p["evidence"])
            if len(llm.candidates(p["alternatives"])) < 2 or not llm.has_evidence(ev):
                continue
            out.append({**p, "_ev": ev})
            if len(out) >= self.settings.llm_max_files:
                break
        return out

    def ask_second_opinion(self, client, p: dict) -> dict:
        """The language model's probabilities over the candidates of one proposal ({"p_llm": …})."""
        cats = {c.name: c for c in self.categories()}
        names = llm.candidates(p["alternatives"])
        options = {n: {"name": n, "description": cats[n].description if n in cats else "",
                       "examples": llm.examples_for(p["neighbours"] or [], n)} for n in names}
        p_llm = llm.ask_rotated(client, lambda order: llm.build_prompt(p["_ev"], [options[n] for n in order]),
                                names, self.settings.llm_rotations, llm.LETTERS)
        return {"p_llm": p_llm}

    def run_second_opinion(self, scan_id: int, proposals: list[dict], threshold: float) -> int:
        """Ask the local language model about unsure proposals and record its answer as a review
        hint. Returns how many were checked."""
        todo = self.second_opinion_candidates(proposals, threshold)
        return self._llm_session(scan_id, [("second_opinion", todo, lambda c, p: self._record_second_opinion(
            p, self.ask_second_opinion(c, p)))])

    def run_llm_steps(self, scan_id: int, proposals: list[dict], threshold: float) -> int:
        """The optional language-model steps of a scan, in one model session: second opinion on
        unsure files, then type and subject of suggested file names."""
        steps = []
        if self.settings.second_opinion and self.settings.backend == "examples":
            steps.append(("second_opinion", self.second_opinion_candidates(proposals, threshold),
                          lambda c, p: self._record_second_opinion(p, self.ask_second_opinion(c, p))))
        if self.settings.name_llm and self.settings.name_suggestions:
            steps.append(("naming", self.name_candidates(proposals), self._ask_and_record_name))
        return self._llm_session(scan_id, steps)

    def _llm_session(self, scan_id: int, steps: list[tuple]) -> int:
        """Run steps [(phase, proposals, fn(client, proposal))] with one model process, which is
        stopped afterwards. Returns how many proposals were handled."""
        steps = [s for s in steps if s[1]]
        if not steps:
            return 0
        self._llm_cancel.clear()
        if self._index is not None:
            self._index.embedder.unload()  # free GPU memory for the language model
        if self._image_index is not None:
            self._image_index.embedder.unload()
        done_total = 0
        self.store.update("scans", scan_id, {"phase": steps[0][0], "llm_total": len(steps[0][1]), "llm_done": 0})
        try:
            with llm.LLMClient(self.settings.llm_model, self.settings.llm_gpu_fraction,
                               self.settings.cpu_threads) as client:
                for phase, todo, fn in steps:
                    self.store.update("scans", scan_id, {"phase": phase, "llm_total": len(todo), "llm_done": 0})
                    for i, p in enumerate(todo, 1):
                        if self._llm_cancel.is_set():
                            return done_total
                        fn(client, p)
                        done_total += 1
                        self.store.update("scans", scan_id, {"llm_done": i})
        except llm.LLMError as e:
            self.store.update("scans", scan_id, {"llm_error": str(e)[:500]})
        return done_total

    def cancel_second_opinion(self) -> None:
        self._llm_cancel.set()

    def _record_second_opinion(self, p: dict, answer: dict) -> None:
        """Store the language model's answer as a hint. Deliberately does NOT change the proposal's
        category, confidence or status: in testing, agreement was a good sign on real inbox files
        but raised confidence for wrong answers on files of unsorted topics."""
        p_llm = answer["p_llm"]
        choice = max(p_llm, key=p_llm.__getitem__)
        note = "agrees" if choice == p["target"] else f"suggests {choice}"
        self.store.update("proposals", p["id"], {
            "llm_choice": choice, "llm_confidence": round(p_llm[choice], 4), "knn_confidence": p["confidence"],
            "reason": (p["reason"] + "; " if p["reason"] else "") + f"second opinion {note} ({p_llm[choice]:.2f})",
            "updated_at": now(),
        })
