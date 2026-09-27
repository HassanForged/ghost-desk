"""The side portrait. A cute little ghost. Black stays black so it floats on the terminal."""

from __future__ import annotations

from pathlib import Path

GHOST = Path(__file__).resolve().parent / "assets" / "ghost.png"

BOOT_WIDTH = 36
BOOT_HEIGHT = 32
SESSION_WIDTH = 30
SESSION_HEIGHT = 38
_BLACK = 16
_DOT = 64
# White-on-gray art needs a higher cut so the background drops to black.
_DOT_BY_ASSET: dict[str, int] = {}
# Cartoon art renders smooth: no dot threshold, background keyed out.
_SMOOTH_ASSETS = {"ghost.png"}
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


def _dark(value) -> bool:
    if isinstance(value, tuple):
        return max(value) <= _BLACK
    return value <= _BLACK


def _load(path: Path, width: int, height: int):
    from PIL import Image

    name = Path(path).name
    image = Image.open(path).convert("RGB")
    image = _crop_subject(image).convert("L")
    target_w, target_h = width, height * 2
    if name in _SMOOTH_ASSETS:
        # Cartoon art: lift the flat gray background to black, keep the
        # smooth shading and clean outlines. No dot threshold.
        image = image.point(lambda v: 0 if v <= 85 else min(255, int((v - 85) * 1.15)))
        return image.resize((target_w, target_h), Image.Resampling.LANCZOS)
    # Dots to pure white first: each output cell then measures dot density,
    # which survives the downscale instead of blurring into mush.
    dot = _DOT_BY_ASSET.get(name, _DOT)
    image = image.point(lambda v: 255 if v > dot else 0)
    target_w, target_h = width, height * 2
    # Center-crop to an integer multiple of the target so the averaging
    # lands evenly instead of banding across dot rows.
    kx = max(1, image.size[0] // target_w)
    ky = max(1, image.size[1] // target_h)
    crop_w, crop_h = target_w * kx, target_h * ky
    left = (image.size[0] - crop_w) // 2
    top = (image.size[1] - crop_h) // 2
    image = image.crop((left, top, left + crop_w, top + crop_h))
    return image.resize((target_w, target_h), Image.Resampling.BOX)


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


def _cell(top, bottom) -> tuple[str, str]:
    if _dark(top) and _dark(bottom):
        return ("", " ")
    upper = f"{top:02x}{top:02x}{top:02x}"
    lower = f"{bottom:02x}{bottom:02x}{bottom:02x}"
    return (f"fg:#{upper} bg:#{lower}", "▄")


def render_blocks(path: Path | None = None, *, width: int = 22, height: int = 26, bob: int = 0):
    """Grayscale density half-blocks from the photo. Bob is a blank row shift, not a new drawing."""
    source = path or GHOST
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
    source = path or GHOST
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
                f"\033[38;2;{top};{top};{top}m"
                f"\033[48;2;{bottom};{bottom};{bottom}m▄"
            )
        rows.append("".join(parts) + reset)
    _ANSI_CACHE[key] = rows
    return rows
