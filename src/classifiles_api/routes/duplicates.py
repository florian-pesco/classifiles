"""Duplicates page: search a root for identical files, review the groups, keep one copy, trash the rest."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from classifiles.service.cleanup import group_payload

from ..deps import Service, guard
from ..windows import open_in_windows

router = APIRouter(prefix="/api/duplicates")


class SearchRequest(BaseModel):
    root: str | None = None  # None: every category folder


class Decision(BaseModel):
    hash: str
    keep: list[int]  # catalog ids of the copies to keep


class ResolveRequest(BaseModel):
    root: str | None = None
    decisions: list[Decision]


class SuggestedRequest(BaseModel):
    root: str | None = None
    only_clear: bool = True


class FoldersRequest(BaseModel):
    root: str | None = None
    keep_folder: str
    drop_folder: str


class IgnoreRequest(BaseModel):
    hashes: list[str]
    ignored: bool = True


@router.get("")
def overview(svc: Service, root: str | None = None):
    return guard(svc.duplicate_overview, root)


@router.get("/ignored")
def ignored(svc: Service, root: str | None = None):
    return [group_payload(g) for g in guard(svc.duplicate_groups, root, True)]


@router.get("/search")
def search_status(svc: Service):
    return svc.duplicate_search_status()


@router.post("/search")
def search(req: SearchRequest, svc: Service):
    return guard(svc.start_duplicate_search, req.root)


@router.post("/search/cancel")
def cancel(svc: Service):
    return svc.cancel_duplicate_search()


@router.post("/resolve")
def resolve(req: ResolveRequest, svc: Service):
    return guard(svc.resolve_duplicates, [d.model_dump() for d in req.decisions], req.root)


@router.post("/resolve-suggested")
def resolve_suggested(req: SuggestedRequest, svc: Service):
    return guard(svc.resolve_suggested_duplicates, req.root, req.only_clear)


@router.post("/resolve-folders")
def resolve_folders(req: FoldersRequest, svc: Service):
    return guard(svc.resolve_duplicate_folders, req.keep_folder, req.drop_folder, req.root)


@router.post("/ignore")
def ignore(req: IgnoreRequest, svc: Service):
    return svc.ignore_duplicates(req.hashes, req.ignored)


@router.post("/{catalog_id}/open")
def open_copy(catalog_id: int, svc: Service, reveal: bool = False):
    """Open a copy with its Windows default app, or show it in Explorer (reveal=true)."""
    path = guard(svc.duplicate_copy, catalog_id)
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    open_in_windows(path, reveal)
    return {"ok": True}
