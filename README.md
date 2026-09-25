# Ghost Desk

Local terminal agent. Your brain, your machine, your notes.

- Plans before it builds, and writes nothing until you approve.
- Checks the work after it writes.
- Keeps memory on this machine.
- Folds skills under a few broad ones.
- Runs little ghosts for split tasks.
- Compaction stays in the background.

## Install

### Mac and Linux

Paste this:

```bash
curl -fsSL https://raw.githubusercontent.com/HassanForged/ghost-desk/main/install.sh | bash
```

Then open a new terminal and run:

```bash
ghost
```

Needs Git. System Python 3.9 on a Mac is fine — the installer fetches Python 3.12 for Ghost Desk only (no Homebrew). It puts `ghost` in `~/.local/bin`.

If `ghost` is not found:

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

Ghost Desk must show a plan and write nothing until you approve.
