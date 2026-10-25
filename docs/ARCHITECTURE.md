# Classifiles: architecture

This document explains how classifiles is built: the moving parts, how data flows through them, where
to change behaviour, and what the current version cannot do. For running it, see [USAGE.md](USAGE.md).

## 1. The big picture

Classifiles takes the files lying in an **inbox** folder (e.g. Downloads). For each file it
proposes one **category** with a confidence. A category is a subfolder of one of your **roots**, a
**manual** category (any folder), or the built-in **Delete**. You review the proposals, and only
accepted files are moved, copied or sent to the trash. Everything is logged and can be undone.

There are three layers. Only the bottom one contains logic.

```
 ┌────────────────────────┐   ┌─────────────────────────┐
 │  Web UI  (web/)        │   │  CLI  (classifiles_cli) │      ← presentation: no logic
 │  React, runs in browser│   │  typer + rich           │
 └───────────┬────────────┘   └────────────┬────────────┘
             │ HTTP/JSON  /api/*           │ direct Python calls
 ┌───────────▼────────────┐                │
 │  REST API              │                │                  ← translation only:
 │  (classifiles_api)     │                │                    HTTP ⇄ service calls
 │  FastAPI               │                │
 └───────────┬────────────┘                │
             │ Python calls                │
 ┌───────────▼─────────────────────────────▼────────────┐
 │  Core library  (src/classifiles)                     │    ← ALL logic lives here
 │  ClassifilesService = the one public entry point     │      (reusable from LifeOS)
 │  taxonomy · extract · rules · deciders · classifier  │
 │  store (SQLite) · mover                              │
 └───────────┬───────────────────────────┬──────────────┘
             │                           │
     ┌───────▼────────┐          ┌───────▼────────────────────────┐
     │ Laya model     │          │ Your files + SQLite DB         │
     │ (PyTorch, GPU) │          │ ~/.local/share/classifiles/... │
     └────────────────┘          └────────────────────────────────┘
```

The rule that keeps this reusable: **the core never imports FastAPI, typer or anything UI-related.**
The CLI and the API are both thin shells around `ClassifilesService`. A future LifeOS can
`import classifiles` and call the same methods the web UI uses.

## 2. Runtime: what actually runs where

Everything runs inside WSL (Ubuntu). There are no background services, Docker containers or separate model servers.

| Piece | What it is | Where it lives | When it runs |
|---|---|---|---|
| Python env | `.venv/` (Python 3.12, PyTorch with CUDA, Laya, FastAPI, …) | `classifiles/.venv/` (~5.8 GB) | Created by `uv sync`. |
| Model weights | Laya English, ModernBERT-large, 421M params, float32 | `~/.cache/huggingface/hub/` (~843 MB) | Downloaded on first use, then read from disk. |
| Model in memory | Loaded by `LayaDecider` **inside the Python process** | GPU memory (~2.5 GB) | Loaded lazily on the first scan, freed when the process exits. |
| Web server | uvicorn running the FastAPI app | process started by `classifiles serve` | Until you press Ctrl+C. |
| Web UI | Static HTML/JS built into `web/dist/` | served by the same FastAPI process | Whenever the server runs. |
| Database | SQLite file | `~/.local/share/classifiles/classifiles.db` (default) | Opened by every CLI/server process. |
| Config | YAML file | `~/.config/classifiles/config.yaml` (default) | Read at startup; the UI's Settings dialog writes it. |

Consequences:
- `classifiles scan` (CLI) starts Python, loads the model (a few seconds), classifies and exits, so the
  model is loaded for every scan. `classifiles serve` keeps the model loaded between scans.
- The CLI and the web server share the same database. You can scan in the UI and `apply` from the CLI.
- The server binds to `127.0.0.1` only. It has no login, so don't expose it on a network.

## 3. Data flow of a scan

