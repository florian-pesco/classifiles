"""Example index and example-based classification, with a deterministic fake embedder (no model)."""

import socket
import zlib

import numpy as np
import pytest

from classifiles.examples import DELETE_KEY, ExampleIndex, sample_folder
from classifiles.evaluate import evaluate, evaluate_decisions
from classifiles.store import Store

DIM = 64


class FakeEmbedder:
    """Bag-of-words hashing: texts sharing words get similar vectors. No model, no network."""

    model_name = "fake"
    loaded = True

    def encode(self, texts, query=False):
        out = np.zeros((len(texts), DIM), np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace("/", " ").replace("_", " ").replace(".", " ").split():
                out[i, zlib.crc32(w.encode()) % DIM] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)

    def unload(self):
        pass


@pytest.fixture
def sorted_tree(tree):
    """Give each category several already-sorted files with telling names."""
    names = {
        "Finance": ["invoice_jan.txt", "invoice_feb.txt", "bank_statement_march.txt", "tax_return.txt"],
        "Work": ["meeting_notes_monday.txt", "project_report.txt", "meeting_agenda.txt"],
        "Photos": ["holiday_beach.jpg", "holiday_mountain.jpg", "camera_photo.jpg"],
        "Software": ["setup_tool.exe", "driver_installer.msi"],
    }
    for cat, files in names.items():
        sub = tree["root"] / cat / "2025"
        sub.mkdir(exist_ok=True)
        for f in files:
            (sub / f).write_text(f.replace("_", " "))
    return tree


@pytest.fixture
def ex_service(service):
    service.settings.backend = "examples"
    service._index = ExampleIndex(service.store, FakeEmbedder(), per_category=100, content_per_category=10)
    service.settings.embedding_model = "fake"
    service.reset_decider()  # the base fixture pre-sets the keyword decider
    return service


def test_sample_folder_spreads_over_subfolders(tmp_path):
    for d in ("big", "small"):
        (tmp_path / d).mkdir()
    for i in range(50):
        (tmp_path / "big" / f"b{i}.jpg").touch()
    (tmp_path / "small" / "s1.pdf").touch()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").touch()
    picked = sample_folder(tmp_path, cap=4)
    assert len(picked) == 4 and "small/s1.pdf" in picked
    assert not any(p.startswith("node_modules") for p in sample_folder(tmp_path, cap=100))


def test_build_is_incremental(ex_service, sorted_tree):
    idx = ex_service.example_index()
    st = idx.build(ex_service.categories(refresh=True))
    assert st["state"] == "done" and st["added"] > 0
    first = idx.size
    st = idx.build(ex_service.categories(refresh=True))
    assert st["added"] == 0 and st["reused"] > 0 and idx.size == first
    (sorted_tree["root"] / "Finance" / "2025" / "invoice_jan.txt").unlink()
    st = idx.build(ex_service.categories(refresh=True))
    assert st["removed"] == 1 and idx.size == first - 1


