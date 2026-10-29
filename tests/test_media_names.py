"""File names for photos and videos: core + title from folder, picture recognition, GPS, app.
Fake models and synthetic files only."""

import struct

import numpy as np
import pytest
from PIL import Image

from classifiles import naming
from classifiles.extract import extract
from classifiles.extract.video import header_meta
from classifiles.labels import LABELS, NULL_PROMPTS, Labeller
from classifiles.places import Places


def test_core_cleanup_and_meaningless_names():
    assert naming.media_core("Oma und Opa (1)") == "Oma_und_Opa"
    assert naming.media_core("Copy of beach - Kopie") == "beach"
    assert naming.media_core("IMG_20250314_103344") == "IMG_20250314_103344"
    assert naming._is_meaningless("3fa85f64e1c94b2a") and naming._is_meaningless("1699876543210")
    assert not naming._is_meaningless("IMG_20250314_103344") and not naming._is_meaningless("Oma_und_Opa")


@pytest.mark.parametrize("name,meta,sub,hints,expected", [
    ("IMG_20250314_103344.jpg", {}, "", {"label": "Strand", "label_share": .6, "place": "Barcelona"},
     "IMG_20250314_103344_Strand_Barcelona.jpg"),
    ("IMG-20250314-WA0012.jpg", {}, "", {"app": "WhatsApp"}, "IMG-20250314-WA0012_WhatsApp.jpg"),
    ("IMG-20250314-WA0012.jpg", {}, "", {"label": "Geburtstag", "label_share": .5, "app": "WhatsApp"},
     "IMG-20250314-WA0012_Geburtstag.jpg"),  # the app only when nothing else is known
    ("Oma und Opa (1).jpg", {}, "", {"label": "Garten", "label_share": .5}, "Oma_und_Opa_Garten.jpg"),
    ("3fa85f64e1c94b2a.jpg", {"taken": "2025:03:14 10:00:00"}, "", {"label": "Hund", "label_share": .7},
     "2025-03-14_Hund.jpg"),
    ("DSC01234.JPG", {}, "Urlaub Spanien 2024", {"label": "Strand", "label_share": .7, "place": "Malaga"},
     "DSC01234_UrlaubSpanien2024_Strand.jpg"),  # at most two title parts: folder, then recognition
    ("Strand Sommer.jpg", {}, "", {"label": "Strand", "label_share": .7}, "Strand_Sommer.jpg"),  # no repeat
])
def test_media_suggestions(name, meta, sub, hints, expected):
    s = naming.suggest_media(name, "image", meta, sub, hints)
    assert s.name == expected


def test_media_reason_and_no_suggestion():
    s = naming.suggest_media("IMG_1.jpg", "image", {}, "", {"label": "Hund", "label_share": .61})
    assert s.reason == "core IMG_1: original name; title Hund: picture recognition (0.61)"
    assert naming.suggest_media("photo.jpg", "image", {}, "", {}) is None  # nothing to add or clean
    assert naming.suggest_media("a.pdf", "pdf") is None
    assert naming.source_app("signal-2025-03-14-101010") == "Signal"
    assert naming.source_app("Screen Recording 2025-03-14") == "Bildschirmaufnahme"


def test_exif_gps(tmp_path):
    path = tmp_path / "gps.jpg"
    im = Image.new("RGB", (32, 32), "green")
    exif = im.getexif()
    exif[271], exif[272] = "Cam", "One"
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (48.0, 8.0, 14.0), 3: "E", 4: (11.0, 34.0, 32.0)})
    im.save(path, exif=exif)
    meta = extract(path, ocr_images=False).meta
    assert meta["gps"] == pytest.approx([48.13722, 11.57556], abs=1e-4) and meta["looks_like"] == "photo"


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def test_mp4_header(tmp_path):
    secs = 3824994600  # 2025-03-14 ... since 1904
    mvhd = _box(b"mvhd", bytes(4) + struct.pack(">II", secs, secs) + bytes(88))
    xyz = _box(b"\xa9xyz", struct.pack(">HH", 18, 0) + b"+48.1372+011.5756/")
    path = tmp_path / "VID_1.mp4"
    path.write_bytes(_box(b"ftyp", b"isom" + bytes(4)) + _box(b"moov", mvhd + _box(b"udta", xyz)) + _box(b"mdat", bytes(64)))
    meta = header_meta(path)
    assert meta["taken"].startswith("2025-03-") and meta["gps"] == [48.1372, 11.5756]
    assert extract(path).meta == meta and extract(path).kind == "video"


