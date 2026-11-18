"""Search page: query the search index, keep it current, open results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..deps import Service, guard
from ..windows import open_in_windows

router = APIRouter(prefix="/api/search")


class SearchRequest(BaseModel):
    text: str = ""
    groups: list[str] = Field(default_factory=list)
    ext: str = ""
    after: float | None = None
    before: float | None = None
    min_size: int | None = None
    max_size: int | None = None
    location: str | None = None
    match: str = "all"
    sort: str = "relevance"
    limit: int = 100


@router.post("")
def search(req: SearchRequest, svc: Service):
    return guard(svc.search, req.model_dump())


@router.get("/index")
def status(svc: Service):
    return svc.search_status()


@router.post("/index/update")
def update(svc: Service):
    return svc.start_search_update()


@router.post("/index/cancel")
def cancel(svc: Service):
    return svc.cancel_search_update()


@router.post("/{file_id}/open")
def open_result(file_id: int, svc: Service, reveal: bool = False):
    """Open a result with its Windows default app, or show it in Explorer (reveal=true)."""
    path = guard(svc.search_result_path, file_id)
    if not path.exists():
        raise HTTPException(404, f"Not there any more (moved or deleted since the last update): {path}")
    open_in_windows(path, reveal)
    return {"ok": True}
