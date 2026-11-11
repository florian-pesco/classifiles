from pathlib import Path

import pytest

from classifiles.mover import free_name, plan_move


def test_scan_skips_partial_downloads(service, tree):
    scan_id, rows = service.scan(tree["inbox"])
    names = {r["name"] for r in rows}
    assert "movie.mp4.crdownload" not in names
    assert len(rows) == 5
    scan = service.get_scan(scan_id)
    assert scan["status"] == "done" and scan["processed"] == 5


def test_threshold_sets_status(service, tree):
    _, rows = service.scan(tree["inbox"])
    by_name = {r["name"]: r for r in rows}
    assert by_name["vlc-3.0-win64.exe"]["status"] == "suggested"
    for r in rows:
        expected = "suggested" if r["confidence"] >= 0.6 else "needs_review"
        assert r["status"] == expected


def test_accept_apply_undo_roundtrip(service, tree):
    scan_id, rows = service.scan(tree["inbox"])
    by_name = {r["name"]: r for r in rows}
    service.accept_suggested(scan_id)
    notes = by_name["meeting_notes.docx"]
    service.review(notes["id"], "accept", "Work")  # explicit override
    zip_ = by_name["bundle.zip"]
    service.review(zip_["id"], "reject")

    plan = service.plan_apply(scan_id)
    assert all(m["error"] is None for m in plan)
    res = service.apply(scan_id)
    assert not res["errors"]
    assert (tree["root"] / "Work" / "meeting_notes.docx").exists()
    assert (tree["root"] / "Software" / "vlc-3.0-win64.exe").exists()
    assert (tree["inbox"] / "bundle.zip").exists()  # rejected stays
    assert service.get_proposal(notes["id"])["status"] == "moved"

    with pytest.raises(ValueError):
        service.review(notes["id"], "reject")

    undo = service.undo()
    assert undo["batch_id"] == res["batch_id"] and not undo["errors"]
    assert (tree["inbox"] / "meeting_notes.docx").exists()
    assert not (tree["root"] / "Work" / "meeting_notes.docx").exists()
    assert service.get_proposal(notes["id"])["status"] == "accepted"
    assert service.undo()["batch_id"] is None


def test_review_rejects_unknown_folder(service, tree):
    _, rows = service.scan(tree["inbox"])
    with pytest.raises(ValueError):
        service.review(rows[0]["id"], "accept", "../etc")


def test_collision_gets_suffix(service, tree):
    (tree["root"] / "Work" / "meeting_notes.docx").write_bytes(b"existing")
    m = plan_move(tree["inbox"] / "meeting_notes.docx", service.category("Work"))
    assert m.dst.name == "meeting_notes (1).docx"


def test_free_name_counts_up(tmp_path: Path):
    (tmp_path / "a.txt").touch()
    (tmp_path / "a (1).txt").touch()
    assert free_name(tmp_path / "a.txt").name == "a (2).txt"


@pytest.mark.parametrize("folder", ["../outside", "Work/../..", "Missing"])
def test_only_known_categories_are_targets(service, tree, folder):
    (tree["root"].parent / "outside").mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        service.category(folder)


def _age(paths, start=1_000_000):
    """Give files distinct mtimes: first path = oldest."""
    import os
    for i, p in enumerate(paths):
        os.utime(p, (start + i * 100, start + i * 100))


def test_limit_takes_newest_files(service, tree):
    inbox = tree["inbox"]
    _age(sorted(inbox.iterdir()))
    newest = sorted((p for p in inbox.iterdir() if not p.name.endswith(".crdownload")),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:2]
    scan_id, rows = service.scan(inbox, max_files=2)
    assert {r["name"] for r in rows} == {p.name for p in newest}
    scan = service.get_scan(scan_id)
    assert (scan["total"], scan["inbox_total"], scan["max_files"]) == (2, 5, 2)


def test_settings_default_limit_and_zero_means_all(service, tree):
    service.settings.max_files = 3
    assert len(service.scan(tree["inbox"])[1]) == 3
    assert len(service.scan(tree["inbox"], max_files=0)[1]) == 5


def test_only_new_walks_through_the_backlog(service, tree):
    seen = set()
    for _ in range(3):
        _, rows = service.scan(tree["inbox"], max_files=2, only_new=True)
        names = {r["name"] for r in rows}
        assert not names & seen
        seen |= names
    assert len(seen) == 5
    assert service.scan(tree["inbox"], only_new=True)[1] == []


