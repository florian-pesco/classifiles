"""Settings: read and change (validated, saved to the config file)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from classifiles import Settings

from ..deps import Service, guard, save_settings
from ..models import SettingsPatch

router = APIRouter(prefix="/api")


@router.get("/settings")
def get_settings(svc: Service):
    return svc.settings.to_dict()


@router.patch("/settings")
def patch_settings(patch: SettingsPatch, svc: Service, request: Request):
    values = patch.model_dump(exclude_none=True)
    if "max_files" in values:
        values["max_files"] = values["max_files"] or None
    guard(Settings.from_dict, {**svc.settings.to_dict(), **values})  # validates ranges, unknown keys
    if {"image_similarity", "image_weight", "trained_weight", "backend", "laya_model"} & set(values):
        svc.reset_decider()  # reloaded on the next scan
    for k, v in values.items():
        setattr(svc.settings, k, v)
    svc.invalidate()
    save_settings(request)
    return svc.settings.to_dict()
