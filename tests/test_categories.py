"""Roots + manual categories, copy/delete actions, trash, config upgrade."""

from pathlib import Path

import pytest

from classifiles.config import Settings
from classifiles.taxonomy import build_categories
from classifiles_api.app import _prune


def _accept(service, rows, name, folder):
    pid = next(r["id"] for r in rows if r["name"] == name)
    service.review(pid, "accept", folder)
    return pid


def test_manual_category_outside_root_with_copy(service, tree, tmp_path):
    external = tmp_path / "Cloud" / "Steuer"
    external.mkdir(parents=True)
    service.set_categories(service.settings.roots, [*service.settings.categories,
                           {"path": str(external), "name": "Steuer", "action": "copy"}])
    cat = service.category("Steuer")
    assert cat.source == "manual" and cat.action == "copy"

    scan_id, rows = service.scan(tree["inbox"])
    _accept(service, rows, "invoice_march.txt", "Steuer")
    res = service.apply(scan_id)
    assert res["moved"][0]["action"] == "copy"
    assert (external / "invoice_march.txt").exists() and (tree["inbox"] / "invoice_march.txt").exists()

    service.undo()
    assert not (external / "invoice_march.txt").exists() and (tree["inbox"] / "invoice_march.txt").exists()


def test_copy_undo_keeps_a_changed_copy(service, tree, tmp_path):
    ext = tmp_path / "Ext"
    ext.mkdir()
    service.set_categories(service.settings.roots, [{"path": str(ext), "action": "copy"}])
    scan_id, rows = service.scan(tree["inbox"])
    _accept(service, rows, "invoice_march.txt", "Ext")
    service.apply(scan_id)
    (ext / "invoice_march.txt").write_text("edited after copying, longer than before")
    res = service.undo()
    assert res["errors"] and (ext / "invoice_march.txt").exists()


def test_delete_goes_to_trash_undo_and_empty(service, tree):
    scan_id, rows = service.scan(tree["inbox"])
    _accept(service, rows, "bundle.zip", "Delete")
    service.apply(scan_id)
    trashed = tree["inbox"] / ".classifiles-trash" / "bundle.zip"
    assert trashed.exists() and not (tree["inbox"] / "bundle.zip").exists()
    assert [t["dst"] for t in service.trash()] == [str(trashed)]
    # trash is hidden, so a new scan does not pick it up
    assert "bundle.zip" not in {p.name for p in service.list_inbox(tree["inbox"])}

    service.undo()
    assert (tree["inbox"] / "bundle.zip").exists() and service.trash() == []
    assert not trashed.parent.exists()  # empty trash folder removed on undo

    service.apply(scan_id)  # accepted again after undo
    assert service.empty_trash()["purged"] == 1
    assert not trashed.exists() and not trashed.parent.exists()
    assert service.undo()["batch_id"] is None  # purged files cannot be undone


def test_delete_rule_and_suggest_flag(service, tree):
    service.set_rules([{"kind": "installer", "folder": "Delete"}])
    _, rows = service.scan(tree["inbox"])
    exe = next(r for r in rows if r["name"].endswith(".exe"))
    assert (exe["target"], exe["source"]) == ("Delete", "rule")
    # by default the model is never offered Delete; with suggest it is
    assert "Delete" not in {c.name for c in service.classifier().categories}
    service.set_categories(service.settings.roots, service.settings.categories, {"enabled": True, "suggest": True})
    assert "Delete" in {c.name for c in service.classifier().categories}


def test_disabled_category_is_not_offered(service, tree):
    entries = [*service.settings.categories, {"path": str(tree["root"] / "Photos"), "enabled": False}]
    service.set_categories(service.settings.roots, entries)
    _, rows = service.scan(tree["inbox"])
    assert all(a["folder"] != "Photos" for r in rows for a in r["alternatives"])
    with pytest.raises(ValueError):
        service.review(rows[0]["id"], "accept", "Photos")


def test_rules_validated_against_categories(service):
    with pytest.raises(ValueError):
        service.set_rules([{"ext": ".exe", "folder": "Nope"}])
    with pytest.raises(ValueError):
        service.set_rules([{"folder": "Work"}])  # no condition


def test_same_folder_name_in_two_roots(tmp_path):
    for r in ("A", "B"):
        (tmp_path / r / "Studies").mkdir(parents=True)
    cats = build_categories([{"path": str(tmp_path / "A")}, {"path": str(tmp_path / "B")}], [], 0,
                            delete={"enabled": True})
    assert sorted(c.name for c in cats) == ["Delete", "Studies (A)", "Studies (B)"]


def test_legacy_config_is_upgraded():
    s = Settings.from_dict({"target_root": "/data", "folder_descriptions": {"Studies": "lectures"},
                            "exclude_folders": ["Backups"], "inboxes": ["/data/in"]})
    assert s.roots == [{"path": "/data"}]
    assert {"path": "/data/Studies", "description": "lectures"} in s.categories
    assert {"path": "/data/Backups", "enabled": False} in s.categories


def test_prune_keeps_only_changes():
    roots = [{"path": "/data"}]
    entries = [{"path": "/data/Studies", "name": "Studies", "description": "", "enabled": True, "action": "move"},
               {"path": "/data/Work", "description": " jobs "},
               {"path": "/elsewhere/Tax", "name": "Tax"}]
    assert _prune(roots, entries) == [{"path": "/data/Work", "description": "jobs"}, {"path": "/elsewhere/Tax"}]