def test_invalid_limit(service, tree):
    with pytest.raises(ValueError):
        service.select_files(tree["inbox"], max_files=-1)


def test_file_deleted_after_scan_start_is_not_classified(service, tree):
    scan_id = service.start_scan(tree["inbox"])
    (tree["inbox"] / "invoice_march.txt").unlink()
    rows = service.run_scan(scan_id)
    assert len(rows) == 4 and service.get_scan(scan_id)["status"] == "done"


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    from classifiles.store import Store

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, inbox TEXT NOT NULL, target_root TEXT NOT NULL,"
                " backend TEXT NOT NULL, threshold REAL NOT NULL, status TEXT NOT NULL DEFAULT 'running',"
                " total INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0, error TEXT,"
                " created_at TEXT NOT NULL)")
    con.execute("INSERT INTO scans (inbox, target_root, backend, threshold, created_at) VALUES ('i','t','b',0.5,'x')")
    con.commit()
    con.close()
    row = Store(db).one("SELECT * FROM scans")
    assert row["inbox_total"] == 0 and row["only_new"] == 0 and row["max_files"] is None


def test_scan_refuses_when_root_unreachable(service, tree, tmp_path):
    service.settings.roots = [{"path": str(tmp_path / "unplugged")}]
    service.invalidate()
    with pytest.raises(ValueError, match="not reachable"):
        service.scan(tree["inbox"])
    with pytest.raises(ValueError, match="not reachable"):
        service.start_index_build()


def test_scan_refuses_without_folder_categories(service, tree, tmp_path):
    empty = tmp_path / "emptyroot"
    empty.mkdir()
    service.settings.roots = [{"path": str(empty)}]
    service.settings.categories = []
    service.invalidate()
    with pytest.raises(ValueError, match="No category folders"):
        service.scan(tree["inbox"])


def test_new_database_has_the_current_schema_version(tmp_path):
    from classifiles.store import SCHEMA_VERSION, Store

    store = Store(tmp_path / "new.db")
    assert store.schema_version == SCHEMA_VERSION
    assert Store(tmp_path / "new.db").schema_version == SCHEMA_VERSION  # reopening runs nothing again
    indexes = {r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "proposals_path" in indexes


def test_database_from_a_newer_version_is_refused(tmp_path):
    import sqlite3

    from classifiles.store import SCHEMA_VERSION, Store

    db = tmp_path / "future.db"
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(RuntimeError, match="newer classifiles"):
        Store(db)


def _make_old(service, scan_id, days):
    from datetime import datetime, timedelta

    then = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    service.store.execute("UPDATE scans SET created_at = ? WHERE id = ?", (then, scan_id))


def test_prune_keeps_decisions(service, tree):
    old_id, rows = service.scan(tree["inbox"])
    by_name = {r["name"]: r for r in rows}
    service.review(by_name["meeting_notes.docx"]["id"], "accept", "Work")
    service.review(by_name["bundle.zip"]["id"], "reject")
    _make_old(service, old_id, 100)
    empty_id = service.start_scan(tree["inbox"], only_new=True)  # an old scan without any proposals
    service.store.execute("UPDATE scans SET status = 'done' WHERE id = ?", (empty_id,))
    _make_old(service, empty_id, 100)
    new_id, new_rows = service.scan(tree["inbox"], max_files=0)

    todo = service.prune_scans(90, dry_run=True)
    assert todo == {"proposals": 3, "scans": 1}
    assert len(service.proposals(old_id)) == 5  # dry run changes nothing

    assert service.prune_scans(90) == todo
    kept = {r["name"] for r in service.proposals(old_id)}
    assert kept == {"meeting_notes.docx", "bundle.zip"}
    assert len(service.proposals(new_id)) == len(new_rows)  # recent scans untouched
    assert {s["id"] for s in service.list_scans()} == {old_id, new_id}
    assert service.prune_scans(90) == {"proposals": 0, "scans": 0}


def test_prune_removes_scans_without_decisions(service, tree):
    scan_id, _ = service.scan(tree["inbox"])
    _make_old(service, scan_id, 10)
    assert service.prune_scans(30) == {"proposals": 0, "scans": 0}
    assert service.prune_scans(7) == {"proposals": 5, "scans": 1}
    assert service.list_scans() == []
    with pytest.raises(ValueError):
        service.prune_scans(-1)