```
 inbox folder
     │  service.select_files(): top-level files only, newest first; skips hidden files,
     │  partial downloads (.crdownload/.part/…), desktop.ini/Thumbs.db
     │  optional: only_new (skip files classified in any earlier scan)
     │  optional: max_files (keep only the N newest)
     ▼
 for each file ───────────────────────────────────────────────────────────────┐
     │                                                                        │
     │ 1. extract(path) → Evidence                        extract/__init__.py │
     │    name, extension, kind, size, modified date                          │
     │    + metadata (pages, title, sheet names, dimensions, EXIF, …)         │
     │    + text snippet (≤ max_chars, default 1500)                          │
     │      pdf → pypdf (first 3 pages)   docx/xlsx/pptx → python-docx/…      │
     │      txt/code/csv → raw text       zip/tar → list of contained names   │
     │      images → OCR (RapidOCR) + "screenshot"/"photo" heuristic          │
     │      everything else → name + type only                                │
     │                                                                        │
     │ 2. Classifier.classify(evidence)                     classifier.py     │
     │    a) rules (rules.py): first matching rule wins → confidence 1.0      │
     │    b) otherwise: decider.choose(state, question, options)              │
     │         state    = evidence.to_state()  (plain text, see below)        │
     │         question = "Which folder does this file belong in?"            │
     │         options  = {folder name: description + sample filenames}       │
     │       → probability per folder; best = target, prob = confidence       │
     │                                                                        │
     │ 3. status = "suggested"     if confidence ≥ threshold                  │
     │           = "needs_review"  otherwise                                  │
     │    → INSERT into proposals table (store.py)                            │
     └────────────────────────────────────────────────────────────────────────┘
```

What the model actually sees for one file (`Evidence.to_state()`):

```
Filename: Rechnung_2026-09.docx
File type: document (.docx)
Size: 36 KB
Modified: 2026-10-16
Content: Rechnung Nr. 88123. Vodafone GmbH. Rechnungsbetrag 49,99 EUR. …
```

And what it sees as options (`Category.option_text()`, from `taxonomy.py`):

```
Finance → "invoices, bills, bank statements, taxes, budgets, receipts"
Uni     → "university courses, lectures, seminar papers, exams, thesis"
Photos  → "files about Photos, e.g. IMG_0001.jpg, …"   ← no description configured
```

A category with a description (set on the Categories page) is described by that text alone. One without
falls back to its name plus the 3 most recently modified filenames inside it. The model only gets
a small text budget per folder (see §4), so a short, well-chosen description is the main accuracy
lever.

## 4. The models: how the decision works

There are two real backends (Settings → Model):

- **Learn from my folders** (`examples`, recommended): compares a file with the files you already
  sorted. It needs no descriptions. See §4a.
- **Laya**: decides from the category descriptions only. See §4b. It has never seen your files, so
  it can only match words.

Measured with `classifiles eval -b examples -b laya -n 40` on a real archive (5 categories, ~10k
indexed examples, 200 held-out files):

| | examples | Laya |
|---|---|---|
| accuracy | 78% | 22% |
| right category in top 3 | 100% | 57% |
| mean confidence when right / wrong | 0.90 / 0.65 | 0.32 / 0.33 |
| at threshold 0.8: share suggested / accuracy of those | 67% / 91% | 1% / 50% |

The remaining mistakes are mostly between categories whose contents genuinely overlap (e.g. a
phone backup folder that also holds personal photos). Held-out files have siblings in the index,
so files from a topic you have never sorted before will score lower. Check the confidence.

### 4a. Example index (`examples.py`, `embedder.py`, `deciders/examples.py`)

```
build (background, low priority)                         classify (per inbox file, ~ms)
────────────────────────────────                         ──────────────────────────────
for each category folder:                                 query text = file name + type + snippet
  sample_folder(): ≤ examples_per_category files,         embed (with a retrieval instruction)
    round-robin over subfolders, skipping .git,           cosine similarity to all examples of the
    node_modules, build dirs, hidden files                  categories the model may suggest
  text = "folder: Uni/Vorlesungen/BWL 1                   k = 20 nearest examples
          file: BWL_Folien.pdf  type: pdf                 weight = exp((sim − best) / 0.05)
          content: <first 400 chars, docs only>"          P(category) = its share of the weight
  embed in batches of 32 (+ pause), store float16         reason = the 3 most similar files
  vectors in SQLite `examples` (incremental by mtime)
+ one "description" example per category
```

