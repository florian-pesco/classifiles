"""Duplicates page: searching a root for identical files, suggestions, trashing, undo."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from classifiles import ClassifilesService, Settings
from classifiles import cleanup as cleanup_mod
from classifiles.cleanup import Copy, Group, suggest
from classifiles.deciders import KeywordDecider
from classifiles.mover import TRASH_DIR
from classifiles.store import Store


@pytest.fixture
def svc(tmp_path: Path) -> ClassifilesService:
    root = tmp_path / "Archive"
    files = {
        "Finance/2024/invoice.pdf": b"INVOICE-1" * 100,
        "Finance/2024/invoice (1).pdf": b"INVOICE-1" * 100,     # copy marker, same folder
        "Work/report.docx": b"REPORT" * 300,
        "Backup/old/report.docx": b"REPORT" * 300,              # same file in another category
        "Backup/old/notes.txt": b"NOTES" * 50,
        "Work/notes.txt": b"NOTES" * 50,
        "Work/unique.txt": b"only once" * 7,                    # unique size: never read
        "Work/same_size_a.txt": b"a" * 64,                      # same size, different content
        "Work/same_size_b.txt": b"b" * 64,
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    old = 1_600_000_000
    os.utime(root / "Backup/old/report.docx", (old, old))      # the older copy
    settings = Settings(roots=[{"path": str(root)}], inboxes=[str(tmp_path / "Inbox")], backend="keyword",
                        db_path=":memory:", image_similarity=False, trained_weight=0.0)
    (tmp_path / "Inbox").mkdir()
    s = ClassifilesService(settings, decider=KeywordDecider(), store=Store(":memory:"))
    s.root = root  # type: ignore[attr-defined]
    return s


def groups_by_name(svc, **kw):
    return {Path(g.copies[0].relpath).name: g for g in svc.duplicate_groups(**kw)}


def test_search_finds_identical_files_and_reads_only_candidates(svc, monkeypatch):
    read = []
    real = cleanup_mod.quick_hash
    monkeypatch.setattr(cleanup_mod, "quick_hash", lambda p, size: read.append(p.name) or real(p, size))
    status = svc.search_duplicates(str(svc.root))
    assert status["state"] == "done"
    assert status["groups"] == 3 and status["copies"] == 3
    assert "unique.txt" not in read  # its size occurs once: certainly no duplicate
    assert set(groups_by_name(svc)) == {"invoice (1).pdf", "notes.txt", "report.docx"}
    spreads = {Path(g.copies[0].relpath).name: g.spread for g in svc.duplicate_groups()}
    assert spreads["invoice (1).pdf"] == "folder" and spreads["report.docx"] == "categories"


def test_second_search_reads_nothing_unchanged(svc, monkeypatch):
    svc.search_duplicates(str(svc.root))
    read = []
    monkeypatch.setattr(cleanup_mod, "full_hash", lambda p: read.append(p) or "x")
    monkeypatch.setattr(cleanup_mod, "quick_hash", lambda p, s: read.append(p) or "x")
    svc.search_duplicates(str(svc.root))
    assert read == []


def test_suggestion_prefers_original_name_and_resolve_trashes_undoably(svc):
    svc.search_duplicates()
    g = groups_by_name(svc)["invoice (1).pdf"]
    keep = next(c for c in g.copies if c.id == g.keep)
    assert keep.relpath == "2024/invoice.pdf" and g.clear

    r = svc.resolve_suggested_duplicates()
    assert r["trashed"] == 1 and not r["errors"]  # only the clear one; the others need a look
    copy = svc.root / "Finance/2024/invoice (1).pdf"
    assert not copy.exists() and (copy.parent / TRASH_DIR / copy.name).exists()
    assert "invoice (1).pdf" not in groups_by_name(svc)
    assert any(t["dst"].endswith(f"{TRASH_DIR}/invoice (1).pdf") for t in svc.trash())

    u = svc.undo(r["batch_id"])
    assert len(u["restored"]) == 1 and copy.exists()
    svc.search_duplicates()  # back in the catalog: found again
    assert "invoice (1).pdf" in groups_by_name(svc)


def test_changed_files_are_not_trashed(svc):
    svc.search_duplicates()
    g = groups_by_name(svc)["notes.txt"]
    keep, other = g.copies
    other.path.write_bytes(b"changed!" * 31)  # same size, other content, newer time
    r = svc.resolve_duplicates([{"hash": g.hash, "keep": [keep.id]}])
    assert r["trashed"] == 0 and "changed" in r["errors"][0]["error"] and other.path.exists()

    keep.path.unlink()  # the copy to keep is gone: nothing may be trashed
    r = svc.resolve_duplicates([{"hash": g.hash, "keep": [keep.id]}])
    assert r["trashed"] == 0 and r["errors"]


def test_keep_needs_a_copy(svc):
    svc.search_duplicates()
    g = groups_by_name(svc)["notes.txt"]
    r = svc.resolve_duplicates([{"hash": g.hash, "keep": []}])
    assert r["trashed"] == 0 and "at least one" in r["errors"][0]["error"]


def test_keep_as_is_hides_a_group(svc):
    svc.search_duplicates()
    g = groups_by_name(svc)["report.docx"]
    svc.ignore_duplicates([g.hash])
    assert "report.docx" not in groups_by_name(svc)
    assert [x.hash for x in svc.duplicate_groups(ignored=True)] == [g.hash]
    assert svc.duplicate_overview()["summary"]["ignored"] == 1
    svc.ignore_duplicates([g.hash], ignored=False)
    assert "report.docx" in groups_by_name(svc)


def test_folder_pairs_and_deciding_a_whole_folder(svc):
    svc.search_duplicates()
    pairs = svc.duplicate_overview()["pairs"]
    backup = str(svc.root / "Backup/old")
    pair = next(p for p in pairs if backup in (p["a"], p["b"]))
    assert pair["files"] == 2 and pair["b_files" if pair["b"] == backup else "a_files"] == 2

    r = svc.resolve_duplicate_folders(keep_folder=str(svc.root / "Work"), drop_folder=backup)
    assert r["trashed"] == 2
    assert not (svc.root / "Backup/old/report.docx").exists() and (svc.root / "Work/report.docx").exists()
    with pytest.raises(ValueError):
        svc.resolve_duplicate_folders(keep_folder=str(svc.root / "Work"), drop_folder=backup)


def test_scope_must_be_a_configured_root(svc, tmp_path):
    with pytest.raises(ValueError):
        svc.cleanup_categories(str(tmp_path))
    assert {c.name for c in svc.cleanup_categories(str(svc.root))} == {"Backup", "Finance", "Work"}


def test_suggest_uses_the_place_vote():
    a = Copy(1, "/r/Finance", "Finance", "x.pdf", 10, 200.0)
    b = Copy(2, "/r/Backup", "Backup", "x.pdf", 10, 100.0)  # older
    g = Group("h", 10, [a, b], place={1: 0.9, 2: 0.1})
    suggest(g)
    assert g.keep == 1 and g.clear and "fits best" in g.reason
    g = Group("h", 10, [a, b], place={1: 0.5, 2: 0.45})  # no clear winner: the oldest
    suggest(g)
    assert g.keep == 2 and not g.clear


def test_place_vote_from_the_example_index(ex_service, sorted_tree):
    """A finance file that also sits in Work: the similar sorted files are in Finance."""
    root = sorted_tree["root"]
    data = b"invoice bank statement tax receipt finance" * 20
    (root / "Finance/2025/invoice_copy_march.txt").write_bytes(data)
    (root / "Work/invoice_copy_march.txt").write_bytes(data)
    cats = ex_service.categories(refresh=True)
    ex_service.example_index().build(cats)
    ex_service.example_index().fill_query_vectors(cats)
    ex_service.search_duplicates()
    g = next(g for g in ex_service.duplicate_groups() if g.copies[0].relpath.endswith("invoice_copy_march.txt"))
    assert g.place, "the copies are in the index, so their places get a score"
    keep = next(c for c in g.copies if c.id == g.keep)
    assert keep.category == "Finance"
