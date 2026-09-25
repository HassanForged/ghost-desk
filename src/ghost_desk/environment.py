"""Host facts for the system prompt. The model must not guess these."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

TOOL_RULES = """NEVER answer these from memory — ALWAYS use a tool:
- Time, date, timezone → shell (`date` or equivalent)
- File contents, whether a path exists, config on disk → file_read
- Git, processes, disk, OS details beyond this block → shell
Do not invent files, exit codes, or HTTP statuses.
Keep working until named files are actually on disk. A text claim of done is not done."""


def build_environment_hints(*, workspace: Path, data_dir: Path | None = None) -> str:
    home = str(Path.home())
    cwd = str(Path(workspace).expanduser().resolve())
    system = platform.system()
    release = platform.release()
    py = sys.version.split()[0]
    if os.name == "nt":
        shell = os.environ.get("COMSPEC") or "cmd.exe"
    else:
        shell = os.environ.get("SHELL") or "/bin/sh"
    lines = [
        "Host:",
        f"- OS: {system} ({release})",
        f"- User home: {home}",
        f"- Workspace (cwd for tools): {cwd}",
        f"- Python: {py}",
        f"- Shell: {shell}",
    ]
    if data_dir is not None:
        lines.append(f"- Data dir: {Path(data_dir).expanduser()}")
    lines.append("")
    lines.append(TOOL_RULES)
    return "\n".join(lines)
