"""Trained classifier: inbox-style example vectors, training, and the honest comparison (fake embedder)."""

import numpy as np
import pytest

from classifiles import trained as tr
from classifiles.examples import inbox_text
from test_examples import FakeEmbedder, ex_service, sorted_tree  # noqa: F401 - fixtures


class SpyEmbedder(FakeEmbedder):
    def __init__(self):
        self.query_texts = []

    def encode(self, texts, query=False):
        if query:
            self.query_texts += texts
        return super().encode(texts, query)


@pytest.fixture
def spy_service(ex_service):
    from classifiles.examples import ExampleIndex

    ex_service._index = ExampleIndex(ex_service.store, SpyEmbedder(), per_category=100, content_per_category=10)
    ex_service.reset_decider()
    return ex_service


def test_inbox_text_has_no_folder():
    t = inbox_text("invoice_jan.txt", "text", "total due")
    assert t.startswith("file: invoice_jan.txt") and "folder" not in t and "content: total due" in t


def test_examples_get_inbox_style_vectors_without_folder(spy_service, sorted_tree):
    idx = spy_service.example_index()
    idx.build(spy_service.categories(refresh=True))
    rows = spy_service.store.query("SELECT origin, qvector FROM examples")
    assert all(r["qvector"] is not None for r in rows if r["origin"] == "scan")
    assert all(r["qvector"] is None for r in rows if r["origin"] == "description")
    assert idx.embedder.query_texts and not any("folder" in t for t in idx.embedder.query_texts)


def test_missing_inbox_style_vectors_are_filled_on_update(spy_service, sorted_tree):
    idx = spy_service.example_index()
    cats = spy_service.categories(refresh=True)
    idx.build(cats)
    before = {r["id"]: r["qvector"] for r in spy_service.store.query("SELECT id, qvector FROM examples "
                                                                     "WHERE origin = 'scan'")}
    spy_service.store.execute("UPDATE examples SET qvector = NULL")  # an index from before this version
    idx.build(cats)  # nothing changed on disk: only the inbox-style vectors are made
    after = {r["id"]: r["qvector"] for r in spy_service.store.query("SELECT id, qvector FROM examples "
                                                                    "WHERE origin = 'scan'")}
    assert after == before


def test_applied_files_get_inbox_style_vectors(ex_service, sorted_tree, monkeypatch):
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_march.txt")
    ex_service.review(r["id"], "accept", "Finance", subpath="")
    ex_service.apply(scan_id)
    q = "SELECT qvector FROM examples WHERE origin = 'applied'"
    assert ex_service.store.one(q)["qvector"] is not None
    ex_service.store.execute("UPDATE examples SET qvector = NULL WHERE origin = 'applied'")
    assert ex_service.fill_applied_query_vectors() == 1 and ex_service.store.one(q)["qvector"] is not None


def _blobs(n, centre, rng, noise=0.3):
    x = centre + rng.normal(size=(n, len(centre))) * noise
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float32)


def test_fit_learns_separable_categories():
    rng = np.random.default_rng(0)
    centres = rng.normal(size=(3, 16))
    X = np.vstack([_blobs(60, c, rng) for c in centres])
    y = ["a"] * 60 + ["b"] * 60 + ["c"] * 60
    m = tr.fit(X, y, C=1.0)
    pred = np.array(m.keys)[m.predict_proba(X).argmax(axis=1)]
    assert (pred == np.array(y)).mean() > 0.95
    p = m.probs(X[0])
    assert abs(sum(p.values()) - 1) < 1e-6 and max(p, key=p.get) == "a"
    assert m.n_train == {"a": 60, "b": 60, "c": 60}


def test_balanced_weights_help_the_small_category():
    rng = np.random.default_rng(1)
    big, small = rng.normal(size=16), rng.normal(size=16)
    small = big + (small - big) * 0.35  # close to the big one: easy to drown
    X = np.vstack([_blobs(600, big, rng, 0.5), _blobs(15, small, rng, 0.5)])
    y = ["big"] * 600 + ["small"] * 15
    test = _blobs(200, small, rng, 0.5)
    m = tr.fit(X, y, C=1.0)
    recall = (np.array(m.keys)[m.predict_proba(test).argmax(axis=1)] == "small").mean()
    from sklearn.linear_model import LogisticRegression

    plain = LogisticRegression(C=1.0, max_iter=1000).fit(X, y)
    assert recall > (plain.predict(test) == "small").mean()
    assert tr.sample_weights(y)[0] * 600 == pytest.approx(tr.sample_weights(y)[-1] * 15)