- **Embedding model:** `Qwen/Qwen3-Embedding-0.6B` (multilingual, 1024 dimensions). It runs locally
  through `sentence-transformers`, in float16 on the GPU, with inputs cut to 256 tokens. The query side
  uses an instruction prompt ("retrieve files stored in the same folder"); the document side doesn't.
- **Resource limits:** `torch.cuda.set_per_process_memory_fraction` (default 0.25), 2 CPU threads,
  `os.nice(10)` for the build thread, a pause between batches, cancellable. Content is read for at
  most `content_per_category` documents per category (never OCR during indexing).
- **Offline:** the model loads with `local_files_only=True` and is downloaded only if missing. Then
  `HF_HUB_OFFLINE=1`; telemetry is off. `tests/test_examples.py` blocks sockets during
  classification to prove no network use.
- **Learning:** after Apply, each moved, copied or trashed file is embedded as an `applied` example of
  its category, in a background thread. This happens with any selected model once an index exists.
  Undo removes the example again. Files applied without learning (e.g. before an index existed) are
  backfilled from their stored evidence by `learn_past_decisions()` (UI: "Learn them now" on the
  index card; CLI: `classifiles index --learn-past`). No file access is needed.
- **Search** is a plain numpy matrix product over all vectors (≈20k × 1024 in float32 ≈ 80 MB of RAM,
  a few ms per query). No vector database is needed at this size.
- **Evaluation** (`evaluate.py`, `classifiles eval`). Output is aggregates only, never file names.
  - **file split:** hide single indexed files and classify them like inbox files (name + content,
    no folder). Optimistic, because their siblings stay in the index.
  - **folder split:** also hide every other file of the same folder. Simulates a topic that isn't
    sorted yet.
  - **`--decisions`:** compare with what you chose in Review. Each proposal records
    `decided_via` (confirm / override / bulk / reject), and the current model can be re-run on the
    stored evidence with the applied files hidden from the index. The History page shows this as
    the Quality card.
