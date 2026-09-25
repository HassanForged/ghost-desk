"""The side portrait. Same photo. Black stays black so it floats on the terminal."""

from __future__ import annotations

from pathlib import Path

HOOD = Path(__file__).resolve().parent / "assets" / "hood.jpg"

BOOT_WIDTH = 36
BOOT_HEIGHT = 32
SESSION_WIDTH = 24
SESSION_HEIGHT = 28
_BLACK = 16
_BLOCK_CACHE: dict[tuple, list] = {}
_ANSI_CACHE: dict[tuple, list[str]] = {}


def activity_for(note: str) -> str:
    text = (note or "").lower()
    if text in {"", "ready", "idle", "cancelled"}:
        return "idle"
    if any(word in text for word in ("search", "web_search", "http")):
        return "searching"
    if "read" in text:
        return "reading"
    return "working"


def _dark(pixel: tuple[int, int, int]) -> bool:
    return max(pixel) <= _BLACK


def _load(path: Path, width: int, height: int):
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image = _crop_subject(image)
    return image.resize((width, height * 2), Image.Resampling.LANCZOS)


def _crop_subject(image, pad: int = 12):
    pixels = image.load()
    width, height = image.size
    top, left, bottom, right = height, width, 0, 0
    found = False
    for y in range(height):
        for x in range(width):
            if _dark(pixels[x, y]):
                continue
            found = True
            if x < left:
                left = x
            if x > right:
                right = x
            if y < top:
                top = y
            if y > bottom:
                bottom = y
    if not found:
        return image
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(width, right + pad + 1)
    bottom = min(height, bottom + pad + 1)
    return image.crop((left, top, right, bottom))


def _cell(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> tuple[str, str]:
    if _dark(top) and _dark(bottom):
        return ("", " ")
    upper = f"{top[0]:02x}{top[1]:02x}{top[2]:02x}"
    lower = f"{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}"
    return (f"fg:#{upper} bg:#{lower}", "▄")


def render_blocks(path: Path | None = None, *, width: int = 22, height: int = 26, bob: int = 0):
    """Truecolor half-blocks from the photo. Bob is a blank row shift, not a new drawing."""
    source = path or HOOD
    key = (str(source), width, height, bob)
    cached = _BLOCK_CACHE.get(key)
    if cached is not None:
        return cached
    if not source.is_file():
        return [[("fg:#8a8a8a", "ghost")]]
    image = _load(source, width, height)
    pixels = image.load()
    rows: list[list[tuple[str, str]]] = []
    shift = bob % 3
    for _ in range(shift):
        rows.append([("fg:#000000", " " * width)])
    for y in range(0, height * 2 - 1, 2):
        line: list[tuple[str, str]] = []
        for x in range(width):
            top = pixels[x, y]
            bottom = pixels[x, min(y + 1, height * 2 - 1)]
            line.append(_cell(top, bottom))
        rows.append(line)
    _BLOCK_CACHE[key] = rows
    return rows


def render_ansi(path: Path | None = None, *, width: int = BOOT_WIDTH, height: int = BOOT_HEIGHT) -> list[str]:
    """Same photo as ANSI truecolor rows for the boot screen."""
    source = path or HOOD
    key = (str(source), width, height)
    cached = _ANSI_CACHE.get(key)
    if cached is not None:
        return cached
    if not source.is_file():
        return ["ghost"]
    image = _load(source, width, height)
    pixels = image.load()
    rows: list[str] = []
    reset = "\033[0m"
    for y in range(0, height * 2 - 1, 2):
        parts: list[str] = []
        for x in range(width):
            top = pixels[x, y]
            bottom = pixels[x, min(y + 1, height * 2 - 1)]
            if _dark(top) and _dark(bottom):
                parts.append(" ")
                continue
            parts.append(
                f"\033[38;2;{top[0]};{top[1]};{top[2]}m"
                f"\033[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▄"
            )
        rows.append("".join(parts) + reset)
    _ANSI_CACHE[key] = rows
    return rows