def test_two_categories_softmax_matches_sklearn():
    rng = np.random.default_rng(2)
    X = np.vstack([_blobs(40, rng.normal(size=8), rng), _blobs(40, rng.normal(size=8), rng)])
    y = ["x"] * 40 + ["y"] * 40
    m = tr.fit(X, y)
    from sklearn.linear_model import LogisticRegression

    ref = LogisticRegression(C=1.0, max_iter=1000, tol=1e-4).fit(X, y, sample_weight=tr.sample_weights(y))
    assert np.allclose(m.predict_proba(X), ref.predict_proba(X), atol=1e-4)


def test_choose_c_and_training_set():
    rng = np.random.default_rng(3)
    X = np.vstack([_blobs(30, rng.normal(size=8), rng) for _ in range(2)])
    C, scores = tr.choose_C(X, ["a"] * 30 + ["b"] * 30, grid=(0.1, 1.0))
    assert C in (0.1, 1.0) and set(scores) == {0.1, 1.0}
    rows = [{"id": i, "category_key": "k", "origin": "applied" if i == 2 else "scan",
             "qvector": np.ones(4, np.float16).tobytes()} for i in range(3)]
    X, y, w = tr.training_set(rows, exclude_ids={0}, applied_weight=3)
    assert X.shape == (2, 4) and list(w) == [1, 3]


def test_blend():
    p = tr.blend({"a": 0.9, "b": 0.1}, {"a": 0.2, "b": 0.8}, 0.5)
    assert abs(sum(p.values()) - 1) < 1e-9 and p["a"] == pytest.approx(0.6)  # sqrt(.9·.2) vs sqrt(.1·.8)
    # a category the trained model doesn't know (Delete) keeps the neighbour probability
    p = tr.blend({"a": 0.5, "b": 0.2, "Delete": 0.3}, {"a": 0.1, "b": 0.9}, 0.5)
    assert p["Delete"] == pytest.approx(0.3) and sum(p.values()) == pytest.approx(1) and p["b"] > p["a"]


def test_evaluation_never_trains_on_test_files(ex_service, sorted_tree, monkeypatch):
    from classifiles.evaluate import evaluate_trained

    ex_service.example_index().build(ex_service.categories(refresh=True))
    excluded = []
    original = tr.training_set
    monkeypatch.setattr(tr, "training_set", lambda rows, exclude_ids=None, applied_weight=3.0:
                        (excluded.append(set(exclude_ids)), original(rows, exclude_ids, applied_weight))[1])
    monkeypatch.setattr(tr, "choose_C", lambda *a, **k: (1.0, {}))
    report = evaluate_trained(ex_service, per_category=2, splits=("file", "folder"), decisions=False, folds=2)
    known = {r["id"] for r in ex_service.store.query("SELECT id FROM examples")}
    assert report["files"] > 0 and excluded
    for name in ("file split", "folder split"):
        res = report[name]["results"]
        assert set(res) == {"neighbours", "trained", "blend 0.25", "blend 0.5", "blend 0.75"}
        assert all(r["files"] == report["files"] for r in res.values())
    # every training run left out at least all held-out files; folder runs also their folders
    file_runs, folder_runs = excluded[:2], excluded[2:]
    assert all(len(e) >= report["files"] for e in file_runs)
    assert all(len(f) > len(e) for e, f in zip(file_runs, folder_runs))
    assert all(e <= known for e in excluded) and file_runs[0] == file_runs[1]


def test_temperature_scaling_divides_the_scores():
    rng = np.random.default_rng(4)
    X = np.vstack([_blobs(40, rng.normal(size=8), rng, 0.8) for _ in range(3)])
    y = ["a"] * 40 + ["b"] * 40 + ["c"] * 40
    T = tr.fit_temperature(X, y, None, C=1.0)
    assert T in tr.T_GRID
    m = tr.fit(X, y)
    sharp = m.predict_proba(X).max(axis=1).mean()
    m.temperature = 0.5
    assert m.predict_proba(X).max(axis=1).mean() > sharp  # lower temperature: more confident


# --- phase 2: the trained model in use ---------------------------------------------------

