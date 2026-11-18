"""What the model knows: the example index, picture index, duplicate catalog, trained classifier,
and learning from applied files."""

from __future__ import annotations

from pathlib import Path

from .. import background
from ..duplicates import Catalog
from ..embedder import Embedder
from ..examples import DELETE_KEY, ExampleIndex, category_key, doc_text
from ..image_index import ImageEmbedder, ImageIndex, is_image
from ..store import now
from .base import ServiceBase
from .helpers import (
    _inbox_text,
    _learned_folder,
    _learned_key,
    _quietly,
    log,
)


class LearningMixin(ServiceBase):
    def trained_model(self):
        """The stored trained classifier for the current embedding model, or None."""
        from ..trained import from_row

        model = self.settings.embedding_model
        if self._trained is None or self._trained[0] != model:
            row = self.store.one("SELECT * FROM classifiers WHERE model = ? ORDER BY id DESC LIMIT 1", (model,))
            self._trained = (model, from_row(row) if row else None)
        return self._trained[1]

    def retrain(self) -> dict:
        """Train the classifier on the inbox-style example vectors (CPU, a few seconds) and store it.
        Delete is left to the similar-files vote (see trained.py)."""
        from ..trained import to_row, train

        if not self._train_lock.acquire(blocking=False):
            return self._trained_status  # already training
        try:
            self._trained_status = {"state": "running", "started_at": now()}
            cats = [c for c in self.categories() if c.usable and c.suggest and c.path]
            rows = self.example_index().training_rows({category_key(c) for c in cats})
            m = train(rows, threads=self.settings.cpu_threads)
            if m is None:
                self._trained_status = {"state": "idle", "message": "not enough examples to train yet"}
                return self._trained_status
            model = self.settings.embedding_model
            self.store.execute("DELETE FROM classifiers WHERE model = ?", (model,))
            self.store.insert("classifiers", to_row(m, model, now()))
            self._trained = (model, m)
            self.reset_decider()
            self._trained_status = {"state": "done", "finished_at": now()}
        except Exception as e:  # noqa: BLE001 - background training: recorded in the status
            log.exception("training the classifier failed")
            self._trained_status = {"state": "failed", "error": str(e)[:300]}
        finally:
            self._train_lock.release()
        return self._trained_status

    def schedule_retrain(self, delay: float | None = None) -> None:
        """Retrain in the background after `delay` seconds; a newer request replaces a pending one."""
        if self.settings.backend != "examples":
            return
        self._retrainer.schedule(self.retrain_delay if delay is None else delay)

    def trained_status(self) -> dict:
        m = self.trained_model()
        row = self.store.one("SELECT created_at FROM classifiers WHERE model = ? ORDER BY id DESC LIMIT 1",
                             (self.settings.embedding_model,))
        names = {category_key(c): c.name for c in self.categories()}
        return {**self._trained_status, "weight": self.settings.trained_weight, "trained": m is not None,
                "trained_at": row["created_at"] if row else None,
                "examples": sum(m.n_train.values()) if m else 0,
                "categories": [names.get(k, k) for k in m.keys] if m else []}

    def image_index(self) -> ImageIndex:
        """Image similarity index (lazy; the image model loads only when first used)."""
        s = self.settings
        if self._image_index is None or self._image_index.embedder.model_name != s.image_model:
            embedder = ImageEmbedder(s.image_model, s.gpu_memory_fraction, s.cpu_threads)
            self._image_index = ImageIndex(self.store, embedder, s.images_per_category)
        self._image_index.per_category = s.images_per_category
        return self._image_index

    def example_index(self) -> ExampleIndex:
        """The example index (created lazily; the embedding model loads only when first used)."""
        s = self.settings
        if self._index is None or self._index.embedder.model_name != s.embedding_model:
            if self._index is not None and self._index.building:
                raise ValueError("Wait for the running index build to finish (or cancel it)")
            embedder = Embedder(s.embedding_model, s.gpu_memory_fraction, s.cpu_threads)
            self._index = ExampleIndex(self.store, embedder, s.examples_per_category, s.content_per_category)
        self._index.per_category = s.examples_per_category
        self._index.content_per_category = s.content_per_category
        return self._index

    def start_index_build(self) -> dict:
        """Build/update the example index in the background (low priority, cancellable)."""
        self.check_ready()
        cats = self.categories(refresh=True)
        index = self.example_index()
        # the file list for duplicate detection is refreshed in the same background thread
        catalog = self._catalog_wanted()
        images = self.image_index() if self.settings.image_similarity else None

        def after():  # same background thread, after the text index
            _quietly(self.fill_applied_query_vectors)
            if self.settings.trained_weight > 0:
                self.retrain()
            if catalog:
                self._catalog.build(cats)
            if images is not None:
                images.build(cats)

        index.start_build(cats, on_done=after)
        return self.index_status()

    def _image_status(self) -> dict:
        if not self.settings.image_similarity:
            return {"enabled": False}
        idx = self.image_index()
        counts = idx.counts()
        return {**idx.status, "enabled": True, "model": idx.embedder.model_name, "total": sum(counts.values()),
                "per_category": {c.name: counts.get(category_key(c), 0) for c in self.categories() if c.path}}

    def _catalog_wanted(self) -> bool:
        return self.settings.detect_duplicates and self.settings.duplicates_in_sorted

    def catalog(self) -> Catalog:
        return self._catalog

    def cancel_index_build(self) -> dict:
        self._catalog.cancel()
        if self._image_index is not None:
            self._image_index.cancel()
        if self._index:
            self._index.cancel()
        return self.index_status()

    def index_status(self) -> dict:
        """Build progress and example counts per category name."""
        index = self.example_index()
        counts = index.counts()
        per_category = []
        for c in self.categories():
            n = counts.get(category_key(c), {})
            per_category.append({"name": c.name, "usable": c.usable, "examples": n.get("scan", 0),
                                 "learned": n.get("applied", 0)})
        return {"status": index.status, "model": index.embedder.model_name,
                "model_loaded": index.embedder.loaded, "total": sum(sum(v.values()) for v in counts.values()),
                "categories": per_category, "unlearned": len(self.unlearned_decisions()),
                "images": self._image_status(), "trained": self.trained_status(),
                "catalog": {**self._catalog.status, "files": self._catalog.count(),
                            "enabled": self._catalog_wanted()}}

    def _learn(self, items: list[tuple[str, str, str, str]]) -> None:
        """Add applied files to the example index in the background. Happens with any model as long
        as an index exists, so no decision is lost when switching models later."""
        if not items:
            return
        index = self.example_index()
        if self.settings.backend != "examples" and index.size == 0:
            return  # no index yet: unlearned decisions can be backfilled later (learn_past_decisions)
        background.run_in_background(index.add, items, name="classifiles-learn")

    def _forget(self, items: list[tuple[str, str]]) -> None:
        if items:
            self.example_index().remove(items)

    def unlearned_decisions(self) -> list[tuple[str, str, str, str]]:
        """Applied files (not undone) that are not in the index as learned examples yet, as
        (category_key, relpath, text) built from the stored evidence. No file access needed."""
        rows = self.store.query("""
            SELECT m.dst, m.action, m.category_key, p.name, p.kind, p.evidence FROM moves m
            JOIN proposals p ON p.id = m.proposal_id
            WHERE m.undone_at IS NULL""")
        learned = {(r["category_key"], r["relpath"]) for r in self.store.query(
            "SELECT category_key, relpath FROM examples WHERE origin = 'applied' AND model = ?",
            (self.settings.embedding_model,))}
        out = []
        for r in rows:
            key, rel = _learned_key(r["dst"], r["action"], r["category_key"])
            if (key, rel) in learned:
                continue
            out.append((key, rel, doc_text(_learned_folder(key, rel), r["name"], r["kind"],
                                           r["evidence"].get("text", "")),
                        _inbox_text(r["name"], r["kind"], r["evidence"])))
        return out

    def fill_applied_query_vectors(self) -> int:
        """Inbox-style vectors for learned examples that were added before they existed, from the
        stored evidence (no file access). Returns how many were filled."""
        index = self.example_index()
        missing = {(r["category_key"], r["relpath"]): r["id"] for r in self.store.query(
            "SELECT id, category_key, relpath FROM examples WHERE origin = 'applied' AND model = ? "
            "AND qvector IS NULL", (self.settings.embedding_model,))}
        if not missing:
            return 0
        todo = {}
        for r in self.store.query("""
                SELECT m.dst, m.action, m.category_key, p.name, p.kind, p.evidence FROM moves m
                JOIN proposals p ON p.id = m.proposal_id WHERE m.undone_at IS NULL"""):
            eid = missing.get(_learned_key(r["dst"], r["action"], r["category_key"]))
            if eid is not None:
                todo[eid] = _inbox_text(r["name"], r["kind"], r["evidence"])
        if todo:
            ids = list(todo)
            index.set_query_vectors(list(zip(ids, index.embedder.encode([todo[i] for i in ids], query=True))))
        return len(todo)

    def learn_past_decisions(self) -> dict:
        """Backfill: learn every applied file that was applied without learning (e.g. while another
        model was selected). Uses the stored evidence only; runs in seconds."""
        items = self.unlearned_decisions()
        self.example_index().add(items)
        by: dict[str, int] = {}
        for key, *_ in items:
            kind = "delete" if key == DELETE_KEY else "move/copy"
            by[kind] = by.get(kind, 0) + 1
        return {"learned": len(items), "by_action": by}

    def _learn_images(self, moved: list[dict]) -> None:
        """Applied pictures become image examples of their category (background, if images are on
        and the image index has been built)."""
        if not self.settings.image_similarity:
            return
        items = []
        for m in moved:
            if m["action"] == "delete" or not is_image(m["dst"]):
                continue
            mv = self.store.one("SELECT category_key FROM moves WHERE proposal_id = ? AND undone_at IS NULL "
                                "ORDER BY id DESC LIMIT 1", (m["proposal_id"],))
            key, rel = _learned_key(m["dst"], m["action"], mv["category_key"] if mv else None)
            items.append((key, rel, Path(m["dst"])))
        if not items:
            return
        index = self.image_index()
        if index.size == 0:
            return
        background.run_in_background(index.add, items, name="classifiles-learn")
