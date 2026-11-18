"""Search page: the index of roots, manual folders and inboxes, and queries over it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from classifiles import ClassifilesService, Settings
from classifiles import search as search_mod
from classifiles.deciders import KeywordDecider
from classifiles.search import HIT_END, HIT_START, normalize, searched_locations
from classifiles.store import Store
from conftest import make_docx

DAY = 86400
NOW = 1_760_000_000


@pytest.fixture
def svc(tmp_path: Path) -> ClassifilesService:
    root, inbox = tmp_path / "Archive", tmp_path / "Inbox"
    files = {
        root / "Finance/2024/Rechnung_März_2024.txt": ("Telekom Rechnung für März, Betrag 39,95 EUR", 300),
        root / "Finance/2024/Kontoauszug.txt": ("Kontoauszug Sparkasse Februar", 280),
        root / "Work/Projekt Alpha/meeting notes.md": ("Agenda: budget review and roadmap", 20),
        root / "Work/holiday.jpg": (None, 5),
        root / "Work/.hidden/secret.txt": ("never listed", 1),
        root / "Work/.classifiles-trash/old.txt": ("never listed", 1),
        inbox / "invoice_telekom.pdf": (None, 2),
        inbox / "setup.exe": (None, 1),
    }
    for path, (text, age) in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.write_bytes(b"\x00binary" * 10)
        else:
            path.write_text(text, encoding="utf-8")
        os.utime(path, (NOW - age * DAY, NOW - age * DAY))
    make_docx(root / "Work/Projekt Alpha/report.docx", "Quarterly report about the Alpha project budget")
    os.utime(root / "Work/Projekt Alpha/report.docx", (NOW - 30 * DAY, NOW - 30 * DAY))
    settings = Settings(roots=[{"path": str(root)}], inboxes=[str(inbox)], backend="keyword", db_path=":memory:",
                        image_similarity=False, trained_weight=0.0,
                        categories=[{"path": str(root / "Finance")}, {"path": str(root / "Work")}])
    s = ClassifilesService(settings, decider=KeywordDecider(), store=Store(":memory:"))
    s.root, s.inbox = root, inbox  # type: ignore[attr-defined]
    s.update_search_index()
    return s


def names(result: dict) -> list[str]:
    return [r["name"] for r in result["results"]]


def test_normalize_and_locations(tmp_path):
    assert normalize("Rechnung_März-2024.PDF") == "rechnung marz 2024 pdf"
    assert normalize("Straße") == "strasse"
    a, b = str(tmp_path / "a"), str(tmp_path / "a/inbox")
    assert searched_locations([b, a, a]) == [a]  # an inbox inside a root is searched once


def test_index_lists_files_but_not_hidden_folders(svc):
    st = svc.search_status()
    assert st["state"] == "done" and st["files"] == 7 and st["texts"] == 4  # 3 .txt/.md + 1 docx
    assert "secret.txt" not in names(svc.search({"text": "never"}))


def test_name_search_ignores_case_accents_and_separators(svc):
    assert names(svc.search({"text": "marz rechnung"})) == ["Rechnung_März_2024.txt"]
    assert names(svc.search({"text": "INVOICE"})) == ["invoice_telekom.pdf"]


def test_folder_matches_rank_after_name_matches(svc):
    r = svc.search({"text": "alpha", "match": "name"})
    assert set(names(r)) == {"meeting notes.md", "report.docx"}  # found by their folder
    assert all(x["in_folder"] and not x["in_name"] for x in r["results"])


def test_content_search_with_prefix_and_snippet(svc):
    r = svc.search({"text": "sparkass"})
    assert names(r) == ["Kontoauszug.txt"]
    hit = r["results"][0]
    assert hit["in_content"] and not hit["in_name"] and f"{HIT_START}Sparkasse{HIT_END}" in hit["snippet"]
    assert set(names(svc.search({"text": "budget"}))) == {"report.docx", "meeting notes.md"}
    assert svc.search({"text": "sparkasse", "match": "name"})["total"] == 0


def test_name_and_content_both_count(svc):
    r = svc.search({"text": "telekom"})
    assert names(r)[0] == "invoice_telekom.pdf"  # in the name: first
    assert "Rechnung_März_2024.txt" in names(r)    # in the content


def test_filters(svc):
    assert names(svc.search({"groups": ["pictures"]})) == ["holiday.jpg"]
    assert names(svc.search({"ext": "PDF"})) == ["invoice_telekom.pdf"]
    recent = svc.search({"after": NOW - 10 * DAY, "sort": "oldest"})
    assert names(recent) == ["holiday.jpg", "invoice_telekom.pdf", "setup.exe"]
    old = svc.search({"before": NOW - 100 * DAY, "groups": ["documents"]})
    assert set(names(old)) == {"Rechnung_März_2024.txt", "Kontoauszug.txt"}
    assert names(svc.search({"location": str(svc.inbox), "groups": ["installers"]})) == ["setup.exe"]
    with pytest.raises(ValueError):
        svc.search({"location": "/elsewhere"})
    with pytest.raises(ValueError):
        svc.search({"groups": ["nonsense"]})


def test_odd_input_does_not_fail(svc):
    for text in ['"', "*", "AND OR NOT", "(", "100%", "a_b"]:
        svc.search({"text": text})


def test_update_is_incremental(svc, monkeypatch):
    read = []
    real = search_mod.extract
    monkeypatch.setattr(search_mod, "extract", lambda p, **kw: read.append(p.name) or real(p, **kw))
    svc.update_search_index()
    assert read == []
    note = svc.root / "Work/Projekt Alpha/meeting notes.md"
    note.write_text("Agenda: hiring plan")
    (svc.root / "Finance/2024/Kontoauszug.txt").unlink()
    svc.update_search_index()
    assert read == ["meeting notes.md"]
    assert names(svc.search({"text": "hiring"})) == ["meeting notes.md"]
    assert svc.search({"text": "kontoauszug"})["total"] == 0


def test_unreachable_location_keeps_its_entries(svc, tmp_path):
    svc.root.rename(tmp_path / "unplugged")
    svc.update_search_index()
    assert svc.search_status()["files"] == 7


def test_without_content_the_text_is_dropped(svc):
    svc.settings.search_content = False
    svc.update_search_index()
    assert svc.search_status()["texts"] == 0
    assert svc.search({"text": "sparkasse"})["total"] == 0


def test_apply_and_undo_keep_the_index_current(svc):
    scan_id = svc.start_scan(str(svc.inbox))
    svc.run_scan(scan_id)
    p = next(p for p in svc.proposals(scan_id) if p["name"] == "invoice_telekom.pdf")
    svc.review(p["id"], "accept", folder="Finance")
    res = svc.apply(scan_id)
    assert res["moved"], res
    hit = svc.search({"text": "invoice_telekom"})["results"][0]
    assert hit["path"] == str(svc.root / "Finance/invoice_telekom.pdf")
    svc.undo(res["batch_id"])
    hit = svc.search({"text": "invoice_telekom"})["results"][0]
    assert hit["path"] == str(svc.inbox / "invoice_telekom.pdf")


def test_result_path_only_for_indexed_files(svc):
    r = svc.search({"text": "setup"})["results"][0]
    assert svc.search_result_path(r["id"]) == svc.inbox / "setup.exe"
    with pytest.raises(KeyError):
        svc.search_result_path(99_999)
