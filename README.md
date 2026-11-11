# classifiles

Sort overflowing inbox folders (e.g. `~/Downloads`) into your existing folder structure. Classifiles
learns from the files you have **already sorted**: a new file is proposed for the category whose
existing files are most similar to it. Anything below the confidence threshold waits for your review.
Nothing changes on disk until you apply it, and everything can be undone.

In the web UI, similar files are suggested as batches to review together, every decision you make is
used right away for the other files of the same scan, and files get a cleaner name suggested
(documents `Subject_Type_Detail`, photos and videos a short title).

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
| `src/classifiles/service/` | `ClassifilesService`, one module per topic (scanning, reviewing, applying, learning, …). |
| `src/classifiles/deciders/` | Decision backends: `examples` (learn from my folders), `laya`, `keyword` (no model). |
| `src/classifiles_api/` | Thin FastAPI layer, one router per area. It also serves the built web UI. |
| `src/classifiles_cli/` | `classifiles` command-line tool. |
| `web/` | React + Vite review UI. |
| `docs/` | Usage and architecture. |

## Setup

```bash
uv sync --extra all              # Python 3.12 venv incl. torch (CUDA), laya, OCR, API, CLI
(cd web && npm install && npm run build)
uv run classifiles serve                 # then set up inbox + categories in the UI
```

The models are downloaded once into the Hugging Face cache when first needed (the embedding model
when you first build the index), then everything runs offline.

## Use

Open **http://127.0.0.1:8765** after `classifiles serve`: scan an inbox on the Review page, check the
proposals, apply, undo if needed. The same works in the terminal:

```bash
uv run classifiles scan                       # classify the first configured inbox
uv run classifiles accept --suggested         # accept everything above threshold
uv run classifiles apply                      # move (--dry-run shows the moves first)
uv run classifiles undo                       # move the last batch back
```

The full guide, including all CLI commands and development, is [docs/USAGE.md](docs/USAGE.md).

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
uv run pytest                    # tests
uvx ruff check src tests         # lint
uv run mypy                      # types
```

## Contributing

- **Ground rules:** local only (no file names, paths or contents leave the PC); gentle on the
  hardware (bounded, cancellable, low-priority work, models unloaded when idle); every change to
  files is reviewable before it happens and can be undone.
- **One branch and one pull request per change.** PR titles follow `type(scope): title`, e.g.
  `feat(naming): …`, `fix(duplicates): …`, `docs(usage): …`, `chore(ci): …`.
- **Classification changes** include before/after numbers from `classifiles eval` (aggregates only).
- **No personal data** in code, tests, commits or PRs: tests use synthetic files and invented names.