- **Reading inbox files** (`service.read_file`) differs from indexing on purpose:
  - **OCR for scanned PDFs** (`extract/pdf.py`): when the first pages have no text layer, the first
    `ocr_pdf_pages` pages (default 2) are rendered with pypdfium2 (≤150 dpi, longest side ≤2000 px)
    and read by RapidOCR (`extract/ocr.py`, shared with image OCR). Indexing never runs OCR.
  - **Download source** (`source.py`): Windows' `Zone.Identifier` stream on downloaded files is
    read through `powershell.exe` (one call per batch of 200; WSL can't read the stream itself).
    Only the domain of the referring page (else of the file's host) is kept, as
    `evidence.meta.source`, and added to the query text ("source: moodle.tum.de"). Skipped silently
    without PowerShell or for files that aren't on a Windows drive.
- **Safety guard:** `check_ready()` refuses scans and index builds when a root isn't reachable
  (e.g. an unmounted drive). Otherwise every file would go to whatever category is left, such as
  Delete, with high confidence.

### 4c. Folder inside the category (`paths.py`)

- **Suggestion:** the k nearest examples of the chosen category vote with their folders, and every
  prefix of a folder gets the neighbour's weight. The deepest prefix with ≥60% of the category's
  weight is `suggested_subpath` (`''` = category folder). Stored neighbours (`proposals.neighbours`)
  let a category change recompute it without re-embedding.
- **New folders:**
  1. *Numbered sibling:* the suggested folder ends in a number (`BWL 1`, `2025`, `Order35`) and the
     file name has the same word with another number. If that sibling exists, it becomes the
     suggestion. Otherwise it is offered as `new_folder`, and the default falls back to the shared
     parent.
  2. *Container:* similar files are spread over several subfolders with no favourite. Similar inbox
     files of the same scan (cosine ≥ 0.8) are grouped, and one name is made from their shared
     file-name words.
- **Safety:** `normalize_subpath` rejects `..`, Windows-invalid characters and trailing dots/spaces.
  `plan_move` checks the target stays inside the category, and folders are only created by
  `execute()` on Apply. The journal stores `created_dir`, and `revert()` removes created folders
  again if they're empty.
- **Learning:** applied files are learned with their path inside the category
  (`moves.category_key` + relative path), so later suggestions include folders created on Apply.

### 4f. Pictures (`image_index.py`)

- **Model:** the image part of SigLIP2-base (`SiglipVisionModel`, 93M parameters, fp16, ≈0.2–0.3 GB GPU
  memory, ~60 ms per picture in a batch). It loads lazily and runs offline after the one-time download.
- **Index:** table `image_examples`. Up to `images_per_category` pictures per category, sampled evenly
  over subfolders, files > 40 MB skipped, JPEGs decoded at reduced size (draft mode). Built in the
  index thread after the text index and the duplicate catalog; incremental by mtime, batches of 32
  with pauses, cancellable. Applied pictures are learned; undo removes them.
- **Classification:** for an inbox picture, the 10 nearest sorted pictures vote (weights
  `exp((sim − best) / 0.02)`). This is combined with the text result:
  `P ∝ P_text^(1−w) · P_image^w`, `w = image_weight` (default 0.5). The nearest pictures also vote
  for the folder inside the category; the reason shows "looks like: …".
- **Measured** (`evaluate_images`, 192 held-out sorted pictures):
  - text only 75% / 65% (file / folder split) → combined **81% / 74%**;
  - at confidence ≥ 0.8, 82% of pictures were suggested, 91% of them right (text only: 59%, 87%).

  On 27 real picture decisions it was neutral (19 → 18 right; it lost on 03_Arbeit, whose sorted
  pictures look different from what arrives in the inbox), so the weight is adjustable in Settings.

### 4e. Duplicates (`duplicates.py`)

- **Exact only:** byte-for-byte identical content. Candidates narrow in three steps: same size →
  same quick hash (first 64 KB + size) → same full BLAKE2b hash. Files with a unique size are never
  read.
- **Catalog:** the SQLite table `catalog` lists every file in the folder categories (size, mtime,
  cached hashes). It's built in the index thread after the example index (Update index,
  incremental by size/mtime). Apply adds moved/copied files with their hash; undo removes them.
- **Scan:** `_mark_duplicates` checks all inbox files (not only the scanned ones) within the inbox and
  against the catalog. Within the inbox the copy to keep comes first (`original_first`: no
  "(1)"/"Kopie"/"Copy" marker, then oldest, then shortest name); the others are duplicates of it. A
  duplicate becomes a Delete proposal (`source = "duplicate"`, confidence 1.0) if Delete is enabled;
  otherwise it is only flagged. Its trash move isn't learned.
- **Review safety:** `duplicate_details()` (`GET /api/proposals/{id}/duplicate`) re-hashes both copies on
  request for the Compare view; `GET /api/proposals/{id}/file?duplicate=true` serves the kept copy for
  preview. `_plan()` re-checks right before a duplicate is trashed and refuses ("Not deleted: …") if
  the kept copy is gone or no longer identical.

### 4d. Second opinion (`llm.py`, `llm_worker.py`)

- **Gating:** example-model proposals below the threshold, with ≥150 characters of text or a file name
  with ≥2 meaningful words, and ≥2 candidate categories; at most `llm_max_files` per scan.
- **Question:** the file (name, type, download source, ≤1200 characters) and the top-3 candidates, each
  with its description and its 3 most similar sorted files. The answer is read from the
  probabilities of the letters A/B/C after the prompt (one forward pass, no generation).
- **Letter bias:** small models prefer certain letters, and the example model's favourite would
  always be "A". So the question is asked once per rotation of the option order (`llm_rotations`,
  default 3) and the answers are averaged.
- **Worker process:** `python -m classifiles.llm_worker` with its own GPU memory limit
  (`llm_gpu_fraction`), nice 10, and a JSON-lines protocol on stdin/stdout (no port). The embedder
  is unloaded first; the worker is shut down after the step, so all GPU memory is freed. It can be
  cancelled; a failure is recorded on the scan (`llm_error`) and the scan still completes.
- **Hint, not confidence:** the result is stored as `llm_choice` / `llm_confidence` and shown in
  Review. Measured on real data (unsure files only, small samples):
  - when both agree, the first choice was right 11 of 13 times on real decisions, but only 6 of 16
    times for files of unsorted topics (folder split);
  - blending the model into the confidence ("confirm only", 4B) lifted 10 of 11 unsure real
    decisions correctly above the threshold, but on new topics only 6 of 11.

  Because of that risk, it doesn't change confidence; `evaluate_second_opinion` /
  `classifiles eval --second-opinion` keep the comparison.
- **For later:** the worker has a `task` field, so naming folders and files can be added as new tasks.

### 4b. Laya

**Laya** is an open "System One" decision model (Apache-2.0). It is an open alternative to TypeSafe's Jev.
It does **not** generate text. It reads the state and the options in one forward pass of a
BERT-style encoder and outputs a probability for each option. Those probabilities are trained to be
*calibrated*: across many decisions, a 0.8 is right roughly 80% of the time. That property is what
makes a confidence threshold meaningful.

- Code: `deciders/laya.py`. It calls `agent.predict(state, {"q": {"type": "choice", …}})` and reads
  `answers.q.probabilities`.
- **Text budgets.** One call reads at most 512 tokens. All options together get ≤192 tokens. If
  they overflow, each option is cut to `(192 − 16) / n` tokens: about 17 tokens (~12 words) with 10
  folders, about 25 with 7. The file's own text gets roughly the remaining 300 tokens (~1200
  characters); anything beyond that is silently dropped. Hence short descriptions.
