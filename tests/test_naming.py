"""Suggested file names: rules, service (use/edit/keep, apply, undo) and the language-model step.
All names here are invented."""

from pathlib import Path

import pytest

from classifiles import naming
from classifiles.naming import suggest


@pytest.mark.parametrize("name,kind,text,sub,meta,expected", [
    ("Rechnung (1).pdf", "pdf", "Telekom Deutschland GmbH\nRechnungsdatum: 14.03.2025\nSehr geehrte Damen und Herren",
     "", {}, "TelekomDeutschland_Rechnung_2025-03-14.pdf"),
    ("Lecture 3 - Slides FINAL_v2.pdf", "pdf", "Introduction to the market and the price", "Lectures/Econ 1", {},
     "Econ1_Slides_Lecture03.pdf"),
    ("invoice-3fa85f64-5717-4562-b3fc-2c963f66afa6.pdf", "pdf", "Invoice\nInvoice date: March 3, 2025\nAcme Inc.",
     "", {}, "Acme_Invoice_2025-03-03.pdf"),
    ("uebungsblatt_04_loesung.pdf", "pdf", "Lösung zu Aufgabe 1", "Econ1", {}, "Econ1_Loesung_Uebungsblatt04.pdf"),
    ("Kontoauszug_2025_02 Kopie.pdf", "pdf", "Musterbank AG Kontoauszug Nr. 2 Februar 2025", "2025", {},
     "Musterbank_Kontoauszug_2025-02.pdf"),
    ("Screenshot 2025-03-14 at 10.22.33.png", "image", "", "", {"looks_like": "screenshot"},
     "Screenshot_2025-03-14.png"),
    ("Project Report.docx", "document", "This report describes the project", "ProjectX", {}, "ProjectX_Report.docx"),
])
def test_suggestions(name, kind, text, sub, meta, expected):
    assert suggest(name, kind, text, meta, sub).name == expected


def test_no_suggestion_out_of_scope_or_already_good():
    assert suggest("IMG_2033.jpg", "image", "", {"looks_like": "photo"}) is None  # camera photo
    assert suggest("setup-3.1.exe", "installer") is None
    assert suggest("backup.zip", "archive") is None
    assert suggest("Econ1_Slides_Lecture03.pdf", "pdf", "", {}, "Lectures/Econ1") is None  # already like this
    assert suggest("notes.txt", "text") is None  # nothing to build a name from


def test_photo_with_text_is_a_scan():
    s = suggest("IMG_2034.jpg", "image", "Kassenbon Beispiel Markt GmbH Summe 12,40 EUR 05.03.2025",
                {"looks_like": "photo"})
    assert s.name == "BeispielMarkt_Quittung_2025-03-05.jpg" and s.lang == "de"


def test_dates():
    assert naming.pick_date("x", "Rechnungsdatum: 01.02.2024, Lieferung 03.02.2024")[0] == "2024-02-01"
    assert naming.pick_date("x", "issued on 14 March 2025")[0] == "2025-03-14"
    assert naming.pick_date("Report_2024-07", "") == ("2024-07", "date in the name")
    assert naming.pick_date("Scan_20231105", "")[0] == "2023-11-05"
    assert naming.pick_date("x", "Datum: 31.02.2024") == (None, "")  # impossible date
    assert naming.pick_date("x", "Stand März 2025")[0] == "2025-03"
    # an unlabelled text date loses against the creation date; undated documents never use text dates
    assert naming.pick_date("x", "Lieferung 03.02.2024", "2024-02-10") == ("2024-02-10", "creation date (file metadata)")
    assert naming.pick_date("x", "Born 03.04.1990", None, dated=False) == (None, "")


def test_cv_in_a_type_folder():
    """The folder 'Lebensläufe' is a type, not a subject; a birth date in a CV is no document date."""
    text = "Curriculum Vitae\nBorn 03.04.1990\nmail: someone@uni-example.edu"
    s = suggest("CV.pdf", "pdf", text, {"created": "2023-05-17"}, "Unterlagen/Lebensläufe")
    assert s.name == "CV_2023-05-17.pdf" and "creation date" in s.reason
    assert naming.topic_folder("Unterlagen/Lebensläufe") is None and naming.topic_folder("Lectures/Econ 1") == "Econ 1"
    assert naming.type_of_word("Rechnungen") == "Invoice" and naming.type_of_word("Econ1") is None
    s = suggest("scan_7.pdf", "pdf", "", {}, "Finanzen/Rechnungen")
    assert s.name.startswith("Rechnung") or "type Invoice" in s.reason or "Rechnung" in s.reason


def test_style():
    assert naming.pascal("müller straße 2") == "MuellerStrasse2"
    assert naming.pascal("ACME project x") == "ACMEProjectX"
    assert naming.pascal("übung", transliterate=False) == "Übung"
    long = suggest("x.pdf", "pdf", "Rechnung " + "Sehr Lange Firmenbezeichnung Mit Vielen Woertern GmbH", {})
    assert long is None or len(Path(long.name).stem) <= naming.MAX_STEM
    assert naming.valid_name('bad:name?.pdf') == "badname.pdf"
    with pytest.raises(ValueError):
        naming.valid_name("  ...  ")


