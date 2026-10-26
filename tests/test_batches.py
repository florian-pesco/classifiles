"""Batch suggestions and learning from decisions within a scan (fake embedder, no model)."""

import numpy as np
import pytest

from classifiles.batches import find_batches, name_pattern
from test_examples import FakeEmbedder, ex_service, sorted_tree  # noqa: F401 - fixtures


def test_name_pattern():
    assert name_pattern("Scan_2025-03-01_0042.pdf") == "scan_<date>_<n>.pdf"
    assert name_pattern("IMG_20240512_103344.JPG") == "img_<date>_<n>.jpg"
    assert name_pattern("invoice-3fa85f64-5717-4562-b3fc-2c963f66afa6.pdf") == "invoice-<id>.pdf"
    assert name_pattern("lecture_03_slides.pdf") == "lecture_<n>_slides.pdf"
    assert name_pattern("report.docx") == "report.docx"


def row(i, name, target="A", conf=0.9, status="suggested", alts=None, source="examples", subpath=""):
    return {"id": i, "name": name, "target": target, "final_target": target, "confidence": conf,
            "status": status, "source": source, "subpath": subpath,
            "alternatives": alts or [{"folder": target, "probability": conf}]}


def test_find_batches_signals():
    doubt = [{"folder": "A", "probability": 0.45}, {"folder": "B", "probability": 0.4}]
    rows = ([row(i, f"Scan_2025-0{i}-01.pdf", target="A" if i < 3 else "B") for i in range(1, 5)]
            + [row(10 + i, f"notes {w}.txt", conf=0.4, status="needs_review", alts=doubt)
               for i, w in enumerate(["alpha", "beta", "gamma"])]
            + [row(20 + i, f"other{w}.txt", target="C", subpath="x") for i, w in enumerate("pqr")]
            + [row(30 + i, f"Copy_{i}.txt", source="duplicate") for i in range(3)])
    rng = np.random.default_rng(0)
    base = rng.normal(size=16)
    vectors = {r["id"]: rng.normal(size=16) for r in rows}
    for i in (10, 11, 12):  # the three notes have nearly identical content
        vectors[i] = base + rng.normal(size=16) * 0.01
    out = {b["kind"]: b for b in find_batches(rows, vectors, threshold=0.6)}

    assert set(out["name"]["ids"]) == {1, 2, 3, 4}
    assert out["name"]["distribution"] == [{"where": "A", "count": 2}, {"where": "B", "count": 2}]
    assert not out["name"]["uniform"]
    assert set(out["content"]["ids"]) == {10, 11, 12}
    assert set(out["doubt"]["ids"]) == {10, 11, 12} and out["doubt"]["title"] == "A or B?"
    assert any(b["id"] == "proposal:C/x" for b in find_batches(rows, vectors, 0.6))
    assert all(not {30, 31, 32} & set(b["ids"]) for b in out.values())  # duplicates left out


def test_fully_decided_batches_are_dropped():
    rows = [row(i, f"Scan_{i}.pdf", status="accepted") for i in range(4)]
    assert find_batches(rows, {}, 0.6) == []


@pytest.fixture
def unsure_scan(ex_service, sorted_tree):
    """Four files about something the sorted folders don't know yet."""
    inbox = sorted_tree["inbox"]
    for p in list(inbox.iterdir()):
        p.unlink()
    for i in range(4):
        (inbox / f"zebra_quarterly_{i}.txt").write_text(f"zebra quarterly part {i}")
    (inbox / "invoice_april.txt").write_text("invoice bank")
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(inbox)
    return ex_service, scan_id, {r["name"]: r for r in rows}


def test_relearn_uses_decisions_right_away(unsure_scan):
    svc, scan_id, by = unsure_scan
    first = by["zebra_quarterly_0.txt"]
    svc.review(first["id"], "accept", "Work", subpath="meetings")
    changed = svc.relearn(scan_id)

    names = {c["name"] for c in changed}
    assert names == {"zebra_quarterly_1.txt", "zebra_quarterly_2.txt", "zebra_quarterly_3.txt"}
    for c in changed:
        assert c["target"] == "Work" and c["subpath"] == "meetings" and c["relearned"] == 1
        assert c["status"] == "suggested" and "your decision: zebra_quarterly_0.txt → Work" in c["reason"]
        assert "qvec" not in c
    assert svc.get_proposal(by["invoice_april.txt"]["id"])["relearned"] == 0  # unrelated file
    assert svc.get_proposal(first["id"])["status"] == "accepted"  # decided file untouched

    # taking the decision back takes its effect back
    svc.review(first["id"], "reset")
    svc.relearn(scan_id)
    again = svc.get_proposal(by["zebra_quarterly_1.txt"]["id"])
    assert again["relearned"] == 0 and again["target"] == by["zebra_quarterly_1.txt"]["target"]


def test_relearn_ignores_rejected_and_can_be_switched_off(unsure_scan):
    svc, scan_id, by = unsure_scan
    svc.review(by["zebra_quarterly_0.txt"]["id"], "reject")
    assert svc.relearn(scan_id) == []  # "keep in inbox" teaches nothing
    svc.settings.relearn_in_scan = False
    svc.review(by["zebra_quarterly_0.txt"]["id"], "accept", "Work")
    assert svc.relearn(scan_id) == []


def test_relearn_keeps_user_decisions(unsure_scan):
    svc, scan_id, by = unsure_scan
    svc.review(by["zebra_quarterly_1.txt"]["id"], "accept", "Finance")  # decided differently
    svc.review(by["zebra_quarterly_0.txt"]["id"], "accept", "Work")
    svc.relearn(scan_id)
    assert svc.get_proposal(by["zebra_quarterly_1.txt"]["id"])["final_target"] == "Finance"


def test_batches_and_bulk_api(unsure_scan, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    svc, scan_id, by = unsure_scan
    client = TestClient(create_app(svc, config_path=str(tmp_path / "cfg.yaml")))
    batches = client.get(f"/api/scans/{scan_id}/batches").json()
    name = next(b for b in batches if b["kind"] == "name")
    assert name["title"] == "zebra_quarterly_‹n›.txt" and name["undecided"] == 4

    ids = name["ids"][:2]
    r = client.post("/api/proposals/bulk", json={"ids": ids + [999999], "action": "accept",
                                                  "folder": "Work", "subpath": "meetings"}).json()
    assert {p["id"] for p in r["updated"]} == set(ids) and r["errors"][0]["proposal_id"] == 999999
    assert {p["id"] for p in r["relearned"]} == set(name["ids"][2:])
    assert all("path" not in p["evidence"] for p in r["relearned"])

    left = client.get(f"/api/scans/{scan_id}/batches").json()
    assert next(b for b in left if b["kind"] == "name")["undecided"] == 2

    one = client.patch(f"/api/proposals/{by['invoice_april.txt']['id']}", json={"action": "accept"}).json()
    assert one["status"] == "accepted" and "relearned_others" in one
    assert client.post("/api/proposals/bulk", json={"ids": ids, "action": "move"}).status_code == 400
    assert client.get("/api/scans/999/batches").status_code == 404