- **At most 10 options per call.** Laya's calibration (`temperature_by_options` in the checkpoint's
  `rl_agent_config.json`) uses temperature ≈0.1 for questions with 11+ options. That makes
  probabilities extremely sharp: an uninformative file scored 0.61 with 10 folders and 0.98 with 11.
  So with more than 10 folders, `deciders/laya.py` uses `laya.predict_tournament` with groups of ≤10:
  a winner per group, then a final decision among the winners.
- Speed: about 30 ms per file on the RTX 5070. Extraction (PDF parsing, OCR) usually takes longer than the model.

Other backends implement the same one-method interface (`deciders/base.py`):

```python
class Decider(Protocol):
    name: str
    def choose(self, state: str, question: str, options: dict[str, str]) -> dict[str, float]: ...
```

- `keyword`: no model. It scores word overlap between the file and the folder description. Use it
  for tests or when no GPU is available. It is not accurate.
- Planned: a SemIf-style backend (reads answer-token logits of Qwen3.5-4B, ~8–9 GB VRAM). It is
  stronger but slower. Adding it means one new file in `deciders/` plus one line in `make_decider()`.

## 5. Proposal lifecycle (status machine)

```
                 scan
                  │
       ┌──────────┴───────────┐
       ▼                      ▼
  suggested             needs_review          (decided by threshold at scan time)
       │  accept / pick folder │
       └──────────┬────────────┘
                  ▼
              accepted ──────── reject ───────► rejected
                  │    ◄─────── reset ────────    (file stays in inbox)
                  │ apply (move)
                  ▼
                moved ───── undo ─────► accepted   (file back in inbox)
```

- Choosing a folder in the UI dropdown **is** an accept, with that folder as `final_target`.
- `target` = what the model or rule proposed. `final_target` = what will actually be used. They
  differ when you override.
- Only `accepted` proposals are ever applied. "moved" means applied in any way (moved, copied or
  trashed); the journal says which.

## 6. Applying safely (`mover.py`, `service.apply/undo/empty_trash`)

Each category has an **action**:

| Action | What happens | Undo |
|---|---|---|
| move (default) | `shutil.move` into the category folder (a rename on the same drive; copy + delete across drives) | move back |
| copy | `shutil.copy2` into the folder; the original stays in the inbox | remove the copy, but only if it is unchanged (same size and time) |
| delete | move into `<inbox>/.classifiles-trash/` (hidden, never scanned) | move back |

- **Dry run first:** `plan_apply()` computes every source → destination without touching anything.
  The UI's "Apply accepted" dialog shows this plan.
- **Only known categories:** destinations come from the category list. There is no free-form path,
  and a disabled category or a missing folder is refused.
