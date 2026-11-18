"""Scans and proposals: start a scan, review, bulk review, batches, preview and open files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from classifiles import background

from ..deps import Service, guard, strip
from ..models import AcceptSuggestedRequest, BulkReviewRequest, ReviewRequest, ScanRequest
from ..windows import open_in_windows

router = APIRouter(prefix="/api")


@router.post("/scans")
def start_scan(req: ScanRequest, svc: Service):
    inbox = req.inbox or (svc.settings.inboxes[0] if svc.settings.inboxes else None)
    if not inbox:
        raise HTTPException(400, "No inbox given or configured")
    scan_id = guard(svc.start_scan, inbox, req.max_files, req.only_new)
    # failures are recorded on the scan row (status=failed); scans keep normal priority
    background.run_in_background(svc.run_scan, scan_id, name="classifiles-scan", low_priority=False)
    return svc.get_scan(scan_id)


@router.post("/scans/second-opinion/cancel")
def cancel_second_opinion(svc: Service):
    """Stop the second-opinion step of a running scan (files checked so far keep their hint)."""
    svc.cancel_second_opinion()
    return {"ok": True}


@router.get("/scans")
def scans(svc: Service):
    return svc.list_scans()


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, svc: Service):
    return guard(svc.get_scan, scan_id)


@router.post("/scans/{scan_id}/relearn")
def relearn(scan_id: int, svc: Service):
    return {"relearned": strip(guard(svc.relearn, scan_id))}


@router.get("/scans/{scan_id}/batches")
def scan_batches(scan_id: int, svc: Service):
    guard(svc.get_scan, scan_id)
    return svc.suggest_batches(scan_id)


@router.get("/proposals")
def proposals(svc: Service, scan_id: int | None = None, status: str | None = None):
    return strip(svc.proposals(scan_id, status))


@router.patch("/proposals/{proposal_id}")
def review(proposal_id: int, req: ReviewRequest, svc: Service):
    """The reviewed proposal, plus `relearned_others`: other proposals of the scan that changed
    because of this decision (learn from my decisions)."""
    p = guard(svc.review, proposal_id, req.action, req.folder, req.subpath, req.name, req.use_suggested_name)
    return {**p, "relearned_others": strip(svc.relearn(p["scan_id"]))}


@router.post("/proposals/bulk")
def bulk_review(req: BulkReviewRequest, svc: Service):
    if req.action not in ("accept", "reject", "reset"):
        raise HTTPException(400, "action must be accept, reject or reset")
    out = svc.bulk_review(req.ids, req.action, req.folder, req.subpath, req.use_suggested_name)
    scan_ids = {p["scan_id"] for p in out["updated"]}
    out["relearned"] = strip([c for s in scan_ids for c in svc.relearn(s)])
    strip(out["updated"])
    return out


@router.post("/proposals/accept-suggested")
def accept_suggested(req: AcceptSuggestedRequest, svc: Service):
    return {"accepted": svc.accept_suggested(req.scan_id, req.top_level)}


def _file_of(svc, proposal_id: int, duplicate: bool) -> Path:
    """The proposal's file where it is now, or (duplicate=True) the identical file it duplicates."""
    if duplicate:
        p = guard(svc.get_proposal, proposal_id)
        if not p.get("duplicate_path"):
            raise HTTPException(404, "This file has no recorded duplicate")
        return Path(p["duplicate_path"])
    return guard(svc.current_path, proposal_id)


@router.get("/proposals/{proposal_id}/file")
def proposal_file(proposal_id: int, svc: Service, duplicate: bool = False):
    """The file itself, for preview; duplicate=true: the identical file it duplicates."""
    path = _file_of(svc, proposal_id, duplicate)
    if not path.is_file():
        raise HTTPException(404, f"File not found: {path}")
    return FileResponse(path, content_disposition_type="inline", filename=path.name)


@router.get("/proposals/{proposal_id}/duplicate")
def duplicate_details(proposal_id: int, svc: Service):
    """Both copies side by side, re-checked now: sizes, dates, fingerprints, why this copy."""
    return guard(svc.duplicate_details, proposal_id)


@router.post("/proposals/{proposal_id}/open")
def open_file(proposal_id: int, svc: Service, reveal: bool = False, duplicate: bool = False):
    """Open the file with its Windows default app, or show it in Explorer (reveal=true).
    duplicate=true: the identical file this one duplicates (only that recorded path)."""
    path = _file_of(svc, proposal_id, duplicate)
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    open_in_windows(path, reveal)
    return {"ok": True}
