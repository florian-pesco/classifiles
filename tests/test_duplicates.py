"""Exact duplicate detection: within the inbox and against the sorted folders."""

from pathlib import Path

import pytest

import classifiles.duplicates as dup
from classifiles.duplicates import Catalog, copy_marked, find_duplicates, original_first


def write(p: Path, data: bytes, mtime: float | None = None) -> Path:
    import os

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_copy_markers():
    for name in ("report (1).pdf", "report - Kopie.pdf", "report - Copy (2).pdf", "Copy of report.pdf",
                 "Kopie von report.pdf", "report_copy.pdf"):
        assert copy_marked(name), name
    for name in ("report.pdf", "BWL 1 Folien.pdf", "copyright.pdf"):
        assert not copy_marked(name), name


def test_original_first_prefers_unmarked_then_oldest(tmp_path):
    a = write(tmp_path / "x (1).pdf", b"1", 100)
    b = write(tmp_path / "x.pdf", b"1", 300)
    c = write(tmp_path / "y.pdf", b"1", 200)
    assert original_first([a, b, c])[0] == c  # unmarked and older than x.pdf


def test_within_inbox(tmp_path):
    keep = write(tmp_path / "Rechnung.pdf", b"same content", 100)
    copy = write(tmp_path / "Rechnung (1).pdf", b"same content", 200)
    other = write(tmp_path / "other.pdf", b"same lengthX")  # same size, different content
    found, hashes = find_duplicates([keep, copy, other], None, {})
    assert list(found) == [copy]
    assert found[copy].kind == "inbox" and found[copy].of == "Rechnung.pdf" and found[copy].path == str(keep)
    assert keep in hashes and copy in hashes and other not in hashes


def test_unique_sizes_are_never_read(tmp_path, monkeypatch):
    files = [write(tmp_path / f"{i}.bin", b"x" * (i + 1)) for i in range(5)]
    monkeypatch.setattr(dup, "quick_hash", lambda *a: pytest.fail("read a file with a unique size"))
    assert find_duplicates(files, None, {}) == ({}, {})


def test_against_sorted_folders(service, tree):
    sorted_file = write(tree["root"] / "Finance" / "2025" / "statement.pdf", b"bank statement 2025")
    inbox_copy = write(tree["inbox"] / "statement (2).pdf", b"bank statement 2025")
    cat = Catalog(service.store)
    cat.build(service.categories(refresh=True))
    assert cat.count() >= 1
    labels = {str(c.path): c.name for c in service.categories() if c.path}
    found, _ = find_duplicates([inbox_copy], cat, labels)
    d = found[inbox_copy]
    assert (d.kind, d.of, d.path) == ("sorted", "Finance/2025/statement.pdf", str(sorted_file))
    row = service.store.one("SELECT quick_hash, full_hash FROM catalog WHERE relpath = ?", ("2025/statement.pdf",))
    assert row["quick_hash"] and row["full_hash"]  # cached for next time


def test_catalog_is_incremental_and_resets_hashes_on_change(service, tree):
    f = write(tree["root"] / "Work" / "a.txt", b"version 1")
    cat = Catalog(service.store)
    cat.build(service.categories(refresh=True))
    service.store.execute("UPDATE catalog SET full_hash = 'x' WHERE relpath = 'a.txt'")
    cat.build(service.categories(refresh=True))
    assert service.store.one("SELECT full_hash FROM catalog WHERE relpath = 'a.txt'")["full_hash"] == "x"
    write(f, b"version 2, longer", 2_000_000_000)
    cat.build(service.categories(refresh=True))
    assert service.store.one("SELECT full_hash FROM catalog WHERE relpath = 'a.txt'")["full_hash"] is None
    f.unlink()
    cat.build(service.categories(refresh=True))
    assert service.store.one("SELECT id FROM catalog WHERE relpath = 'a.txt'") is None


def test_scan_proposes_duplicates_for_delete(service, tree):
    write(tree["root"] / "Finance" / "statement.pdf", b"bank statement")
    write(tree["inbox"] / "statement.pdf", b"bank statement")              # already sorted
    write(tree["inbox"] / "notes.txt", b"my notes", 100)                    # kept
    write(tree["inbox"] / "notes (1).txt", b"my notes", 200)                # inbox copy
    service.catalog().build(service.categories(refresh=True))
    _, rows = service.scan(tree["inbox"])
    by = {r["name"]: r for r in rows}
    s, c, k = by["statement.pdf"], by["notes (1).txt"], by["notes.txt"]
    assert (s["target"], s["source"], s["status"], s["confidence"]) == ("Delete", "duplicate", "suggested", 1.0)
    assert s["duplicate_kind"] == "sorted" and s["duplicate_of"] == "Finance/statement.pdf"
    assert (c["target"], c["duplicate_kind"], c["duplicate_of"]) == ("Delete", "inbox", "notes.txt")
    assert k["duplicate_of"] is None and k["source"] != "duplicate" and k["content_hash"]


