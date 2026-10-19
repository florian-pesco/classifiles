import pytest

from classifiles.classifier import Classifier
from classifiles.deciders import KeywordDecider
from classifiles.extract import extract
from classifiles.rules import Rule, load_rules
from classifiles.taxonomy import load_taxonomy
from conftest import DESCRIPTIONS


def test_taxonomy_is_one_level_and_skips_hidden(tree, settings):
    cats = load_taxonomy(tree["root"], DESCRIPTIONS)
    assert [c.name for c in cats] == ["Finance", "Photos", "Software", "Work"]
    fin = cats[0]
    assert fin.option_text() == "invoices, bank statements, taxes, receipts"  # description alone
    bare = load_taxonomy(tree["root"])[0]
    assert "Rechnung_2025.pdf" in bare.option_text()  # samples stand in for a missing description


def test_rules():
    r = Rule.from_dict({"ext": ["exe", ".MSI"], "folder": "Software"})
    assert r.ext == [".exe", ".msi"]
    with pytest.raises(ValueError):
        Rule.from_dict({"folder": "X"})
    rn = Rule.from_dict({"name": "rechnung|invoice", "folder": "Finance"})
    assert rn.name.search("Invoice_2026.pdf")


def test_rule_wins_over_model(tree, settings):
    cats = load_taxonomy(tree["root"], DESCRIPTIONS)
    clf = Classifier(cats, KeywordDecider(), load_rules(settings.rules))
    c = clf.classify(extract(tree["inbox"] / "vlc-3.0-win64.exe"))
    assert (c.target, c.confidence, c.source) == ("Software", 1.0, "rule")


def test_model_ranks_and_returns_alternatives(tree, settings):
    cats = load_taxonomy(tree["root"], DESCRIPTIONS)
    clf = Classifier(cats, KeywordDecider())
    c = clf.classify(extract(tree["inbox"] / "invoice_march.txt"))
    assert c.target == "Finance"
    assert c.alternatives[0]["folder"] == "Finance"
    assert len(c.alternatives) == 4
    assert abs(sum(a["probability"] for a in c.alternatives) - 1) < 0.01


def test_rule_with_unknown_folder_is_skipped(tree):
    cats = load_taxonomy(tree["root"])
    clf = Classifier(cats, KeywordDecider(), load_rules([{"ext": ".exe", "folder": "Nope"}]))
    assert clf.rules == []


def test_explicit_missing_config_is_an_error(tmp_path):
    from classifiles.config import Settings

    with pytest.raises(FileNotFoundError):
        Settings.load(tmp_path / "nope.yaml")


def test_inbox_inside_root_is_not_a_category(service, tree):
    inbox = tree["root"] / "Inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("invoice")
    _, rows = service.scan(inbox)
    assert "Inbox" not in {c.name for c in service.categories()}
    assert rows[0]["target"] != "Inbox"


def test_samples_only_without_description_and_not_recursive(tree):
    nested = tree["root"] / "Work" / "deep"
    nested.mkdir()
    (nested / "nested_file.txt").write_text("x")
    cats = {c.name: c for c in load_taxonomy(tree["root"], {"Finance": "money"})}
    assert cats["Finance"].samples == []
    assert "nested_file.txt" not in cats["Work"].samples and "Quarterly_report.docx" in cats["Work"].samples
