"""Second opinion from a local language model, with a fake model (no download, no GPU)."""

import sys
import textwrap

import pytest

from classifiles import llm
from classifiles.extract.base import Evidence
from test_examples import ex_service, sorted_tree  # noqa: F401


class FakeClient:
    """Stands in for LLMClient. `pick` is the category name it prefers (by content), or None to
    always answer 'A' (a model with pure letter bias)."""

    instances: list = []
    pick: str | None = None
    fail_after: int | None = None

    def __init__(self, model, gpu_fraction=0.7, threads=2):
        self.prompts = []
        self.info = {"gpu_gb": 0}
        FakeClient.instances.append(self)

    def choose(self, prompt, options):
        self.prompts.append(prompt)
        if FakeClient.fail_after is not None and len(self.prompts) > FakeClient.fail_after:
            raise llm.LLMError("model crashed")
        if FakeClient.pick is None:
            return {o: (0.9 if o == "A" else 0.1 / (len(options) - 1)) for o in options}
        for letter in options:  # find the letter whose option line names the preferred category
            if f"\n{letter}) {FakeClient.pick}" in prompt:
                return {o: (0.95 if o == letter else 0.05 / (len(options) - 1)) for o in options}
        return {o: 1 / len(options) for o in options}

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def so_service(ex_service, sorted_tree, monkeypatch):
    FakeClient.instances, FakeClient.pick, FakeClient.fail_after = [], None, None
    monkeypatch.setattr(llm, "LLMClient", FakeClient)
    ex_service.settings.second_opinion = True
    ex_service.settings.threshold = 0.99  # make everything "unsure" in the tiny fixture
    ex_service.example_index().build(ex_service.categories(refresh=True))
    (sorted_tree["inbox"] / "invoice_april_bank.txt").write_text("invoice for april, bank transfer " * 10)
    return ex_service


def _first_choice(service, inbox):
    """Target and candidates the example model gives the test file (without a second opinion)."""
    service.settings.second_opinion = False
    _, rows = service.scan(inbox)
    service.settings.second_opinion = True
    r = next(r for r in rows if r["name"] == "invoice_april_bank.txt")
    return r["target"], llm.candidates(r["alternatives"])


def test_hint_only_never_changes_the_proposal(so_service, sorted_tree):
    target, cands = _first_choice(so_service, sorted_tree["inbox"])
    other = next(c for c in cands if c != target)
    FakeClient.pick = other
    scan_id, rows = so_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_april_bank.txt")
    assert r["llm_choice"] == other and r["target"] == target  # category unchanged
    assert r["confidence"] == r["knn_confidence"] and r["status"] == "needs_review"
    assert f"second opinion suggests {other}" in r["reason"]
    assert so_service.get_scan(scan_id)["llm_done"] >= 1


def test_agreement_is_recorded(so_service, sorted_tree):
    target, _ = _first_choice(so_service, sorted_tree["inbox"])
    FakeClient.pick = target
    _, rows = so_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_april_bank.txt")
    assert r["llm_choice"] == target and "second opinion agrees" in r["reason"]


def test_rotation_cancels_letter_bias(so_service, sorted_tree):
    FakeClient.pick = None  # always answers "A"
    _, rows = so_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_april_bank.txt")
    n = len(llm.candidates(r["alternatives"]))
    assert len(FakeClient.instances[0].prompts) >= n  # asked once per rotation
    assert r["llm_confidence"] == pytest.approx(1 / n, abs=0.01)  # pure letter bias averages out


def test_gating(so_service, sorted_tree):
    so_service.settings.llm_max_files = 1
    scan_id, rows = so_service.scan(sorted_tree["inbox"])
    asked = [r for r in rows if r["llm_choice"]]
    assert len(asked) == 1
    assert all(r["source"] == "examples" for r in asked)          # never rule hits
    so_service.settings.second_opinion = False
    FakeClient.instances = []
    _, rows = so_service.scan(sorted_tree["inbox"])
    assert FakeClient.instances == [] and not any(r["llm_choice"] for r in rows)


def test_prompt_contents(so_service, sorted_tree):
    so_service.scan(sorted_tree["inbox"])
    prompt = FakeClient.instances[0].prompts[0]
    assert "Candidate folders:" in prompt and "A) " in prompt and "B) " in prompt
    assert "already there: Finance/2025/" in prompt or "already there: Work/2025/" in prompt
    assert prompt.rstrip().endswith("Answer with one letter: A, B, C.") or "Answer with one letter" in prompt


def test_model_failure_keeps_the_scan(so_service, sorted_tree):
    FakeClient.fail_after = 1
    scan_id, rows = so_service.scan(sorted_tree["inbox"])
    scan = so_service.get_scan(scan_id)
    assert scan["status"] == "done" and "model crashed" in scan["llm_error"]


def test_cancel_stops_the_step(so_service, sorted_tree, monkeypatch):
    original = FakeClient.choose

    def cancel_after_first(self, prompt, options):
        so_service.cancel_second_opinion()
        return original(self, prompt, options)

    monkeypatch.setattr(FakeClient, "choose", cancel_after_first)
    scan_id, _ = so_service.scan(sorted_tree["inbox"])
    assert so_service.get_scan(scan_id)["llm_done"] == 1


def test_combine_and_helpers():
    c = llm.combine({"A": 0.6, "B": 0.4, "X": 0.0}, {"A": 0.9, "B": 0.1}, 0.5)
    assert set(c) == {"A", "B"} and abs(sum(c.values()) - 1) < 1e-9 and c["A"] > 0.6
    ev = Evidence(path="", name="x.pdf", ext=".pdf", kind="pdf", mime="", size=1, modified="", text="")
    assert not llm.has_evidence(ev)
    ev.name = "Projektarbeit Smart Home.pdf"
    assert llm.has_evidence(ev)
    nb = [{"category": "Studies", "dir": "V/Econ 1", "file": "a.pdf", "weight": 1},
          {"category": "Work", "dir": "", "file": "b.docx", "weight": .5}]
    assert llm.examples_for(nb, "Studies") == ["Studies/V/Econ 1/a.pdf"] and llm.examples_for(nb, "Work") == ["Work/b.docx"]


def test_client_protocol_with_a_fake_worker():
    worker = textwrap.dedent("""
        import json, sys
        print(json.dumps({"ready": True, "gpu_gb": 0}), flush=True)
        for line in sys.stdin:
            req = json.loads(line)
            if req.get("task") == "shutdown":
                break
            print(json.dumps({"id": req["id"], "probs": {o: 1 / len(req["options"]) for o in req["options"]}}), flush=True)
    """)
    with llm.LLMClient("fake", cmd=[sys.executable, "-c", worker]) as client:
        assert client.choose("q", ["A", "B"]) == {"A": 0.5, "B": 0.5}
    broken = 'import json; print(json.dumps({"ready": False, "error": "no GPU"}), flush=True)'
    with pytest.raises(llm.LLMError, match="no GPU"):
        llm.LLMClient("fake", cmd=[sys.executable, "-c", broken])
