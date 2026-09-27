# Ghost Desk

Local terminal agent. Your brain, your machine, your notes.

![Ghost Desk session](docs/welcome.png)

Ghost Desk lives in your terminal. It reads what's on your machine, remembers
what matters, and does the work — but it plans before it builds, and writes
nothing until you approve.

## The safety model

Three rules, enforced in code, not in prompts:

- **Reads stay local.** Shell, files, and memory all run on this machine.
- **Writes wait for a yes.** Any file write, edit, or shell command that changes
  state asks first. The plan is shown up front; nothing is written until you
  approve it.
- **Memory stays on disk.** Sessions, notes, and lessons live in
  `~/.ghost-desk/`. Nothing phones home.

## Brains

Pick one on first boot — or switch anytime with `/model`:

![First boot](docs/boot.png)

| # | Brain | What it needs |
|---|-------|---------------|
| 1 | ChatGPT subscription | OAuth sign-in |
| 2 | Claude subscription | OAuth sign-in |
| 3 | Grok subscription | OAuth sign-in |
| 4 | API key | Your own key |
| 5 | Local model | Ollama on `127.0.0.1:11434` — fully offline |

## Tools

Ten tools, no more: `shell`, `file_read`, `file_write`, `file_edit`,
`http_fetch`, `web_search`, `open`, `clipboard`, `little_ghost`,
`close_ghosts`.

`little_ghost` splits a task across parallel subagents when a job is big
enough to deserve it. Every tool call is checked after it runs, and the work
is re-read against the plan before it's called done.

## Memory

- **Sessions** persist per conversation — `/sessions` lists them,
  `/resume` picks one back up.
- **Notes** are verified facts the desk keeps — `/memory` shows recent ones,
  `/recall` searches everything ever said.
- **Lessons** start as drafts. Correct the desk ("no, do it this way") and the
  correction is staged, not applied — `/promote` saves it into memory.
- **Skills** fold under a few broad parents so the context stays small;
  `/curate` merges duplicates and prunes dead ones.
- **Background jobs** run on a schedule — `/bg add <schedule> <prompt>`,
  `/bg digest` reads what they found.

## Commands

`/help` lists everything in the terminal. The ones you'll use most:

```text
/new          start a fresh conversation
/plan         show the current plan
/model        show or switch the model
/memory       recent verified notes
/recall       search past sessions
/resume       continue a session
/promote      save the newest correction into memory
/bg           background jobs
/quit         leave
```

Enter sends. Alt-Enter inserts a newline. Ctrl+C cancels the current turn.

## Install

### Mac and Linux

```bash
curl -fsSL https://raw.githubusercontent.com/HassanForged/ghost-desk/main/install.sh | bash
```

Then open a new terminal:

```bash
ghost
```

Needs Git. On a Mac with system Python 3.9 that's fine — the installer fetches
Python 3.12 for Ghost Desk only (no Homebrew) and puts `ghost` in
`~/.local/bin`. If `ghost` isn't found:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Windows

```powershell
git clone https://github.com/HassanForged/ghost-desk.git
cd ghost-desk
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
ghost
```

## Try this

```text
build a one-page site for you@example.com
```

Ghost Desk must show a plan and write nothing until you approve. That's the
whole product in one sentence.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```
