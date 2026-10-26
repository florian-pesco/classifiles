"""Subfolder suggestions, new folders, and applying into folders inside a category."""

import numpy as np
import pytest

from classifiles.mover import MoveError, plan_move
from classifiles.paths import (name_tokens, normalize_subpath, split_existing, suggest_folder_name,
                               suggest_subpath)
from test_examples import FakeEmbedder  # noqa: F401  (fixtures below reuse its setup)
from test_examples import ex_service, sorted_tree  # noqa: F401


def n(cat, d, w):
    return {"category": cat, "dir": d, "weight": w}


def test_deepest_folder_most_neighbours_agree_on():
    nb = [n("Studies", "Lectures/Econ 1", 1), n("Studies", "Lectures/Econ 1", .8), n("Studies", "Lectures/Ana", .3),
          n("Work", "x", 1)]
    assert suggest_subpath(nb, "Studies") == ("Lectures/Econ 1", False)
    assert suggest_subpath(nb, "Work") == ("x", False)
    assert suggest_subpath(nb, "Other") == ("", False)


def test_spread_children_mark_a_container():
    nb = [n("Studies", "Lectures/A", 1), n("Studies", "Lectures/B", .9), n("Studies", "Lectures/C", .8)]
    assert suggest_subpath(nb, "Studies") == ("Lectures", True)


def test_top_level_when_no_folder_has_a_majority():
    nb = [n("Studies", "A", 1), n("Studies", "B", 1), n("Studies", "", 1)]
    sub, container = suggest_subpath(nb, "Studies")
    assert sub == ""


def test_folder_names_from_file_names():
    assert name_tokens("ha05-solution1-1.pdf") == ["ha", "solution"]
    assert name_tokens("IMG_20250510_221937713.jpg") == []
    assert suggest_folder_name(["Econ2_Slides_chapter3.pdf", "Econ2_Exercise_04.pdf", "econ2-exam.pdf"]) == "Econ"
    assert suggest_folder_name(["Projektarbeit Smart Home.pdf"]) == "Projektarbeit Smart Home"
    assert suggest_folder_name(["2024-01-01.pdf"]) == ""


@pytest.mark.parametrize("bad", ["../x", "a/../../b", "a:b", "x?", "name. "])
def test_unsafe_subpaths_rejected(bad):
    with pytest.raises(ValueError):
        normalize_subpath(bad)


def test_normalize_and_split(tmp_path):
    assert normalize_subpath("/A\\B/ ") == "A/B"
    (tmp_path / "A").mkdir()
    assert split_existing(tmp_path, "A/New/Deeper") == ("A", ["New", "Deeper"])


def test_scan_suggests_subfolder_and_apply_uses_it(ex_service, sorted_tree, monkeypatch):
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    ex_service.example_index().build(ex_service.categories(refresh=True))
    (sorted_tree["inbox"] / "invoice_april.txt").write_text("invoice april")
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    inv = next(r for r in rows if r["name"] == "invoice_april.txt")
    assert (inv["target"], inv["suggested_subpath"], inv["subpath"]) == ("Finance", "2025", "2025")

    ex_service.review(inv["id"], "accept")
    plan = {p["proposal_id"]: p for p in ex_service.plan_apply(scan_id)}[inv["id"]]
    assert plan["dst"].endswith("Finance/2025/invoice_april.txt") and plan["creates"] is None
    ex_service.apply(scan_id)
    assert (sorted_tree["root"] / "Finance" / "2025" / "invoice_april.txt").exists()
    learned = ex_service.store.one("SELECT relpath FROM examples WHERE origin = 'applied'")
    assert learned["relpath"] == "2025/invoice_april.txt"
    ex_service.undo()
    assert (sorted_tree["inbox"] / "invoice_april.txt").exists()
    assert ex_service.store.query("SELECT * FROM examples WHERE origin = 'applied'") == []