def test_type_and_language():
    assert naming.detect_type("Mietvertrag Wohnung", "")[0] == "Contract"
    assert naming.detect_type("x", "Your payslip for March")[0] == "Payslip"
    assert naming.detect_type("x", "Dear customer")[0] is None  # a greeting alone is too weak
    assert naming.language("Sehr geehrte Damen und Herren, wir bitten Sie") == "de"
    assert naming.language("Dear customer, please find your invoice attached") == "en"


def test_subject_candidates_ranked():
    c = naming.subject_candidates("report_final", "Beispiel Consulting GmbH\nwww.beispiel.de", "Projects/ProjectX",
                                  "portal.example.org")
    assert [w for _, w in c] == ["folder", "sender in the text"]  # name words are only junk/type words
    c = naming.subject_candidates("x", "Beispiel Consulting GmbH", "", type_="CV")
    assert c == [] or all(w != "sender in the text" for _, w in c)  # a CV's sender line says nothing
    assert naming.subject_candidates("x", "", "2025")[:1] == []  # a year folder is no subject
    assert naming.sender("Kontakt: info@beispiel-shop.de") == "beispiel-shop"
    assert naming.sender("Mail: someone@gmail.com") is None


# --- service ----------------------------------------------------------------------------

@pytest.fixture
def named(service, tree):
    (tree["inbox"] / "Rechnung (1).txt").write_text("Beispiel Energie GmbH\nRechnungsdatum: 14.03.2025\nIhre Rechnung")
    return service


def _by(rows, name):
    return next(r for r in rows if r["name"] == name)


def test_scan_suggests_but_keeps_original_by_default(named, tree):
    _, rows = named.scan(tree["inbox"])
    r = _by(rows, "Rechnung (1).txt")
    assert r["suggested_name"] == "BeispielEnergie_Rechnung_2025-03-14.txt" and r["new_name"] is None
    assert "'rechnung' in the name" in r["name_reason"]
    assert _by(rows, "vlc-3.0-win64.exe")["suggested_name"] is None  # installer: out of scope


def test_use_edit_keep_apply_and_undo(named, tree):
    scan_id, rows = named.scan(tree["inbox"])
    r = _by(rows, "Rechnung (1).txt")
    named.review(r["id"], "accept", "Finance", subpath="", use_suggested_name=True)
    assert named.get_proposal(r["id"])["new_name"] == "BeispielEnergie_Rechnung_2025-03-14.txt"
    p = named.review(r["id"], "accept", "Finance", subpath="", name="Energy bill")
    assert p["new_name"] == "Energy bill.txt"  # typed names keep the user's style; extension added
    named.review(r["id"], "accept", "Finance", subpath="", use_suggested_name=True)
    (tree["root"] / "Finance" / "BeispielEnergie_Rechnung_2025-03-14.txt").write_text("already there")
    plan = next(m for m in named.plan_apply(scan_id) if m["proposal_id"] == r["id"])
    assert plan["renamed"] and plan["dst"].endswith("BeispielEnergie_Rechnung_2025-03-14_2.txt")
    named.apply(scan_id)
    assert (tree["root"] / "Finance" / "BeispielEnergie_Rechnung_2025-03-14_2.txt").exists()
    named.undo()
    assert (tree["inbox"] / "Rechnung (1).txt").exists()  # back under its original name
    p = named.review(r["id"], "accept", "Finance", subpath="", use_suggested_name=False)
    assert p["new_name"] is None


def test_suggestion_follows_folder_changes(named, tree):
    (tree["root"] / "Finance" / "Energy").mkdir()
    _, rows = named.scan(tree["inbox"])
    r = _by(rows, "Rechnung (1).txt")
    named.review(r["id"], "accept", "Finance", subpath="", use_suggested_name=True)
    p = named.review(r["id"], "accept", "Finance", subpath="Energy")
    assert p["suggested_name"] == "Energy_Rechnung_2025-03-14.txt" and p["new_name"] == p["suggested_name"]
    p = named.review(r["id"], "accept", "Delete")
    assert p["suggested_name"] is None and p["new_name"] is None  # deleted files keep their name
    p = named.review(r["id"], "reset")
    assert p["new_name"] is None and p["suggested_name"]


def test_bulk_and_setting_default(named, tree):
    named.settings.use_suggested_names = True
    scan_id, rows = named.scan(tree["inbox"])
    r = _by(rows, "Rechnung (1).txt")
    assert r["new_name"] == r["suggested_name"]
    out = named.bulk_review([r["id"]], "accept", "Finance", "", use_suggested_name=False)
    assert out["updated"][0]["new_name"] is None
    named.settings.name_suggestions = False
    _, rows = named.scan(tree["inbox"])
    assert all(x["suggested_name"] is None for x in rows)


