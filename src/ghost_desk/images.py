"""The literal ghost picture, for terminals that can show real images.

Kitty graphics protocol or iTerm2 inline images. Opt-in only via
GHOST_DESK_IMG=kitty|iterm2; the default everywhere is the pixel-art
portrait. A wrong guess never breaks the session.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

ASSET = Path(__file__).resolve().parent / "assets" / "ghost.png"

_CHUNK = 4096
_IMAGE_ID = 31


def detect_protocol() -> str | None:
    """Return 'kitty', 'iterm2', or None.

    The real picture is opt-in only: GHOST_DESK_IMG=kitty|iterm2.
    Everything else (including unset) gets the pixel-art portrait.
    """
    override = os.environ.get("GHOST_DESK_IMG", "").strip().lower()
    if override in ("kitty", "iterm2"):
        return override
    return None


def png_bytes() -> bytes:
    return ASSET.read_bytes()


def place(row: int, col: int) -> str:
    """1-based cursor positioning."""
    return f"\x1b[{row};{col}H"


def kitty_show(*, cols: int, rows: int, image_id: int = _IMAGE_ID) -> str:
    """Transmit-and-display the PNG, scaled to a cols x rows cell box."""
    raw = base64.b64encode(png_bytes()).decode("ascii")
    head = f"\x1b_Ga=T,f=100,q=2,i={image_id},c={cols},r={rows},m="
    chunks = [raw[i : i + _CHUNK] for i in range(0, len(raw), _CHUNK)] or [""]
    parts = []
    for n, chunk in enumerate(chunks):
        more = 1 if n < len(chunks) - 1 else 0
        parts.append(f"{head}{more};{chunk}\x1b\\")
    return "".join(parts)


def kitty_delete(image_id: int = _IMAGE_ID) -> str:
    return f"\x1b_Ga=d,d=i,q=2,i={image_id}\x1b\\"


def iterm2_show(*, cols: int, rows: int) -> str:
    """iTerm2 inline image, scaled to a cols x rows cell box."""
    data = png_bytes()
    raw = base64.b64encode(data).decode("ascii")
    name = base64.b64encode(b"ghost.png").decode("ascii")
    return (
        f"\x1b]1337;File=name={name};size={len(data)};"
        f"width={cols};height={rows};preserveAspectRatio=1;inline=1:{raw}\x07"
    )


def show_sequence(protocol: str, *, cols: int, rows: int, image_id: int = _IMAGE_ID) -> str:
    if protocol == "kitty":
        return kitty_show(cols=cols, rows=rows, image_id=image_id)
    return iterm2_show(cols=cols, rows=rows)


def install_picture(output, protocol: str | None, chrome: dict, busy_fn) -> callable:
    """Paint the real picture over the ghost pane after each screen flush.

    The picture plane survives prompt_toolkit redraws, so it only repaints
    when its placement changes (resize, or the busy bob on kitty).
    Returns a cleanup callable. Never raises.
    """
    noop = lambda: None
    if not protocol:
        return noop
    if not all(hasattr(output, name) for name in ("write_raw", "flush", "get_size")):
        return noop
    real_flush = output.flush
    placed: dict = {}

    def geometry():
        try:
            size = output.get_size()
        except Exception:
            return None
        srows, scols = size.rows, size.columns
        if scols < 60 or srows < 20:
            return None
        try:
            busy, tick = busy_fn()
        except Exception:
            busy, tick = False, 0
        # iTerm2 has no image delete, so it stays perfectly still.
        bob = (tick % 2) if (busy and protocol == "kitty") else 0
        row = 1 + bob
        col = scols - chrome["ghost_width"]  # 1-based; pane is the last width+1 cols
        return (row, col, chrome["ghost_height"], chrome["ghost_width"])

    def wrapped() -> None:
        real_flush()
        try:
            geo = geometry()
            key = (protocol, geo)
            if key == placed.get("key"):
                return
            if protocol == "kitty" and placed.get("key"):
                output.write_raw(kitty_delete())
            placed["key"] = key
            if geo is None:
                real_flush()
                return
            row, col, rows, cols = geo
            output.write_raw("\x1b7" + place(row, col))
            output.write_raw(show_sequence(protocol, cols=cols, rows=rows))
            output.write_raw("\x1b8")
            real_flush()
        except Exception:
            pass

    try:
        output.flush = wrapped  # type: ignore[method-assign]
    except Exception:
        return noop

    def cleanup() -> None:
        try:
            output.flush = real_flush  # type: ignore[method-assign]
        except Exception:
            pass
        if protocol == "kitty":
            try:
                output.write_raw(kitty_delete())
                real_flush()
            except Exception:
                pass

    return cleanup
