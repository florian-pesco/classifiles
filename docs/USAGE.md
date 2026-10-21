# Classifiles: how to use it

Classifiles is meant to be used through its **web UI**. Everything is configured there: categories,
rules and settings. You never need to edit a file. This guide also covers the config file (for the
curious), the CLI, and development. For how it works inside, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Start and stop

In a **WSL (Ubuntu) terminal**:

```bash
cd ~/projects/classifiles
uv run classifiles serve
```

Then open **http://127.0.0.1:8765** in your Windows browser. (WSL forwards localhost.)

- **Stop:** press **Ctrl+C** in that terminal, or close it. Stopping is always safe: nothing changes
  on disk unless you confirm "Apply".
- **Lost the terminal?** Run `pkill -f "classifiles serve"`.
- **Python code changed** (e.g. after an update): stop and start again.

`uv run` runs a command inside the project's Python environment. `uv` is in `~/.local/bin`.

---

## 2. First-time setup in the UI

1. **Settings → Inbox folders:** add the folder(s) you want to clean up (Browse… opens a folder
   picker). Windows drives are under `/mnt/c`, `/mnt/d`, …
2. **Categories → + Add root:** pick the folder that holds your category folders. Each of its
   subfolders becomes a category. Press **Save categories** to load them.
3. Untick categories that should never be offered (e.g. a `Backups` folder).
4. **Settings → Model → "Learn from my folders"**, then **Save settings**.
5. **Categories → Example index → Build index.** Classifiles samples files you have already sorted
   (spread over all subfolders) and learns what each category contains. The first build takes a
   few minutes; it runs at low priority, you can keep working, and **Cancel** stops it. Later runs
   (**Update index**) only process new or changed files. The first build downloads the embedding
   model once (~1.2 GB); after that everything runs offline.
6. Optional: **+ Add folder** under *Manual categories* for any folder outside your roots (another
   drive, OneDrive, …). Give it a name, and choose **copy** if the original should stay in the inbox.
7. Optional: **Rules** for obvious cases (see §4), e.g. *installer → Delete*.

**Threshold:** with "Learn from my folders", 0.8 is a good start. In testing, two thirds of the
files were suggested at that level and 91% of those were right. Use 0.9 if you prefer fewer but
safer suggestions (≈96% right).

Descriptions are **optional** with "Learn from my folders". They add a little help for categories
with few files. Every file you apply is learned automatically, so proposals get better as you use it.
This includes files you send to **Delete**: that's how the model learns what you consider junk.
If the index card says that earlier applied files are not learned yet (e.g. from before the index
existed), press **Learn them now**.

An inbox folder is never offered as a category, even if it sits inside a root.

---

## 3. Daily use: the Review page

1. **Inbox + Scan & classify.** Optional:
   - **number field** (empty = all): classify only the N **newest** files.
   - **Only new**: skip files already classified in an earlier scan. Together with the number this
     works through a big backlog in chunks: 100 → review → apply → next 100 → …
2. **Review the list.**
   - Filter chips (*Needs review*, *Suggested*, …), **search** by name or category, **sort** by
     confidence, name, category, type, size or date.
   - Click a **file name** for details: why it was proposed, all scores, the text the model read.
     "No readable content" means it was judged by name and type only, so look twice.
   - **preview** shows images, PDFs and text in the browser, with **Open in Windows** / **Show in Explorer**.
   - **Category dropdown:** choosing a category accepts the file into it. 🗑 *Delete* is at the bottom.
   - **✓** accept as proposed · **✕** keep in inbox · **↺** reset.
   - **Accept all suggested** accepts everything at or above the threshold.
3. **Apply accepted** shows exactly what will happen (move / copy / to trash, from → to). Nothing
   happens until you confirm.
4. **Undo last apply** reverses the last batch. Older batches can be undone on the History page.
5. **Older scans:** pick one from the scan dropdown, or open it from *History & trash*.

Status names: **Suggested** (confidence ≥ threshold) · **Needs review** (below) · **Accepted** ·
**Kept in inbox** · **Done** (moved, copied or in trash).

---

## 4. Rules

Rules handle the obvious cases without the model, with confidence 100%.

- Checked **top to bottom**; the **first matching rule wins** (reorder with ↑ ↓).
- A rule matches when **all filled-in conditions** match; empty fields are ignored.

| Field | Matches | Example |
|---|---|---|
| Extensions | file extension | `.exe, .msi` |
| Name contains | text in the file name, case-insensitive; `\|` means "or" (it is a regular expression) | `Rechnung\|Invoice` |
| Name pattern | wildcards `*` and `?` | `Screenshot*`, `*_scan.pdf` |
| File types | groups of extensions | *installer* = .exe .msi .deb .dmg …; *image*, *audio*, *video*, *archive*, … |

Examples: *File type installer → Delete*; *Name contains Rechnung|Invoice → Finance*;
*Extensions .log, .tmp → Delete*.

A rule must target an existing, enabled category. Saving checks this.

---

## 5. Deleting files safely

**Delete** is a built-in category. Files sent there are **not deleted right away**: they move into a
hidden `.classifiles-trash` folder inside their inbox. That's fast, can be undone, and the folder is
never scanned.

- **Undo** brings them back like any other change.
- **History & trash → Empty trash…** deletes them permanently, after a confirmation. This cannot be
  undone.
- By default, only **rules** and **your own choice** send files to Delete. On the Categories page you can
  allow the model to suggest it too ("Let the model suggest Delete"). Then a good description helps,
  e.g. *installers, temporary or generated files, duplicates*.

