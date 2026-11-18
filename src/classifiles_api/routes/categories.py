"""Categories, rules, subfolders inside a category, and the folder picker."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from classifiles.extract.base import KINDS

from ..deps import Service, guard, save_settings
from ..models import CategoriesUpdate, RulesUpdate
from ..windows import drives

router = APIRouter(prefix="/api")


def categories_payload(svc) -> dict:
    return {
        "roots": svc.settings.roots,
        "delete": svc.settings.delete,
        "categories": [c.to_dict() for c in svc.categories()],
        "unreachable_roots": svc.unreachable_roots(),
    }


@router.get("/categories")
def get_categories(svc: Service, refresh: bool = False):
    if refresh:
        svc.invalidate()
    return guard(categories_payload, svc)


@router.put("/categories")
def put_categories(req: CategoriesUpdate, svc: Service, request: Request):
    guard(svc.set_categories, req.roots, _prune(req.roots, req.categories), req.delete)
    save_settings(request)
    return categories_payload(svc)


@router.get("/rules")
def get_rules(svc: Service):
    return {"rules": svc.settings.rules, "kinds": [*sorted(KINDS), "other"]}


@router.put("/rules")
def put_rules(req: RulesUpdate, svc: Service, request: Request):
    guard(svc.set_rules, req.rules)
    save_settings(request)
    return get_rules(svc)


@router.get("/categories/subfolders")
def subfolders(svc: Service, category: str, path: str = ""):
    """Subfolders inside a category folder (for the path editor). Only inside the category."""
    from classifiles.paths import normalize_subpath

    cat = guard(svc.category, category)
    if cat.path is None:
        return {"path": "", "dirs": []}
    sub = guard(normalize_subpath, path)
    folder = cat.path / sub if sub else cat.path
    if not folder.is_dir():
        return {"path": sub, "dirs": [], "exists": False}
    return {"path": sub, "dirs": _subdirs(folder), "exists": True}


@router.get("/fs")
def browse(path: str | None = None):
    p = Path(path).expanduser() if path else None
    shortcuts = [s for s in [str(Path.home()), "/mnt/c/Users", *sorted(drives())] if Path(s).is_dir()]
    if p is None:
        return {"path": None, "parent": None, "dirs": [], "shortcuts": shortcuts}
    if not p.is_dir():
        raise HTTPException(400, f"Not a folder: {p}")
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "dirs": _subdirs(p), "shortcuts": shortcuts}


def _subdirs(folder: Path) -> list[str]:
    """Visible subfolders, sorted case-insensitively (none if the folder can't be read)."""
    try:
        return sorted((e.name for e in os.scandir(folder) if e.is_dir() and not e.name.startswith((".", "$"))),
                      key=str.lower)
    except PermissionError:
        return []


def _prune(roots: list[dict], entries: list[dict]) -> list[dict]:
    """Drop default values so the config file only holds what you changed. An auto category
    (direct subfolder of a root) with nothing changed needs no entry; a manual one always does."""
    root_keys = {os.path.normpath(os.path.expanduser(r["path"])) for r in roots}
    out = []
    for e in entries:
        path = os.path.normpath(os.path.expanduser(e["path"]))
        kept = {"path": path}
        if e.get("name") and e["name"] != os.path.basename(path):
            kept["name"] = e["name"]
        if (e.get("description") or "").strip():
            kept["description"] = e["description"].strip()
        if e.get("enabled", True) is False:
            kept["enabled"] = False
        if e.get("action", "move") != "move":
            kept["action"] = e["action"]
        is_auto = os.path.dirname(path) in root_keys
        if len(kept) > 1 or not is_auto:
            out.append(kept)
    return out