def test_category_only_and_category_change(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    (sorted_tree["inbox"] / "invoice_april.txt").write_text("invoice april")
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    inv = next(r for r in rows if r["name"] == "invoice_april.txt")
    assert ex_service.review(inv["id"], "accept", subpath="")["subpath"] == ""       # category only
    p = ex_service.review(inv["id"], "accept", "Work")                               # recomputed for Work
    assert p["final_target"] == "Work" and p["subpath"] in ("", "2025")
    assert ex_service.review(inv["id"], "reset")["subpath"] == "2025"
    ex_service.accept_suggested(scan_id, top_level=True)
    assert all(r["subpath"] == "" for r in ex_service.proposals(scan_id, "accepted"))


def test_new_folder_is_created_on_apply_and_removed_on_undo(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    txt = next(r for r in rows if r["name"] == "invoice_march.txt")
    ex_service.review(txt["id"], "accept", "Finance", subpath="2026/Q1")
    plan = {p["proposal_id"]: p for p in ex_service.plan_apply(scan_id)}[txt["id"]]
    assert plan["creates"].endswith("Finance/2026")
    ex_service.apply(scan_id)
    assert (sorted_tree["root"] / "Finance" / "2026" / "Q1" / "invoice_march.txt").exists()
    ex_service.undo()
    assert not (sorted_tree["root"] / "Finance" / "2026").exists()  # created folders removed again


def test_new_folder_kept_on_undo_if_not_empty(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    txt = next(r for r in rows if r["name"] == "invoice_march.txt")
    ex_service.review(txt["id"], "accept", "Finance", subpath="New")
    ex_service.apply(scan_id)
    (sorted_tree["root"] / "Finance" / "New" / "other.txt").write_text("added by the user")
    ex_service.undo()
    assert (sorted_tree["root"] / "Finance" / "New" / "other.txt").exists()


def test_subpath_cannot_leave_category(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    _, rows = ex_service.scan(sorted_tree["inbox"])
    with pytest.raises(ValueError):
        ex_service.review(rows[0]["id"], "accept", "Finance", subpath="../Work")
    with pytest.raises(MoveError):
        plan_move(sorted_tree["inbox"] / "invoice_march.txt", ex_service.category("Finance"), "../Work")


def test_new_folder_suggestion_groups_similar_inbox_files(ex_service, sorted_tree):
    ex_service.example_index().build(ex_service.categories(refresh=True))
    scan_id, rows = ex_service.scan(sorted_tree["inbox"])
    ids = [r["id"] for r in rows[:3]]
    v = np.ones(4) / 2
    far = np.array([1.0, 0, 0, 0])
    cands = [{"id": ids[0], "category": "Finance", "parent": "", "name": "Econ2_Slides.pdf", "vector": v},
             {"id": ids[1], "category": "Finance", "parent": "", "name": "Econ2_Exercise.pdf", "vector": v},
             {"id": ids[2], "category": "Finance", "parent": "", "name": "2025.pdf", "vector": far}]
    ex_service._suggest_new_folders(cands)
    got = {r["id"]: r["new_folder"] for r in ex_service.proposals(scan_id)}
    assert got[ids[0]] == got[ids[1]] == "Econ" and got[ids[2]] is None
    # a suggested name that already exists becomes a normal subfolder suggestion
    cands = [{"id": ids[2], "category": "Finance", "parent": "", "name": "2025 statements.pdf", "vector": far}]
    (sorted_tree["root"] / "Finance" / "statements").mkdir()
    ex_service._suggest_new_folders(cands)
    assert ex_service.get_proposal(ids[2])["subpath"] == "statements"


@pytest.mark.parametrize("sub,name,expected", [
    ("Lectures/Econ 1", "Econ2_Slides_chapter1.txt", "Lectures/Econ 2"),
    ("2025", "Rechnung_2026_01.pdf", "2026"),
    ("Kunden/Order35", "order 36 notes.txt", "Kunden/Order36"),
    ("Lectures/Econ 1", "Econ1_Slides.txt", None),        # same number
    ("Lectures/Analysis", "Analysis2.pdf", None),       # folder has no number
    ("2025", "Rechnung_05.pdf", None),                     # no year in the name
    ("", "Econ2.pdf", None),
])
def test_numbered_sibling(sub, name, expected):
    from classifiles.paths import numbered_sibling

    assert numbered_sibling(sub, name) == expected


def test_scan_proposes_numbered_sibling(ex_service, sorted_tree):
    fin = sorted_tree["root"] / "Finance"
    ex_service.example_index().build(ex_service.categories(refresh=True))
    (sorted_tree["inbox"] / "invoice_2026_jan.txt").write_text("invoice january")
    _, rows = ex_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_2026_jan.txt")
    assert (r["target"], r["subpath"], r["new_folder"]) == ("Finance", "", "2026")
    # once "2026" exists, it is suggested directly
    (fin / "2026").mkdir()
    _, rows = ex_service.scan(sorted_tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_2026_jan.txt")
    assert (r["subpath"], r["new_folder"]) == ("2026", None)