def test_duplicates_only_flagged_when_delete_is_disabled(service, tree):
    service.set_categories(service.settings.roots, service.settings.categories, {"enabled": False, "suggest": False})
    write(tree["inbox"] / "a.txt", b"same", 100)
    write(tree["inbox"] / "a (1).txt", b"same", 200)
    _, rows = service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "a (1).txt")
    assert r["duplicate_of"] == "a.txt" and r["target"] != "Delete"


def test_switches(service, tree):
    write(tree["root"] / "Finance" / "s.pdf", b"content")
    write(tree["inbox"] / "s.pdf", b"content")
    service.catalog().build(service.categories(refresh=True))
    service.settings.duplicates_in_sorted = False
    _, rows = service.scan(tree["inbox"])
    assert next(r for r in rows if r["name"] == "s.pdf")["duplicate_of"] is None
    service.settings.detect_duplicates = False
    write(tree["inbox"] / "s (1).pdf", b"content")
    _, rows = service.scan(tree["inbox"])
    assert not any(r["duplicate_of"] for r in rows)


def test_apply_trashes_duplicate_without_learning_and_keeps_catalog_current(service, tree, monkeypatch):
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    learned = []
    monkeypatch.setattr(service, "_learn", lambda items: learned.extend(items))
    write(tree["inbox"] / "a.txt", b"same", 100)
    write(tree["inbox"] / "a (1).txt", b"same", 200)
    scan_id, rows = service.scan(tree["inbox"])
    by = {r["name"]: r for r in rows}
    service.review(by["a (1).txt"]["id"], "accept")
    service.review(by["a.txt"]["id"], "accept", "Work", subpath="")
    service.apply(scan_id)
    assert (tree["inbox"] / ".classifiles-trash" / "a (1).txt").exists()
    assert [rel for _, rel, _ in learned] == ["a.txt"]  # the duplicate was not learned
    assert service.store.one("SELECT full_hash FROM catalog WHERE relpath = 'a.txt'")["full_hash"]
    service.undo()
    assert service.store.one("SELECT id FROM catalog WHERE relpath = 'a.txt'") is None


def test_index_build_also_builds_the_catalog(service, tree, monkeypatch):
    from test_examples import FakeEmbedder

    from classifiles.examples import ExampleIndex

    service.settings.embedding_model = "fake"
    service._index = ExampleIndex(service.store, FakeEmbedder(), per_category=50, content_per_category=5)
    service.start_index_build()
    service._index._thread.join(30)
    st = service.index_status()["catalog"]
    assert st["state"] == "done" and st["files"] >= 4 and st["enabled"]


def _dup_scan(service, tree):
    write(tree["root"] / "Finance" / "statement.pdf", b"bank statement")
    write(tree["inbox"] / "statement.pdf", b"bank statement")
    write(tree["inbox"] / "notes.txt", b"my notes", 100)
    write(tree["inbox"] / "notes (1).txt", b"my notes", 200)
    service.catalog().build(service.categories(refresh=True))
    scan_id, rows = service.scan(tree["inbox"])
    return scan_id, {r["name"]: r for r in rows}


def test_duplicate_details_recheck_and_explain(service, tree):
    _, by = _dup_scan(service, tree)
    d = service.duplicate_details(by["notes (1).txt"]["id"])
    assert d["identical_now"] and d["this"]["fingerprint"] == d["other"]["fingerprint"]
    assert "looks like a copy" in d["keep_reason"] and "BLAKE2b" in d["method"]
    s = service.duplicate_details(by["statement.pdf"]["id"])
    assert s["kind"] == "sorted" and "already sorted" in s["keep_reason"]
    write(tree["root"] / "Finance" / "statement.pdf", b"bank statemenX")  # same size, changed
    assert not service.duplicate_details(by["statement.pdf"]["id"])["identical_now"]
    with pytest.raises(ValueError):
        service.duplicate_details(by["notes.txt"]["id"])


def test_apply_refuses_when_the_kept_copy_changed_or_is_gone(service, tree):
    scan_id, by = _dup_scan(service, tree)
    service.review(by["statement.pdf"]["id"], "accept")
    service.review(by["notes (1).txt"]["id"], "accept")
    write(tree["root"] / "Finance" / "statement.pdf", b"changed since the scan")
    (tree["inbox"] / "notes.txt").unlink()
    res = service.apply(scan_id)
    assert not res["moved"] and len(res["errors"]) == 2
    assert all(e["error"].startswith("Not deleted:") for e in res["errors"])
    assert (tree["inbox"] / "statement.pdf").exists() and (tree["inbox"] / "notes (1).txt").exists()


def test_api_serves_the_identical_file_and_details(service, tree, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    _, by = _dup_scan(service, tree)
    client = TestClient(create_app(service, config_path=str(tmp_path / "cfg.yaml")))
    pid = by["statement.pdf"]["id"]
    r = client.get(f"/api/proposals/{pid}/file", params={"duplicate": "true"})
    assert r.status_code == 200 and r.content == b"bank statement"
    assert client.get(f"/api/proposals/{pid}/duplicate").json()["identical_now"] is True
    assert client.get(f"/api/proposals/{by['notes.txt']['id']}/duplicate").status_code == 400
    assert client.get(f"/api/proposals/{by['notes.txt']['id']}/file", params={"duplicate": "true"}).status_code == 404
