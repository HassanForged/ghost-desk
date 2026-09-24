"""Little ghosts. One level deep. A successor sees the task and the handoff, not the old transcript."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ghost_desk.config import Config

# Used only when that route's key is actually set. Otherwise the desk model is used.
_ROUTE_DEFAULTS = {
    "design": "claude-3-5-sonnet",
    "code": "grok-3",
    "research": "gpt-4o-mini",
}
_ROUTE_ENV = {
    "design": ("GHOST_DESIGN_API_KEY", "GHOST_DESIGN_BASE_URL", "GHOST_DESIGN_MODEL"),
    "code": ("GHOST_CODE_API_KEY", "GHOST_CODE_BASE_URL", "GHOST_CODE_MODEL"),
    "research": ("GHOST_RESEARCH_API_KEY", "GHOST_RESEARCH_BASE_URL", "GHOST_RESEARCH_MODEL"),
}


@dataclass
class Handoff:
    done: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    confidence: str = "low"

    def text(self) -> str:
        def block(title: str, items: list[str]) -> str:
            if not items:
                return f"{title}: none"
            return title + ":\n" + "\n".join(f"- {item}" for item in items)

        return "\n".join(
            [
                block("done", self.done),
                block("tried", self.tried),
                block("failed", self.failed),
                block("remaining", self.remaining),
                f"files_changed: {', '.join(self.files_changed) if self.files_changed else 'none'}",
                f"confidence: {self.confidence}",
                "summary:",
                self.summary or "(empty)",
            ]
        )


def route(kind: str, config: Config) -> Config:
    spec = _ROUTE_ENV.get(kind)
    if spec is None:
        return config
    key_name, url_name, model_name = spec
    key = os.environ.get(key_name, "").strip()
    if not key:
        return config
    return Config(
        api_key=key,
        base_url=os.environ.get(url_name, "").strip() or config.base_url,
        model=os.environ.get(model_name, "").strip() or _ROUTE_DEFAULTS[kind],
        working_directory=config.working_directory,
        data_dir=config.data_dir,
        provider="openai_compatible",
        auth_mode="api_key",
    )


def write_handoff(handoff: Handoff, data_dir, task: str):
    from pathlib import Path
    from datetime import datetime, timezone

    folder = Path(data_dir) / "handoffs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = folder / f"{stamp}.md"
    body = "\n".join(
        [
            f"# handoff {stamp}",
            "",
            f"goal: {task.strip() or '(none)'}",
            "",
            handoff.text(),
            "",
            "next command: read this handoff and continue. Do not restart from zero.",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")
    return path


def handoff_from(result, task: str) -> Handoff:
    done: list[str] = []
    failed: list[str] = []
    tried: list[str] = []
    files: list[str] = []
    for check in result.report.checks:
        tried.append(f"{check.action} {check.detail}")
        if check.ok:
            done.append(check.line())
        else:
            failed.append(check.line())
        if check.action == "file_write" and check.ok:
            files.append(check.detail)
    remaining = list(failed)
    if result.stop_reason != "stop":
        remaining.append(f"stopped: {result.stop_reason}")
    if not remaining and task:
        remaining = []
    if done and not failed:
        confidence = "high"
    elif done:
        confidence = "medium"
    else:
        confidence = "low"
    return Handoff(
        done=done,
        tried=tried,
        failed=failed,
        remaining=remaining,
        summary=(result.text or "")[:2000],
        files_changed=files,
        confidence=confidence,
    )


def spawn(
    task: str,
    *,
    kind: str,
    config: Config,
    gate,
    memory,
    depth: int,
    prior: str = "",
    client=None,
    client_factory=None,
    budget=None,
) -> Handoff:
    if depth > 0:
        raise RuntimeError("a little ghost cannot spawn another")
    from ghost_desk.agent import Budget, DeskSession, run_turn

    routed = route(kind, config)
    if client is None:
        if client_factory is not None:
            client = client_factory(routed)
        else:
            from ghost_desk.providers import build_client

            client = build_client(routed)
    user = task.strip() + "\nFinish an existing file in place. Do not create a second copy."
    if prior.strip():
        user = user + "\n\nPrevious handoff:\n" + prior.strip()
    ghost_budget = budget or Budget(max_turns=4, max_tokens=20_000, max_seconds=60)
    result = run_turn(
        user,
        config=routed,
        memory=memory,
        session=DeskSession(),
        client=client,
        gate=gate,
        spawn_fn=None,
        depth=1,
        budget=ghost_budget,
    )
    handoff = handoff_from(result, task)
    if config.data_dir:
        write_handoff(handoff, config.data_dir, task)
    return handoff
