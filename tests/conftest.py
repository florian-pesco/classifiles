from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from classifiles import ClassifilesService, Settings
from classifiles.deciders import KeywordDecider
from classifiles.store import Store

FOLDERS = {
    "Finance": ("invoices, bank statements, taxes, receipts", ["Rechnung_2025.pdf", "Kontoauszug_Jan.pdf"]),
    "Work": ("work projects, reports, meeting notes", ["Quarterly_report.docx"]),
    "Software": ("installers, setup programs, drivers", ["setup.exe"]),
    "Photos": ("pictures, screenshots, camera images", ["IMG_0001.jpg"]),
}
DESCRIPTIONS = {k: v[0] for k, v in FOLDERS.items()}


def make_docx(path: Path, text: str) -> None:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    d.save(str(path))


def make_png(path: Path, size=(1920, 1080)) -> None:
    from PIL import Image

    Image.new("RGB", size, "white").save(path)


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    root, inbox = tmp_path / "Documents", tmp_path / "Downloads"
    for name, (_, samples) in FOLDERS.items():
        (root / name).mkdir(parents=True)
        for s in samples:
            (root / name / s).write_bytes(b"x")
    (root / ".hidden").mkdir()
    inbox.mkdir()
    make_docx(inbox / "meeting_notes.docx", "Meeting notes for the project report, action items for work")
    (inbox / "invoice_march.txt").write_text("Invoice receipt: total amount due 39.95 EUR, bank transfer")
    (inbox / "vlc-3.0-win64.exe").write_bytes(b"MZ\x00\x00binary")
    make_png(inbox / "Screenshot 2026-10-01.png")
    with zipfile.ZipFile(inbox / "bundle.zip", "w") as z:
        z.writestr("readme.txt", "hello")
    (inbox / "movie.mp4.crdownload").write_bytes(b"partial")
    return {"root": root, "inbox": inbox}


@pytest.fixture
def settings(tree) -> Settings:
    return Settings(
        roots=[{"path": str(tree["root"])}],
        inboxes=[str(tree["inbox"])],
        threshold=0.6,
        backend="keyword",
        categories=[{"path": str(tree["root"] / k), "description": d} for k, d in DESCRIPTIONS.items()],
        rules=[{"kind": "installer", "folder": "Software"}],
        db_path=":memory:",
        image_similarity=False,  # tests that need it use a fake image model
        trained_weight=0.0,  # tests that need the trained classifier switch it on
        name_media_labels=False, name_media_place=False,  # no real picture model / town download in tests
    )


@pytest.fixture
def service(settings) -> ClassifilesService:
    return ClassifilesService(settings, decider=KeywordDecider(), store=Store(":memory:"))
