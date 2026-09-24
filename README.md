# Ghost Desk

Local CLI agent. The process, tools, skills, memory, and logs stay on your machine. The only call that leaves is the chat API. Python 3.11 or newer. No API key required for the stub / local Ollama path.

## Install

```powershell
cd C:\Users\Hassan\ghost-desk
py -m pip install -e ".[dev]"
ghost --help
```

## Choose a brain

```powershell
ghost setup
```

`ghost setup` uses the same provider list as Hermes and OpenClaw:

1. ChatGPT or Codex Subscription (device code)
2. OpenAI API key
3. Claude (API key, or a Claude Code setup-token)
4. xAI Grok OAuth (SuperGrok / Premium+). Uses a Grok CLI login already on this machine when one exists
5. xAI API key
6. Ollama, no key
7. OpenRouter or any OpenAI-compatible URL

Running it again updates the saved provider. Memory, skills, and sessions stay. Subscription tokens are stored in `~/.ghost-desk/auth.json` and are not printed. An API key can also be set with `GHOST_API_KEY`, `GHOST_BASE_URL`, and `GHOST_MODEL`.

Set `fallback_provider` and `fallback_model` in the config when a second brain should take rate limits and outages. Auth failures stay on the saved brain.

The desk reads `SOUL.md`, `USER.md`, and `MEMORY.md` from the data folder, and the first project file it finds: `.ghost.md`, `AGENTS.md`, `CLAUDE.md`, or `.cursorrules`. `@path` pastes a workspace file into the message. A correction stays a draft until `/promote`. `/resume` continues a saved session.

## Safety

- Reads inside the working folder are automatic.
- Writes in that folder ask the first time for each path.
- Destructive commands ask every time.
- `.env`, SSH keys, and paths outside the folder are refused unless you explicitly allow them.

## v0

`ghost` opens the desk. `ghost setup` chooses the brain. `ghost smoke` asks it one short question. `ghost export` writes Markdown. `ghost curate` rewrites skills. Telegram comes later. A local model runs only when the saved brain is Ollama or another URL on this machine.
