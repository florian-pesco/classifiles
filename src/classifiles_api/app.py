"""REST API over ClassifilesService. Thin by design: all logic lives in the classifiles package;
the routes (one module per area in routes/) only translate HTTP to service calls."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from classifiles import ClassifilesService, Settings

from .routes import categories, history, index, scans, settings

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(service: ClassifilesService | None = None, config_path: str | None = None) -> FastAPI:
    config_path = config_path or os.environ.get("CLASSIFILES_CONFIG")
    svc = service or ClassifilesService(Settings.load(config_path))
    app = FastAPI(title="classifiles")
    app.state.service = svc
    app.state.config_path = config_path
    for module in (settings, categories, index, scans, history):
        app.include_router(module.router)

    if WEB_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, f"No such endpoint: /{path}")
            # Always revalidate the page itself, so a rebuilt UI shows up on a normal reload.
            # (The assets it references have content hashes in their names and may be cached.)
            return FileResponse(WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app
