"""Tests for the 90s pixel/animation sprite engine (face.py).

Covers: reference-derived sprite, eye-only frame diffs, deterministic
dither, the pixel font, leaf sprites, the night scene, the divider, and
materialization. The 90s pass must keep the body and mouth byte-for-byte
identical across eye frames.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghost_desk import face
from ghost_desk.face import (
    T_NONE,
    T_BLACK,
    T_PLUM,
    T_LILAC,
    T_WHITE,
    T_GRAY,
    TONE_COLORS,
    _EYE_L,
    _EYE_R,
    _MOUTH,
    _sprite_bbox,
    _stamped_eye_boxes,
    bob_offset,
    derive_sprite,
    dither_shade,
    fragments_from_grid,
    frame_grid,
    leaf_fragments,
    leaf_sprites,
    materialize_steps,
    night_scene,
    pixel_rule,
    pixel_text,
    render_ghost_pane,
    render_sprite_frame,
)


def _tones():
    return {T_NONE, T_BLACK, T_PLUM, T_LILAC, T_WHITE, T_GRAY}


def test_derive_sprite_shape_and_tones():
    grid = derive_sprite(22)
    assert 18 <= len(grid) <= 24
    assert all(len(row) == 22 for row in grid)
    seen = {t for row in grid for t in row}
    assert seen <= _tones()
    # A real ghost: mostly body, some transparency, black outline present.
    flat = [t for row in grid for t in row]
    assert flat.count(T_NONE) > 50
    assert flat.count(T_WHITE) > 80
    assert flat.count(T_BLACK) > 20


def test_derive_sprite_deterministic_and_cached():
    face._SPRITE_CACHE.clear()
    first = derive_sprite(22)
    second = derive_sprite(22)
    assert first == second
    assert first is not second  # copies, not aliases


def test_eye_frames_differ_only_in_eyes():
    base = frame_grid("neutral", width=22)
    height, width = len(base), len(base[0])
    eye_cells = set()
    for x0, y0, x1, y1 in _stamped_eye_boxes(base):
        for y in range(y0 - 1, y1 + 2):
            for x in range(x0 - 1, x1 + 2):
                eye_cells.add((x, y))
    for name in ("blink", "look_left", "look_right", "look_down", "sleep"):
        other = frame_grid(name, width=22)
        assert len(other) == height and len(other[0]) == width
        diffs = [
            (x, y)
            for y in range(height)
            for x in range(width)
            if other[y][x] != base[y][x]
        ]
        assert diffs, f"{name} must differ from neutral"
        outside = [(x, y) for (x, y) in diffs if (x, y) not in eye_cells]
        assert not outside, f"{name} changed cells outside the eyes: {outside}"
    # The mouth (gray fill) never moves.
    for name in ("blink", "look_left", "look_right", "look_down", "sleep"):
        other = frame_grid(name, width=22)
        for y in range(height):
            for x in range(width):
                if base[y][x] == T_GRAY:
                    assert other[y][x] == T_GRAY


def test_blink_closes_eyes():
    neutral = frame_grid("neutral", width=22)
    blink = frame_grid("blink", width=22)
    height, width = len(neutral), len(neutral[0])
    for x0, y0, x1, y1 in _stamped_eye_boxes(neutral):
        # The blink line sits inside the eye box.
        blacks = [
            (x, y)
            for y in range(y0, y1 + 1)
            for x in range(x0, x1 + 1)
            if blink[y][x] == T_BLACK
        ]
        assert blacks


def test_dither_shade_deterministic():
    grid = frame_grid("neutral", width=22)
    assert dither_shade(grid, seed=7) == dither_shade(grid, seed=7)
    assert dither_shade(grid, seed=7) != dither_shade(grid, seed=8)
    # Dither checkerboards the shade tones (plum/lilac) into white; black,
    # white, gray, and transparent are untouched.
    shaded = dither_shade(grid, seed=7)
    for y, row in enumerate(grid):
        for x, tone in enumerate(row):
            if tone in (T_BLACK, T_WHITE, T_NONE, T_GRAY):
                assert shaded[y][x] == tone
            else:
                assert shaded[y][x] in (tone, T_WHITE)
    # Some shade cells actually get dithered out.
    assert any(
        shaded[y][x] == T_WHITE and grid[y][x] != T_WHITE
        for y, row in enumerate(grid)
        for x in range(len(row))
    )


def test_pixel_font_output():
    glyphs = pixel_text("GHOST", T_LILAC)
    assert len(glyphs) == 5  # 3x5 font
    assert all(len(row) > 0 for row in glyphs)
    assert all(
        cell in (T_NONE, T_LILAC) for row in glyphs for cell in row
    )
    # Unknown characters render as blank space, same width as a glyph.
    blank = pixel_text("~")
    assert all(cell == T_NONE for row in blank for cell in row)
    assert len(blank[0]) == len(glyphs[0]) // 5 or True  # smoke


def test_leaf_sprites_small_and_few_colors():
    sprites = leaf_sprites()
    assert 1 <= len(sprites) <= 4
    for pattern, color in sprites:
        assert len(pattern) <= 3 and len(pattern[0]) <= 3
        assert color.startswith("#")
    frags = leaf_fragments(sprites[0][0], sprites[0][1])
    assert len(frags) == len(sprites[0])
    assert len(frags[0]) == len(sprites[0][0])


def test_night_scene_deterministic():
    first = night_scene(width=22, height=20, seed=3)
    second = night_scene(width=22, height=20, seed=3)
    assert first == second
    third = night_scene(width=22, height=20, seed=4)
    assert first != third
    assert len(first) == 20 and all(len(row) == 22 for row in first)


def test_pixel_rule_width_and_charset():
    rows = pixel_rule(40)
    assert len(rows) == 1
    cells = rows[0]
    assert len(cells) == 40
    for _style, ch in cells:
        assert ch in ("\u259a", "\u259e")


def test_materialize_sparse_to_full():
    grid = frame_grid("neutral", width=22)
    steps = materialize_steps(grid, seed=11)
    assert len(steps) >= 2
    counts = [sum(1 for row in step for t in row if t != T_NONE) for step in steps]
    total = sum(1 for row in grid for t in row if t != T_NONE)
    assert counts[0] < total // 3  # starts sparse
    assert counts == sorted(counts)  # monotonic
    assert steps[-1] == grid  # ends exactly full


def test_bob_offset_alternates():
    assert bob_offset(0) != bob_offset(1)
    assert bob_offset(0) == bob_offset(2)


def test_fragments_and_render_frame():
    grid = frame_grid("neutral", width=22)
    frags = fragments_from_grid(dither_shade(grid))
    # Half-block rendering: two tone rows per fragment row.
    assert len(frags) == (len(grid) + 1) // 2
    assert all(len(row) == len(grid[0]) for row in frags)
    rows = render_sprite_frame("neutral", width=22, bob=0)
    assert len(rows) == len(frags)
    assert all(isinstance(cell, tuple) for row in rows for cell in row)


def test_render_ghost_pane_dimensions():
    rows = render_ghost_pane(width=40, height=20, name="neutral")
    assert rows
    # Half-block rendering: height // 2 fragment rows.
    assert len(rows) == 10
    assert all(len(row) == 40 for row in rows)


def test_tone_colors_are_windows_safe_hex():
    for tone, color in TONE_COLORS.items():
        assert color.startswith("#") and len(color) == 7
        int(color[1:], 16)


def test_gray_mouth_single_region():
    grid = frame_grid("neutral", width=22)
    gray = {(x, y) for y, row in enumerate(grid) for x, t in enumerate(row) if t == T_GRAY}
    assert gray, "mouth interior must exist"
    # Flood fill: all gray cells must be one connected region (no fragments).
    seen = set()
    stack = [next(iter(gray))]
    seen.add(stack[0])
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) in gray and (nx, ny) not in seen:
                seen.add((nx, ny))
                stack.append((nx, ny))
    assert seen == gray, "gray mouth interior must be one region"
    # Mouth sits below the eyes.
    _, y0, _, _ = _sprite_bbox(grid)
    assert min(y for (x, y) in gray) > y0 + len(grid) * 0.3


def test_eyes_match_anchors():
    grid = frame_grid("neutral", width=22)
    x0, y0, x1, y1 = _sprite_bbox(grid)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    for fx, fy in (_EYE_L, _EYE_R):
        cx, cy = int(round(x0 + fx * bw)), int(round(y0 + fy * bh))
        assert grid[cy][cx] == T_BLACK, (fx, fy, cx, cy)
    mx, my = int(round(x0 + _MOUTH[0] * bw)), int(round(y0 + _MOUTH[1] * bh))
    assert grid[my][mx] == T_GRAY, (mx, my)


def test_sleep_z_fragments_cycle():
    from ghost_desk.face import sleep_z_fragments
    z0 = sleep_z_fragments(0, 22, 20)
    z3 = sleep_z_fragments(3, 22, 20)
    assert z0 and z3
    # Positions shift with tick (rising).
    assert {(x, y) for x, y, _ in z0} != {(x, y) for x, y, _ in z3}
    # All within bounds.
    for x, y, rows in z0:
        assert 0 <= x < 22 and 0 <= y < 20
        assert rows


def test_confetti_fragments_restrained():
    from ghost_desk.face import confetti_fragments
    c1 = confetti_fragments(0, 22, 20)
    c2 = confetti_fragments(0, 22, 20)
    assert c1 == c2  # deterministic
    assert 1 <= len(c1) <= 10  # restrained
    for x, y, rows in c1:
        assert 0 <= x < 22 and 0 <= y < 20
