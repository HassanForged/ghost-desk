"""The side portrait. A cute little ghost. Black stays black so it floats on the terminal."""

from __future__ import annotations

import math
import random
from pathlib import Path

GHOST = Path(__file__).resolve().parent / "assets" / "ghost.png"
GHOST_REF = Path(__file__).resolve().parent / "assets" / "ghost-ref.png"

BOOT_WIDTH = 36
BOOT_HEIGHT = 32
SESSION_WIDTH = 30
SESSION_HEIGHT = 38
_BLACK = 16
_DOT = 64
# White-on-gray art needs a higher cut so the background drops to black.
_DOT_BY_ASSET: dict[str, int] = {}
# Pixel art: hard-quantized to the cartoon's own tones, nearest-neighbor.
_PIXEL_ART_ASSETS = {"ghost.png"}
# The cartoon's palette: black lines, lilac-tinted shading, white body.
_PIXEL_PALETTE = {
    0: (0, 0, 0),
    80: (110, 90, 110),  # deep plum
    160: (201, 168, 204),  # pale lilac
    255: (255, 255, 255),
}


def _pixel_tone(v: int) -> int:
    """The cartoon's palette: black lines, two shading grays, white body."""
    if v <= 85:
        return 0
    if v <= 150:
        return 80
    if v <= 205:
        return 160
    return 255
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
    if name in _PIXEL_ART_ASSETS:
        # Pixel art: quantize to the cartoon's tones, then nearest-neighbor
        # so every pixel lands hard. No blur, no speckle. Shading carries
        # a whisper of lilac.
        tones = image.point(_pixel_tone).resize((target_w, target_h), Image.Resampling.NEAREST)
        rgb = Image.new("RGB", tones.size)
        src, dst = tones.load(), rgb.load()
        for y in range(tones.size[1]):
            for x in range(tones.size[0]):
                dst[x, y] = _PIXEL_PALETTE[src[x, y]]
        return rgb
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


def _hex(value) -> str:
    if isinstance(value, tuple):
        return "%02x%02x%02x" % value
    return f"{value:02x}{value:02x}{value:02x}"


def _cell(top, bottom) -> tuple[str, str]:
    if _dark(top) and _dark(bottom):
        return ("", " ")
    return (f"fg:#{_hex(top)} bg:#{_hex(bottom)}", "▀")


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
            r1, g1, b1 = top if isinstance(top, tuple) else (top, top, top)
            r2, g2, b2 = bottom if isinstance(bottom, tuple) else (bottom, bottom, bottom)
            parts.append(f"\033[38;2;{r1};{g1};{b1}m\033[48;2;{r2};{g2};{b2}m▀")
        rows.append("".join(parts) + reset)
    _ANSI_CACHE[key] = rows
    return rows


# ---------------------------------------------------------------------------
# Reference-derived pixel sprite engine (the 90s pass).
#
# The portrait is derived from Hassan's reference cartoon (assets/ghost-ref.png)
# into a tiny tone grid. Tones: 0 = transparent, 1 = black, 2 = deep plum,
# 3 = pale lilac, 4 = white. Tones 5-7 are extra prop colors for the crew.
# Eye frames differ ONLY in the eye regions; the mouth and body never move.
# ---------------------------------------------------------------------------

T_NONE, T_BLACK, T_PLUM, T_LILAC, T_WHITE = 0, 1, 2, 3, 4
T_PAPER, T_AMBER, T_BRICK = 5, 6, 7  # ghost-crew prop colors
T_GRAY = 8  # screaming-mouth interior, sampled from the reference

TONE_COLORS = {
    T_BLACK: "#14101a",
    T_PLUM: "#6e5a6e",
    T_LILAC: "#c9a8cc",
    T_WHITE: "#f0edf2",
    T_PAPER: "#e8e4e8",
    T_AMBER: "#d8c98a",
    T_BRICK: "#7a5f7e",
    T_GRAY: "#aaaaac",
}

SPRITE_FRAMES = (
    "neutral",
    "blink",
    "look_left",
    "look_right",
    "look_down",
    "glance_meter",
    "sleep",
)

_SPRITE_CACHE: dict[int, list[list[int]]] = {}


def _pil_image():
    from PIL import Image

    return Image


