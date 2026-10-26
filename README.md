# classifiles

Sort overflowing inbox folders (e.g. `~/Downloads`) into your existing folder structure. Classifiles
learns from the files you have **already sorted**: a new file is proposed for the category whose
existing files are most similar to it. Anything below the confidence threshold waits for your review.
Nothing changes on disk until you apply it, and everything can be undone.

```
inbox file ─► extract (name, type, metadata, text/OCR snippet)
           ─► rules (exact matches, confidence 1.0)
           ─► nearest already-sorted files (local embedding model) → probability per category
           ─► suggested (≥ threshold) | needs_review  ─► you accept/override/reject ─► move/copy/trash (journaled, undoable)
```

## Privacy and resource use

- **Everything runs on your PC.** File names, paths and contents are never sent anywhere. The
  models (an embedding model for "learn from my folders", optionally Laya) are downloaded once from
  Hugging Face. That download sends no data, and afterwards the models load offline (`HF_HUB_OFFLINE`,
  telemetry disabled). A test checks that classification opens no network connections.
  Text recognition (OCR) for scans and images, and the image model that compares pictures, run
  locally too. The "downloaded from" hint reads
  Windows' local download note via PowerShell and keeps only the website's domain.
- **Gentle on the hardware.** GPU memory is capped (default 25%). Indexing runs in a background thread
  at low CPU priority, in small batches with pauses, is cancellable and incremental, and is bounded by
  per-category caps. Classifying a file takes milliseconds.

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
