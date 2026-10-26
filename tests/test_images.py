"""Image similarity with a fake image model (colour histogram), so no download and no GPU."""

import numpy as np
import pytest
from PIL import Image

from classifiles.image_index import ImageIndex, is_image
from test_examples import FakeEmbedder


class FakeImageEmbedder:
    """Vector = normalised mean colour + a bit of structure: same-coloured pictures are similar."""

    model_name = "fake-image"
    loaded = True

    def encode_paths(self, paths):
        vecs, ok = [], []
        for i, p in enumerate(paths):
            try:
                with Image.open(p) as im:
                    a = np.asarray(im.convert("RGB").resize((8, 8)), dtype=np.float32).reshape(-1) / 255
            except Exception:  # noqa: BLE001
                continue
            v = np.concatenate([a, [1.0]])
            vecs.append(v / np.linalg.norm(v))
            ok.append(i)
        return (np.stack(vecs).astype(np.float32) if vecs else np.zeros((0, 193), np.float32)), ok

    def unload(self):
        pass


def picture(path, colour, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


@pytest.fixture
def img_service(service, tree):
    from classifiles.examples import ExampleIndex

    service.settings.backend = "examples"
    service.settings.embedding_model = "fake"
    service.settings.image_similarity = True
    service.settings.image_model = "fake-image"
    service._index = ExampleIndex(service.store, FakeEmbedder(), per_category=100, content_per_category=5)
    service._image_index = ImageIndex(service.store, FakeImageEmbedder(), per_category=50, temperature=0.05)
    service.reset_decider()
    # sorted pictures: Photos are green, Work has blue diagrams; names carry no hint
    for i in range(4):
        picture(tree["root"] / "Photos" / "2025" / f"IMG_{i:04d}.jpg", (40, 160 + i, 60))
        picture(tree["root"] / "Work" / "diagrams" / f"IMG_{i + 10:04d}.png", (30, 60, 200 + i))
    return service


def test_is_image():
    assert is_image("a.JPG") and is_image("b.webp") and not is_image("c.pdf")


def test_image_index_build_and_incremental(img_service):
    idx = img_service.image_index()
    st = idx.build(img_service.categories(refresh=True))
    assert st["state"] == "done" and idx.size == 8
    assert idx.build(img_service.categories(refresh=True))["added"] == 0  # unchanged: nothing re-encoded
    counts = img_service.index_status()["images"]["per_category"]
    assert counts["Photos"] == 4 and counts["Work"] == 4


def test_pictures_are_classified_by_what_they_look_like(img_service, tree):
    img_service.example_index().build(img_service.categories(refresh=True))
    img_service.image_index().build(img_service.categories(refresh=True))
    picture(tree["inbox"] / "IMG_9999.jpg", (42, 162, 61))      # green -> Photos
    picture(tree["inbox"] / "IMG_8888.png", (31, 61, 203))      # blue  -> Work
    _, rows = img_service.scan(tree["inbox"])
    by = {r["name"]: r for r in rows}
    assert by["IMG_9999.jpg"]["target"] == "Photos" and "looks like: Photos/2025/" in by["IMG_9999.jpg"]["reason"]
    assert by["IMG_8888.png"]["target"] == "Work"
    assert by["IMG_9999.jpg"]["subpath"] == "2025" and by["IMG_8888.png"]["subpath"] == "diagrams"


def test_no_image_examples_means_text_only(img_service, tree):
    img_service.example_index().build(img_service.categories(refresh=True))  # images not built
    picture(tree["inbox"] / "IMG_9999.jpg", (42, 162, 61))
    _, rows = img_service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "IMG_9999.jpg")
    assert "looks like" not in r["reason"]


def test_switch_off(img_service, tree):
    img_service.example_index().build(img_service.categories(refresh=True))
    img_service.image_index().build(img_service.categories(refresh=True))
    img_service.settings.image_similarity = False
    img_service.reset_decider()
    picture(tree["inbox"] / "IMG_9999.jpg", (42, 162, 61))
    _, rows = img_service.scan(tree["inbox"])
    assert "looks like" not in next(r for r in rows if r["name"] == "IMG_9999.jpg")["reason"]


def test_applied_pictures_are_learned_and_forgotten(img_service, tree, monkeypatch):
    monkeypatch.setattr("classifiles.service.threading.Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    img_service.example_index().build(img_service.categories(refresh=True))
    img_service.image_index().build(img_service.categories(refresh=True))
    picture(tree["inbox"] / "IMG_7777.jpg", (200, 30, 30))
    scan_id, rows = img_service.scan(tree["inbox"])
    r = next(r for r in rows if r["name"] == "IMG_7777.jpg")
    img_service.review(r["id"], "accept", "Photos", subpath="")
    img_service.apply(scan_id)
    learned = img_service.store.query("SELECT relpath FROM image_examples WHERE relpath = 'IMG_7777.jpg'")
    assert learned
    img_service.undo()
    assert not img_service.store.query("SELECT relpath FROM image_examples WHERE relpath = 'IMG_7777.jpg'")


def test_index_build_also_builds_images(img_service):
    img_service.start_index_build()
    img_service._index._thread.join(30)
    st = img_service.index_status()["images"]
    assert st["enabled"] and st["state"] == "done" and st["total"] == 8
