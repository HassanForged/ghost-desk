"""Call the model, run tools, append the checked results, stop at the budget."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ghost_desk.client import ClientError, MissingKey
from ghost_desk.compact import Compactor, extractive
from ghost_desk.config import Config
from ghost_desk.memory import Memory
from ghost_desk.notes import record_verified
from ghost_desk.permissions import PermissionGate
from ghost_desk.plan import PlanSession, verify_checklist
from ghost_desk.verify import Check, VerificationReport, repair_near_facts, verify_facts
from ghost_desk.skills import render_parents
from ghost_desk.tools import execute, schemas, tool_message

_BASE = """You are Ghost Desk, a local harness in a conversation, like Hermes and OpenClaw. The signed-in model is the brain. This computer is the body.
You are allowed to use this PC when the person asks. Open apps, search, read and edit files, run shell, and use the clipboard.
Talk with them, then do the work. Do not say you are not allowed to use the computer.
You can search the web, fetch a page, read and edit files, run shell, open local apps, and use the clipboard.
When they ask to open Chrome, YouTube, or another app on this computer, call the open tool. That launch is allowed. Do not refuse it for being outside the workspace.
When they ask to close extra Ghost Desk windows, call close_ghosts. That leaves this window and the Telegram gateway running.
Do not invent files, exit codes, or HTTP statuses.
Reads inside the workspace are allowed. Writes and destructive commands wait for a yes.
If a plan is not approved, do not write files.
A little ghost is one level deep. Trust a file it names only after this desk re-reads it."""

PERSONALITIES = {
    "helpful": "Be warm and plain-spoken. Stay with the topic they brought up. If they say hello, say hello back and wait for what they want.",
    "concise": "Be brief, but still answer the person. No status-report voice.",
    "technical": "Talk like a teammate who is pairing with them. Name the concrete next step, then do it if they asked.",
}


@dataclass
class Budget:
    max_turns: int = 8
    max_tokens: int = 80_000
    max_seconds: float = 90


@dataclass
class AgentResult:
    text: str
    report: VerificationReport
    stop_reason: str


@dataclass
class DeskSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    history: list[dict] = field(default_factory=list)
    plan: PlanSession = field(default_factory=PlanSession)
    compactor: Compactor = field(default_factory=Compactor)
    model_override: str = ""
    parents_text: str = ""
    personality: str = "helpful"


def _topic(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9_-]{4,}", text or "")
    return " ".join(words[:4]) or "desk"


def _note_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_-]{4,}", (text or "").lower())


def system_prompt(
    session: DeskSession,
    memory: Memory,
    user_text: str,
    workspace: Path | None = None,
    data_dir: Path | None = None,
) -> str:
    parts = [_BASE, PERSONALITIES.get(session.personality, PERSONALITIES["helpful"])]
    if data_dir is not None and workspace is not None:
        from ghost_desk.context import load_context

        loaded = load_context(data_dir, workspace)
        if loaded:
            parts.append(loaded)
    if session.parents_text:
        parts.append("Skills:\n" + session.parents_text)
    notes = memory.search_notes(_note_words(user_text), limit=5)
    if notes:
        lines = [f"- {note['topic']}: {note['body']}" for note in notes]
        parts.append("Verified notes:\n" + "\n".join(lines))
    draft = session.plan.draft
    if draft is not None and draft.status == "approved":
        checklist = "\n".join(f"- {item}" for item in draft.checklist)
        parts.append(
            "Approved plan:\n"
            + draft.request
            + "\n"
            + checklist
            + "\nWrite only what this plan lists. The desk re-reads files after you write them."
        )
    return "\n\n".join(parts)


def _keep_facts(data_dir: Path, text: str) -> None:
    from ghost_desk.context import remember
    from ghost_desk.plan import facts_in

    for email in facts_in(text):
        remember(data_dir, "memory", f"email: {email}")
    named = re.search(r"(?i)the name is\s+(.+?)(?:\.|,|$)", text or "")
    if named:
        remember(data_dir, "memory", "name: " + named.group(1).strip())
    from ghost_desk.skills import file_learned_trick

    trick = re.search(r"(?i)^learn this trick:\s*(\w+)", text or "")
    if trick:
        file_learned_trick(data_dir / "skills", trick.group(1).lower())


def _missing_path_reply(text: str, workspace: Path) -> str:
    match = re.search(r"(?i)what is inside\s+(\S+)", text or "")
    if not match:
        return ""
    raw = match.group(1).strip("?.\"'")
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    if path.is_file():
        return ""
    return f"{path} is missing."


def _read_config_model(text: str, config: Config, report: VerificationReport) -> str:
    if not re.search(r"(?i)model", text or "") or not re.search(r"(?i)config", text or ""):
        return ""
    path = config.config_file()
    if not path.is_file():
        report.add(Check("file_read", str(path), "missing", False, "config file is missing"))
        return "config.json is missing."
    body = path.read_text(encoding="utf-8")
    report.add(Check("file_read", str(path), body[:200], True, "read config"))
    return "Read config file " + str(path) + " model=" + config.model


def _refuse_done_without_files(user: str, reply: str, workspace: Path) -> str:
    if not re.search(r"(?i)\b(done|finished|complete|written)\b", f"{user}\n{reply}"):
        return reply
    names = re.findall(r"[\w.-]+\.(?:html|css|js|txt|md|py)", f"{user}\n{reply}")
    missing = []
    for name in names:
        path = workspace / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(name)
    if not missing:
        return reply
    return "Not done. Missing: " + ", ".join(dict.fromkeys(missing))


def _assistant_message(text: str, tool_calls) -> dict:
    if not tool_calls:
        return {"role": "assistant", "content": text}
    payload = []
    for call in tool_calls:
        payload.append(
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
        )
    return {"role": "assistant", "content": text or None, "tool_calls": payload}


def _log(memory: Memory, session: DeskSession, role: str, content: str, raw: dict | None = None) -> None:
    memory.add_message(session.id, role, content, raw)


def _verify_ghost_files(paths: list[str], report: VerificationReport) -> str:
    """Re-read every path a little ghost claims it changed. Downgrade trust when one is missing."""
    missing = False
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace")[:200]
            report.add(Check("file_read", str(path), body, True, "re-read before trust"))
        else:
            missing = True
            report.add(Check("file_read", str(path), "missing", False, "ghost named a file that is not on disk"))
    return "low" if missing else ""


def run_turn(
    text: str,
    *,
    config: Config,
    memory: Memory,
    session: DeskSession,
    client=None,
    gate: PermissionGate | None = None,
    spawn_fn: Callable | None = None,
    on_text: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    depth: int = 0,
    budget: Budget | None = None,
) -> AgentResult:
    budget = budget or Budget()
    status = on_status or (lambda _note: None)
    workspace = config.workspace()
    gate = gate or PermissionGate(workspace)
    report = VerificationReport()

    if depth == 0:
        from ghost_desk.context import draft_lesson, expand_mentions

        text = expand_mentions(text, workspace)
        draft_lesson(config.data_path(), text)
        (config.data_path() / "current_session.txt").write_text(session.id, encoding="utf-8")
        _keep_facts(config.data_path(), text)
        missing = _missing_path_reply(text, workspace)
        if missing:
            reply = {"role": "assistant", "content": missing}
            session.history.append(user_message := {"role": "user", "content": text})
            _log(memory, session, "user", text, user_message)
            session.history.append(reply)
            _log(memory, session, "assistant", missing, reply)
            return AgentResult(missing, report, "stop")
        forced = _read_config_model(text, config, report)
        if forced:
            text = text + "\n\n" + forced

    user_message = {"role": "user", "content": text}
    session.history.append(user_message)
    _log(memory, session, "user", text, user_message)

    if depth == 0:
        prior = "\n".join(
            message["content"]
            for message in session.history[:-1]
            if message.get("role") == "user" and isinstance(message.get("content"), str)
        )
        hold = session.plan.handle(text, prior=prior)
        if hold is not None:
            reply = {"role": "assistant", "content": hold}
            session.history.append(reply)
            _log(memory, session, "assistant", hold, reply)
            return AgentResult(hold, report, "plan_wait")

    if client is None:
        from ghost_desk.providers import build_client

        try:
            client = build_client(config)
        except MissingKey as exc:
            message = str(exc)
            reply = {"role": "assistant", "content": message}
            session.history.append(reply)
            _log(memory, session, "assistant", message, reply)
            return AgentResult(message, report, "no_key")

    from ghost_desk.background import observe_message

    if depth == 0:
        observe_message(memory, text)

    def summarize(messages: list[dict]) -> str:
        return extractive(messages)

    session.compactor.maybe_start(session.history, summarize)
    session.compactor.wait_if_needed(session.history, status)
    before = len(session.history)
    session.history = session.compactor.apply(session.history)
    if len(session.history) < before and session.history:
        from ghost_desk.context import flush_summary

        flush_summary(config.data_path(), str(session.history[0].get("content") or ""))

    prompt = system_prompt(session, memory, text, workspace=workspace, data_dir=config.data_path())
    messages: list[dict] = [{"role": "system", "content": prompt}, *session.history]
    tools = schemas(include_ghost=depth == 0 and spawn_fn is not None)
    model = session.model_override or config.model
    tokens = 0
    started = time.monotonic()
    final = ""
    reason = "stop"
    used_fallback = False

    for _turn in range(budget.max_turns):
        if time.monotonic() - started > budget.max_seconds:
            reason = "wall_clock"
            break
        if tokens >= budget.max_tokens:
            reason = "tokens"
            break
        status("waiting for model")
        try:
            response = client.complete(
                messages,
                tools or None,
                model=model,
                stream=True,
                on_text=on_text,
            )
        except MissingKey as exc:
            message = str(exc)
            reply = {"role": "assistant", "content": message}
            session.history.append(reply)
            _log(memory, session, "assistant", message, reply)
            return AgentResult(message, report, "no_key")
        except ClientError as exc:
            message = str(exc)
            if not used_fallback:
                from ghost_desk.context import fallback_worthy, make_fallback

                if fallback_worthy(message):
                    alternate = make_fallback(config)
                    if alternate is not None:
                        used_fallback = True
                        client = alternate
                        status("fallback provider")
                        continue
            reply = {"role": "assistant", "content": message}
            session.history.append(reply)
            _log(memory, session, "assistant", message, reply)
            return AgentResult(message, report, "api_error")
        except KeyboardInterrupt:
            status("cancelled")
            message = "cancelled"
            reply = {"role": "assistant", "content": message}
            session.history.append(reply)
            _log(memory, session, "assistant", message, reply)
            return AgentResult(message, report, "cancelled")
        tokens += response.prompt_tokens + response.completion_tokens
        assistant = _assistant_message(response.text, response.tool_calls)
        messages.append(assistant)
        session.history.append(assistant)
        _log(memory, session, "assistant", response.text or "", assistant)
        if not response.tool_calls:
            final = response.text or ""
            reason = "stop"
            break
        for call in response.tool_calls:
            status(f"tool {call.name}")
            if call.name == "little_ghost":
                outcome_payload, ghost_text = _spawn_ghost(
                    call.arguments,
                    config=config,
                    gate=gate,
                    memory=memory,
                    depth=depth,
                    spawn_fn=spawn_fn,
                    report=report,
                )
                tool_payload = tool_message(call.id, outcome_payload)
                messages.append(tool_payload)
                session.history.append(tool_payload)
                _log(memory, session, "tool", ghost_text, tool_payload)
                continue
            outcome = execute(call.name, call.arguments, gate)
            if outcome.check is not None:
                report.add(outcome.check)
            if outcome.name == "http_fetch" and outcome.ok:
                memory.add_research(
                    topic=str(outcome.payload.get("url", "http")),
                    source=str(outcome.payload.get("url", "")),
                    confidence="high" if outcome.payload.get("status") == 200 else "medium",
                    body=str(outcome.payload.get("body", ""))[:500],
                )
            tool_payload = tool_message(call.id, outcome.payload)
            messages.append(tool_payload)
            session.history.append(tool_payload)
            _log(memory, session, "tool", outcome.check.line() if outcome.check else "", tool_payload)
    else:
        reason = "max_turns"

    mutated = any(check.action in {"file_write", "shell"} and check.ok for check in report.checks)
    draft = session.plan.draft
    if depth == 0 and draft is not None and draft.status == "approved":
        if mutated:
            for check in verify_checklist(draft.checklist, workspace):
                report.add(check)
        repair_near_facts(draft.facts, workspace)
        for check in verify_facts(draft.facts, workspace):
            report.add(check)
        if any(not check.ok for check in report.checks):
            from ghost_desk.subagents import handoff_from, write_handoff

            handoff = handoff_from(AgentResult(final, report, reason), text)
            path = write_handoff(handoff, config.data_path(), text)
            final = (final + f"\n\nHandoff written: {path}").strip()

    record_verified(memory, session.id, report, _topic(text))
    report_text = report.text()
    final = _refuse_done_without_files(text, final, workspace)
    if reason != "stop":
        final = f"Stopped: {reason}.\n{report_text}"
    elif report.checks:
        final = (final + "\n\n" + report_text).strip()
    elif not final:
        final = report_text

    closing = {"role": "assistant", "content": final}
    session.history.append(closing)
    _log(memory, session, "assistant", final, closing)
    return AgentResult(final, report, reason)


def _spawn_ghost(arguments: dict, *, config, gate, memory, depth: int, spawn_fn, report: VerificationReport):
    if depth > 0 or spawn_fn is None:
        reason = "a little ghost cannot spawn another"
        report.add(Check("little_ghost", reason, "refused", False, reason))
        return {"ok": False, "error": reason}, reason
    task = str(arguments.get("task", "")).strip()
    if not task:
        reason = "little ghost task is empty"
        report.add(Check("little_ghost", reason, "refused", False, reason))
        return {"ok": False, "error": reason}, reason
    handoff = spawn_fn(
        task=task,
        kind=str(arguments.get("kind", "code")),
        prior=str(arguments.get("prior_handoff") or ""),
        config=config,
        gate=gate,
        memory=memory,
        depth=depth,
    )
    downgrade = _verify_ghost_files(list(handoff.files_changed), report)
    if downgrade:
        handoff.confidence = downgrade
        handoff.failed.append("a claimed file was not on disk")
    report.add(
        Check(
            "little_ghost",
            task,
            handoff.summary[:240],
            handoff.confidence != "low",
            f"confidence {handoff.confidence}",
        )
    )
    payload = {
        "ok": handoff.confidence != "low",
        "handoff": handoff.text(),
        "confidence": handoff.confidence,
        "files_changed": handoff.files_changed,
    }
    return payload, handoff.text()
