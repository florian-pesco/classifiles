"""Download source hint: the website a file was downloaded from, as a domain only.

Windows attaches an NTFS alternate data stream `Zone.Identifier` to downloaded files, e.g.

    [ZoneTransfer]
    ZoneId=3
    ReferrerUrl=https://www.lms.example.edu/course/view.php?id=123
    HostUrl=https://www.lms.example.edu/pluginfile.php/456/file.pdf

WSL cannot read these streams through /mnt/<drive>, but powershell.exe can. Files are read in
batches (one PowerShell call per batch). Only the domain is kept (the page you were on, or else
the server the file came from); full URLs are discarded immediately, since they can contain IDs or
tokens. Everything stays local.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BATCH = 200
TIMEOUT = 60  # seconds per PowerShell call
_DRIVE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")

# Reads the stream for each path from stdin (JSON list) and prints {path: content} as JSON.
# [string] drops the extra properties PowerShell attaches to file content (else it serializes as an
# object); UTF-8 both ways keeps non-ASCII file names intact.
_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$paths = [Console]::In.ReadToEnd() | ConvertFrom-Json
$out = @{}
foreach ($p in $paths) {
  $z = Get-Content -LiteralPath $p -Stream Zone.Identifier -Raw
  if ($z) { $out[[string]$p] = [string]$z }
}
$out | ConvertTo-Json -Compress
"""


def windows_path(path: Path | str) -> str | None:
    """/mnt/d/Folder/file.pdf -> D:\\Folder\\file.pdf; None for paths not on a Windows drive."""
    m = _DRIVE.match(str(path))
    if not m:
        return None
    return f"{m[1].upper()}:" + (m[2] or "/").replace("/", "\\")


def domain(url: str | None) -> str | None:
    """'https://www.lms.example.edu/x?id=1' -> 'lms.example.edu'; None for local/blank/odd URLs."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    return host[4:] if host.startswith("www.") else host


def parse_zone_identifier(text: str) -> str | None:
    """Domain from a Zone.Identifier stream: the referring page if known, else the file's host."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip().lower()] = value.strip()
    return domain(values.get("referrerurl")) or domain(values.get("hosturl"))


def available() -> bool:
    return shutil.which("powershell.exe") is not None


def download_sources(paths: list[Path], runner=None) -> dict[Path, str]:
    """{path: domain} for the files that have a download source. Silently returns {} when
    PowerShell isn't available or the files aren't on a Windows drive.

    runner(script, stdin) -> stdout can be injected for tests."""
    wanted = {windows_path(p): p for p in paths}
    wanted.pop(None, None)
    if not wanted:
        return {}
    if runner is None:
        if not available():
            return {}
        runner = _run_powershell
    found: dict[Path, str] = {}
    items = list(wanted.items())
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        try:
            out = runner(_SCRIPT, json.dumps([w for w, _ in batch]))
            streams = json.loads(out) if out.strip() else {}
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.debug("download source lookup failed: %s", e)
            continue
        if isinstance(streams, dict):
            for win, text in streams.items():
                d = parse_zone_identifier(text) if isinstance(text, str) else None
                if d and win in wanted:
                    found[wanted[win]] = d
    return found


def _run_powershell(script: str, stdin: str) -> str:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       input=stdin.encode("utf-8"), capture_output=True, timeout=TIMEOUT, cwd="/mnt/c")
    return r.stdout.decode("utf-8", errors="replace").lstrip("\ufeff")