---

## 6. Settings page

| Setting | Meaning |
|---|---|
| Inbox folders | Folders to clean up; the first is pre-filled on Review. |
| Confidence threshold | At or above → *Suggested*; below → *Needs review*. Applies to the next scan. |
| Default max files | Pre-fills the number field on Review. |
| Model | **Learn from my folders** (recommended: compares with your already-sorted files), Laya English or multilingual (decide from the descriptions only), or keyword matching (testing only). |
| Learn from my folders: limits | Example files per category (default 3000), documents whose text is read (default 300), max GPU memory share (default 25%). Lower values make the index faster and lighter. |
| Sample file names | For categories **without** a description, this many file names from the folder are shown to the model instead. |

Category, rule and setting changes all apply to the **next scan**. Existing proposals keep their
status.

---

## 7. The config file (optional)

The UI saves everything to `~/.config/classifiles/config.yaml`. You don't need to touch it, but it is
plain text:

```yaml
inboxes: [/mnt/d/Archive/Inbox]
roots:                                   # every subfolder becomes a category
  - path: /mnt/d/Archive
categories:                              # only what differs from the defaults
  - path: /mnt/d/Archive/Personal
    description: personal documents, ID, contracts, insurance
  - path: /mnt/d/Archive/Backups
    enabled: false                       # never offered
  - path: /mnt/c/Users/me/OneDrive/Steuer   # outside any root = manual category
    name: Steuer
    description: tax documents, Steuerbescheid, Finanzamt
    action: copy                         # move (default) | copy
delete:
  enabled: true
  suggest: false                         # may the model propose Delete?
  description: ''
rules:
  - {kind: installer, folder: Delete}
  - {name: "Rechnung|Invoice", folder: Personal}
threshold: 0.7
max_files: null
backend: laya
laya_model: convaiinnovations/laya      # or: ml (multilingual)
samples_per_folder: 3
max_text_chars: 1500
db_path: /home/<you>/.local/share/classifiles/classifiles.db
```

Categories are identified by **path**. A category's name is its folder name unless you set `name`. If
two roots both have a folder with the same name, the UI shows them as `Studies (RootA)` / `Studies (RootB)`.
Rules refer to these names.

Files in the older format (`target_root`, `folder_descriptions`, `exclude_folders`) are read and
converted automatically. The new format is written the next time you save in the UI.

To keep several separate setups, use separate config files: `uv run classifiles serve -c other.yaml`.

---

## 8. CLI (optional)

The same actions also work in the terminal. The CLI and the UI share one database.

```bash
uv run classifiles init --root /mnt/d/Archive --inbox /mnt/d/Archive/Inbox
uv run classifiles folders                      # categories and what the model sees
uv run classifiles index                        # build/update the example index (Ctrl+C cancels)
uv run classifiles index --learn-past           # learn applied files that are not learned yet
uv run classifiles eval -b examples -b laya     # accuracy on held-out sorted files (file + folder split)
uv run classifiles eval --decisions             # accuracy against your real Review decisions
uv run classifiles scan --limit 100 --only-new
uv run classifiles list --status needs_review
uv run classifiles accept --suggested
uv run classifiles accept 41 42 --to Studies
uv run classifiles reject 43
uv run classifiles apply --dry-run
uv run classifiles apply
uv run classifiles undo
```

`--help` works on every command. Every command takes `-c / --config path.yaml`.

---

## 9. Using it as a Python library (LifeOS)

```python
from classifiles import ClassifilesService, Settings

svc = ClassifilesService(Settings.load())
scan_id, proposals = svc.scan("/mnt/d/Archive/Inbox", max_files=100)
svc.review(proposals[0]["id"], "accept", "Studies")
svc.accept_suggested(scan_id)
print(svc.plan_apply(scan_id))       # dry run
svc.apply(scan_id)
svc.undo()
```

---

## 10. Developing

| Task | Command |
|---|---|
| Run tests | `uv run pytest` |
| Backend with auto-reload | `uv run uvicorn classifiles_api.app:create_app --factory --reload --port 8765` |
| Frontend with hot reload | `cd web && npm run dev` → http://localhost:5173 (proxies `/api` to 8765) |
| Rebuild the UI served by `classifiles serve` | `cd web && npm run build` |
| API playground | http://127.0.0.1:8765/docs |

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Banner "No categories yet" | Add a root or a manual category on the Categories page. |
| A category shows "folder missing" | Its folder was moved, renamed, or its drive isn't mounted. |
| Red banner "Category folder not reachable" | The drive with your categories isn't mounted (unplugged, or WSL started without it). Scans and index updates pause until it is back; connect it and press "Check again". If WSL doesn't mount it by itself: `sudo mount -t drvfs D: /mnt/d`. |
| "The example index is empty" | Build it: Categories → Example index → Build index. |
| Proposals are poor for one category | It has few or untypical example files. Check its count on the Categories page, sort a few files by hand (they are learned on Apply), or add a description. |
| `Config file not found` | The file given with `-c` doesn't exist. Create it with `classifiles init -c …`. |
| "Only new" finds nothing | Every inbox file already has a proposal in some scan. Untick it to re-classify. |
| External/USB drive not visible | `sudo mkdir -p /mnt/e && sudo mount -t drvfs E: /mnt/e` |
| Port already in use | A server is still running: `pkill -f "classifiles serve"`, or use `--port`. |
| Undo reports a file as skipped | The file was changed or moved again since. Classifiles never overwrites. |
| Many `fontTools is required…` log lines | Harmless warnings from the PDF reader. |
