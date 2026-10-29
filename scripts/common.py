"""Shared paths and tool discovery for development scripts (Python 3.11+)."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = os.environ.copy()
# Homebrew keeps versioned Node installations separate from the global Node.
for candidate in (Path("/opt/homebrew/opt/node@24/bin"), Path("/usr/local/opt/node@24/bin")):
    if (candidate / "node").exists():
        ENV["PATH"] = str(candidate) + os.pathsep + ENV.get("PATH", "")
        break


def command(*args: str) -> list[str]:
    executable = shutil.which(args[0], path=ENV.get("PATH"))
    if executable is None:
        raise SystemExit(f"Missing tool: {args[0]}. Install it before continuing.")
    return [executable, *args[1:]]


def run(directory: str, *args: str) -> None:
    print(f"[{directory}] {' '.join(args)}", flush=True)
    subprocess.run(command(*args), cwd=ROOT / directory, env=ENV, check=True)