def test_classifies_by_similar_files(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    inbox = sorted_tree["inbox"]
    (inbox / "invoice_april.txt").write_text("invoice for april")
    (inbox / "meeting_notes_friday.txt").write_text("meeting notes")
    _, rows = ex_service.scan(inbox)
    by = {r["name"]: r for r in rows}
    assert by["invoice_april.txt"]["target"] == "Finance"
    assert "similar to: Finance/2025/invoice_" in by["invoice_april.txt"]["reason"]
    assert by["meeting_notes_friday.txt"]["target"] == "Work"
    assert by["invoice_april.txt"]["source"] == "examples"


def test_empty_index_gives_clear_error(ex_service, sorted_tree):
    with pytest.raises(ValueError, match="example index is empty"):
        ex_service.scan(sorted_tree["inbox"])


def test_disabled_category_never_proposed(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    entries = [*ex_service.settings.categories, {"path": str(sorted_tree["root"] / "Finance"), "enabled": False}]
    ex_service.set_categories(ex_service.settings.roots, entries)
    (sorted_tree["inbox"] / "invoice_may.txt").write_text("invoice")
    _, rows = ex_service.scan(sorted_tree["inbox"])
    assert all(r["target"] != "Finance" for r in rows)


def test_learns_from_apply_and_forgets_on_undo(ex_service, sorted_tree, monkeypatch):
    idx = ex_service.example_index()
    idx.build(ex_service.categories(refresh=True))
    # run the background learning synchronously
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    pid = next(r["id"] for r in rows if r["name"] == "bundle.zip")
    ex_service.review(pid, "accept", "Delete")
    ex_service.apply(scan_id)
    learned = ex_service.store.query("SELECT category_key, relpath FROM examples WHERE origin = 'applied'")
    assert learned == [{"category_key": DELETE_KEY, "relpath": "bundle.zip"}]
    ex_service.undo()
    assert ex_service.store.query("SELECT * FROM examples WHERE origin = 'applied'") == []


def test_evaluation_holds_files_out(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    report = evaluate(ex_service, ["examples"], per_category=2)
    r = report["results"]["examples (file split)"]
    assert report["files"] == 8 and 0 <= r["accuracy"] <= 1
    assert {"range", "files", "accuracy"} <= set(r["calibration"][0])


def test_classification_makes_no_network_calls(ex_service, sorted_tree, monkeypatch):
    ex_service.example_index().build(ex_service.categories(refresh=True))

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    _, rows = ex_service.scan(sorted_tree["inbox"])
    assert rows


def test_store_is_migrated_with_examples_table(tmp_path):
    s = Store(tmp_path / "x.db")
    assert s.query("SELECT COUNT(*) AS n FROM examples")[0]["n"] == 0


def test_index_api(ex_service, sorted_tree, tmp_path):
    import time

    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    client = TestClient(create_app(ex_service, config_path=str(tmp_path / "cfg.yaml")))
    assert client.get("/api/index").json()["total"] == 0
    client.post("/api/index/build")
    for _ in range(100):
        st = client.get("/api/index").json()
        if st["status"]["state"] != "running":
            break
        time.sleep(0.05)
    assert st["status"]["state"] == "done"
    finance = next(c for c in st["categories"] if c["name"] == "Finance")
    assert finance["examples"] >= 4
    assert client.patch("/api/settings", json={"gpu_memory_fraction": 2}).status_code == 400


def test_decided_via_is_recorded(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    a, b, c = rows[0], rows[1], rows[2]
    ex_service.review(a["id"], "accept")
    other = next(n for n in ("Finance", "Work") if n != b["target"])
    ex_service.review(b["id"], "accept", other)
    ex_service.review(c["id"], "reject")
    got = {r["id"]: r["decided_via"] for r in ex_service.proposals(scan_id)}
    assert (got[a["id"]], got[b["id"]], got[c["id"]]) == ("confirm", "override", "reject")
    ex_service.review(c["id"], "reset")
    assert ex_service.get_proposal(c["id"])["decided_via"] is None


def test_folder_split_hides_siblings(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    report = evaluate(ex_service, ["examples"], per_category=2, splits=("file", "folder"))
    assert set(report["results"]) == {"examples (file split)", "examples (folder split)"}
    # every category's files share one folder: with siblings hidden only the descriptions remain,
    # so the folder split must be clearly worse than the file split
    assert report["results"]["examples (folder split)"]["accuracy"] < report["results"]["examples (file split)"]["accuracy"]


def test_evaluate_decisions_and_no_file_names(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    (sorted_tree["inbox"] / "invoice_april.txt").write_text("invoice april")
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    inv = next(r for r in rows if r["name"] == "invoice_april.txt")
    ex_service.review(inv["id"], "accept", "Finance")
    ex_service.apply(scan_id)
    report = evaluate_decisions(ex_service)
    assert report["decisions"] == 1 and report["decided_via"] == {"confirm": 1}
    assert report["recorded"]["examples"]["accuracy"] == 1.0
    assert report["current"]["files"] == 1
    assert "invoice_april" not in repr(report)  # aggregates only


def test_quality_api(ex_service, sorted_tree, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    client = TestClient(create_app(ex_service, config_path=str(tmp_path / "cfg.yaml")))
    q = client.get("/api/quality").json()
    assert q["decisions"] == 0 and "current" not in q
    assert client.get("/api/categories").json()["unreachable_roots"] == []


def test_backfill_past_decisions_and_always_learn(ex_service, sorted_tree, monkeypatch):
    # decisions made with another model (index empty): nothing is learned at apply time
    ex_service.settings.backend = "keyword"
    ex_service.reset_decider()
    from classifiles.deciders import KeywordDecider
    ex_service._decider = KeywordDecider()
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    zip_ = next(r for r in rows if r["name"] == "bundle.zip")
    txt = next(r for r in rows if r["name"] == "invoice_march.txt")
    ex_service.review(zip_["id"], "accept", "Delete")
    ex_service.review(txt["id"], "accept", "Finance")
    ex_service.apply(scan_id)
    assert len(ex_service.unlearned_decisions()) == 2

    res = ex_service.learn_past_decisions()
    assert res == {"learned": 2, "by_action": {"delete": 1, "move/copy": 1}}
    assert ex_service.unlearned_decisions() == []
    assert ex_service.index_status()["unlearned"] == 0
    assert ex_service.learn_past_decisions()["learned"] == 0  # idempotent

    # once an index exists, applies are learned even with another model selected
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    notes = next(r for r in rows if r["name"] == "meeting_notes.docx")
    ex_service.review(notes["id"], "accept", "Work")
    ex_service.apply(scan_id)
    assert ex_service.unlearned_decisions() == []


def test_decision_eval_hides_learned_deletions(ex_service, sorted_tree, monkeypatch):
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    ex_service.example_index().build(ex_service.categories(refresh=True))
    ex_service.set_categories(ex_service.settings.roots, ex_service.settings.categories,
                              {"enabled": True, "suggest": True})
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    zip_ = next(r for r in rows if r["name"] == "bundle.zip")
    ex_service.review(zip_["id"], "accept", "Delete")
    ex_service.apply(scan_id)  # learned as a Delete example
    seen = {}
    original = ex_service.example_index().query

    def spy(vec, allowed, exclude_ids=None, k=None):
        seen["excluded"] = set(exclude_ids or ())
        return original(vec, allowed, exclude_ids, k)

    monkeypatch.setattr(ex_service.example_index(), "query", spy)
    evaluate_decisions(ex_service)
    learned_id = ex_service.store.one("SELECT id FROM examples WHERE origin = 'applied'")["id"]
    assert learned_id in seen["excluded"]
