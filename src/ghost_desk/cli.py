"""ghost — local CLI agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ghost_desk import __version__
from ghost_desk.background import parse_schedule, write_digest
from ghost_desk.config import Config, SetupError, load_config, needs_setup, setup_interactive
from ghost_desk.curator import curate
from ghost_desk.memory import Memory
from ghost_desk.skills import ensure_skills
from ghost_desk.tui import export_now, run_tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghost",
        description=(
            "Ghost Desk — local CLI agent. "
            "Code stays on disk. Only the chat API leaves the machine."
        ),
    )
    parser.add_argument("--version", action="version", version=f"ghost-desk {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("setup", help="Choose a brain: subscription, API key, or a local model")
    commands.add_parser("smoke", help="Ask the saved brain one short question without printing secrets")
    export = commands.add_parser("export", help="Write sessions, notes, skills, and research as Markdown")
    export.add_argument("--out", default="", help="Output folder. Default: data_dir/export")
    commands.add_parser("curate", help="Rewrite parent skills, merge duplicates, and prune dead children")
    commands.add_parser("digest", help="Write the monthly review digest")
    commands.add_parser("sessions", help="List saved sessions")
    commands.add_parser("gateway", help="Listen on Telegram and answer with this desk")
    bg = commands.add_parser("bg", help="List or add a background job")
    bg.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    bg.add_argument("schedule", nargs="?", default="")
    bg.add_argument("prompt", nargs="?", default="")
    return parser


def _redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "stored")
    return cleaned


def _secrets(config: Config) -> list[str]:
    from ghost_desk.oauth import load_tokens

    found: list[str] = []
    if config.api_key:
        found.append(config.api_key)
    tokens = load_tokens(config.data_path() / "auth.json")
    if tokens is not None:
        found.append(tokens.access_token)
        if tokens.refresh_token:
            found.append(tokens.refresh_token)
    return found


def run_smoke(config: Config, client=None) -> int:
    """One short reply from the saved brain. Secrets stay out of the output."""
    if not config.config_file().is_file():
        print("no config yet. Run ghost setup.", file=sys.stderr)
        return 2
    secrets = _secrets(config)
    if client is None:
        from ghost_desk.providers import build_client

        client = build_client(config)
    try:
        reply = client.complete(
            [{"role": "user", "content": "Reply with the single word pong."}],
            stream=False,
        )
        text = str(getattr(reply, "text", "") or "")
    except Exception as exc:
        print(_redact(str(exc), secrets), file=sys.stderr)
        return 2
    shown = _redact(text, secrets).strip()
    if not shown:
        print("provider returned an empty reply", file=sys.stderr)
        return 2
    provider = config.provider or "openai_compatible"
    auth = config.auth_mode or "api_key"
    line = f"smoke ok  provider={provider}  model={config.model}  auth={auth}"
    body = shown.splitlines()[0][:200]
    if any(secret and secret in f"{line}\n{body}" for secret in secrets):
        print("smoke printed a secret", file=sys.stderr)
        return 2
    print(line)
    print(body)
    return 0


def play_boot(first: bool) -> str:
    from ghost_desk.boot import run_boot

    return run_boot(skip_to_menu=not first)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()
    if args.command == "smoke":
        return run_smoke(config)
    if args.command == "setup":
        screen = None
        try:
            from ghost_desk.boot import BootScreen

            screen = BootScreen()
            screen.enter()
            screen.play_checks(signed=not needs_setup(config))
            number = screen.choose()
            setup_interactive(
                cfg=config,
                boot_choice=number,
                input_fn=screen.ask,
                output_fn=screen.log,
            )
            screen.log("brain locked")
            screen.log("ready")
        except SetupError as exc:
            print(exc, file=sys.stderr)
            return 2
        except (EOFError, KeyboardInterrupt):
            print("setup did not finish", file=sys.stderr)
            return 2
        finally:
            if screen is not None:
                screen.leave()
        return 0
    if args.command == "export":
        target = Path(args.out).expanduser() if args.out else None
        path = export_now(config, target)
        print(f"exported {path}")
        return 0
    if args.command == "curate":
        root = ensure_skills(config.data_path())
        stats = curate(root)
        print(f"curated skills: wrote {stats['written']}, removed {stats['removed']}, parents {stats['parents']}")
        return 0
    if args.command == "gateway":
        from ghost_desk.gateway import serve

        return serve(config)
    if args.command == "sessions":
        memory = Memory(config.data_path())
        try:
            rows = memory.list_sessions()
            if not rows:
                print("No sessions yet.")
                return 0
            for row in rows:
                print(f"{row['session_id']}\t{row['ts']}\t{row['messages']}")
            return 0
        finally:
            memory.close()
    if args.command == "digest":
        memory = Memory(config.data_path())
        try:
            print(write_digest(memory))
        finally:
            memory.close()
        return 0
    if args.command == "bg":
        memory = Memory(config.data_path())
        try:
            if args.action == "list":
                jobs = memory.list_jobs()
                if not jobs:
                    print("No background jobs.")
                    return 0
                for job in jobs:
                    print(f"{job['name']}\t{job['schedule']}\t{job['prompt']}")
                return 0
            if not args.schedule or not args.prompt:
                print("Usage: ghost bg add <schedule> <prompt>", file=sys.stderr)
                return 2
            name = "job-" + args.schedule
            memory.add_job(name, parse_schedule(args.schedule), args.prompt)
            print(f"added {name}")
            return 0
        finally:
            memory.close()
    return run_tui(config)


if __name__ == "__main__":
    raise SystemExit(main())