def test_duplicates_are_not_renamed(named, tree):
    (tree["inbox"] / "Rechnung (2).txt").write_text((tree["inbox"] / "Rechnung (1).txt").read_text())
    _, rows = named.scan(tree["inbox"])
    dup = next(r for r in rows if r["source"] == "duplicate")
    assert dup["suggested_name"] is None


def test_api(named, tree, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    client = TestClient(create_app(named, config_path=str(tmp_path / "cfg.yaml")))
    scan_id, rows = named.scan(tree["inbox"])
    r = _by(rows, "Rechnung (1).txt")
    p = client.patch(f"/api/proposals/{r['id']}", json={"action": "accept", "folder": "Finance", "subpath": "",
                                                         "use_suggested_name": True}).json()
    assert p["new_name"] == r["suggested_name"]
    p = client.patch(f"/api/proposals/{r['id']}", json={"action": "accept", "name": "a<b>.txt"}).json()
    assert p["new_name"] == "ab.txt"
    bad = client.patch(f"/api/proposals/{r['id']}", json={"action": "accept", "name": "..."})
    assert bad.status_code == 400
    b = client.post("/api/proposals/bulk", json={"ids": [r["id"]], "action": "accept", "use_suggested_name": True})
    assert b.json()["updated"][0]["new_name"] == r["suggested_name"]
    assert client.patch("/api/settings", json={"name_llm": True}).json()["name_llm"] is True


# --- the language-model step --------------------------------------------------------------

class FakeNamer:
    """Answers the type question with "Contract" and the subject question with the second option."""

    def __init__(self):
        self.prompts = []

    def choose(self, prompt, letters):
        self.prompts.append(prompt)
        lines = [line for line in prompt.splitlines() if len(line) > 3 and line[1:3] == ") "]
        if "What kind of document" in prompt:
            pick = next(line[0] for line in lines if line[3:].startswith("Contract"))
        else:
            pick = lines[1][0]
        return {letter: (0.9 if letter == pick else 0.1 / (len(letters) - 1)) for letter in letters}


def test_language_model_picks_type_and_subject(named, tree, monkeypatch):
    fake = FakeNamer()

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr("classifiles.service.llm.LLMClient", Client)
    (tree["inbox"] / "scan_0042.txt").write_text(
        "Vereinbarung zwischen Beispiel Verwaltung GmbH und Mieterin. " * 6 + " Datum: 01.02.2024")
    named.settings.name_llm = True
    scan_id, rows = named.scan(tree["inbox"])
    r = named.get_proposal(_by(rows, "scan_0042.txt")["id"])
    assert r["name_llm"]["type"] == "Contract" and "chosen by the language model" in r["name_reason"]
    assert r["suggested_name"].endswith("_Vertrag_2024-02-01.txt")
    assert any("What kind of document" in p for p in fake.prompts)
    assert named.get_scan(scan_id)["llm_error"] is None


def test_language_model_failure_keeps_rule_names(named, tree, monkeypatch):
    from classifiles import llm

    def broken(*a, **k):
        raise llm.LLMError("no model")

    monkeypatch.setattr("classifiles.service.llm.LLMClient", broken)
    (tree["inbox"] / "scan_0043.txt").write_text("Rechnung Beispiel Energie GmbH " * 10 + " Datum: 01.02.2024")
    named.settings.name_llm = True
    scan_id, rows = named.scan(tree["inbox"])
    assert _by(rows, "scan_0043.txt")["suggested_name"] and "no model" in named.get_scan(scan_id)["llm_error"]


def test_clean_up_when_only_a_subject_is_found():
    s = suggest("mueller hans (1).pdf", "pdf")
    assert s.name == "MuellerHans.pdf" and s.reason == "clean-up of the original name"
    assert suggest("MuellerHans.pdf", "pdf") is None  # already in this style


def test_language_model_also_helps_without_a_rule_name(named, tree, monkeypatch):
    fake = FakeNamer()

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr("classifiles.service.llm.LLMClient", Client)
    (tree["inbox"] / "zx.txt").write_text("Zwischen den Parteien wird Folgendes festgelegt und unterschrieben. " * 4)
    named.settings.name_llm = True
    _, rows = named.scan(tree["inbox"])
    r = named.get_proposal(_by(rows, "zx.txt")["id"])
    assert r["name_llm"]["type"] == "Contract" and r["suggested_name"]


def test_clean_up_keeps_type_words_and_other_kinds_lose_copy_markers(named, tree):
    assert suggest("homework (1).pdf", "pdf", "", {}, "Lectures").name == "Homework.pdf"
    assert naming.suggest_cleanup("project(1).zip").name == "project.zip"
    assert naming.suggest_cleanup("setup-3.1 (2).exe").name == "setup-3.1.exe"  # versions stay
    assert naming.suggest_cleanup("backup.tar.gz") is None
    (tree["inbox"] / "bundle (1).zip").write_bytes((tree["inbox"] / "bundle.zip").read_bytes() + b"x")
    _, rows = named.scan(tree["inbox"])
    r = _by(rows, "bundle (1).zip")
    assert r["suggested_name"] == "bundle.zip" and r["name_reason"] == "copy marker removed"
