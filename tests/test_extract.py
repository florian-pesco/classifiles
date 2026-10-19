from classifiles.extract import extract


def test_docx_text(tree):
    ev = extract(tree["inbox"] / "meeting_notes.docx")
    assert ev.kind == "document"
    assert "Meeting notes" in ev.text


def test_text_file(tree):
    ev = extract(tree["inbox"] / "invoice_march.txt")
    assert ev.kind == "text" and "39.95" in ev.text


def test_screenshot_detected(tree):
    ev = extract(tree["inbox"] / "Screenshot 2026-10-01.png")
    assert ev.kind == "image"
    assert ev.meta["dimensions"] == "1920x1080"
    assert ev.meta["looks_like"] == "screenshot"


def test_zip_listing(tree):
    ev = extract(tree["inbox"] / "bundle.zip")
    assert ev.kind == "archive" and "readme.txt" in ev.text


def test_installer_and_state(tree):
    ev = extract(tree["inbox"] / "vlc-3.0-win64.exe")
    assert ev.kind == "installer"
    state = ev.to_state()
    assert "Filename: vlc-3.0-win64.exe" in state and "installer" in state


def test_broken_file_does_not_raise(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"not a pdf")
    ev = extract(p)
    assert ev.kind == "pdf" and "extract_error" in ev.meta
