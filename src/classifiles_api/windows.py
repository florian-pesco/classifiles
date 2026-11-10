"""Windows helpers (WSL): drives for the folder picker, opening a file or showing it in Explorer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def drives() -> list[str]:
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


def open_in_windows(path: Path, reveal: bool) -> None:
    win = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    wslenv = ":".join(x for x in (os.environ.get("WSLENV", ""), "CLASSIFILES_REVEAL") if x)
    env = {**os.environ, "CLASSIFILES_REVEAL": "1" if reveal else "0", "WSLENV": wslenv}
    proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_OPEN],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd="/mnt/c", env=env)
    assert proc.stdin is not None
    proc.stdin.write((win + "\n").encode("utf-8"))
    proc.stdin.close()
