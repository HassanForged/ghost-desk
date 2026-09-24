"""Workspace tools. Each one has a name, a description, and a JSON schema."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghost_desk.permissions import PermissionGate
from ghost_desk.verify import Check, verify_http, verify_read, verify_shell, verify_write

READ_LIMIT = 200_000
HTTP_LIMIT = 200_000
SHELL_LIMIT = 100_000


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


SHELL = Tool(
    name="shell",
    description=(
        "Run a shell command. The working directory is the workspace. "
        "The result includes the exit code and the output so it can be checked."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command line to run."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)

FILE_READ = Tool(
    name="file_read",
    description="Read a text file inside the workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace, or absolute inside it."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)

FILE_WRITE = Tool(
    name="file_write",
    description="Write a text file inside the workspace. The file is re-read afterwards.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)

HTTP_FETCH = Tool(
    name="http_fetch",
    description="HTTP GET a URL and return the status code and body.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
)

LITTLE_GHOST = Tool(
    name="little_ghost",
    description=(
        "Spawn a little ghost with its own context and budget. "
        "It cannot spawn another ghost. The handoff lists done, tried, failed, and remaining. "
        "Files it names are re-read before they are trusted."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["design", "code", "research", "default"],
                "description": "design, code, and research pick a routed model when that key exists.",
            },
            "prior_handoff": {
                "type": "string",
                "description": "Optional handoff from a ghost that already stopped. Nothing else from that ghost is kept.",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    },
)

OPEN = Tool(
    name="open",
    description=(
        "Open a local app or an https page on this computer. "
        "Use this when the person asks to open Chrome, YouTube, Edge, or another app. "
        "This is not limited to the workspace folder."
    ),
    parameters={
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Program name, such as chrome, msedge, or firefox.",
            },
            "url": {
                "type": "string",
                "description": "http or https page, such as https://www.youtube.com.",
            },
        },
        "additionalProperties": False,
    },
)

FILE_EDIT = Tool(
    name="file_edit",
    description="Replace one exact snippet in a workspace file. The file is re-read afterwards.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Exact text that appears once in the file."},
            "new": {"type": "string"},
        },
        "required": ["path", "old", "new"],
        "additionalProperties": False,
    },
)

WEB_SEARCH = Tool(
    name="web_search",
    description="Search the public web and return a few titles and links. No API key.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)

CLOSE_GHOSTS = Tool(
    name="close_ghosts",
    description=(
        "Close other Ghost Desk chat windows. Leave this window and the Telegram gateway running."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)

CLIPBOARD = Tool(
    name="clipboard",
    description="Read or write the Windows clipboard on this computer.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "text": {"type": "string", "description": "Text to copy when action is set."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)

BASE_TOOLS = (SHELL, FILE_READ, FILE_WRITE, FILE_EDIT, HTTP_FETCH, WEB_SEARCH, OPEN, CLIPBOARD, CLOSE_GHOSTS)

_APP_NAMES = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "googlechrome": "chrome",
    "msedge": "msedge",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "notepad": "notepad",
    "explorer": "explorer",
    "calc": "calc",
}
_SITE_NAMES = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
}


def schemas(include_ghost: bool = False) -> list[dict[str, Any]]:
    tools = list(BASE_TOOLS)
    if include_ghost:
        tools.append(LITTLE_GHOST)
    return [tool.schema() for tool in tools]


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    payload: dict[str, Any]
    check: Check | None


def _refused(name: str, reason: str) -> ToolOutcome:
    check = Check(name, reason, "refused", False, reason)
    return ToolOutcome(name, False, {"ok": False, "error": reason}, check)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def execute(
    name: str,
    arguments: dict[str, Any],
    gate: PermissionGate,
    *,
    timeout: int = 30,
) -> ToolOutcome:
    if name == "file_read":
        return _file_read(str(arguments.get("path", "")), gate)
    if name == "file_write":
        return _file_write(str(arguments.get("path", "")), str(arguments.get("content", "")), gate)
    if name == "shell":
        return _shell(str(arguments.get("command", "")), gate, timeout)
    if name == "http_fetch":
        return _http(str(arguments.get("url", "")))
    if name == "file_edit":
        return _file_edit(
            str(arguments.get("path", "")),
            str(arguments.get("old", "")),
            str(arguments.get("new", "")),
            gate,
        )
    if name == "web_search":
        return _web_search(str(arguments.get("query", "")))
    if name == "open":
        return _open(str(arguments.get("app", "")), str(arguments.get("url", "")))
    if name == "clipboard":
        return _clipboard(str(arguments.get("action", "")), str(arguments.get("text", "")))
    if name == "close_ghosts":
        return _close_ghosts()
    return _refused(name or "tool", f"unknown tool {name}")


def _file_read(raw: str, gate: PermissionGate) -> ToolOutcome:
    if not raw.strip():
        return _refused("file_read", "path is empty")
    decision = gate.check_read(raw)
    if not decision.allowed or decision.path is None:
        return _refused("file_read", decision.reason)
    path = decision.path
    if not path.is_file():
        check = Check("file_read", str(path), "missing", False, "file not found")
        return ToolOutcome("file_read", False, {"ok": False, "error": "file not found", "path": str(path)}, check)
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > READ_LIMIT
    text = _clip(text, READ_LIMIT)
    check = verify_read(path, text, truncated=truncated)
    return ToolOutcome(
        "file_read",
        True,
        {"ok": True, "path": str(path), "content": text, "truncated": truncated},
        check,
    )


def _file_write(raw: str, content: str, gate: PermissionGate) -> ToolOutcome:
    if not raw.strip():
        return _refused("file_write", "path is empty")
    decision = gate.check_write(raw)
    if not decision.allowed or decision.path is None:
        return _refused("file_write", decision.reason)
    path = decision.path
    path.parent.mkdir(parents=True, exist_ok=True)
    claimed = str(path.resolve())
    if claimed in gate.claimed:
        return _refused("file_write", "another ghost is already writing this file")
    gate.claimed.add(claimed)
    path.write_text(content, encoding="utf-8")
    check = verify_write(path, content)
    return ToolOutcome(
        "file_write",
        check.ok,
        {"ok": check.ok, "path": str(path), "bytes": len(content.encode("utf-8"))},
        check,
    )


def _shell(command: str, gate: PermissionGate, timeout: int) -> ToolOutcome:
    if not command.strip():
        return _refused("shell", "command is empty")
    decision = gate.check_shell(command)
    if not decision.allowed:
        return _refused("shell", decision.reason)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=gate.workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        check = Check("shell", command, str(stdout), False, f"timed out after {timeout}s")
        return ToolOutcome("shell", False, {"ok": False, "error": check.conclusion}, check)
    stdout = _clip(completed.stdout or "", SHELL_LIMIT)
    stderr = _clip(completed.stderr or "", SHELL_LIMIT)
    check = verify_shell(command, completed.returncode, stdout, stderr)
    return ToolOutcome(
        "shell",
        check.ok,
        {
            "ok": check.ok,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        },
        check,
    )


def _file_edit(raw: str, old: str, new: str, gate: PermissionGate) -> ToolOutcome:
    if not raw.strip() or old == "":
        return _refused("file_edit", "path and old text are required")
    decision = gate.check_write(raw)
    if not decision.allowed or decision.path is None:
        return _refused("file_edit", decision.reason)
    path = decision.path
    if not path.is_file():
        check = Check("file_edit", str(path), "missing", False, "file not found")
        return ToolOutcome("file_edit", False, {"ok": False, "error": "file not found"}, check)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return _refused("file_edit", "old text was not in the file")
    if count > 1:
        return _refused("file_edit", "old text appears more than once")
    updated = text.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8")
    check = verify_write(path, updated)
    return ToolOutcome("file_edit", check.ok, {"ok": check.ok, "path": str(path)}, check)


def parse_search_results(html: str, limit: int = 5) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    for match in pattern.finditer(html or ""):
        title = re.sub(r"<[^>]+>", "", match.group(2))
        title = " ".join(title.split())
        href = match.group(1).replace("&amp;", "&")
        if title and href:
            found.append({"title": title, "url": href})
        if len(found) >= limit:
            break
    return found


def web_search(query: str, fetch=None) -> list[dict[str, str]]:
    import urllib.parse

    question = " ".join((query or "").split())
    if not question:
        raise ValueError("query is empty")
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(question)
    if fetch is None:
        request = urllib.request.Request(url, headers={"User-Agent": "GhostDesk/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read(HTTP_LIMIT).decode("utf-8", "replace")
    else:
        html = fetch(url)
    return parse_search_results(html)


def _web_search(query: str) -> ToolOutcome:
    try:
        results = web_search(query)
    except ValueError as exc:
        return _refused("web_search", str(exc))
    except urllib.error.URLError as exc:
        check = Check("web_search", query, str(exc.reason), False, "search failed")
        return ToolOutcome("web_search", False, {"ok": False, "error": str(exc.reason)}, check)
    check = Check("web_search", query, f"{len(results)} results", True, "search finished")
    return ToolOutcome("web_search", True, {"ok": True, "query": query, "results": results}, check)


def clipboard_run(action: str, text: str = "", runner=None) -> str:
    kind = (action or "").strip().lower()
    if kind not in {"get", "set"}:
        raise ValueError("clipboard action is get or set")
    if kind == "set" and text == "":
        raise ValueError("clipboard set needs text")
    if runner is not None:
        return runner(kind, text)
    if kind == "get":
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise OSError((completed.stderr or "clipboard read failed").strip())
        return completed.stdout or ""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
        input=text,
        capture_output=True,
        text=True,
        timeout=15,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise OSError((completed.stderr or "clipboard write failed").strip())
    return text


def _clipboard(action: str, text: str) -> ToolOutcome:
    try:
        value = clipboard_run(action, text)
    except ValueError as exc:
        return _refused("clipboard", str(exc))
    except (OSError, subprocess.TimeoutExpired) as exc:
        check = Check("clipboard", action, str(exc), False, "clipboard failed")
        return ToolOutcome("clipboard", False, {"ok": False, "error": str(exc)}, check)
    shown = value if action == "get" else "copied"
    check = Check("clipboard", action, shown[:200], True, "clipboard ready")
    return ToolOutcome("clipboard", True, {"ok": True, "action": action, "text": value if action == "get" else ""}, check)


def ancestor_pids() -> set[int]:
    import os

    protected = {os.getpid()}
    pid = os.getpid()
    if not os_name_is_windows():
        return protected
    for _ in range(8):
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").ParentProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        raw = (completed.stdout or "").strip()
        if not raw.isdigit():
            break
        parent = int(raw)
        if parent <= 0 or parent in protected:
            break
        protected.add(parent)
        pid = parent
    return protected


def extra_ghost_chats(processes: list[dict], protected: set[int]) -> list[int]:
    """Ghost Desk chat processes. The gateway and this window stay."""
    found: list[int] = []
    for proc in processes:
        name = str(proc.get("name") or "").lower()
        command = str(proc.get("command") or "").lower()
        pid = int(proc.get("pid") or 0)
        if pid <= 0 or pid in protected:
            continue
        if "gateway" in command:
            continue
        if name == "ghost.exe" or command.endswith("\\ghost.exe") or command.endswith("/ghost.exe") or command.strip() == "ghost":
            found.append(pid)
    return found


def close_extra_ghosts(list_processes=None, killer=None, protected=None) -> list[int]:
    import os

    if list_processes is None:
        list_processes = _windows_processes
    if protected is None:
        protected = ancestor_pids() if os_name_is_windows() else {os.getpid()}
    targets = extra_ghost_chats(list_processes(), protected)
    stop = killer or _kill_pid
    for pid in targets:
        stop(pid)
    return targets


def _windows_processes() -> list[dict]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return []
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    rows = raw if isinstance(raw, list) else [raw]
    return [
        {"pid": int(row.get("ProcessId") or 0), "name": row.get("Name") or "", "command": row.get("CommandLine") or ""}
        for row in rows
        if isinstance(row, dict)
    ]


def _kill_pid(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _close_ghosts() -> ToolOutcome:
    try:
        closed = close_extra_ghosts()
    except (OSError, subprocess.TimeoutExpired) as exc:
        check = Check("close_ghosts", "ghost", str(exc), False, "could not close")
        return ToolOutcome("close_ghosts", False, {"ok": False, "error": str(exc)}, check)
    if not closed:
        check = Check("close_ghosts", "ghost", "none", True, "only this Ghost Desk window is open")
        return ToolOutcome("close_ghosts", True, {"ok": True, "closed": []}, check)
    check = Check("close_ghosts", "ghost", ",".join(str(pid) for pid in closed), True, "closed extra Ghost Desk windows")
    return ToolOutcome("close_ghosts", True, {"ok": True, "closed": closed}, check)


def normalize_open(app: str, url: str) -> tuple[str, str]:
    """Turn 'chrome' plus 'youtube' into a Windows start target. Refuse anything else."""
    app_name = " ".join((app or "").split()).strip().lower()
    page = (url or "").strip()
    if app_name in _SITE_NAMES and not page:
        page = _SITE_NAMES[app_name]
        app_name = "chrome"
    if page.lower() in _SITE_NAMES:
        page = _SITE_NAMES[page.lower()]
    if page and not page.startswith(("http://", "https://")):
        if re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?", page):
            page = "https://" + page
        else:
            raise ValueError("only an http or https page can be opened")
    program = ""
    if app_name:
        program = _APP_NAMES.get(app_name, "")
        if not program:
            raise ValueError("that app is not in the open list")
    if not program and not page:
        raise ValueError("say which app or page to open")
    return program, page


def launch_open(app: str, url: str, runner=None) -> str:
    program, page = normalize_open(app, url)
    if os_name_is_windows():
        command = ["cmd", "/c", "start", ""]
        if program:
            command.append(program)
        if page:
            command.append(page)
    else:
        command = ["xdg-open", page or program]
    launch = runner or subprocess.Popen
    launch(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    shown = " ".join(part for part in (program or "browser", page) if part)
    return shown


def os_name_is_windows() -> bool:
    import os

    return os.name == "nt"


def _open(app: str, url: str) -> ToolOutcome:
    try:
        shown = launch_open(app, url)
    except ValueError as exc:
        return _refused("open", str(exc))
    except OSError as exc:
        check = Check("open", f"{app} {url}".strip(), str(exc), False, "could not start")
        return ToolOutcome("open", False, {"ok": False, "error": str(exc)}, check)
    check = Check("open", shown, "started", True, "opened on this computer")
    return ToolOutcome("open", True, {"ok": True, "opened": shown}, check)


def _http(url: str) -> ToolOutcome:
    if not url.startswith(("http://", "https://")):
        return _refused("http_fetch", "only http and https URLs are fetched")
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "GhostDesk/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = getattr(response, "status", 200)
            body = response.read(HTTP_LIMIT).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read(HTTP_LIMIT).decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        check = Check("http_fetch", url, str(exc.reason), False, "connection failed")
        return ToolOutcome("http_fetch", False, {"ok": False, "error": str(exc.reason), "url": url}, check)
    check = verify_http(url, int(status), body)
    return ToolOutcome(
        "http_fetch",
        check.ok,
        {"ok": check.ok, "url": url, "status": int(status), "body": body},
        check,
    )


def tool_message(call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}
