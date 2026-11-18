"""Suggested file names: computing them for a proposal, picture/video hints, the language-model choice."""

from __future__ import annotations

from pathlib import Path, PurePath

from .. import llm, naming
from ..store import now
from .base import ServiceBase
from .helpers import (
    log,
)


class NamesMixin(ServiceBase):
    def _is_media(self, name: str, kind: str, evidence: dict) -> bool:
        """Photos and videos named by suggest_media (screenshots and pictures with text are documents)."""
        return kind in naming.MEDIA_KINDS and not naming.in_scope(kind, name, evidence.get("text", ""),
                                                                  evidence.get("meta") or {})

    def _media_hints(self, ev, image_vector=None) -> dict | None:
        """Title hints for a photo/video: what it shows, the GPS town, the source app. Best effort."""
        s = self.settings
        if not (s.name_suggestions and s.name_media) or not self._is_media(ev.name, ev.kind, ev.to_dict()):
            return None
        hints: dict = {}
        app = naming.source_app(PurePath(ev.name).stem)
        if app:
            hints["app"] = app
        if s.name_media_labels:
            try:
                vec = image_vector
                if vec is None:
                    emb = self.image_index().embedder
                    if ev.kind == "video":
                        from ..extract.video import middle_frame

                        frame = middle_frame(Path(ev.path))
                        vec = emb.encode_images([frame])[0] if frame is not None else None
                    else:
                        vecs, ok = emb.encode_paths([Path(ev.path)])
                        vec = vecs[0] if ok else None
                if vec is not None:
                    label, share = self.labeller().label(vec)
                    if label:
                        hints.update(label=label, label_share=round(share, 3))
            except Exception:  # noqa: BLE001 - a name hint must never stop a scan
                log.exception("picture recognition for a file name failed")
        gps = (ev.meta or {}).get("gps")
        if s.name_media_place and gps:
            place = self.places().nearest(*gps)
            if place:
                hints["place"] = place
        return hints

    def labeller(self):
        from ..labels import Labeller

        if self._labeller is None or self._labeller.model != self.settings.image_model:
            self._labeller = Labeller(self.store, self.settings.image_model, threads=self.settings.cpu_threads)
        return self._labeller

    def places(self):
        from ..places import Places

        if self._places is None:
            self._places = Places()
        return self._places

    def _name_fields(self, name: str, kind: str, evidence: dict, target: str | None, subpath: str | None,
                     type_: str | None = None, subject: str | None = None, hints: dict | None = None) -> dict:
        """suggested_name / name_reason for a file going to `target`/`subpath` (none for Delete)."""
        none = {"suggested_name": None, "name_reason": None}
        if not self.settings.name_suggestions or not target:
            return none
        try:
            if self.category(target).action == "delete":
                return none
        except ValueError:
            return none
        if self._is_media(name, kind, evidence):
            if not self.settings.name_media:
                return none
            s = naming.suggest_media(name, kind, evidence.get("meta") or {}, subpath or "", hints,
                                     self.settings.name_transliterate)
        elif kind not in naming.DOC_KINDS and kind not in naming.MEDIA_KINDS:
            s = naming.suggest_cleanup(name)  # archives, installers, code …: only "(1)" and the like
        else:
            s = naming.suggest(name, kind, evidence.get("text", ""), evidence.get("meta") or {}, subpath or "",
                               self.settings.name_transliterate, type_, subject)
        return {"suggested_name": s.name, "name_reason": s.reason} if s else none

    def _renamed(self, p: dict, target: str | None, subpath: str | None) -> dict:
        """Recomputed name fields after the category or folder changed. A chosen suggestion follows
        the new suggestion; a name typed by the user stays."""
        llm_choice = p.get("name_llm") or {}
        fields = self._name_fields(p["name"], p["kind"], p["evidence"], target, subpath, llm_choice.get("type"),
                                   llm_choice.get("subject") if llm_choice.get("subject_where") != "folder" else None,
                                   p.get("name_hints"))
        if p.get("new_name") and p["new_name"] == p.get("suggested_name"):
            fields["new_name"] = fields["suggested_name"]
        elif fields["suggested_name"] is None and p.get("new_name") == p.get("suggested_name"):
            fields["new_name"] = None
        return fields

    @staticmethod
    def _typed_name(name: str, original: str) -> str | None:
        """A name typed in Review: '' keeps the original; without an extension, the original's is added."""
        name = name.strip()
        if not name or name == original:
            return None
        n = naming.valid_name(name)
        if not PurePath(n).suffix and PurePath(original).suffix:
            n += PurePath(original).suffix.lower()
        return n

    def name_candidates(self, proposals: list[dict]) -> list[dict]:
        """Proposals whose name the language model helps with: in scope, readable text, not a
        duplicate or Delete (also when the rules found no name: the model may find the type);
        at most llm_max_files."""
        out = []
        for p in proposals:
            ev = p["evidence"]
            if p["source"] == "duplicate" or p["status"] == "moved" or len(ev.get("text") or "") < llm.MIN_TEXT:
                continue
            if not naming.in_scope(p["kind"], p["name"], ev.get("text", ""), ev.get("meta") or {}):
                continue
            try:
                if self.category(p["final_target"]).action == "delete":
                    continue
            except ValueError:
                continue
            out.append(p)
            if len(out) >= self.settings.llm_max_files:
                break
        return out

    def _ask_and_record_name(self, client, p: dict) -> None:
        from ..extract.base import Evidence

        ev_d = p["evidence"]
        ev = Evidence.from_dict(ev_d)
        rule_type, _ = naming.detect_type(p["name"], ev.text)
        types = llm.type_options(p["name"], ev.text, rule_type)
        p_type = llm.ask_rotated(client, lambda order: llm.build_type_prompt(ev, order), types,
                                 self.settings.llm_rotations)
        best_type = max(p_type, key=p_type.__getitem__)
        t = None if best_type == llm.OTHER else best_type
        cands = naming.subject_candidates(PurePath(p["name"]).stem, ev.text, p["subpath"] or "",
                                          (ev_d.get("meta") or {}).get("source"), t)[:5]
        subject = where = None
        if cands:
            names = [c for c, _ in cands] + [llm.NONE]
            word = naming.TYPE_WORDS[t][0] if t else None
            p_subj = llm.ask_rotated(client, lambda order: llm.build_subject_prompt(ev, order, word), names,
                                     self.settings.llm_rotations)
            best = max(p_subj, key=p_subj.__getitem__)
            if best != llm.NONE:
                subject, where = best, dict(cands)[best]
        choice = {"type": t, "subject": subject, "subject_where": where}
        fields = self._name_fields(p["name"], p["kind"], ev_d, p["final_target"], p["subpath"], t, subject)
        update = {**fields, "name_llm": choice, "updated_at": now()}
        if p.get("new_name") and p["new_name"] == p.get("suggested_name"):
            update["new_name"] = fields["suggested_name"]
        self.store.update("proposals", p["id"], update)