- **Never overwrites:** if `Invoice.pdf` exists, the file becomes `Invoice (1).pdf`, `(2)`, …
- **Journal:** every operation is a row in `moves` (src, dst, action, batch). One "Apply" = one batch.
  Undo never overwrites: if something new sits at the original place, or the file was moved again,
  that item is reported as skipped.
- **Permanent deletion** happens only in `empty_trash()` (UI: History & trash → Empty trash). It
  refuses any file that isn't inside a `.classifiles-trash` folder.

## 7. Database (`store.py`)

One SQLite file, four tables:

| Table | One row per | Key columns |
|---|---|---|
| `scans` | scan run | inbox, target_root (the roots at scan time), backend, threshold, max_files, only_new, status (running/done/failed), processed/total, inbox_total |
| `proposals` | file in a scan | path, evidence (JSON), target, confidence, alternatives (JSON top 4), source, status, final_target, decided_via, neighbours (JSON), suggested_subpath, subpath, new_folder |
| `moves` | applied operation | batch_id, src, dst, action (move/copy/delete), category_key, created_dir, moved_at, undone_at, purged_at (trash emptied) |
| `examples` | example file (vector) | category_key (folder path or `__delete__`), relpath, mtime, origin (scan/applied/description), has_content, model, vector (float16) |

You can inspect it directly:

```bash
sqlite3 ~/.local/share/classifiles/classifiles.db "select name, final_target, confidence, status from proposals order by id desc limit 20"
```

(Install with `sudo apt install sqlite3` if needed.)

## 8. Code map: where to change what

```
src/classifiles/
  __init__.py          exports ClassifilesService, Settings
  config.py            Settings dataclass ⇄ YAML. Add a setting: add a field here.
  service.py           ClassifilesService — the facade. Every user action is a method here:
                         categories, select_files, start_scan/run_scan/scan, proposals, review,
                         accept_suggested, plan_apply, apply, moves, undo
  taxonomy.py          build_categories(): roots' subfolders + manual entries + Delete; option_text()
  extract/
    __init__.py        dispatch by file kind; never lets a broken file crash a scan
    base.py            KINDS (extension → kind table), Evidence, to_state()
    pdf.py office.py text.py image.py archive.py   one extractor per family
  rules.py             rule syntax (ext/kind/name/glob) and matching
  deciders/            model backends behind the Decider protocol (+ make_decider factory)
  classifier.py        rules → decider → Classification(target, confidence, alternatives)
  store.py             SQLite schema + tiny helpers
  mover.py             plan_move / execute / revert / purge: move, copy, trash (safety checks)
  embedder.py          local embedding model with resource caps (lazy, offline after download)
  examples.py          example index: sample folders, build/update, kNN query, learn/forget
  evaluate.py          hold-out evaluation (`classifiles eval`)
src/classifiles_api/app.py   create_app(): REST endpoints, background scan thread, serves web/dist
src/classifiles_cli/main.py  `classifiles` command (typer)
web/src/api.ts               typed fetch wrappers for every endpoint
web/src/App.tsx              the whole UI: scan bar, filters, rows, move dialog, settings dialog
web/src/index.css            styles (light/dark via prefers-color-scheme)
tests/                       pytest; conftest.py builds a throwaway folder tree per test
```

Common modifications:

| I want to… | Change |
|---|---|
| Improve accuracy for a category | Edit its description on the Categories page. No code. |
| Force certain files somewhere | Add a `rules` entry in the YAML. No code. |
| Ignore a folder as a category | Untick it on the Categories page. |
| Support a new file type's content | Add the extension to `KINDS` in `extract/base.py`; add an extractor and register it in `_EXTRACTORS` (`extract/__init__.py`). |
| Change what the model reads | `Evidence.to_state()` (`extract/base.py`) and `Category.option_text()` (`taxonomy.py`). |
| Change the question wording | `QUESTION` in `classifier.py`. |
| Try another model | New class in `deciders/` with `choose()`, register it in `deciders/__init__.py::make_decider`, set `backend:` in config. |
| Use the multilingual Laya (better for German) | Set `laya_model: ml` in the YAML. See §10 for the comparison. |
| Add a new action/endpoint | Method on `ClassifilesService` first; then expose it in `app.py` and/or `main.py`; then `api.ts` + UI. |
| Skip more junk files | `SKIP_SUFFIXES` / `SKIP_NAMES` in `service.py`. |

