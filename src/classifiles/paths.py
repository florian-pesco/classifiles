"""Folder paths inside a category: suggest an existing subfolder, propose a new one, validate input.

A *subpath* is a path relative to the category folder ("" = the category folder itself), always
with "/" separators, e.g. "Lectures/Econ 1".

Suggestion (example backend): the nearest already-sorted files vote with their folders. Every
prefix of a neighbour's folder gets its weight ("Lectures", "Lectures/Econ 1", ...). The
deepest prefix holding at least `share` of the category's total weight wins. That is a folder most
similar files agree on.

New folder: if the suggested folder is a *container* (its similar files are spread over several
of its subfolders, none dominant), the file may belong to a subfolder that doesn't exist yet. Then
a new folder under it is proposed, with a name taken from the file name (or from the common words
of similar inbox files in the same scan). New folders are only created when the user confirms.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

DEFAULT_SHARE = 0.6
CONTAINER_MIN_CHILDREN = 2
CONTAINER_MAX_CHILD_SHARE = 0.5

_INVALID = re.compile(r'[<>:"\\|?*\x00-\x1f]')  # not allowed on Windows drives
_NOISE = {"final", "copy", "kopie", "neu", "new", "draft", "entwurf", "scan", "img", "image", "screenshot",
          "dsc", "file", "document", "doc", "pdf", "version", "v", "the", "der", "die", "das", "und", "and"}
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


# --- suggesting an existing subfolder ---------------------------------------------------

def _prefixes(d: str) -> list[str]:
    parts = [p for p in PurePosixPath(d.replace("\\", "/")).parts if p not in ("", ".")]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def suggest_subpath(neighbours: list[dict], category: str, share: float = DEFAULT_SHARE) -> tuple[str, bool]:
    """(subpath, is_container) for a category from its weighted neighbours.

    is_container: the suggested folder has similar files spread over several of its subfolders
    without a clear favourite, i.e. a new subfolder might fit better."""
    mine = [n for n in neighbours if n["category"] == category]
    total = sum(n["weight"] for n in mine)
    if not mine or total <= 0:
        return "", False
    weight: dict[str, float] = defaultdict(float)
    for n in mine:
        for p in _prefixes(n["dir"]):
            weight[p] += n["weight"]
    best = ""
    for p, w in weight.items():
        if w / total >= share and p.count("/") >= best.count("/") and (len(p) > len(best) or not best):
            best = p
    return best, _is_container(weight, best)


def _is_container(weight: dict[str, float], parent: str) -> bool:
    depth = parent.count("/") + 1 if parent else 0
    children = {p: w for p, w in weight.items()
                if p.count("/") == depth and (p.startswith(parent + "/") if parent else "/" not in p)}
    if len(children) < CONTAINER_MIN_CHILDREN:
        return False
    return max(children.values()) / sum(children.values()) < CONTAINER_MAX_CHILD_SHARE


# --- numbered siblings ("Econ 1" -> "Econ 2", "2025" -> "2026") ---------------------------------

_NUMBERED = re.compile(r"^(?P<stem>.*?)(?P<sep>[\s_-]*)(?P<num>\d+)$")
_YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def numbered_sibling(subpath: str, filename: str) -> str | None:
    """If the suggested folder ends in a number ("Econ 1", "2025", "Order35") and the file name has
    the same word with a different number ("Econ2_Slides.pdf", "Rechnung_2026_01.pdf"), return the
    sibling folder's subpath ("Lectures/Econ 2", "2026"). Otherwise None.

    Similar files can't tell a new course or year from the existing one (they look alike), but the
    number in the name can."""
    if not subpath:
        return None
    parent, _, last = subpath.rpartition("/")
    m = _NUMBERED.match(last)
    if not m:
        return None
    stem, sep, num = m["stem"].strip(), m["sep"], m["num"]
    name = Path(filename).stem
    if stem:
        found = re.search(rf"(?i)(?<![^\W\d_]){re.escape(stem)}[\s_-]*(\d+)", name)
        number = found.group(1) if found else None
    elif len(num) == 4 and _YEAR.fullmatch(num):
        years = _YEAR.findall(name)
        number = years[0] if years else None
    else:
        return None
    if not number or int(number) == int(num):
        return None
    sibling = f"{stem}{sep}{number}" if stem else number
    return f"{parent}/{sibling}" if parent else sibling


# --- naming a new folder ----------------------------------------------------------------

def name_tokens(filename: str) -> list[str]:
    """Meaningful words of a file name: no extension, numbers, dates, hashes, version tags or
    filler words. 'ha05-solution1-1.pdf' -> ['ha', 'solution']."""
    stem = Path(filename).stem
    out = []
    for t in _TOKEN.findall(stem):
        word = re.sub(r"\d+", "", t)  # 'ha05' -> 'ha', 'Econ2' -> 'Econ'
        if len(word) < 2 or word.lower() in _NOISE or re.fullmatch(r"[0-9a-f]{8,}", t.lower()):
            continue
        out.append(word)
    return out


def suggest_folder_name(names: list[str], max_words: int = 3) -> str:
    """Folder name for one file, or for a group of similar files: the words shared by at least two
    and at least half of the names (in the order of the first name). Empty if nothing sensible."""
    token_lists = [name_tokens(n) for n in names if name_tokens(n)]
    if not token_lists:
        return ""
    if len(token_lists) == 1:
        words = token_lists[0]
    else:
        need = max(2, (len(token_lists) + 1) // 2)
        counts: dict[str, int] = defaultdict(int)
        for tl in token_lists:
            for t in {t.lower() for t in tl}:
                counts[t] += 1
        words = [t for t in token_lists[0] if counts[t.lower()] >= need]
    seen, unique = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique.append(w)
    return clean_folder_name(" ".join(unique[:max_words]))


# --- validating -------------------------------------------------------------------------

def clean_folder_name(name: str) -> str:
    name = _INVALID.sub(" ", name.replace("/", " "))
    return re.sub(r"\s+", " ", name).strip(" .")


def normalize_subpath(subpath: str | None) -> str:
    """'/Lectures\\\\Econ 1/' -> 'Lectures/Econ 1'. Raises ValueError for unsafe paths."""
    if not subpath:
        return ""
    parts = [p.strip() for p in subpath.replace("\\", "/").split("/") if p.strip() not in ("", ".")]
    for p in parts:
        if p == ".." or _INVALID.search(p) or p != p.rstrip(" ."):
            raise ValueError(f"Invalid folder name: {p!r}")
    return "/".join(parts)


def split_existing(category_path: Path, subpath: str) -> tuple[str, list[str]]:
    """(existing part, missing folder names) of a subpath inside the category folder."""
    existing, missing = [], []
    current = category_path
    for part in [p for p in subpath.split("/") if p]:
        current = current / part
        if not missing and current.is_dir():
            existing.append(part)
        else:
            missing.append(part)
    return "/".join(existing), missing
