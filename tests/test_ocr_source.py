"""OCR for scanned PDFs and the download source hint (with a fake PowerShell)."""

import json
from pathlib import Path

import pytest

from classifiles.examples import query_text
from classifiles.extract import extract
from classifiles.extract.ocr import engine
from classifiles.source import domain, download_sources, parse_zone_identifier, windows_path

needs_ocr = pytest.mark.skipif(engine() is None, reason="OCR extra not installed")


def scanned_pdf(path: Path, lines: list[str]) -> Path:
    """An image-only PDF (no text layer), like a scanner produces."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=48)
    im = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        draw.text((120, 200 + i * 90), line, fill="black", font=font)
    im.save(path, resolution=150)
    return path


@needs_ocr
def test_scanned_pdf_is_read_by_ocr(tmp_path):
    pdf = scanned_pdf(tmp_path / "scan.pdf", ["Rechnung Nr. 4711", "Betrag 39,95 EUR"])
    off = extract(pdf, ocr_pdf_pages=0)
    assert off.text == "" and off.meta["note"] == "scanned PDF without a text layer"
    on = extract(pdf, ocr_pdf_pages=2)
    assert "Rechnung" in on.text and "4711" in on.text
    assert on.meta["note"] == "text read by OCR (first page)"


@needs_ocr
def test_image_ocr_can_be_switched_off(tmp_path):
    from PIL import Image, ImageDraw, ImageFont

    img = tmp_path / "note.png"
    im = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(im).text((40, 100), "Steuerbescheid 2025", fill="black", font=ImageFont.load_default(size=48))
    im.save(img)
    assert "Steuerbescheid" in extract(img, ocr_images=True).text
    assert extract(img, ocr_images=False).text == ""


def test_windows_paths_and_domains():
    assert windows_path("/mnt/d/Daten/Inbox/a b.pdf") == "D:\\Daten\\Inbox\\a b.pdf"
    assert windows_path("/home/me/file.pdf") is None
    assert domain("https://www.lms.example.edu/course/view.php?id=1") == "lms.example.edu"
    assert domain("about:internet") is None and domain("file:///C:/x") is None and domain("") is None


def test_referrer_preferred_and_full_url_dropped():
    z = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://www.lms.example.edu/x?id=1\r\nHostUrl=https://cdn.example.org/f.pdf\r\n"
    assert parse_zone_identifier(z) == "lms.example.edu"
    assert parse_zone_identifier("[ZoneTransfer]\nZoneId=3\nHostUrl=https://files.example.org/a?token=s3cret") == "files.example.org"
    assert parse_zone_identifier("[ZoneTransfer]\nZoneId=3") is None


def test_download_sources_batches_and_tolerates_bad_output(monkeypatch):
    import classifiles.source as src

    monkeypatch.setattr(src, "BATCH", 2)
    calls = []

    def fake(script, stdin):
        paths = json.loads(stdin)
        calls.append(paths)
        if len(calls) == 2:
            return "not json"
        return json.dumps({p: "[ZoneTransfer]\nHostUrl=https://www.example.org/x" for p in paths if "dl" in p})

    files = [Path(f"/mnt/d/In/{n}.pdf") for n in ("dl1", "x", "dl2", "y", "dl3")]
    got = download_sources(files + [Path("/tmp/local.pdf")], runner=fake)
    assert [len(c) for c in calls] == [2, 2, 1]  # the /tmp file is never sent
    assert got == {files[0]: "example.org", files[4]: "example.org"}  # batch 2 failed, skipped


def test_no_lookup_for_files_outside_windows_drives():
    assert download_sources([Path("/tmp/a.pdf")], runner=lambda s, i: pytest.fail("called")) == {}


def test_scan_adds_source_to_evidence_and_query(service, tree, monkeypatch):
    target = tree["inbox"] / "invoice_march.txt"
    monkeypatch.setattr("classifiles.service.download_sources", lambda files: {target: "lms.example.edu"})
    _, rows = service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "invoice_march.txt")
    assert r["evidence"]["meta"]["source"] == "lms.example.edu"
    from classifiles.extract.base import Evidence

    ev = Evidence(**{k: v for k, v in r["evidence"].items() if k in Evidence.__dataclass_fields__})
    assert "source: lms.example.edu" in query_text(ev)


def test_source_lookup_can_be_switched_off(service, tree, monkeypatch):
    service.settings.use_download_source = False
    monkeypatch.setattr("classifiles.service.download_sources", lambda files: pytest.fail("called"))
    service.scan(tree["inbox"])


def test_settings_validation(service, tmp_path):
    from fastapi.testclient import TestClient

    from classifiles_api.app import create_app

    client = TestClient(create_app(service, config_path=str(tmp_path / "cfg.yaml")))
    assert client.patch("/api/settings", json={"ocr_pdf_pages": 9}).status_code == 400
    s = client.patch("/api/settings", json={"ocr_pdf_pages": 1, "ocr_images": False, "use_download_source": False}).json()
    assert (s["ocr_pdf_pages"], s["ocr_images"], s["use_download_source"]) == (1, False, False)
