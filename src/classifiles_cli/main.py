"""Command-line interface for classifiles."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from classifiles import ClassifilesService, Settings
from classifiles.config import DEFAULT_CONFIG_PATH

app = typer.Typer(help="Classify inbox files into your folder taxonomy.", no_args_is_help=True)
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help=f"Config file (default {DEFAULT_CONFIG_PATH})")


def _service(config: Optional[Path], **overrides) -> ClassifilesService:
    settings = Settings.load(config)
    for k, v in overrides.items():
        if v is not None:
            setattr(settings, k, v)
    return ClassifilesService(settings)


def _status_style(status: str) -> str:
    return {"suggested": "green", "needs_review": "yellow", "accepted": "cyan",
            "rejected": "dim", "moved": "blue"}.get(status, "")


def _proposal_table(rows: list[dict], threshold: float) -> Table:
    t = Table(show_lines=False)
    for col in ("id", "file", "→ folder", "conf", "source", "status", "alternatives"):
        t.add_column(col, overflow="fold")
    for p in rows:
        conf_style = "green" if p["confidence"] >= threshold else "yellow"
        alts = ", ".join(f"{a['folder']} {a['probability']:.2f}" for a in p["alternatives"][1:3])
        t.add_row(str(p["id"]), p["name"], p["final_target"] or "—", f"[{conf_style}]{p['confidence']:.2f}[/]",
                  p["source"], f"[{_status_style(p['status'])}]{p['status']}[/]", alts)
    return t


@app.command()
def init(
    root: list[Path] = typer.Option(..., "--root", "--target-root",
                                    help="Folder whose subfolders become categories (repeatable)"),
    inbox: list[Path] = typer.Option([Path.home() / "Downloads"], help="Inbox folder(s) to sort"),
    threshold: float = 0.7,
    config: Optional[Path] = ConfigOpt,
):
    """Write a starter config file. Fine-tune categories and rules in the web UI afterwards."""
    s = Settings(roots=[{"path": str(r.expanduser().resolve())} for r in root],
                 inboxes=[str(p.expanduser().resolve()) for p in inbox], threshold=threshold)
    path = s.save(config)
    console.print(f"Wrote [bold]{path}[/]. Run `classifiles serve` and open Categories to add descriptions.")


@app.command()
def folders(config: Optional[Path] = ConfigOpt):
    """Show the category folders and how they are described to the model."""
    svc = _service(config)
    t = Table("category", "action", "source", "status", "description given to the model")
    for c in svc.categories():
        status = "ok" if c.usable else ("missing" if not c.exists else "disabled")
        if c.usable and not c.suggest:
            status = "rules/manual only"
        t.add_row(c.name, c.action, c.source, status, c.option_text())
    console.print(t)


@app.command()
def scan(
    inbox: Optional[Path] = typer.Argument(None, help="Inbox folder (default: first configured inbox)"),
    backend: Optional[str] = typer.Option(None, help="laya | keyword"),
    threshold: Optional[float] = None,
    limit: Optional[int] = typer.Option(None, "--limit", "-n",
                                        help="Classify at most N files, newest first (0 = all)"),
    only_new: bool = typer.Option(False, "--only-new", help="Skip files classified in an earlier scan"),
    config: Optional[Path] = ConfigOpt,
):
    """Classify the files in the inbox and store the proposals for review."""
    svc = _service(config, backend=backend, threshold=threshold)
    inbox = inbox or Path(svc.settings.inboxes[0])
    scan_id = svc.start_scan(inbox, limit, only_new)
    scan = svc.get_scan(scan_id)
    total = scan["total"]
    if total < scan["inbox_total"]:
        console.print(f"Classifying {total} of {scan['inbox_total']} files in the inbox.")
    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("Loading model…" if svc.settings.backend == "laya" else "Classifying…", total=total)
        def on_progress(i, n, p):
            progress.update(task, completed=i, description=f"Classifying {p['name'][:40]}")
        rows = svc.run_scan(scan_id, on_progress)
    console.print(_proposal_table(rows, svc.settings.threshold))
    n_ok = sum(p["status"] == "suggested" for p in rows)
    console.print(f"Scan [bold]{scan_id}[/]: {n_ok} suggested, {len(rows) - n_ok} need review "
                  f"(threshold {svc.settings.threshold}).")


@app.command("list")
def list_(
    scan_id: Optional[int] = typer.Option(None, "--scan"),
    status: Optional[str] = typer.Option(None, help="suggested|needs_review|accepted|rejected|moved"),
    config: Optional[Path] = ConfigOpt,
):
    """List proposals of a scan (default: latest)."""
    svc = _service(config)
    console.print(_proposal_table(svc.proposals(scan_id, status), svc.settings.threshold))


@app.command()
def accept(
    ids: list[int] = typer.Argument(None, help="Proposal ids; omit with --suggested"),
    folder: Optional[str] = typer.Option(None, "--to", help="Override the target folder"),
    suggested: bool = typer.Option(False, "--suggested", help="Accept all proposals above threshold"),
    scan_id: Optional[int] = typer.Option(None, "--scan"),
    config: Optional[Path] = ConfigOpt,
):
    """Accept proposals (optionally into a different folder)."""
    svc = _service(config)
    if suggested:
        console.print(f"Accepted {svc.accept_suggested(scan_id)} suggested proposals.")
    for i in ids or []:
        p = svc.review(i, "accept", folder)
        console.print(f"{p['name']} → [cyan]{p['final_target']}[/]")


@app.command()
def reject(ids: list[int], config: Optional[Path] = ConfigOpt):
    """Reject proposals (files stay where they are)."""
    svc = _service(config)
    for i in ids:
        svc.review(i, "reject")
    console.print(f"Rejected {len(ids)}.")


@app.command()
def apply(
    scan_id: Optional[int] = typer.Option(None, "--scan"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    config: Optional[Path] = ConfigOpt,
):
    """Move all accepted files into their folders."""
    svc = _service(config)
    plan = svc.plan_apply(scan_id)
    if not plan:
        console.print("Nothing accepted to move. Use `classifiles accept`.")
        raise typer.Exit()
    for m in plan:
        console.print(f"[red]✗ {m['src']}: {m['error']}[/]" if m["error"] else f"{m['src']}\n  → {m['dst']}")
    if dry_run or not (yes or typer.confirm(f"Move {sum(not m['error'] for m in plan)} files?")):
        raise typer.Exit()
    res = svc.apply(scan_id)
    console.print(f"Moved {len(res['moved'])} files (batch [bold]{res['batch_id']}[/]); {len(res['errors'])} errors.")
    for e in res["errors"]:
        console.print(f"[red]✗ {e['src']}: {e['error']}[/]")


@app.command()
def undo(batch_id: Optional[str] = typer.Argument(None, help="Batch id (default: last)"),
         config: Optional[Path] = ConfigOpt):
    """Move the files of a batch back to where they came from."""
    res = _service(config).undo(batch_id)
    if not res["batch_id"]:
        console.print("Nothing to undo.")
        return
    console.print(f"Restored {len(res['restored'])} files from batch {res['batch_id']}; {len(res['errors'])} errors.")
    for e in res["errors"]:
        console.print(f"[red]✗ move {e['move_id']}: {e['error']}[/]")


@app.command()
def index(
    learn_past: bool = typer.Option(False, "--learn-past",
                                    help="Only learn applied files that are not learned yet (no folder scan)"),
    config: Optional[Path] = ConfigOpt,
):
    """Build or update the example index from your already-sorted files (low priority; Ctrl+C cancels)."""
    import time

    svc = _service(config)
    if learn_past:
        r = svc.learn_past_decisions()
        console.print(f"Learned {r['learned']} past decisions {r['by_action'] or ''}.")
        return
    svc.start_index_build()
    idx = svc.example_index()
    try:
        with Progress(console=console, transient=True) as progress:
            task = progress.add_task("starting…", total=None)
            while idx.building:
                st = idx.status
                progress.update(task, description=st.get("message", ""), completed=st.get("done", 0),
                                total=st.get("total") or None)
                time.sleep(0.5)
    except KeyboardInterrupt:
        idx.cancel()
        console.print("Cancelling…")
        while idx.building:
            time.sleep(0.2)
    st = idx.status
    console.print(f"Index {st['state']}: {st.get('added', 0)} embedded, {st.get('reused', 0)} unchanged, "
                  f"{st.get('removed', 0)} removed." + (f" Error: {st['error']}" if st.get("error") else ""))
    t = Table("category", "examples", "learned")
    for c in svc.index_status()["categories"]:
        t.add_row(c["name"], str(c["examples"]), str(c["learned"]))
    console.print(t)


@app.command("eval")
def eval_(
    backend: list[str] = typer.Option(["examples"], "--backend", "-b", help="examples | laya | keyword (repeatable)"),
    per_category: int = typer.Option(30, "--per-category", "-n", help="Held-out files per category"),
    split: list[str] = typer.Option(["file", "folder"], "--split",
                                    help="file: hide single files; folder: hide whole folders (new topics)"),
    decisions: bool = typer.Option(False, "--decisions", help="Measure against your real Review decisions instead"),
    second_opinion: bool = typer.Option(False, "--second-opinion",
                                        help="Measure the language model hint on unsure files (decisions + folder split)"),
    seed: int = 0,
    config: Optional[Path] = ConfigOpt,
):
    """Measure accuracy (read-only; prints aggregates only, never file names)."""
    from classifiles.evaluate import evaluate, evaluate_decisions

    svc = _service(config)
    if second_opinion:
        from classifiles.evaluate import evaluate_second_opinion

        for source in ("decisions", "folder"):
            r = evaluate_second_opinion(svc, source=source, per_category=per_category, seed=seed)
            a = r["agreement"]
            agree_rate = f"{a['agree_right'] / a['agree']:.0%}" if a["agree"] else "—"
            console.print(f"[bold]{source}[/]: {r['files']} files, {r['gated']} unsure files asked "
                          f"({r['model']}, {r['seconds_per_file']} s/file, {r['model_gpu_gb']} GB)")
            console.print(f"  both agree: {a['agree']} files, first choice right in {agree_rate}")
            console.print(f"  they disagree: {a['disagree']} files, example model right {a['disagree_example_right']}, "
                          f"language model right {a['disagree_llm_right']}\n")
            svc = _service(config)  # fresh service: the embedder was unloaded for the model
        return
    if decisions:
        report = evaluate_decisions(svc)
        console.print(f"[bold]{report['decisions']} decided files[/] (how: {report['decided_via']})\n")
        for name, r in report["recorded"].items():
            _print_summary(f"recorded at scan time: {name}", r)
        if "current" in report:
            _print_summary("current model (examples), re-run on the same files", report["current"])
        return
    with Progress(console=console, transient=True) as progress:
        task = progress.add_task("…", total=None)
        report = evaluate(svc, backend, per_category, seed, tuple(split),
                          lambda what, i, n: progress.update(task, description=what, completed=i, total=n))
    console.print(f"[bold]{report['files']} held-out files[/] ({per_category} per category)\n")
    for name, r in report["results"].items():
        _print_summary(name, r)


def _print_summary(name: str, r: dict) -> None:
    if not r.get("files"):
        console.print(f"[bold]{name}[/]: no files\n")
        return
    console.print(f"[bold]{name}[/] ({r['files']} files): accuracy [bold]{r['accuracy']:.0%}[/], top-3 {r['top3']:.0%}, "
                  f"mean confidence right {r['mean_confidence_right']} / wrong {r['mean_confidence_wrong']}")
    t = Table("category", "files", "recall")
    for cat, v in r["per_category"].items():
        t.add_row(cat, str(v["files"]), f"{v['recall']:.0%}")
    cal = Table("confidence", "files", "accuracy")
    for b in r["calibration"]:
        cal.add_row(b["range"], str(b["files"]), "—" if b["accuracy"] is None else f"{b['accuracy']:.0%}")
    th = Table("threshold", "suggested", "accuracy of suggested")
    for x in r["thresholds"]:
        th.add_row(str(x["threshold"]), f"{x['suggested']:.0%}", "—" if x["accuracy"] is None else f"{x['accuracy']:.0%}")
    console.print(t, cal, th)
    if r.get("by_decided_via"):
        console.print("by how you decided: " + "; ".join(f"{k}: {v['accuracy']:.0%} of {v['files']}"
                                                         for k, v in r["by_decided_via"].items()))
    if r["confusions"]:
        console.print("most common mistakes (true → proposed): " + "; ".join(f"{k} ×{n}" for k, n in r["confusions"]))
    console.print()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, config: Optional[Path] = ConfigOpt):
    """Start the web UI / REST API."""
    import os

    import uvicorn

    if config:
        os.environ["CLASSIFILES_CONFIG"] = str(config)
    console.print(f"Classifiles UI on http://{host}:{port}")
    uvicorn.run("classifiles_api.app:create_app", factory=True, host=host, port=port)


def main() -> None:
    try:
        app()
    except (KeyError, ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error:[/] {e.args[0] if e.args else e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
