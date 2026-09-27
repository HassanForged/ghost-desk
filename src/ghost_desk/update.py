"""Self-update: the tap on the shoulder, and the `/update` install run.

Two jobs, both quiet by design:

1. Detection — read the installed commit from the pip dist's
   ``direct_url.json`` (``vcs_info.commit_id``), compare it against
   ``git ls-remote <repo> HEAD``. Runs once per boot in a background
   thread, ~10s after the UI settles. Any network failure or error
   stays silent: no blocking, no nagging.
2. ``/update`` — ``pip install --upgrade git+<repo>`` with output
   captured and hidden. The TUI in tui.py drives the dissolve /
   stitch / re-materialize show around it via :class:`UpdateSequence`.

The running process is still the old code after a successful install,
so the success copy is honest about needing a restart.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

REPO_URL = "https://github.com/HassanForged/ghost-desk.git"
INSTALL_SPEC = "git+" + REPO_URL

#: Seconds after boot before the background check runs (lets the UI settle).
CHECK_DELAY_S = 10.0
#: Timeout for the `git ls-remote` probe.
CHECK_TIMEOUT_S = 10.0
#: Timeout for the pip install itself.
INSTALL_TIMEOUT_S = 600.0

_SHA_RE = re.compile(r"[0-9a-f]{40}")


def installed_commit() -> str | None:
    """The commit this install came from, or None when unknowable.

    pip's git installs record it in the dist's ``direct_url.json``
    under ``vcs_info.commit_id``. Editable checkouts, sdists, and
    wheels have no such record — that is fine, we just stay silent.
    """
    try:
        dist = importlib.metadata.distribution("ghost-desk")
        text = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    vcs_info = data.get("vcs_info") or {}
    commit = (vcs_info.get("commit_id") or "").strip().lower()
    if not _SHA_RE.fullmatch(commit):
        return None
    return commit


def remote_head(timeout: float = CHECK_TIMEOUT_S) -> str | None:
    """The commit ``HEAD`` currently points at on GitHub. None on any error."""
    try:
        proc = subprocess.run(
            ["git", "ls-remote", REPO_URL, "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    match = _SHA_RE.search(proc.stdout or "")
    return match.group(0) if match else None


def newer_available(installed: str | None, remote: str | None) -> bool:
    """True when the remote commit differs from the installed one.

    Hashes carry no ordering, so any difference against the published
    HEAD counts as "newer". Unparseable or missing input is never an
    update — detection stays silent rather than guessing.
    """
    if not installed or not remote:
        return False
    mine = installed.strip().lower()
    theirs = remote.strip().lower()
    if not _SHA_RE.fullmatch(mine) or not _SHA_RE.fullmatch(theirs):
        return False
    return mine != theirs


def update_available(timeout: float = CHECK_TIMEOUT_S) -> str | None:
    """The remote commit when an update is available, else None.

    Never raises: network trouble, git trouble, or an unknowable
    installed commit all mean "stay silent".
    """
    try:
        remote = remote_head(timeout=timeout)
        if remote is None:
            return None
        installed = installed_commit()
        return remote if newer_available(installed, remote) else None
    except Exception:
        return None


@dataclass
class UpdateResult:
    """Outcome of the pip install run."""

    ok: bool
    commit: str | None = None  # installed commit after a successful install
    error: str = ""  # pip's error tail on failure


def _pip_error_tail(output: str, lines: int = 8) -> str:
    tail = (output or "").strip().splitlines()[-lines:]
    return "\n".join(tail).strip()


def install_update(timeout: float = INSTALL_TIMEOUT_S) -> UpdateResult:
    """Run ``pip install --upgrade git+<repo>`` with output hidden.

    Never raises: a pip that cannot even start is reported as a
    failure with a plain reason, not a traceback.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", INSTALL_SPEC],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        return UpdateResult(ok=False, error=f"pip did not run: {exc}")
    if proc.returncode != 0:
        tail = _pip_error_tail(proc.stderr) or _pip_error_tail(proc.stdout)
        return UpdateResult(ok=False, error=tail or "pip failed with no output")
    importlib.invalidate_caches()
    return UpdateResult(ok=True, commit=installed_commit())


