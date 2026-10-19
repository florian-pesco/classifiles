"""Deterministic rules checked before the model. A matching rule wins with confidence 1.0.

Each rule is a dict with a target ``folder`` and one or more conditions (all must match):
    - ext:   ".exe" or [".exe", ".msi"]       (file extension, case-insensitive)
    - kind:  "installer" or ["audio", "video"] (see classifiles.extract.base.KINDS)
    - name:  "Rechnung|Invoice"                (regex searched in the filename, case-insensitive)
    - glob:  "Screenshot*.png"                 (fnmatch on the filename)
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .extract import Evidence


def _as_list(v) -> list[str]:
    return [v] if isinstance(v, str) else list(v)


@dataclass
class Rule:
    folder: str
    ext: list[str] | None = None
    kind: list[str] | None = None
    name: re.Pattern | None = None
    glob: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        if "folder" not in d:
            raise ValueError(f"Rule needs a 'folder': {d}")
        if not any(k in d for k in ("ext", "kind", "name", "glob")):
            raise ValueError(f"Rule needs at least one condition (ext/kind/name/glob): {d}")
        return cls(
            folder=d["folder"],
            ext=[e.lower() if e.startswith(".") else "." + e.lower() for e in _as_list(d["ext"])] if "ext" in d else None,
            kind=_as_list(d["kind"]) if "kind" in d else None,
            name=re.compile(d["name"], re.I) if "name" in d else None,
            glob=d.get("glob"),
        )

    def matches(self, ev: Evidence) -> bool:
        if self.ext is not None and ev.ext not in self.ext:
            return False
        if self.kind is not None and ev.kind not in self.kind:
            return False
        if self.name is not None and not self.name.search(ev.name):
            return False
        if self.glob is not None and not fnmatch.fnmatch(ev.name.lower(), self.glob.lower()):
            return False
        return True

    def describe(self) -> str:
        parts = []
        if self.ext:
            parts.append("ext " + "/".join(self.ext))
        if self.kind:
            parts.append("kind " + "/".join(self.kind))
        if self.name:
            parts.append(f"name ~ /{self.name.pattern}/")
        if self.glob:
            parts.append(f"glob {self.glob}")
        return " & ".join(parts)


def load_rules(raw: list[dict]) -> list[Rule]:
    return [Rule.from_dict(d) for d in raw]


def first_match(rules: list[Rule], ev: Evidence) -> Rule | None:
    return next((r for r in rules if r.matches(ev)), None)