After changing Python code: restart `classifiles serve`. After changing `web/src`: run
`npm run build` in `web/` (or use the dev server, see USAGE.md).

## 9. REST API reference

All endpoints are under `http://127.0.0.1:8765/api`. Interactive docs: `http://127.0.0.1:8765/docs`.

| Method | Path | Does |
|---|---|---|
| GET | `/settings` | current settings |
| PATCH | `/settings` | change settings (also saved to the YAML) |
| GET / PUT | `/categories` | roots, Delete settings, and the full category list / save them (`?refresh=true` re-reads folders) |
| GET / PUT | `/rules` | rules (+ list of file kinds) / validate and save |
| GET | `/fs?path=` | list subfolders (folder picker) |
| GET | `/proposals/{id}/file` | the file itself, for preview |
| POST | `/proposals/{id}/open?reveal=` | open in Windows / show in Explorer |
| GET | `/batches` | applied batches, for History |
| GET / POST | `/trash`, `/trash/empty` | trash contents / delete permanently |
| GET | `/index` | example index: build status, example counts per category |
| POST | `/index/build`, `/index/cancel` | start (background) / cancel an index build |
| POST | `/scans` `{inbox?, max_files?, only_new?}` | start a scan in a background thread; returns the scan row. `max_files`: omitted/null = settings default, 0 = all |
| GET | `/scans`, `/scans/{id}` | list scans / progress of one |
| GET | `/proposals?scan_id=&status=` | proposals (default: latest scan) |
| PATCH | `/proposals/{id}` `{action, folder?}` | accept / reject / reset |
| POST | `/proposals/accept-suggested` `{scan_id}` | accept everything ≥ threshold |
| POST | `/apply/plan` `{scan_id}` | dry run |
| POST | `/apply` `{scan_id}` | move accepted files |
| GET | `/moves` | move journal |
| POST | `/undo?batch_id=` | undo a batch (default: last) |

## 10. Limitations of this first version

Be aware of these before relying on it:

**Classification quality**
- **Top-level files only.** Subfolders inside the inbox (e.g. an unzipped folder) are ignored.
- **English model by default.** In a 14-folder test with German content (Laborbefund,
  Mietvertrag, Kfz-Versicherung), the English model got 3/6 right. The multilingual checkpoint
  (`laya_model: ml`) got 5/6, and its one "miss" (Kfz-Versicherung → Insurance) was arguably right.
  This is a small sample, but if your files are mostly German, try `ml`.
- **Junk looks confident.** A file with no usable content (e.g. a corrupt or text-less PDF) is
  judged by its name alone and can still get a high score (`scan_0012.pdf` → Books at 0.66–0.98
  depending on the model). Check files whose details show no content before accepting them.
- **One level of categories.** A file goes into `Finance/`, never `Finance/Taxes/2025/`.
- **Limited reading window.** About 300 tokens of the file itself (~1200 characters, see §4). For
  long documents only the beginning counts.
- **Confidence with more than 10 folders.** After the tournament, probabilities are computed among
  the finalists only, so confidence is somewhat less meaningful than with ≤10 folders.
- **Images are judged by OCR text + filename + EXIF.** There is no visual understanding, so a photo
  of a receipt without readable text is a guess.
- **Scanned PDFs without a text layer** are classified by filename only (they are flagged in the
  metadata); OCR is not run on PDFs.
- **No learning yet.** Your accepts and overrides are stored but not fed back into the model or the
  descriptions.

**Workflow**
- Threshold changes apply to the **next** scan. Existing proposals keep their status.
- Rescanning the same inbox creates new proposals for the same files, unless you tick "Only new".
- Renaming or removing a category later breaks accepted proposals that point at it; Apply reports them.
- The database grows with every scan. There is no cleanup command yet.
- Copy + undo removes the copy only if it is unchanged; otherwise it stays and is reported.

**Planned next steps:** SemIf/Qwen backend, learning from your review decisions, suggested
filenames/notes/links, a vision model for images, named profiles in the UI.
