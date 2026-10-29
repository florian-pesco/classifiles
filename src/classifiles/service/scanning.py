"""Scanning an inbox: selecting files, reading and classifying them, duplicates, folder suggestions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..classifier import Classifier
from ..duplicates import find_duplicates
from ..examples import category_key
from ..extract import extract
from ..paths import numbered_sibling, suggest_folder_name, suggest_subpath
from ..source import download_sources
from ..store import now
from .base import ServiceBase
from .helpers import (
    SKIP_NAMES,
    SKIP_SUFFIXES,
    _vec_bytes,
)


class ScanningMixin(ServiceBase):
    def list_inbox(self, inbox: str | Path) -> list[Path]:
        """Classifiable files directly in the inbox, newest first."""
        inbox = Path(inbox).expanduser()
        if not inbox.is_dir():
            raise FileNotFoundError(f"Inbox does not exist: {inbox}")
        files = [
            p for p in inbox.iterdir()
            if p.is_file() and not p.name.startswith(".")
            and p.suffix.lower() not in SKIP_SUFFIXES and p.name.lower() not in SKIP_NAMES
        ]
        files.sort(key=lambda p: (-p.stat().st_mtime, p.name))
        return files

    def select_files(self, inbox: str | Path, max_files: int | None = None, only_new: bool = False) -> list[Path]:
        """The files a scan will classify: newest first, optionally skipping already-classified
        files (any earlier proposal for the same path), capped at max_files."""
        files = self.list_inbox(inbox)
        if only_new:
            seen = {r["path"] for r in self.store.query("SELECT DISTINCT path FROM proposals")}
            files = [p for p in files if str(p) not in seen]
        if max_files is not None:
            if max_files < 1:
                raise ValueError("max_files must be at least 1")
            files = files[:max_files]
        return files

    def start_scan(self, inbox: str | Path, max_files: int | None = None, only_new: bool = False) -> int:
        """Create a scan record; call run_scan(id) to classify (possibly in a background thread).

        max_files defaults to settings.max_files; pass 0 to scan all files regardless."""
        inbox = str(Path(inbox).expanduser().resolve())
        self.check_ready()
        if max_files is None:
            max_files = self.settings.max_files
        max_files = max_files or None
        files = self.select_files(inbox, max_files, only_new)
        return self.store.insert("scans", {
            "inbox": inbox, "target_root": ", ".join(r["path"] for r in self.settings.roots),
            "backend": self.settings.backend, "threshold": self.settings.threshold,
            "status": "running", "max_files": max_files, "only_new": int(only_new),
            "inbox_total": len(self.list_inbox(inbox)), "total": len(files), "processed": 0,
            "created_at": now(),
        })

    def run_scan(self, scan_id: int, on_progress: Callable[[int, int, dict], None] | None = None) -> list[dict]:
        scan = self.get_scan(scan_id)
        try:
            clf = self.classifier(scan["inbox"])
            files = self.select_files(scan["inbox"], scan["max_files"], bool(scan["only_new"]))
            self.store.update("scans", scan_id, {"total": len(files)})
            sources = download_sources(files) if self.settings.use_download_source else {}
            out, candidates = [], []
            for i, path in enumerate(files, 1):
                try:
                    proposal, candidate = self._classify_file(scan_id, clf, path, scan["threshold"],
                                                              sources.get(path))
                except FileNotFoundError:  # moved or deleted since the scan started
                    proposal = None
                else:
                    assert proposal is not None
                    out.append(proposal)
                    if candidate:
                        candidates.append(candidate)
                self.store.update("scans", scan_id, {"processed": i})
                if on_progress and proposal:
                    on_progress(i, len(files), proposal)
            if candidates:
                self._suggest_new_folders(candidates)
                out = [self.get_proposal(p["id"]) for p in out]
            if self.settings.detect_duplicates and out:
                self._mark_duplicates(scan["inbox"], out)
                out = [self.get_proposal(p["id"]) for p in out]
            if self.run_llm_steps(scan_id, out, scan["threshold"]):
                out = [self.get_proposal(p["id"]) for p in out]
            self.store.update("scans", scan_id, {"status": "done"})
            return out
        except Exception as e:
            self.store.update("scans", scan_id, {"status": "failed", "error": str(e)[:500]})
            raise

    def scan(self, inbox: str | Path, on_progress=None, max_files: int | None = None,
             only_new: bool = False) -> tuple[int, list[dict]]:
        scan_id = self.start_scan(inbox, max_files, only_new)
        return scan_id, self.run_scan(scan_id, on_progress)

    def read_file(self, path: Path, source: str | None = None):
        """Evidence for an inbox file, with the inbox reading options (OCR, download source)."""
        s = self.settings
        ev = extract(path, s.max_text_chars, ocr_pdf_pages=s.ocr_pdf_pages, ocr_images=s.ocr_images)
        if source:
            ev.meta["source"] = source
        return ev

    def _classify_file(self, scan_id: int, clf: Classifier, path: Path, threshold: float,
                       source: str | None = None) -> tuple[dict, dict | None]:
        """Classify and store one file. Returns the proposal and, if a new subfolder might fit,
        a candidate for _suggest_new_folders()."""
        ev = self.read_file(path, source)
        c = clf.classify(ev)
        status = "suggested" if c.target and c.confidence >= threshold else "needs_review"
        subpath, new_folder, container = self._folder_for(c.target, c.neighbours, ev.name)
        hints = self._media_hints(ev, c.image_vector)
        names = self._name_fields(ev.name, ev.kind, ev.to_dict(), c.target, subpath, hints=hints)
        pid = self.store.insert("proposals", {
            **names, "new_name": names["suggested_name"] if self.settings.use_suggested_names else None,
            "name_hints": hints,
            "scan_id": scan_id, "path": str(path), "name": ev.name, "kind": ev.kind, "size": ev.size,
            "evidence": ev.to_dict(), "target": c.target, "confidence": c.confidence, "source": c.source,
            "alternatives": c.alternatives, "reason": c.reason, "status": status,
            "final_target": c.target, "neighbours": c.neighbours, "suggested_subpath": subpath,
            "subpath": subpath, "new_folder": new_folder, "updated_at": now(),
            "qvec": _vec_bytes(c.vector), "ivec": _vec_bytes(c.image_vector),
        })
        candidate = None
        if container and c.vector is not None:
            candidate = {"id": pid, "category": c.target, "parent": subpath, "name": ev.name, "vector": c.vector}
        return self.get_proposal(pid), candidate

    def _mark_duplicates(self, inbox: str, proposals: list[dict]) -> None:
        """Find exact duplicates (within the whole inbox, and against the sorted folders) and
        propose them for Delete. The copy to keep stays a normal proposal."""
        cats = [c for c in self.categories() if c.usable and c.path is not None]
        labels = {category_key(c): c.name for c in cats}
        catalog = self._catalog if self.settings.duplicates_in_sorted else None
        dups, hashes = find_duplicates(self.list_inbox(inbox), catalog, labels)
        delete = next((c for c in self.categories() if c.source == "delete" and c.usable), None)
        for p in proposals:
            path = Path(p["path"])
            d = dups.get(path)
            if d is None:
                if path in hashes:
                    self.store.update("proposals", p["id"], {"content_hash": hashes[path]})
                continue
            where = "in your folders" if d.kind == "sorted" else "in the inbox"
            values: dict[str, Any] = {"duplicate_of": d.of, "duplicate_kind": d.kind, "duplicate_path": d.path,
                      "suggested_name": None, "new_name": None, "name_reason": None,
                      "content_hash": d.content_hash, "updated_at": now(),
                      "reason": f"identical to {d.of} ({where})"}
            if delete is not None:
                values.update({"target": delete.name, "final_target": delete.name, "confidence": 1.0,
                               "source": "duplicate", "status": "suggested", "subpath": "",
                               "suggested_subpath": "", "new_folder": None,
                               "alternatives": [{"folder": delete.name, "probability": 1.0},
                                                *[a for a in p["alternatives"] if a["folder"] != delete.name][:3]]})
            self.store.update("proposals", p["id"], values)

    def _folder_for(self, target: str | None, neighbours: list[dict], filename: str) -> tuple[str, str | None, bool]:
        """(subpath, new_folder, is_container) for a file in category `target` (see paths.py)."""
        if not target:
            return "", None, False
        subpath, container = suggest_subpath(neighbours, target)
        new_folder = None
        sibling = numbered_sibling(subpath, filename)
        if sibling:
            cat_path = self.category(target).path
            if cat_path is not None and (cat_path / sibling).is_dir():
                subpath = sibling  # e.g. "Econ 2" exists already: that's the folder
            elif cat_path is not None:
                new_folder = sibling  # e.g. "Econ 2" doesn't exist yet: propose it ...
                subpath = sibling.rpartition("/")[0]  # ... and default to the shared parent folder
            container = False
        return subpath, new_folder, container

    def _suggest_new_folders(self, candidates: list[dict], min_similarity: float = 0.8) -> None:
        """Propose a new subfolder for files whose similar files are spread over many subfolders.
        Similar inbox files of the same scan (same category and parent folder) are grouped and get
        one shared name from their common words. If that folder already exists, it is suggested
        as a normal subfolder instead."""
        import numpy as np

        groups: dict[tuple[str, str], list[dict]] = {}
        for cand in candidates:
            groups.setdefault((cand["category"], cand["parent"]), []).append(cand)
        for (category, parent), members in groups.items():
            try:
                cat_path = self.category(category).path
            except ValueError:
                continue
            if cat_path is None:
                continue
            clusters: list[list[dict]] = []
            for m in members:  # greedy: join the first cluster whose seed is similar enough
                for cl in clusters:
                    if float(np.dot(cl[0]["vector"], m["vector"])) >= min_similarity:
                        cl.append(m)
                        break
                else:
                    clusters.append([m])
            for cl in clusters:
                name = suggest_folder_name([m["name"] for m in cl])
                if not name:
                    continue
                sub = f"{parent}/{name}" if parent else name
                if (cat_path / sub).is_dir():  # exists already: just suggest it
                    for m in cl:
                        self.store.update("proposals", m["id"], {"suggested_subpath": sub, "subpath": sub})
                else:
                    for m in cl:
                        self.store.update("proposals", m["id"], {"new_folder": sub})

    def get_scan(self, scan_id: int) -> dict:
        scan = self.store.one("SELECT * FROM scans WHERE id = ?", (scan_id,))
        if not scan:
            raise KeyError(f"No scan {scan_id}")
        return scan

    def list_scans(self, limit: int = 50) -> list[dict]:
        """Scans, newest first, with counts per proposal status."""
        scans = self.store.query("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))
        counts = self.store.query("SELECT scan_id, status, COUNT(*) AS n FROM proposals GROUP BY scan_id, status")
        by_scan: dict[int, dict] = {}
        for r in counts:
            by_scan.setdefault(r["scan_id"], {})[r["status"]] = r["n"]
        for s in scans:
            s["counts"] = by_scan.get(s["id"], {})
        return scans

    def latest_scan_id(self) -> int | None:
        row = self.store.one("SELECT id FROM scans ORDER BY id DESC LIMIT 1")
        return row["id"] if row else None