@pytest.fixture
def trained_service(ex_service, sorted_tree):
    """Enough sorted files per category (≥ 6) to train, and the trained model switched on."""
    more = {"Finance": ["invoice_mar", "invoice_apr", "bank_fees", "tax_notice"],
            "Work": ["meeting_minutes", "project_plan", "weekly_report", "meeting_notes_friday"],
            "Photos": ["holiday_lake", "camera_portrait", "holiday_city", "photo_family"],
            "Software": ["setup_editor", "installer_driver", "setup_player", "driver_update"]}
    for cat, names in more.items():
        for n in names:
            (sorted_tree["root"] / cat / "2025" / f"{n}.txt").write_text(n.replace("_", " "))
    ex_service.settings.trained_weight = 0.5
    ex_service.retrain_delay = 0
    ex_service.example_index().build(ex_service.categories(refresh=True))
    return ex_service


def test_retrain_stores_and_uses_the_model(trained_service):
    assert trained_service.trained_model() is None
    assert trained_service.retrain()["state"] == "done"
    m = trained_service.trained_model()
    assert m is not None and "__delete__" not in m.keys and len(m.keys) == 4
    trained_service._trained = None  # reload from the database
    again = trained_service.trained_model()
    assert np.allclose(again.coef, m.coef) and again.temperature == m.temperature and again.n_train == m.n_train
    st = trained_service.trained_status()
    assert st["trained"] and st["examples"] == sum(m.n_train.values()) and st["weight"] == 0.5

    _, rows = trained_service.scan(trained_service.settings.inboxes[0])
    r = next(r for r in rows if r["name"] == "invoice_march.txt")
    assert r["target"] == "Finance" and r["reason"].startswith("trained model: Finance")
    assert "similar to:" in r["reason"] and r["subpath"] == "2025"  # folders still come from similar files


def test_weight_zero_means_similar_files_only(trained_service):
    trained_service.retrain()
    trained_service.settings.trained_weight = 0.0
    trained_service.reset_decider()
    assert trained_service.decider().trained is None
    _, rows = trained_service.scan(trained_service.settings.inboxes[0])
    assert not any(r["reason"].startswith("trained model") for r in rows)


def test_first_use_trains_once_in_the_background(trained_service):
    trained_service.reset_decider()
    trained_service.decider()
    trained_service._train_timer.join(30)
    assert trained_service.trained_model() is not None
    assert trained_service.decider().trained is not None  # the decider was rebuilt with the model


def test_apply_and_undo_retrain(trained_service, monkeypatch):
    monkeypatch.setattr(trained_service, "_learn", lambda items: trained_service.example_index().add(items))
    trained_service.retrain()
    before = trained_service.trained_model().n_train
    scan_id, rows = trained_service.scan(trained_service.settings.inboxes[0])
    r = next(r for r in rows if r["name"] == "invoice_march.txt")
    trained_service.review(r["id"], "accept", "Finance", subpath="")
    trained_service.apply(scan_id)
    trained_service._train_timer.join(30)
    key = next(k for k in before if k.endswith("Finance"))
    assert trained_service.trained_model().n_train[key] == before[key] + 1  # the applied file was learned
    trained_service.undo()
    trained_service._train_timer.join(30)
    assert trained_service.trained_model().n_train[key] == before[key]


def test_decider_blend_fills_categories_without_neighbours():
    from classifiles.deciders.examples import ExampleDecider

    class Index:
        size = 1

        def query(self, vec, allowed, exclude_ids=None, extra=None, base=None):
            return {"a": 1.0}, []  # only "a" got neighbour votes

    class Cat:
        def __init__(self, name):
            self.name, self.path = name, f"/x/{name}"

    class Model:
        def probs(self, vec):
            return {"/x/a": 0.1, "/x/b": 0.9}

    d = ExampleDecider(Index(), trained=Model(), trained_weight=0.5)
    probs, reason, _ = d.score(np.ones(2), None, [Cat("a"), Cat("b")])
    assert set(probs) == {"a", "b"} and probs["b"] > 0 and reason.startswith("trained model: b 90%")


def test_api_weight_retrain_and_quality(trained_service, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    client = TestClient(create_app(trained_service, config_path=str(tmp_path / "cfg.yaml")))
    assert client.patch("/api/settings", json={"trained_weight": 1.5}).status_code == 400
    assert client.patch("/api/settings", json={"trained_weight": 0.25}).json()["trained_weight"] == 0.25
    client.post("/api/index/retrain")
    trained_service._train_timer.join(30)
    assert client.get("/api/index").json()["trained"]["trained"]
    q = client.get("/api/quality?rerun=true").json()
    assert q["decisions"] == 0 and "mix" not in q  # no decisions yet: nothing to compare
