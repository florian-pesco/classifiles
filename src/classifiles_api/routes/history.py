"""Applying decisions, quality, history (applied batches, moves), undo and the trash."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import Service, guard
from ..models import ScopeRequest

router = APIRouter(prefix="/api")


@router.post("/apply/plan")
def plan(req: ScopeRequest, svc: Service):
    return svc.plan_apply(req.scan_id)


@router.post("/apply")
def apply(req: ScopeRequest, svc: Service):
    return svc.apply(req.scan_id)


@router.get("/quality")
def quality(svc: Service, rerun: bool = False):
    """How right proposals were, measured against your Review decisions (aggregates only)."""
    from classifiles.evaluate import evaluate_decisions

    return guard(evaluate_decisions, svc, rerun)


@router.get("/batches")
def batches(svc: Service):
    return svc.batches()


@router.get("/moves")
def moves(svc: Service):
    return svc.moves()


@router.post("/undo")
def undo(svc: Service, batch_id: str | None = None):
    return svc.undo(batch_id)


@router.get("/trash")
def trash(svc: Service):
    return svc.trash()


@router.post("/trash/empty")
def empty_trash(svc: Service):
    return svc.empty_trash()
