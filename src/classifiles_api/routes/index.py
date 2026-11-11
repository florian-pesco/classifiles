"""What the model learned: example index, picture index, duplicate catalog, trained classifier."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import Service, guard

router = APIRouter(prefix="/api/index")


@router.get("")
def index_status(svc: Service):
    return guard(svc.index_status)


@router.post("/build")
def index_build(svc: Service):
    return guard(svc.start_index_build)


@router.post("/learn-past")
def index_learn_past(svc: Service):
    return guard(svc.learn_past_decisions)


@router.post("/retrain")
def index_retrain(svc: Service):
    """Train the classifier again now (background, a few seconds)."""
    svc.schedule_retrain(0)
    return svc.trained_status()


@router.post("/cancel")
def index_cancel(svc: Service):
    return svc.cancel_index_build()