def _fallback_sprite(width: int) -> list[list[int]]:
    """A tiny procedural ghost for when the reference art is missing."""
    height = max(8, round(width * 0.9))
    grid = [[T_NONE] * width for _ in range(height)]
    cx = width // 2
    for y in range(height):
        for x in range(width):
            dx = (x - cx) / (width * 0.42)
            dy = (y - height * 0.42) / (height * 0.42)
            if dx * dx + dy * dy <= 1:
                grid[y][x] = T_WHITE
    for ex in (cx - width // 5, cx + width // 5):
        ey = height // 3
        grid[ey][ex] = T_BLACK
        grid[ey][ex + 1] = T_BLACK
    for mx in range(cx - 2, cx + 3):
        grid[height * 2 // 3][mx] = T_BLACK
    return grid


def _body_bbox(image) -> tuple[int, int, int, int]:
    """Tight bbox of the ghost body: bright pixels, with a small pad."""
    px = image.load()
    w, h = image.size
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if sum(px[x, y]) // 3 > 150:
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0, 0, w, h)
    pad = 4
    return (
        max(0, min(xs) - pad),
        max(0, min(ys) - pad),
        min(w, max(xs) + pad + 1),
        min(h, max(ys) + pad + 1),
    )


# Feature anchors as fractions of the sprite's non-transparent body bbox,
# measured from the reference cartoon (assets/ghost-ref.png, body bbox
# fractions), then nudged inward for the pixel scale: the sprite's head
# runs narrower than the reference's, so the raw fractions push the face
# into the cheeks. Two oval eyes looking up-right, and the large open
# oval mouth below them.
_EYE_L = (0.390, 0.272)
_EYE_R = (0.650, 0.185)
_MOUTH = (0.570, 0.491)
_EYE_SIZE = (0.128, 0.237)  # width, height fractions of the body bbox
_MOUTH_SIZE = (0.374, 0.320)


def _sprite_bbox(grid: list[list[int]]) -> tuple[int, int, int, int]:
    """Non-transparent bbox of a tone grid: (x0, y0, x1, y1) inclusive."""
    height = len(grid)
    width = len(grid[0]) if height else 0
    xs = [x for y in range(height) for x in range(width) if grid[y][x] != T_NONE]
    ys = [y for y in range(height) for x in range(width) if grid[y][x] != T_NONE]
    if not xs:
        return (0, 0, width - 1, height - 1)
    return (min(xs), min(ys), max(xs), max(ys))


def _paint_ellipse(
    grid: list[list[int]], cx: float, cy: float, rx: float, ry: float, tone: int
) -> None:
    """Fill an axis-aligned ellipse, clipped to non-transparent cells."""
    height = len(grid)
    width = len(grid[0]) if height else 0
    for y in range(max(0, int(cy - ry) - 1), min(height, int(cy + ry) + 2)):
        for x in range(max(0, int(cx - rx) - 1), min(width, int(cx + rx) + 2)):
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            if dx * dx + dy * dy <= 1 and grid[y][x] != T_NONE:
                grid[y][x] = tone


def _paint_ring(
    grid: list[list[int]],
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    tone: int,
    fill: int,
) -> None:
    """A 1px ellipse outline with a flat fill inside."""
    height = len(grid)
    width = len(grid[0]) if height else 0
    for y in range(max(0, int(cy - ry) - 1), min(height, int(cy + ry) + 2)):
        for x in range(max(0, int(cx - rx) - 1), min(width, int(cx + rx) + 2)):
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            if dx * dx + dy * dy > 1 or grid[y][x] == T_NONE:
                continue
            edge = False
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                ndx = (nx - cx) / rx
                ndy = (ny - cy) / ry
                if ndx * ndx + ndy * ndy > 1:
                    edge = True
                    break
            grid[y][x] = tone if edge else fill


def _stamp_features(
    grid: list[list[int]],
    *,
    eye_l: tuple[float, float] = _EYE_L,
    eye_r: tuple[float, float] = _EYE_R,
    mouth: tuple[float, float] = _MOUTH,
    eye_size: tuple[float, float] = _EYE_SIZE,
    mouth_size: tuple[float, float] = _MOUTH_SIZE,
    eye_min: tuple[float, float] = (2.0, 3.0),
    mouth_min: tuple[float, float] = (4.0, 4.0),
    shade: bool = True,
) -> list[list[int]]:
    """Stamp the reference's face: two oval eyes and the open screaming mouth.

    The face is 100% stamped vector art positioned on the sprite's own
    non-transparent bbox — the interior is cleared to white first, so no
    downscaled residual ever survives to clash with the stamps. Eyes are
    solid black ovals (the right eye sits higher, giving the up-right
    gaze); the mouth is a black oval ring with the reference's flat
    light-gray interior. A whisper of lilac side-shading goes on first
    so the stamps sit on top of it.

    Anchor/size overrides let the buddy reuse the same technique at icon
    scale with its own face geometry.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0
    out = [row[:] for row in grid]
    x0, y0, x1, y1 = _sprite_bbox(out)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1

    if shade:
        # 90s side-shading: lilac on the body's right flank, dithered later.
        # Kept clear of the face (starts right of the mouth) so it never
        # reads as a stray mark.
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if out[y][x] == T_WHITE and x >= x0 + bw * 0.84:
                    out[y][x] = T_LILAC

    # Oval eyes, solid black. Asymmetric heights sell the up-right gaze.
    ew, eh = max(eye_min[0], eye_size[0] * bw), max(eye_min[1], eye_size[1] * bh)
    for fx, fy in (eye_l, eye_r):
        _paint_ellipse(out, x0 + fx * bw, y0 + fy * bh, ew / 2, eh / 2, T_BLACK)
    # The open screaming mouth: black oval ring, flat gray interior.
    mw, mh = max(mouth_min[0], mouth_size[0] * bw), max(mouth_min[1], mouth_size[1] * bh)
    _paint_ring(
        out, x0 + mouth[0] * bw, y0 + mouth[1] * bh, mw / 2, mh / 2, T_BLACK, T_GRAY
    )
    return out


def _outline_grid(grid: list[list[int]]) -> list[list[int]]:
    """Clean 1px outline: any filled cell adjacent to transparency goes black.

    Shared by the portrait derivation and the buddy (which re-outlines
    after cropping the natural-aspect body to its exact box).
    """
    work_h = len(grid)
    work_w = len(grid[0]) if work_h else 0
    for y in range(work_h):
        for x in range(work_w):
            if grid[y][x] == T_NONE:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < work_h and 0 <= nx < work_w and grid[ny][nx] == T_NONE:
                        grid[y][x] = T_BLACK
                        break
                if grid[y][x] == T_BLACK:
                    break
    return grid


def _largest_component(mask: list[list[bool]]) -> list[list[bool]]:
    """Keep only the largest 4-connected True component: drops specks."""
    work_h = len(mask)
    work_w = len(mask[0]) if work_h else 0
    seen = [[False] * work_w for _ in range(work_h)]
    best: list[tuple[int, int]] = []
    for y in range(work_h):
        for x in range(work_w):
            if not mask[y][x] or seen[y][x]:
                continue
            cells: list[tuple[int, int]] = []
            cstack = [(x, y)]
            seen[y][x] = True
            while cstack:
                cx, cy = cstack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (
                        0 <= nx < work_w
                        and 0 <= ny < work_h
                        and mask[ny][nx]
                        and not seen[ny][nx]
                    ):
                        seen[ny][nx] = True
                        cstack.append((nx, ny))
            if len(cells) > len(best):
                best = cells
    out = [[False] * work_w for _ in range(work_h)]
    for x, y in best:
        out[y][x] = True
    return out


def _derive_big(
    work_w: int, work_h: int | None = None, stamp: bool = True
) -> list[list[int]]:
    """Derive the ghost at working resolution.

    The body mask comes from the reference via flood fill (dark-gray
    backdrop goes transparent). Side arm protrusions are carved off — at
    sprite scale they blob under the outline — and the thin tail is
    dilated so it survives outlining with a white interior.

    work_h defaults to the reference body's aspect; pass it explicitly
    (with stamp=False) to derive an unstamped body at an exact size —
    the buddy's path.
    """
    Image = _pil_image()
    image = Image.open(GHOST_REF).convert("RGB")
    bx0, by0, bx1, by1 = _body_bbox(image)
    body = image.crop((bx0, by0, bx1, by1))
    bw, bh = body.size
    if work_h is None:
        work_h = max(8, round(work_w * bh / bw))
    gray = body.convert("L")
    gpx = gray.load()
    backdrop = [[False] * bw for _ in range(bh)]
    stack = (
        [(x, 0) for x in range(bw)]
        + [(x, bh - 1) for x in range(bw)]
        + [(0, y) for y in range(bh)]
        + [(bw - 1, y) for y in range(bh)]
    )
    while stack:
        x, y = stack.pop()
        if not (0 <= x < bw and 0 <= y < bh):
            continue
        if backdrop[y][x] or gpx[x, y] >= 110:
            continue
        backdrop[y][x] = True
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    mask_img = Image.new("L", (bw, bh))
    mpx = mask_img.load()
    for y in range(bh):
        for x in range(bw):
            mpx[x, y] = 0 if backdrop[y][x] else 255
    mask_small = mask_img.resize((work_w, work_h), Image.Resampling.BOX)
    smpx = mask_small.load()
    mask = [[smpx[x, y] >= 128 for x in range(work_w)] for y in range(work_h)]
    # Drop specks first: a sliver of backdrop-adjacent white on the
    # reference's right edge would otherwise poison the smoothing below.
    mask = _largest_component(mask)
    # Right arm: carve the bump clean off.
    for y in range(int(work_h * 0.10), int(work_h * 0.43)):
        for x in range(int(work_w * 0.76), work_w):
            mask[y][x] = False
    # Left arm: don't carve — smooth. The arm's join leaves a backdrop
    # wedge that reads as a stray bar, so force the left edge onto a
    # gentle S-curve through the arm band (filling the wedge). Control
    # points are (row_frac, edge_frac) of the working grid.
    for rf, ef in (
        (0.34, 0.14),
        (0.38, 0.14),
        (0.43, 0.14),
        (0.48, 0.14),
        (0.52, 0.18),
        (0.57, 0.23),
        (0.62, 0.27),
        (0.67, 0.27),
        (0.71, 0.23),
        (0.76, 0.18),
    ):
        y = round(rf * work_h)
        if 0 <= y < work_h:
            edge = round(ef * work_w)
            maxx = max((x for x in range(work_w) if mask[y][x]), default=-1)
            for x in range(work_w):
                mask[y][x] = edge <= x <= maxx
    # Dilate the wavy tail so it keeps a white interior under the outline.
    tail = [[False] * work_w for _ in range(work_h)]
    for y in range(int(work_h * 0.70), work_h):
        for x in range(int(work_w * 0.10), int(work_w * 0.45)):
            if mask[y][x]:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < work_h and 0 <= nx < work_w:
                            tail[ny][nx] = True
    for y in range(int(work_h * 0.70), work_h):
        for x in range(int(work_w * 0.10), int(work_w * 0.45)):
            if tail[y][x]:
                mask[y][x] = True
    # Keep only the largest component: drops detached specks.
    mask = _largest_component(mask)
    grid: list[list[int]] = [
        [T_WHITE if mask[y][x] else T_NONE for x in range(work_w)]
        for y in range(work_h)
    ]
    # Clean 1px outline.
    _outline_grid(grid)
    # Guarantee: the interior is pure white before stamping. The face is
    # 100% stamped vector art — zero downscaled residuals can survive.
    for y in range(work_h):
        for x in range(work_w):
            if grid[y][x] not in (T_NONE, T_BLACK):
                grid[y][x] = T_WHITE
    if not stamp:
        return grid
    return _stamp_features(grid)


def derive_sprite(width: int = 22) -> list[list[int]]:
    """Derive the ghost as a tone grid from the reference cartoon.

    The reference sits on a dark-gray backdrop (luminance ~70-90) with
    near-black outlines (<40). A flood fill from the image edges through
    dark pixels marks the backdrop transparent. The sprite is derived at
    the target width: a clean 1px outline, the arm join smoothed into a
    gentle S-curve, a dilated wavy tail. The reference's face — two oval
    eyes looking up-right, the open screaming mouth with its flat gray
    interior — is stamped as clean pixel art positioned on the sprite's
    own non-transparent bbox. Deterministic; cached per width.
    """
    cached = _SPRITE_CACHE.get(width)
    if cached is not None:
        return [row[:] for row in cached]
    _pil_image()
    try:
        grid = _derive_big(width)
    except OSError:
        grid = _fallback_sprite(width)
    _SPRITE_CACHE[width] = [row[:] for row in grid]
    return grid


def _stamped_eye_boxes(
    grid: list[list[int]],
    *,
    eye_l: tuple[float, float] = _EYE_L,
    eye_r: tuple[float, float] = _EYE_R,
    eye_size: tuple[float, float] = _EYE_SIZE,
) -> list[tuple[int, int, int, int]]:
    """Eye bounding boxes from the stamp anchors: exact, no detection needed.

    Tight to the painted ellipse (ceil/floor of the true extent) so frame
    animation never touches the mouth or outline.
    """
    x0, y0, x1, y1 = _sprite_bbox(grid)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    ew, eh = max(2.0, eye_size[0] * bw), max(3.0, eye_size[1] * bh)
    boxes = []
    for fx, fy in (eye_l, eye_r):
        cx, cy = x0 + fx * bw, y0 + fy * bh
        boxes.append(
            (
                math.ceil(cx - ew / 2 - 1e-9),
                math.ceil(cy - eh / 2 - 1e-9),
                math.floor(cx + ew / 2 + 1e-9),
                math.floor(cy + eh / 2 + 1e-9),
            )
        )
    boxes.sort(key=lambda b: b[0])
    return boxes


def _clear_eyes(grid: list[list[int]], boxes) -> None:
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                grid[y][x] = T_WHITE


def _apply_frame(
    base: list[list[int]],
    name: str,
    *,
    eye_l: tuple[float, float] = _EYE_L,
    eye_r: tuple[float, float] = _EYE_R,
    eye_size: tuple[float, float] = _EYE_SIZE,
) -> list[list[int]]:
    """One animation frame of a stamped ghost. Only the eyes ever change.

    Shared by the portrait (frame_grid) and the buddy (buddy_frame).
    """
    grid = [row[:] for row in base]
    height = len(grid)
    gwidth = len(grid[0]) if height else 0
    boxes = _stamped_eye_boxes(grid, eye_l=eye_l, eye_r=eye_r, eye_size=eye_size)
    if name in ("blink", "sleep"):
        _clear_eyes(grid, boxes)
        for x0, y0, x1, y1 in boxes:
            mid = (y0 + y1) // 2
            for x in range(x0, x1 + 1):
                if 0 <= x < gwidth and 0 <= mid < height and grid[mid][x] != T_NONE:
                    grid[mid][x] = T_BLACK
        return grid
    deltas = {
        "look_left": (-1, 0),
        "look_right": (1, 0),
        "look_down": (0, 1),
        "glance_meter": (0, -1),
    }
    dx, dy = deltas.get(name, (0, 0))
    for x0, y0, x1, y1 in boxes:
        saved: list[tuple[int, int, int]] = []
        for y in range(max(0, y0), min(height, y1 + 1)):
            for x in range(max(0, x0), min(gwidth, x1 + 1)):
                if grid[y][x] == T_BLACK:
                    saved.append((x, y, T_BLACK))
                elif grid[y][x] != T_NONE:
                    grid[y][x] = T_WHITE
        for x, y, tone in saved:
            nx, ny = x + dx, y + dy
            # The mouth (gray) is sacred: a shifted eye never paints over it.
            if 0 <= nx < gwidth and 0 <= ny < height and grid[ny][nx] not in (T_NONE, T_GRAY):
                grid[ny][nx] = tone
    return grid


def frame_grid(name: str = "neutral", width: int = 22) -> list[list[int]]:
    """One animation frame of the ghost. Only the eyes ever change.

    Frames: neutral, blink, look_left, look_right, look_down, glance_meter
    (eyes flick up toward the context bar), sleep (eyes shut).
    """
    base = derive_sprite(width)
    if name in (None, "neutral"):
        return base
    return _apply_frame(base, name)


def dither_shade(grid: list[list[int]], seed: int = 0) -> list[list[int]]:
    """Checkerboard-dither the lilac/plum shade tones into white. Deterministic.

    Black, white, gray (the mouth interior), and transparent are untouched.
    """
    return [
        [
            tone
            if tone not in (T_PLUM, T_LILAC) or (x + y + seed) % 2 == 0
            else T_WHITE
            for x, tone in enumerate(row)
        ]
        for y, row in enumerate(grid)
    ]


def bob_offset(tick: int) -> int:
    """The gentle idle float: 0 or 1 cell."""
    return tick % 2


def fragments_from_grid(grid: list[list[int]]) -> list[list[tuple[str, str]]]:
    """Tone grid -> prompt_toolkit half-block fragment rows.

    Tone 0 is transparent: a fully transparent cell is a blank space, and a
    half-transparent cell uses the half block that keeps the visible tone.
    """
    rows: list[list[tuple[str, str]]] = []
    height = len(grid)
    width = len(grid[0]) if height else 0
    for y in range(0, height, 2):
        top = grid[y]
        bottom = grid[y + 1] if y + 1 < height else [T_NONE] * width
        line: list[tuple[str, str]] = []
        for x in range(width):
            t, b = top[x], bottom[x]
            if t == T_NONE and b == T_NONE:
                line.append(("", " "))
            elif t == T_NONE:
                line.append((f"bg:{TONE_COLORS[b]}", " "))
            elif b == T_NONE:
                line.append((f"fg:{TONE_COLORS[t]}", "\u2580"))
            elif t == b:
                line.append((f"fg:{TONE_COLORS[t]}", "\u2588"))
            else:
                line.append((f"fg:{TONE_COLORS[t]} bg:{TONE_COLORS[b]}", "\u2580"))
        rows.append(line)
    return rows


def render_sprite_frame(
    name: str = "neutral", *, width: int = 22, dither: bool = True, bob: int = 0
) -> list[list[tuple[str, str]]]:
    """The reference ghost as fragment rows: a drop-in for render_blocks."""
    grid = frame_grid(name, width=width)
    if dither:
        grid = dither_shade(grid)
    rows = fragments_from_grid(grid)
    shift = bob % 3
    if shift and rows:
        blank = [("", " ")] * len(rows[0])
        rows = [blank] * shift + rows[:-shift] if shift < len(rows) else [blank] * len(rows)
    return rows


def night_scene(
    width: int = 22,
    height: int = 20,
    seed: int = 0,
    ghost_grid: list[list[int]] | None = None,
) -> list[list[int]]:
    """A deterministic little night: stars, a dithered moon, dithered ground,
    and the ghost standing on the left. Same seed -> same sky."""
    rng = random.Random(seed)
    grid = [[T_NONE] * width for _ in range(height)]
    for _ in range(max(6, width * height // 50)):
        x = rng.randrange(width)
        y = rng.randrange(max(1, height - 4))
        if grid[y][x] == T_NONE:
            grid[y][x] = T_WHITE if rng.random() < 0.2 else T_LILAC
    mx, my, mr = width - 4, 2, 1
    for y in range(my - mr, my + mr + 1):
        for x in range(mx - mr, mx + mr + 1):
            if (
                0 <= x < width
                and 0 <= y < height - 3
                and (x - mx) ** 2 + (y - my) ** 2 <= mr * mr + 0.5
            ):
                grid[y][x] = T_WHITE if (x + y) % 2 == 0 else T_LILAC
    for y in (height - 2, height - 1):
        for x in range(width):
            if (x * 2 + y) % 3 == 0:
                grid[y][x] = T_PLUM
    ghost = ghost_grid if ghost_grid is not None else dither_shade(frame_grid("neutral", width=16))
    gh = len(ghost)
    gw = len(ghost[0]) if gh else 0
    ox, oy = 1, max(0, height - 2 - gh)
    for y in range(gh):
        for x in range(gw):
            tone = ghost[y][x]
            if tone != T_NONE and 0 <= oy + y < height and 0 <= ox + x < width:
                grid[oy + y][ox + x] = tone
    return grid


def render_ghost_pane(
    name: str = "neutral",
    *,
    seed: int = 7,
    overlays: tuple = (),
    width: int = 22,
    height: int = 20,
) -> list[list[tuple[str, str]]]:
    """The full ghost pane: night scene plus fragment overlays.

    Each overlay is (fragment_rows, ox, oy) in fragment space — leaves,
    confetti, sleep Z's stamp themselves on top of the scene.
    """
    ghost = dither_shade(frame_grid(name, width=16))
    scene = night_scene(width, height, seed, ghost_grid=ghost)
    rows = fragments_from_grid(scene)
    for frag_rows, ox, oy in overlays:
        for dy, frow in enumerate(frag_rows):
            y = oy + dy
            if not 0 <= y < len(rows):
                continue
            for dx, cell in enumerate(frow):
                x = ox + dx
                if 0 <= x < len(rows[y]):
                    rows[y][x] = cell
    return rows


def materialize_steps(
    grid: list[list[int]], seed: int = 0, steps: int = 12
) -> list[list[list[int]]]:
    """Pixel materialization: sparse static resolving into the full sprite.

    Deterministic per seed. The first step is sparse, the last step is
    exactly the full sprite.
    """
    cells = [
        (x, y)
        for y, row in enumerate(grid)
        for x, tone in enumerate(row)
        if tone != T_NONE
    ]
    rng = random.Random(seed)
    rng.shuffle(cells)
    total = len(cells)
    out: list[list[list[int]]] = []
    for i in range(1, steps + 1):
        shown = set(cells[: total * i // steps])
        out.append(
            [
                [tone if (x, y) in shown else T_NONE for x, tone in enumerate(row)]
                for y, row in enumerate(grid)
            ]
        )
    if out:
        out[-1] = [row[:] for row in grid]
    return out


def pixel_rule(width: int) -> list[list[tuple[str, str]]]:
    """A dithered divider from block characters only (U+2580-U+259F)."""
    cells = []
    for x in range(width):
        if x % 2 == 0:
            cells.append(("fg:#4a3648", "\u259a"))
        else:
            cells.append(("fg:#2e2133", "\u259e"))
    return [cells]


# A tiny 3x5 pixel font for the wordmark. Uppercase; anything missing
# renders as a space.
_FONT_3X5 = {
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "B": ("##.", "#.#", "##.", "#.#", "##."),
    "C": (".##", "#..", "#..", "#..", ".##"),
    "D": ("##.", "#.#", "#.#", "#.#", "##."),
    "E": ("###", "#..", "##.", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "G": (".##", "#..", "#.#", "#.#", ".##"),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "J": ("..#", "..#", "..#", "#.#", ".#."),
    "K": ("#.#", "#.#", "##.", "#.#", "#.#"),
    "L": ("#..", "#..", "#..", "#..", "###"),
    "M": ("#.#", "###", "#.#", "#.#", "#.#"),
    "N": ("#.#", "##.", "#.#", ".##", "#.#"),
    "O": (".#.", "#.#", "#.#", "#.#", ".#."),
    "P": ("##.", "#.#", "##.", "#..", "#.."),
    "Q": (".#.", "#.#", "#.#", "##.", ".##"),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "S": (".##", "#..", ".#.", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "#.#", "###"),
    "V": ("#.#", "#.#", "#.#", "#.#", ".#."),
    "W": ("#.#", "#.#", "#.#", "###", "#.#"),
    "X": ("#.#", "#.#", ".#.", "#.#", "#.#"),
    "Y": ("#.#", "#.#", ".#.", ".#.", ".#."),
    "Z": ("###", "..#", ".#.", "#..", "###"),
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("##.", "..#", ".#.", "#..", "###"),
    "3": ("##.", "..#", ".#.", "..#", "##."),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "##.", "..#", "##."),
    "6": (".##", "#..", "##.", "#.#", ".#."),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": (".#.", "#.#", ".#.", "#.#", ".#."),
    "9": (".#.", "#.#", ".##", "..#", "##."),
    " ": ("...", "...", "...", "...", "..."),
    ".": ("...", "...", "...", "...", ".#."),
    ",": ("...", "...", "...", ".#.", "#.."),
    "!": (".#.", ".#.", ".#.", "...", ".#."),
    "-": ("...", "...", "###", "...", "..."),
    "/": ("..#", "..#", ".#.", "#..", "#.."),
    "'": (".#.", ".#.", "...", "...", "..."),
    ":": ("...", ".#.", "...", ".#.", "..."),
}


def pixel_text(text: str, tone: int = T_LILAC) -> list[list[int]]:
    """Render text in the 3x5 pixel font as a tone grid (lilac by default)."""
    glyphs = [_FONT_3X5.get(ch.upper(), _FONT_3X5[" "]) for ch in (text or "")]
    width = len(glyphs) * 4 - 1 if glyphs else 0
    grid = [[T_NONE] * width for _ in range(5)]
    for i, glyph in enumerate(glyphs):
        ox = i * 4
        for y in range(5):
            for x in range(3):
                if glyph[y][x] == "#":
                    grid[y][ox + x] = tone
    return grid


_LEAF_TONES = ("#e8a34f", "#c9764f", "#a85f52", "#8a7a4a")  # muted fall


def leaf_sprites() -> list[tuple[list[list[int]], str]]:
    """Tiny pixel leaves: (3x3 tone pattern, muted fall color)."""
    across = [
        [T_NONE, T_WHITE, T_NONE],
        [T_WHITE, T_WHITE, T_WHITE],
        [T_NONE, T_WHITE, T_NONE],
    ]
    tilt_l = [
        [T_NONE, T_WHITE, T_WHITE],
        [T_WHITE, T_WHITE, T_WHITE],
        [T_WHITE, T_NONE, T_NONE],
    ]
    tilt_r = [
        [T_WHITE, T_WHITE, T_NONE],
        [T_WHITE, T_WHITE, T_WHITE],
        [T_NONE, T_NONE, T_WHITE],
    ]
    return [
        (across, _LEAF_TONES[0]),
        (tilt_l, _LEAF_TONES[1]),
        (tilt_r, _LEAF_TONES[2]),
        (across, _LEAF_TONES[3]),
    ]


def leaf_fragments(pattern: list[list[int]], color: str) -> list[list[tuple[str, str]]]:
    """A 3x3 tone leaf pattern as fragment rows (3 cols x 2 rows)."""
    rows: list[list[tuple[str, str]]] = []
    height = len(pattern)
    width = len(pattern[0]) if height else 0
    for y in range(0, height, 2):
        top = pattern[y]
        bottom = pattern[y + 1] if y + 1 < height else [T_NONE] * width
        line: list[tuple[str, str]] = []
        for x in range(width):
            t, b = top[x], bottom[x]
            if t == T_NONE and b == T_NONE:
                line.append(("", " "))
            elif t == T_NONE:
                line.append((f"bg:{color}", " "))
            elif b == T_NONE:
                line.append((f"fg:{color}", "\u2580"))
            else:
                line.append((f"fg:{color}", "\u2588"))
        rows.append(line)
    return rows


# ---------------------------------------------------------------------------
# Ghost crew: one mini ghost per active worker, with a working prop.
# ---------------------------------------------------------------------------

CREW_ACTIVITIES = ("reading", "writing", "building", "thinking")

CREW_LABELS = {
    "reading": "reading\u2026",
    "writing": "editing\u2026",
    "building": "building\u2026",
    "thinking": "thinking\u2026",
}

_READ_HINTS = ("read", "glob", "grep", "search", "fetch", "curl", "wget", "http")
_WRITE_HINTS = ("write", "edit", "apply", "patch", "note", "memory", "todo")
_BUILD_HINTS = (
    "exec", "shell", "run", "bash", "build", "test", "cargo",
    "make", "npm", "node", "python", "uv", "pip", "git", "go", "docker",
)


def classify_activity(tool_name: str) -> str:
    """Map a tool name to a crew activity: reading, writing, building, thinking."""
    name = (tool_name or "").lower()
    if any(hint in name for hint in _READ_HINTS):
        return "reading"
    if any(hint in name for hint in _WRITE_HINTS):
        return "writing"
    if any(hint in name for hint in _BUILD_HINTS):
        return "building"
    return "thinking"


def _draw_crew_prop(cell: list[list[int]], activity: str, frame: int) -> None:
    """Stamp the activity prop into the right side of the 16x12 crew cell."""
    height = len(cell)
    if activity == "reading":
        # A little document; the text lines flip between frames.
        for y in range(3, 8):
            for x in range(11, 15):
                cell[y][x] = T_PAPER
        for lx in range(12, 14):
            cell[4 + frame][lx] = T_PLUM
            cell[6 - frame][lx] = T_PLUM
    elif activity == "writing":
        # A pencil that bobs as it writes.
        oy = 2 + frame
        for y in range(oy, oy + 4):
            for x in range(12, 14):
                if y < height:
                    cell[y][x] = T_AMBER
        if oy + 4 < height:
            cell[oy + 4][12] = T_WHITE
            cell[oy + 4][13] = T_WHITE
    elif activity == "building":
        # Two bricks stacked; the top one drops into place.
        for y in (8, 9):
            for x in range(11, 15):
                cell[y][x] = T_BRICK
        cell[8][12] = T_BLACK
        cell[9][13] = T_BLACK
        top = 3 if frame == 0 else 6
        for y in (top, top + 1):
            for x in range(11, 15):
                if y < height:
                    cell[y][x] = T_BRICK
        if top + 1 < height:
            cell[top][13] = T_BLACK


def crew_frame(activity: str, frame: int) -> list[list[tuple[str, str]]]:
    """One mini ghost with its working prop: 16 fragment cols x 6 rows.

    frame is 0 or 1; the prop animates between the two frames.
    """
    act = activity if activity in CREW_ACTIVITIES else "thinking"
    ghost = dither_shade(frame_grid("neutral", width=10))
    cell = [[T_NONE] * 16 for _ in range(12)]
    gh = len(ghost)
    gw = len(ghost[0]) if gh else 0
    oy = max(0, (12 - gh) // 2)
    for y in range(gh):
        for x in range(gw):
            tone = ghost[y][x]
            if tone != T_NONE and oy + y < 12:
                cell[oy + y][x] = tone
    _draw_crew_prop(cell, act, frame % 2)
    return fragments_from_grid(cell)


def sleep_z_fragments(
    tick: int = 0, width: int = 22, height: int = 20
) -> list[tuple[int, int, list[list[tuple[str, str]]]]]:
    """Floating pixel Z's for sleep: three Z's rising, cycled by tick.

    Returns [(ox, oy, fragment_rows)] in ghost-pane fragment space,
    positioned above the ghost's head, drifting up with the tick.
    """
    # Z from the 3x5 pixel font, as tone grid -> fragments.
    z_grid = pixel_text("Z", tone=T_LILAC)
    frags = fragments_from_grid(z_grid)
    cx = width // 2
    out = []
    for i, dx in enumerate((-4, 0, 4)):
        # Rise 1 cell every 3 ticks, wrap within the top rows.
        oy = 3 - ((tick // 3 + i) % 4)
        if 0 <= oy < height:
            out.append((cx + dx, oy, frags))
    return out


def confetti_fragments(
    seed: int = 0, width: int = 22, height: int = 20
) -> list[tuple[int, int, list[list[tuple[str, str]]]]]:
    """Restrained lilac/amber confetti for the done bounce.

    A handful of single-pixel dots scattered above the ghost —
    celebratory but lowkey. Deterministic per seed.
    """
    import random
    rng = random.Random(seed)
    colors = ["#c9a8cc", "#c9a8cc", "#d4a24e", "#c9a8cc", "#d4a24e"]
    out = []
    for _ in range(8):
        x = rng.randint(max(0, width // 2 - 7), min(width - 1, width // 2 + 7))
        y = rng.randint(0, max(0, height // 3))
        color = rng.choice(colors)
        out.append((x, y, [[(f"fg:{color}", "•")]]))
    return out


# --- compact buddy sprite ---------------------------------------------------
# The buddy: the same reference-derived ghost as the portrait, re-derived
# at exactly 16x12 for the full-width header dock. Same technique as the
# portrait (commit 4ab6d30): body mask from ghost-ref.png, interior cleared
# to white, face stamped as bbox-relative vector art — but with buddy-scale
# face geometry, since the portrait's anchors crowd and merge at icon size.
# Only eye cells ever differ between life frames.

BUDDY_W = 16
BUDDY_H = 12
BUDDY_ROWS = 6  # half-block fragment rows

# Buddy face anchors: fractions of the buddy body bbox, matching the
# hand-authored sprite below. Worried 3x3 eyes, right higher for the
# up-right gaze; the open screaming mouth sits well below them.
# (bbox of the art: x 1-14, y 0-11 -> 14x12; left eye center (5,5),
# right eye center (10,4), mouth interior center (7.5,8.5).)
_BUDDY_EYE_L = (4 / 14, 5 / 12)
_BUDDY_EYE_R = (9 / 14, 4 / 12)
_BUDDY_MOUTH = (6.5 / 14, 8.5 / 12)
_BUDDY_EYE_SIZE = (3 / 14, 3 / 12)
_BUDDY_MOUTH_SIZE = (5 / 14, 4 / 12)

# Hand-authored buddy sprite, 16x12. X = black outline, # = white body,
# G = gray screaming-mouth interior. Derived-from-reference bodies turn to
# lumpy noise at this size, and the stamped face merged into the outline;
# chunky hand pixels read as the spooked ghost instead. Flat white (no
# dither) so it stays clean at icon scale.
_BUDDY_ART = [
    "      XXXX      ",
    "    XX####XX    ",
    "   X########X   ",
    "  X######XXX#X  ",
    " X##XXX##XXX##X ",
    " X##XXX##XXX##X ",
    " X##XXX#######X ",
    " X####XXXX####X ",
    " X####XGGX####X ",
    " X####XGGX####X ",
    "  X###XXXX###X  ",
    "   XX######XX   ",
]

_BUDDY_CACHE: list[list[int]] | None = None


def buddy_grid() -> list[list[int]]:
    """The buddy as a tone grid: hand-authored 16x12 sprite.

    Rounded white body, black outline, two worried black eyes (right
    higher, the up-right gaze) and the open screaming mouth with its gray
    interior. Cached; frames are derived from this by buddy_frame().
    """
    global _BUDDY_CACHE
    if _BUDDY_CACHE is None:
        tones = {"X": T_BLACK, "#": T_WHITE, "G": T_GRAY}
        _BUDDY_CACHE = [
            [tones.get(ch, T_NONE) for ch in row] for row in _BUDDY_ART
        ]
    return [row[:] for row in _BUDDY_CACHE]


def buddy_frame(name: str = "neutral") -> list[list[int]]:
    """The buddy with a life-state eye transform. Only eye cells ever differ."""
    base = buddy_grid()
    if name in (None, "neutral"):
        return base
    return _apply_frame(
        base, name, eye_l=_BUDDY_EYE_L, eye_r=_BUDDY_EYE_R, eye_size=_BUDDY_EYE_SIZE
    )