# ---------------------------------------------------------------------------
# The /update show: dissolve -> stitch -> re-materialize.
# ---------------------------------------------------------------------------

#: Ticks (the TUI animates at 2 ticks/sec) for each scripted phase.
DISSOLVE_TICKS = 2
REMATERIALIZE_TICKS = 2

HOLD_STILL = "hold still…"
STITCHING = "stitching new sheets…"
TAP_LINE = "hey — new sheets just dropped. /update when you're ready."
RESTART_NOTE = "restart me to wear the new sheets."
DARK_LINE = "something moved in the dark: "


class UpdateSequence:
    """Frame-level state machine for the `/update` show.

    The TUI calls :meth:`tick` from its existing animation loop —
    no new timer thread. pip runs in a worker thread and drops its
    :class:`UpdateResult` into ``self.result`` when done.
    """

    def __init__(self) -> None:
        self.phase = "dissolve"  # dissolve -> stitch -> rematerialize -> done
        self.frame = 0
        self.result: UpdateResult | None = None
        self.finished = False

    @property
    def dissolve_frac(self) -> float:
        """How much of the ghost has dithered out (0..1)."""
        if self.phase == "dissolve":
            return min(1.0, (self.frame + 1) / DISSOLVE_TICKS)
        if self.phase == "stitch":
            return 1.0
        if self.phase == "rematerialize":
            return max(0.0, 1.0 - (self.frame + 1) / REMATERIALIZE_TICKS)
        return 0.0

    def tick(self) -> bool:
        """Advance one tick. Returns True when the show is over."""
        if self.finished:
            return True
        if self.phase == "dissolve":
            self.frame += 1
            if self.frame >= DISSOLVE_TICKS:
                self.phase = "stitch"
                self.frame = 0
        elif self.phase == "stitch":
            self.frame += 1
            if self.result is not None:
                self.phase = "rematerialize"
                self.frame = 0
        elif self.phase == "rematerialize":
            self.frame += 1
            if self.frame >= REMATERIALIZE_TICKS:
                self.phase = "done"
                self.finished = True
        return self.finished

    def final_lines(self) -> list[tuple[str, str]]:
        """Chat lines to print when the show finishes, as (role, text)."""
        result = self.result
        if result is not None and result.ok:
            short = (result.commit or "")[:7] or "????????"
            return [
                ("ghost", f"back. good as new. ({short})"),
                ("ghost", RESTART_NOTE),
            ]
        error = (result.error if result is not None else "") or "the update failed"
        return [("note", DARK_LINE + error)]


# ---------------------------------------------------------------------------
# The tap on the shoulder: once per boot, background thread, never nagging.
# ---------------------------------------------------------------------------


class UpdateChecker(threading.Thread):
    """Sleep ``delay``s, check once, call back at most once. Never raises."""

    def __init__(
        self,
        callback,
        *,
        delay: float = CHECK_DELAY_S,
        timeout: float = CHECK_TIMEOUT_S,
    ) -> None:
        super().__init__(daemon=True, name="ghost-update-check")
        self._callback = callback
        self._delay = delay
        self._timeout = timeout

    def run(self) -> None:
        try:
            if self._delay > 0:
                time.sleep(self._delay)
            remote = update_available(timeout=self._timeout)
            if remote:
                self._callback(remote)
        except Exception:
            pass


def start_update_check(callback, *, delay: float = CHECK_DELAY_S,
                       timeout: float = CHECK_TIMEOUT_S) -> UpdateChecker:
    """Launch the one-shot background update check. Never raises."""
    checker = UpdateChecker(callback, delay=delay, timeout=timeout)
    try:
        checker.start()
    except Exception:
        pass
    return checker
