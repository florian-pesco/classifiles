# classifiles

Sort overflowing inbox folders (e.g. `~/Downloads`) into your existing folder structure with a local
decision model. Every file gets a proposed folder plus a calibrated confidence. Anything below the
threshold waits for your review. Nothing moves until you accept it, and every move can be undone.

```
inbox file ─► extract (name, type, metadata, text/OCR snippet)
           ─► rules (exact matches, confidence 1.0)
           ─► decision model picks one of the category folders (probability per folder)
           ─► suggested (≥ threshold) | needs_review  ─► you accept/override/reject ─► move (journaled, undoable)
```

Categories are the subfolders of one or more **roots**, plus any **manual** folders, plus a built-in **Delete** (to an undoable trash). All of it is configured in the web UI.

**Docs:** [docs/USAGE.md](docs/USAGE.md) covers setup and running (UI, CLI, library).
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) covers how it works, where to change things, and its limitations.

## Layout

| Path | What |
|---|---|
| `src/classifiles/` | Core library, no web code. Use `ClassifilesService` from other apps (e.g. LifeOS). |
| `src/classifiles/deciders/` | Pluggable decision backends: `laya` (local model), `keyword` (no model). |
| `src/classifiles_api/` | Thin FastAPI layer over the service. It also serves the built web UI. |
| `src/classifiles_cli/` | `classifiles` command-line tool. |
| `web/` | React + Vite review UI. |

## Setup

```bash
uv sync --extra all              # Python 3.12 venv incl. torch (CUDA), laya, OCR, API, CLI
(cd web && npm install && npm run build)
uv run classifiles serve                 # then set up inbox + categories in the UI
```

On first use the Laya model (~0.8 GB) is downloaded into the Hugging Face cache.

## Use

```bash
uv run classifiles folders                    # what the model sees for each folder
uv run classifiles scan                       # classify the first configured inbox
uv run classifiles list --status needs_review
uv run classifiles accept --suggested         # accept everything above threshold
uv run classifiles accept 12 --to Finance     # override one
uv run classifiles reject 13                  # leave it in the inbox
uv run classifiles apply --dry-run            # show the moves
uv run classifiles apply                      # move
uv run classifiles undo                       # move the last batch back

uv run classifiles serve                      # web UI on http://127.0.0.1:8765
```

For frontend development, run `classifiles serve` and `cd web && npm run dev` (Vite proxies `/api`).

## Config

Everything is set in the web UI and saved to `~/.config/classifiles/config.yaml`. The format is described in [docs/USAGE.md](docs/USAGE.md#7-the-config-file-optional).

## As a library

```python
from classifiles import ClassifilesService, Settings

svc = ClassifilesService(Settings.load())
scan_id, proposals = svc.scan("~/Downloads")
svc.accept_suggested(scan_id)
print(svc.plan_apply(scan_id))
svc.apply(scan_id)
```

## Tests

```bash
uv run pytest
```
