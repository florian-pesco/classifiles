"""What every route needs: the service, error mapping, saving settings, and safe proposal output."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from classifiles import ClassifilesService


def get_service(request: Request) -> ClassifilesService:
    return request.app.state.service


Service = Annotated[ClassifilesService, Depends(get_service)]


def guard(fn, *args, **kwargs):
    """Call fn; KeyError -> 404, ValueError / FileNotFoundError -> 400."""
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0] if e.args else e)) from e
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e


def save_settings(request: Request) -> None:
    request.app.state.service.settings.save(request.app.state.config_path)


def strip(rows: list[dict]) -> list[dict]:
    """Proposals as sent to the browser: without the absolute path inside the evidence."""
    for r in rows:
        r.get("evidence", {}).pop("path", None)
    return rows
