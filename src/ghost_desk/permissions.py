"""Read is automatic. A workspace write is confirmed once per path. Destructive commands ask every time."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Ask = Callable[[str], bool]

SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}

# Destructive matches are not remembered. Each call asks again.
_DESTRUCTIVE = re.compile(
    r"(?i)(\brm\b|\brmdir\b|\bdel\b|\berase\b|"
    r"remove-item|git\s+reset\s+--hard|git\s+clean\s+-fd|"
    r"\bmkfs\b|\bdd\s+if=|\bshutdown\b|\breboot\b|"
    r"cipher\s+/w|\bdrop\s+table\b)"
)
_READONLY = re.compile(
    r"(?i)^(?:dir|ls|pwd|whoami|ver|where|type|cat|more|echo|"
    r"get-process|tasklist|"
    r"git\s+status|git\s+diff|git\s+log|git\s+show|git\s+rev-parse)(?:\s|$)"
)
_PROCESS_QUERY = re.compile(r"(?i)\b(get-process|tasklist|get-ciminstance\s+win32_process)\b")
_PROCESS_CHANGE = re.compile(r"(?i)\b(stop-process|taskkill|kill)\b")
_WRITE_HINT = re.compile(
    r"(?i)(?:>>?|\bmkdir\b|\btouch\b|new-item|set-content|add-content|"
    r"out-file|\bcopy\b|\bmove\b)"
)
_WIN_ABS = re.compile(r"[A-Za-z]:\\[^\s\"']+")
_POSIX_ABS = re.compile(r"(?:^|[\s\"'=])(/(?!/)[^\s\"']+)")
_SPLIT = re.compile(r"[\n;&|]+")
_OPEN_APP = r'(?:chrome|msedge|firefox|notepad|explorer|calc|"google chrome")'
_OPEN_CMD = re.compile(
    rf'(?i)^(?:start-process|start)\s+(?:""\s+)?{_OPEN_APP}(?:\s+https?://\S+)?\s*$'
)
_OPEN_EXE = re.compile(
    r'(?i)^(?:"[^"]*(?:chrome|msedge|firefox)\.exe"|[^\s"]*(?:chrome|msedge|firefox)\.exe)(?:\s+https?://\S+)?\s*$'
)


@dataclass
class Decision:
    allowed: bool
    reason: str
    path: Path | None = None
    kind: str = "read"


def is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if path.suffix.lower() in SECRET_SUFFIXES:
        return True
    parts = [part.lower() for part in path.parts]
    if ".ssh" in parts:
        return True
    if name.startswith(("id_rsa", "id_ed25519", "id_dsa")):
        return True
    if "credentials" in name or name.endswith(".secret"):
        return True
    return False


def inside_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
    except (ValueError, OSError):
        return False
    return True


def mentions_secret(command: str) -> bool:
    low = command.lower()
    needles = (".env", ".ssh", "id_rsa", "id_ed25519", "id_dsa", ".pem", "credentials")
    return any(needle in low for needle in needles)


def _command_paths(command: str) -> list[Path]:
    found = [Path(match) for match in _WIN_ABS.findall(command)]
    found.extend(Path(match.group(1)) for match in _POSIX_ABS.finditer(command))
    return found


def is_local_open(command: str) -> bool:
    """Launching Chrome, Edge, Firefox, or a page. Not a general shell command."""
    if mentions_secret(command) or _DESTRUCTIVE.search(command):
        return False
    parts = [part.strip() for part in _SPLIT.split(command) if part.strip()]
    if len(parts) != 1:
        return False
    piece = parts[0]
    return bool(_OPEN_CMD.match(piece) or _OPEN_EXE.match(piece))


def shell_kind(command: str, workspace: Path) -> str:
    if mentions_secret(command):
        return "secret"
    if is_local_open(command):
        return "open"
    if _PROCESS_QUERY.search(command) and not _PROCESS_CHANGE.search(command) and not _DESTRUCTIVE.search(command):
        return "read"
    for path in _command_paths(command):
        if is_secret_path(path):
            return "secret"
        if not inside_workspace(path, workspace):
            return "outside"
    worst = "read"
    for part in _SPLIT.split(command):
        piece = part.strip()
        if not piece:
            continue
        if _DESTRUCTIVE.search(piece):
            return "destructive"
        if _PROCESS_QUERY.search(piece) and not _PROCESS_CHANGE.search(piece):
            continue
        if _WRITE_HINT.search(piece) or not _READONLY.match(piece):
            worst = "write"
    return worst


class PermissionGate:
    def __init__(self, workspace: Path, ask: Ask | None = None):
        self.workspace = workspace.resolve()
        self.ask = ask or (lambda _prompt: False)
        self._write_ok: set[Path] = set()
        self._shell_ok: set[str] = set()
        self.claimed: set[str] = set()

    def _resolve(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = self.workspace / path
        return path

    def check_read(self, raw: str) -> Decision:
        return self._check_path(self._resolve(raw), write=False)

    def check_write(self, raw: str) -> Decision:
        return self._check_path(self._resolve(raw), write=True)

    def _check_path(self, path: Path, write: bool) -> Decision:
        if is_secret_path(path):
            if self.ask(f"Allow secret path {path}?"):
                return Decision(True, "user allowed secret", path, "secret")
            return Decision(False, f"refused secret path {path.name}", path, "secret")
        if not inside_workspace(path, self.workspace):
            if self.ask(f"Allow path outside workspace {path}?"):
                return Decision(True, "user allowed outside path", path, "outside")
            return Decision(False, f"refused path outside workspace: {path}", path, "outside")
        if not write:
            return Decision(True, "read allowed", path, "read")
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in self._write_ok:
            return Decision(True, "write already confirmed this session", resolved, "write")
        if self.ask(f"Allow write {path}?"):
            self._write_ok.add(resolved)
            return Decision(True, "write confirmed", resolved, "write")
        return Decision(False, f"write not confirmed: {path}", resolved, "write")

    def check_shell(self, command: str) -> Decision:
        kind = shell_kind(command, self.workspace)
        if kind == "secret":
            if self.ask(f"Allow shell touching secrets: {command}?"):
                return Decision(True, "user allowed secret shell", None, "secret")
            return Decision(False, "refused shell that touches secrets", None, "secret")
        if kind == "open":
            return Decision(True, "open a local app or page", None, "open")
        if kind == "outside":
            if self.ask(f"Allow shell outside workspace: {command}?"):
                return Decision(True, "user allowed outside shell", None, "outside")
            return Decision(False, "refused shell outside workspace", None, "outside")
        if kind == "destructive":
            if self.ask(f"Allow destructive command: {command}?"):
                return Decision(True, "user allowed destructive", None, "destructive")
            return Decision(False, "destructive command needs confirmation", None, "destructive")
        if kind == "read":
            return Decision(True, "read-only shell", None, "read")
        if command in self._shell_ok:
            return Decision(True, "shell write already confirmed", None, "write")
        if self.ask(f"Allow shell write: {command}?"):
            self._shell_ok.add(command)
            return Decision(True, "shell write confirmed", None, "write")
        return Decision(False, f"shell write not confirmed: {command}", None, "write")
