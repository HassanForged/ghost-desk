"""Root wrapper so `python ghost_boot.py` still works."""

from ghost_desk.boot import *  # noqa: F403
from ghost_desk.boot import run_boot

if __name__ == "__main__":
    import sys

    try:
        print(run_boot(skip_to_menu="--menu" in sys.argv))
    except KeyboardInterrupt:
        raise SystemExit(130)
