from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

KINDS = {
    "pdf": {".pdf"},
    "document": {".docx", ".odt", ".rtf", ".doc"},
    "spreadsheet": {".xlsx", ".xlsm", ".xls", ".ods"},
    "presentation": {".pptx", ".ppt", ".odp", ".key"},
    "text": {".txt", ".md", ".log", ".rst", ".tex"},
    "data": {".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ics", ".vcf"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".rs", ".go",
             ".rb", ".php", ".sh", ".ps1", ".bat", ".sql", ".html", ".css", ".ipynb", ".r", ".m"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".svg"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"},
    "installer": {".exe", ".msi", ".deb", ".rpm", ".dmg", ".pkg", ".appimage", ".apk", ".msix", ".snap"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv"},
    "ebook": {".epub", ".mobi", ".azw3"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
    "disk-image": {".iso", ".img", ".vhd", ".vhdx"},
    "torrent": {".torrent"},
}
_EXT_TO_KIND = {ext: kind for kind, exts in KINDS.items() for ext in exts}


def kind_of(path: Path) -> str:
    return _EXT_TO_KIND.get(path.suffix.lower(), "other")


@dataclass
class Evidence:
    path: str
    name: str
    ext: str
    kind: str
    mime: str
    size: int
    modified: str
    text: str = ""
    meta: dict = field(default_factory=dict)

    def to_state(self) -> str:
        """Plain-text description of the file for the decision model."""
        lines = [f"Filename: {self.name}", f"File type: {self.kind} ({self.ext or 'no extension'})",
                 f"Size: {_human(self.size)}", f"Modified: {self.modified[:10]}"]
        for k, v in self.meta.items():
            if k != "extract_error" and v not in (None, "", [], {}):
                lines.append(f"{k.replace('_', ' ').capitalize()}: {_short(v)}")
        if self.text:
            lines.append(f"Content: {self.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def _short(v, n: int = 200) -> str:
    s = ", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
