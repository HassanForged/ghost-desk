# Ghost Desk

Local terminal agent. Your brain, your machine, your notes.

- Plans before it builds, and writes nothing until you approve.
- Checks the work after it writes.
- Keeps memory on this machine.
- Folds skills under a few broad ones.
- Runs little ghosts for split tasks.
- Compaction stays in the background.

## Install

Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
ghost --help
```

On macOS or Linux, activate with `source .venv/bin/activate`.

The stub and local Ollama need no API key.

## Try this

```text
build a one-page site for you@example.com
```

Ghost Desk must show a plan and write nothing until you approve.
