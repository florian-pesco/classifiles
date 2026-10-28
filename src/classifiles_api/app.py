"""REST API over ClassifilesService. Thin by design: all logic lives in the classifiles package."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from classifiles import ClassifilesService, Settings
from classifiles.extract.base import KINDS

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class ScanRequest(BaseModel):
    inbox: str | None = None
    max_files: int | None = None  # None = settings default; 0 = all files
    only_new: bool = False


class ReviewRequest(BaseModel):
    action: str  # accept | reject | reset
    folder: str | None = None
    subpath: str | None = None  # folder inside the category; "" = top level; None = keep/suggest


class BulkReviewRequest(BaseModel):
    ids: list[int]
    action: str  # accept | reject | reset
    folder: str | None = None
    subpath: str | None = None


class SettingsPatch(BaseModel):
    inboxes: list[str] | None = None
    threshold: float | None = None
    max_files: int | None = None  # 0 = no limit
    backend: str | None = None
    laya_model: str | None = None
    samples_per_folder: int | None = None
    examples_per_category: int | None = None
    content_per_category: int | None = None
    gpu_memory_fraction: float | None = None
    cpu_threads: int | None = None
    ocr_pdf_pages: int | None = None
    ocr_images: bool | None = None
    use_download_source: bool | None = None
    second_opinion: bool | None = None
    image_similarity: bool | None = None
    image_weight: float | None = None
    images_per_category: int | None = None
    detect_duplicates: bool | None = None
    duplicates_in_sorted: bool | None = None
    llm_model: str | None = None
    llm_max_files: int | None = None
    relearn_in_scan: bool | None = None
    trained_weight: float | None = None


class CategoriesUpdate(BaseModel):
    roots: list[dict]
    categories: list[dict]  # per-category settings: path + name/description/enabled/action
    delete: dict | None = None


class RulesUpdate(BaseModel):
    rules: list[dict]


class ScopeRequest(BaseModel):
    scan_id: int | None = None


class AcceptSuggestedRequest(BaseModel):
    scan_id: int | None = None
    top_level: bool = False  # True: ignore subfolder suggestions, put files in the category folder


def create_app(service: ClassifilesService | None = None, config_path: str | None = None) -> FastAPI:
    config_path = config_path or os.environ.get("CLASSIFILES_CONFIG")
    svc = service or ClassifilesService(Settings.load(config_path))
    app = FastAPI(title="classifiles")
    app.state.service = svc

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))

    def save():
        svc.settings.save(config_path)

    # --- settings ---------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return svc.settings.to_dict()

    @app.patch("/api/settings")
    def patch_settings(patch: SettingsPatch):
        values = patch.model_dump(exclude_none=True)
        if "max_files" in values:
            values["max_files"] = values["max_files"] or None
        if "ocr_pdf_pages" in values and not 0 <= values["ocr_pdf_pages"] <= 5:
            raise HTTPException(400, "ocr_pdf_pages must be between 0 and 5")
        if "image_weight" in values and not 0 <= values["image_weight"] <= 1:
            raise HTTPException(400, "image_weight must be between 0 and 1")
        if "trained_weight" in values and not 0 <= values["trained_weight"] <= 1:
            raise HTTPException(400, "trained_weight must be between 0 and 1")
        if {"image_similarity", "image_weight", "trained_weight"} & set(values):
            svc.reset_decider()
        if "llm_max_files" in values and not 1 <= values["llm_max_files"] <= 1000:
            raise HTTPException(400, "llm_max_files must be between 1 and 1000")
        if "gpu_memory_fraction" in values and not 0.05 <= values["gpu_memory_fraction"] <= 0.9:
            raise HTTPException(400, "gpu_memory_fraction must be between 0.05 and 0.9")
        if {"backend", "laya_model"} & set(values):
            svc.reset_decider()  # reloaded on the next scan
        for k, v in values.items():
            setattr(svc.settings, k, v)
        svc.invalidate()
        save()
        return svc.settings.to_dict()

    # --- categories & rules -------------------------------------------------------------

    def categories_payload():
        return {
            "roots": svc.settings.roots,
            "delete": svc.settings.delete,
            "categories": [c.to_dict() for c in svc.categories()],
            "unreachable_roots": svc.unreachable_roots(),
        }

    @app.get("/api/categories")
    def get_categories(refresh: bool = False):
        if refresh:
            svc.invalidate()
        return guard(categories_payload)

    @app.put("/api/categories")
    def put_categories(req: CategoriesUpdate):
        guard(svc.set_categories, req.roots, _prune(req.roots, req.categories), req.delete)
        save()
        return categories_payload()

    @app.get("/api/rules")
    def get_rules():
        return {"rules": svc.settings.rules, "kinds": sorted(KINDS) + ["other"]}

    @app.put("/api/rules")
    def put_rules(req: RulesUpdate):
        guard(svc.set_rules, req.rules)
        save()
        return get_rules()

    # --- example index ------------------------------------------------------------------

    @app.get("/api/index")
    def index_status():
        return guard(svc.index_status)

    @app.post("/api/index/build")
    def index_build():
        return guard(svc.start_index_build)

    @app.post("/api/index/learn-past")
    def index_learn_past():
        return guard(svc.learn_past_decisions)

    @app.post("/api/index/retrain")
    def index_retrain():
        """Train the classifier again now (background, a few seconds)."""
        svc.schedule_retrain(0)
        return svc.trained_status()

    @app.post("/api/index/cancel")
    def index_cancel():
        return svc.cancel_index_build()

    @app.get("/api/categories/subfolders")
    def subfolders(category: str, path: str = ""):
        """Subfolders inside a category folder (for the path editor). Only inside the category."""
        from classifiles.paths import normalize_subpath

        cat = guard(svc.category, category)
        if cat.path is None:
            return {"path": "", "dirs": []}
        sub = guard(normalize_subpath, path)
        folder = cat.path / sub if sub else cat.path
        if not folder.is_dir():
            return {"path": sub, "dirs": [], "exists": False}
        try:
            dirs = sorted((e.name for e in os.scandir(folder) if e.is_dir() and not e.name.startswith((".", "$"))),
                          key=str.lower)
        except PermissionError:
            dirs = []
        return {"path": sub, "dirs": dirs, "exists": True}

    # --- folder browser -----------------------------------------------------------------

    @app.get("/api/fs")
    def browse(path: str | None = None):
        p = Path(path).expanduser() if path else None
        shortcuts = [s for s in [str(Path.home()), "/mnt/c/Users", *sorted(_drives())] if Path(s).is_dir()]
        if p is None:
            return {"path": None, "parent": None, "dirs": [], "shortcuts": shortcuts}
        if not p.is_dir():
            raise HTTPException(400, f"Not a folder: {p}")
        try:
            dirs = sorted((e.name for e in os.scandir(p) if e.is_dir() and not e.name.startswith((".", "$"))),
                          key=str.lower)
        except PermissionError:
            dirs = []
        parent = str(p.parent) if p.parent != p else None
        return {"path": str(p), "parent": parent, "dirs": dirs, "shortcuts": shortcuts}

    # --- scans & proposals --------------------------------------------------------------

    @app.post("/api/scans")
    def start_scan(req: ScanRequest):
        inbox = req.inbox or (svc.settings.inboxes[0] if svc.settings.inboxes else None)
        if not inbox:
            raise HTTPException(400, "No inbox given or configured")
        scan_id = guard(svc.start_scan, inbox, req.max_files, req.only_new)

        def run():
            try:
                svc.run_scan(scan_id)
            except Exception:
                pass  # recorded on the scan row as status=failed

        threading.Thread(target=run, daemon=True).start()
        return svc.get_scan(scan_id)

    @app.post("/api/scans/second-opinion/cancel")
    def cancel_second_opinion():
        """Stop the second-opinion step of a running scan (files checked so far keep their hint)."""
        svc.cancel_second_opinion()
        return {"ok": True}

    @app.get("/api/scans")
    def scans():
        return svc.list_scans()

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: int):
        return guard(svc.get_scan, scan_id)

    @app.get("/api/proposals")
    def proposals(scan_id: int | None = None, status: str | None = None):
        rows = svc.proposals(scan_id, status)
        for r in rows:
            r["evidence"].pop("path", None)
        return rows

    @app.patch("/api/proposals/{proposal_id}")
    def review(proposal_id: int, req: ReviewRequest):
        """The reviewed proposal, plus `relearned_others`: other proposals of the scan that changed
        because of this decision (learn from my decisions)."""
        p = guard(svc.review, proposal_id, req.action, req.folder, req.subpath)
        return {**p, "relearned_others": _strip(svc.relearn(p["scan_id"]))}

    @app.post("/api/proposals/bulk")
    def bulk_review(req: BulkReviewRequest):
        if req.action not in ("accept", "reject", "reset"):
            raise HTTPException(400, "action must be accept, reject or reset")
        out = svc.bulk_review(req.ids, req.action, req.folder, req.subpath)
        scans = {p["scan_id"] for p in out["updated"]}
        out["relearned"] = _strip([c for s in scans for c in svc.relearn(s)])
        _strip(out["updated"])
        return out

    @app.post("/api/scans/{scan_id}/relearn")
    def relearn(scan_id: int):
        return {"relearned": _strip(guard(svc.relearn, scan_id))}

    @app.get("/api/scans/{scan_id}/batches")
    def scan_batches(scan_id: int):
        guard(svc.get_scan, scan_id)
        return svc.suggest_batches(scan_id)

    @app.post("/api/proposals/accept-suggested")
    def accept_suggested(req: AcceptSuggestedRequest):
        return {"accepted": svc.accept_suggested(req.scan_id, req.top_level)}

    @app.get("/api/proposals/{proposal_id}/file")
    def proposal_file(proposal_id: int, duplicate: bool = False):
        """The file itself, for preview; duplicate=true: the identical file it duplicates."""
        if duplicate:
            p = guard(svc.get_proposal, proposal_id)
            if not p.get("duplicate_path"):
                raise HTTPException(404, "This file has no recorded duplicate")
            path = Path(p["duplicate_path"])
        else:
            path = guard(svc.current_path, proposal_id)
        if not path.is_file():
            raise HTTPException(404, f"File not found: {path}")
        return FileResponse(path, content_disposition_type="inline", filename=path.name)

    @app.get("/api/proposals/{proposal_id}/duplicate")
    def duplicate_details(proposal_id: int):
        """Both copies side by side, re-checked now: sizes, dates, fingerprints, why this copy."""
        return guard(svc.duplicate_details, proposal_id)

    @app.post("/api/proposals/{proposal_id}/open")
    def open_file(proposal_id: int, reveal: bool = False, duplicate: bool = False):
        """Open the file with its Windows default app, or show it in Explorer (reveal=true).
        duplicate=true: the identical file this one duplicates (only that recorded path)."""
        if duplicate:
            p = guard(svc.get_proposal, proposal_id)
            if not p.get("duplicate_path"):
                raise HTTPException(404, "This file has no recorded duplicate")
            path = Path(p["duplicate_path"])
        else:
            path = guard(svc.current_path, proposal_id)
        if not path.exists():
            raise HTTPException(404, f"File not found: {path}")
        _open_in_windows(path, reveal)
        return {"ok": True}

    # --- applying, history, trash -------------------------------------------------------

    @app.post("/api/apply/plan")
    def plan(req: ScopeRequest):
        return svc.plan_apply(req.scan_id)

    @app.post("/api/apply")
    def apply(req: ScopeRequest):
        return svc.apply(req.scan_id)

    @app.get("/api/quality")
    def quality(rerun: bool = False):
        """How right proposals were, measured against your Review decisions (aggregates only)."""
        from classifiles.evaluate import evaluate_decisions

        return guard(evaluate_decisions, svc, rerun)

    @app.get("/api/batches")
    def batches():
        return svc.batches()

    @app.get("/api/moves")
    def moves():
        return svc.moves()

    @app.post("/api/undo")
    def undo(batch_id: str | None = None):
        return svc.undo(batch_id)

    @app.get("/api/trash")
    def trash():
        return svc.trash()

    @app.post("/api/trash/empty")
    def empty_trash():
        return svc.empty_trash()

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


def _strip(rows: list[dict]) -> list[dict]:
    """Proposals as sent to the browser: without the absolute path inside the evidence."""
    for r in rows:
        r.get("evidence", {}).pop("path", None)
    return rows


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


def _drives() -> list[str]:
    try:
        return [f"/mnt/{d}" for d in os.listdir("/mnt") if len(d) == 1]
    except OSError:
        return []


# The Windows path arrives on stdin, so spaces, quotes or & in names can't break the command line
# (passing it as an argument made explorer.exe fall back to "Documents" for paths with spaces).
_PS_OPEN = r"""
[Console]::InputEncoding = [Text.Encoding]::UTF8
$p = [Console]::In.ReadLine()
if ($env:CLASSIFILES_REVEAL -eq '1') { Start-Process explorer.exe -ArgumentList ('/select,"' + $p + '"') }
else { Invoke-Item -LiteralPath $p }
"""


def _open_in_windows(path: Path, reveal: bool) -> None:
    win = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    wslenv = ":".join(x for x in (os.environ.get("WSLENV", ""), "CLASSIFILES_REVEAL") if x)
    env = {**os.environ, "CLASSIFILES_REVEAL": "1" if reveal else "0", "WSLENV": wslenv}
    proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_OPEN],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd="/mnt/c", env=env)
    proc.stdin.write((win + "\n").encode("utf-8"))
    proc.stdin.close()