def test_nearest_town(tmp_path):
    np.savez_compressed(tmp_path / "cities15000.npz", name=np.array(["Musterstadt", "Beispielhausen"]),
                        lat=np.array([48.14, 52.52], np.float32), lon=np.array([11.58, 13.40], np.float32))
    places = Places(cache_dir=tmp_path, url="http://invalid")
    assert places.nearest(48.13, 11.57) == "Musterstadt"
    assert places.nearest(0.0, 0.0) is None  # nothing within 30 km
    broken = Places(cache_dir=tmp_path / "missing", url="http://invalid.invalid/x.zip")
    assert broken.nearest(48.1, 11.5) is None and broken.error  # offline: no place, no crash


def _fake_labeller(store):
    n = len(LABELS) + len(NULL_PROMPTS)
    calls = []

    def encode(prompts):
        calls.append(len(prompts))
        return np.eye(n, dtype=np.float32), 50.0, -5.0

    return Labeller(store, "fake-image", encode_texts=encode), calls


def test_labeller_scores_and_caches(service):
    lab, _ = _fake_labeller(service.store)
    strand = [w for w, _ in LABELS].index("Strand")
    v = np.zeros(len(LABELS) + len(NULL_PROMPTS), np.float32)
    v[strand] = 1.0
    assert lab.label(v)[0] == "Strand"
    v2 = np.zeros_like(v)
    v2[len(LABELS)] = 1.0  # closest to "a photo": nothing special
    assert lab.label(v2)[0] is None
    again, calls2 = _fake_labeller(service.store)
    assert again.label(v)[0] == "Strand" and calls2 == []  # text side cached in the database


@pytest.fixture
def media_service(service, tree, monkeypatch):
    service.settings.name_media_labels = True
    service.settings.name_media_place = True

    class Lab:
        model = service.settings.image_model

        def label(self, vec):
            return "Strand", 0.8

    class Pl:
        def nearest(self, lat, lon):
            return "Musterstadt"

    service._labeller, service._places = Lab(), Pl()

    class Emb:
        model_name = "fake"

        def encode_paths(self, paths):
            return np.ones((len(paths), 4), np.float32), list(range(len(paths)))

        def encode_images(self, images):
            return np.ones((len(images), 4), np.float32)

    class Idx:
        embedder = Emb()

    monkeypatch.setattr(service, "image_index", lambda: Idx())
    path = tree["inbox"] / "IMG_20250314_103344.jpg"
    im = Image.new("RGB", (40, 30), (200, 180, 120))
    exif = im.getexif()
    exif[271] = "Cam"
    exif.get_ifd(0x8825).update({1: "N", 2: (48.0, 8.0, 0.0), 3: "E", 4: (11.0, 34.0, 0.0)})
    im.save(path, exif=exif)
    return service


def test_scan_names_photos(media_service, tree):
    _, rows = media_service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "IMG_20250314_103344.jpg")
    assert r["name_hints"] == {"label": "Strand", "label_share": 0.8, "place": "Musterstadt"}
    assert r["suggested_name"] == "IMG_20250314_103344_Strand_Musterstadt.jpg"
    shot = next(r for r in rows if r["name"] == "Screenshot 2026-10-01.png")
    assert shot["name_hints"] is None  # screenshots keep the document rules


def test_photo_name_follows_folder_and_switches(media_service, tree):
    (tree["root"] / "Photos" / "Sommerfest").mkdir()
    _, rows = media_service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "IMG_20250314_103344.jpg")
    p = media_service.review(r["id"], "accept", "Photos", subpath="Sommerfest")
    assert p["suggested_name"] == "IMG_20250314_103344_Sommerfest_Strand.jpg"
    media_service.settings.name_media = False
    _, rows = media_service.scan(tree["inbox"])
    assert next(r for r in rows if r["name"] == "IMG_20250314_103344.jpg")["suggested_name"] is None
