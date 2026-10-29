"""Second opinion from a local language model, for files the example model is unsure about.

The example model proposes categories with probabilities. For gated files (below the threshold,
with usable evidence), the language model gets the file plus the top-3 candidate categories, each
with its most similar already-sorted files, and picks one letter. It is asked once per rotation
of the option order and the answers are averaged, which removes its preference for certain
letters.

The answer is a review hint ("both agree" / "suggests X"), not a change of confidence. Measured on
real data, blending it into the confidence also raised wrong answers above the threshold for
files of unsorted topics (see evaluate_second_opinion; `combine` is kept for that comparison).
The model runs in its own process (llm_worker.py), started for the step and stopped afterwards.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading

from .paths import name_tokens

log = logging.getLogger(__name__)

LETTERS = ["A", "B", "C"]
CONTENT_CHARS = 1200
MIN_TEXT = 150
READY_TIMEOUT = 1800  # first start may include the one-time model download
ANSWER_TIMEOUT = 120


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Runs llm_worker.py as a child process and asks it questions over stdin/stdout."""

    def __init__(self, model: str, gpu_fraction: float = 0.7, threads: int = 2, cmd: list[str] | None = None):
        self.model = model
        cmd = cmd or [sys.executable, "-m", "classifiles.llm_worker", "--model", model,
                      "--gpu-fraction", str(gpu_fraction), "--threads", str(threads)]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self._next = 0
        ready = self._read(READY_TIMEOUT)
        if not ready.get("ready"):
            self.close()
            raise LLMError(f"Language model could not start: {ready.get('error', 'unknown error')}")
        self.info = ready

    def _read(self, timeout: float) -> dict:
        result: dict = {}

        def read():
            line = self._proc.stdout.readline()
            if line:
                result.update(json.loads(line))

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive() or not result:
            self.close()
            raise LLMError("Language model stopped responding")
        return result

    def choose(self, prompt: str, options: list[str]) -> dict[str, float]:
        self._next += 1
        self._proc.stdin.write(json.dumps({"id": self._next, "task": "choose", "prompt": prompt,
                                           "options": options}) + "\n")
        self._proc.stdin.flush()
        res = self._read(ANSWER_TIMEOUT)
        if "error" in res:
            raise LLMError(res["error"])
        return res["probs"]

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.stdin.write(json.dumps({"task": "shutdown"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                self._proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --- deciding which files get a second opinion --------------------------------------------

def has_evidence(ev, min_text: int = MIN_TEXT) -> bool:
    """Enough to reason about: some text (incl. OCR) or a file name with at least two words."""
    return len(ev.text or "") >= min_text or len(name_tokens(ev.name)) >= 2


def candidates(alternatives: list[dict], k: int = 3, min_prob: float = 0.005) -> list[str]:
    """Top-k categories of the example model worth asking about."""
    return [a["folder"] for a in alternatives[:k] if a["probability"] >= min_prob]


# --- the question -----------------------------------------------------------------------

def examples_for(neighbours: list[dict], category: str, n: int = 3) -> list[str]:
    """Paths of the most similar already-sorted files in a category ('Studies/Lectures/Econ 1/x.pdf')."""
    out = []
    for nb in neighbours:
        if nb["category"] == category and nb.get("file"):
            out.append("/".join(p for p in (category, nb.get("dir", ""), nb["file"]) if p))
            if len(out) >= n:
                break
    return out


def build_prompt(ev, options: list[dict]) -> str:
    """options: [{"name", "description", "examples": [paths]}] in letter order."""
    lines = [f"File name: {ev.name}", f"Type: {ev.kind}"]
    if ev.meta.get("source"):
        lines.append(f"Downloaded from: {ev.meta['source']}")
    if ev.text:
        lines.append(f"Content (start): {ev.text[:CONTENT_CHARS]}")
    lines.append("")
    lines.append("Candidate folders:")
    for letter, opt in zip(LETTERS, options):
        lines.append(f"{letter}) {opt['name']}" + (f": {opt['description']}" if opt.get("description") else ""))
        for ex in opt.get("examples", []):
            lines.append(f"   - already there: {ex}")
    lines.append("")
    lines.append(f"Which folder does this file belong in? Answer with one letter: {', '.join(LETTERS[:len(options)])}.")
    return "\n".join(lines)


def ask_rotated(client, build, names: list[str], rotations: int, letters: list[str] | None = None) -> dict[str, float]:
    """Ask once per rotation of the option order and average. Small models prefer certain letters
    (often "A"), which would otherwise always favour the first option."""
    letters = (letters or NAME_LETTERS)[:len(names)]
    out = {n: 0.0 for n in names}
    rotations = max(1, min(rotations, len(names)))
    for r in range(rotations):
        order = names[r:] + names[:r]
        probs = client.choose(build(order), letters)
        for n, letter in zip(order, letters):
            out[n] += probs[letter] / rotations
    return out


# --- file names -------------------------------------------------------------------------

NAME_LETTERS = ["A", "B", "C", "D", "E", "F"]
OTHER = "Other"
NONE = "None of these"


def type_options(name: str, text: str, rule_type: str | None, k: int = 5) -> list[str]:
    """Up to k document types worth asking about (best keyword scores, the rule's choice, a few
    common ones), plus "Other"."""
    from .naming import type_scores

    ranked = [t for t, _ in sorted(type_scores(name, text).items(), key=lambda kv: -kv[1])]
    opts = ([rule_type] if rule_type else []) + ranked
    # fill up with common types (no keyword found: these are the usual suspects)
    opts += ["Letter", "Contract", "Invoice", "Report", "Notes", "Form", "Certificate"]
    uniq = list(dict.fromkeys(o for o in opts if o and o not in ("Screenshot", "Scan")))[:k]
    return uniq + [OTHER]


def build_type_prompt(ev, options: list[str]) -> str:
    from .naming import TYPE_WORDS

    lines = [f"File name: {ev.name}", f"Content (start): {ev.text[:CONTENT_CHARS]}", "",
             "What kind of document is this?"]
    for letter, opt in zip(NAME_LETTERS, options):
        label = opt if opt == OTHER else f"{opt} ({TYPE_WORDS[opt][1]})" if TYPE_WORDS[opt][1] != opt else opt
        lines.append(f"{letter}) {label if opt != OTHER else 'Something else'}")
    lines += ["", f"Answer with one letter: {', '.join(NAME_LETTERS[:len(options)])}."]
    return "\n".join(lines)


def build_subject_prompt(ev, options: list[str], type_word: str | None) -> str:
    lines = [f"File name: {ev.name}", f"Content (start): {ev.text[:CONTENT_CHARS]}", "",
             f"This {type_word.lower() if type_word else 'document'} gets a new file name that starts with who it is "
             "from or what it is about. Which fits best?"]
    for letter, opt in zip(NAME_LETTERS, options):
        lines.append(f"{letter}) {opt}")
    lines += ["", f"Answer with one letter: {', '.join(NAME_LETTERS[:len(options)])}."]
    return "\n".join(lines)


def combine(p_examples: dict[str, float], p_llm: dict[str, float], weight: float) -> dict[str, float]:
    """Geometric blend over the candidates (both renormalised to the candidates first)."""
    names = list(p_llm)
    a_total = sum(p_examples.get(n, 0.0) for n in names) or 1.0
    eps = 1e-6
    raw = {n: max(p_examples.get(n, 0.0) / a_total, eps) ** (1 - weight) * max(p_llm[n], eps) ** weight
           for n in names}
    total = sum(raw.values())
    return {n: v / total for n, v in raw.items()}
