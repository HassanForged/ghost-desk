"""Boot and session screens: photo on the right, log on the left."""

from ghost_desk.face import GHOST, render_ansi, render_blocks


def test_portrait_ansi_is_the_ghost_not_a_drawing():
    assert GHOST.is_file()
    rows = render_ansi()
    blob = "\n".join(rows)
    assert rows
    assert "\033[38;2" in blob or "\033[48;2" in blob
    assert "· · ·" not in blob


def test_boot_paint_puts_photo_ansi_on_the_right(monkeypatch):
    from ghost_desk import boot

    written: list[str] = []
    monkeypatch.setattr(boot, "_write", written.append)
    monkeypatch.setattr(boot.time, "sleep", lambda _s: None)
    boot._paint(["GHOST DESK", "which brain"], level=4, cursor=False)
    blob = "".join(written)
    assert "GHOST DESK" in blob
    assert "which brain" in blob
    assert "\033[38;2" in blob or "\033[48;2" in blob
    assert "· · ·" not in blob


def test_boot_menu_matches_the_reference_screen():
    from ghost_desk.boot import _menu

    menu = _menu()
    assert menu[0] == "pick a brain for the ghost"
    assert menu[1:] == [
        "  1  chatgpt or codex subscription",
        "  2  openai api key",
        "  3  claude subscription",
        "  4  grok subscription (oauth)",
        "  5  grok api key",
        "  6  local model (ollama)",
        "  7  openrouter or any openai-compatible url",
    ]


def test_session_is_fullwidth_with_docked_buddy():
    from ghost_desk.tui import session_chrome

    chrome = session_chrome()
    assert chrome["header"] is True
    assert chrome["fullwidth"] is True
    assert "col_width" not in chrome  # no centered column anymore
    assert chrome["buddy_width"] == 16
    assert chrome["buddy_rows"] == 6


def test_user_messages_render_as_prefixed_lines():
    from ghost_desk.tui import _you_fragments

    fragments = _you_fragments("hello ghost", 100)
    text = "".join(part for _, part in fragments)
    assert text.startswith("› hello ghost")
    assert "╭" not in text and "╮" not in text  # no bubble chrome
    for line in text.split("\n"):
        assert len(line) <= 100  # wrapped to the terminal width


def test_user_message_wraps_at_width():
    from ghost_desk.tui import _you_fragments

    fragments = _you_fragments("word " * 40, 40)
    text = "".join(part for _, part in fragments)
    assert text.count("› ") == 1  # prefix only on the first wrapped line
    for line in text.split("\n"):
        assert len(line) <= 40


def test_header_line_places_meter_right():
    from ghost_desk.tui import header_line

    cells = header_line(busy=False, meter_colors=("#c9a8cc",) * 10, model="grok-4.6", width=100)
    text = "".join(ch for _, ch in cells)
    assert text.startswith("ghost desk · haunting")
    assert text.rstrip().endswith("grok-4.6")
    assert "█" * 10 in text


def test_header_line_busy_and_full_access():
    from ghost_desk.tui import header_line

    cells = header_line(busy=True, phase="thinking", elapsed=4, full_access=True, width=100)
    text = "".join(ch for _, ch in cells)
    assert "rattling chains…  4s" in text
    assert "full access" in text


def test_header_line_drops_meter_when_narrow():
    from ghost_desk.tui import header_line

    cells = header_line(busy=False, meter_colors=("#c9a8cc",) * 10, model="x" * 60, width=40)
    text = "".join(ch for _, ch in cells)
    assert "█" not in text  # dropped rather than wrapped
    assert text.startswith("ghost desk · haunting")


def test_buddy_is_compact():
    from ghost_desk.face import BUDDY_ROWS, BUDDY_W, buddy_frame, fragments_from_grid

    assert BUDDY_W == 10
    assert BUDDY_ROWS == 5
    rows = fragments_from_grid(buddy_frame("neutral"))
    assert len(rows) == BUDDY_ROWS
    assert all(len(r) == BUDDY_W for r in rows)


def test_buddy_eye_frames_only_touch_eyes():
    from ghost_desk.face import BUDDY_H, BUDDY_W, buddy_frame

    base = buddy_frame("neutral")
    for name in ("blink", "sleep", "look_left", "look_right", "look_down", "glance_meter"):
        frame = buddy_frame(name)
        diff = [(x, y) for y in range(BUDDY_H) for x in range(BUDDY_W) if frame[y][x] != base[y][x]]
        assert diff, name
        boxes = [(2, 2, 4, 5), (5, 2, 7, 5)]  # eye boxes, ±1 for look shifts
        assert all(
            any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes) for x, y in diff
        ), name


def test_update_show_uses_buddy_rows():
    from ghost_desk.face import BUDDY_ROWS, BUDDY_W
    from ghost_desk.tui import _update_buddy_rows
    from ghost_desk.update import UpdateSequence

    seq = UpdateSequence()
    rows = _update_buddy_rows(seq)
    assert len(rows) == BUDDY_ROWS
    assert all(len(r) == BUDDY_W for r in rows)


def test_meter_takes_block_count():
    from ghost_desk.health import ContextHealth

    h = ContextHealth(model="x")
    assert len(h.block_colors(10)) == 10
    assert len(h.block_colors()) == 20  # default unchanged


def test_greeting_matches_time_of_day():
    from ghost_desk.tui import _greeting

    assert _greeting() in {"Good morning.", "Good afternoon.", "Good evening."}


def test_chips_are_suggestions():
    from ghost_desk.tui import CHIPS

    assert len(CHIPS) == 3
    assert all(chip and len(chip) < 30 for chip in CHIPS)


def test_portrait_is_pixel_art_with_hard_tones():
    import re

    rows = render_blocks(width=30, height=38, bob=0)
    colors = set()
    for row in rows:
        for style, _ch in row:
            colors.update(re.findall(r"#([0-9a-f]{6})", style))
    # The cartoon's own palette: black lines, lilac shading, white body.
    assert colors <= {"000000", "6e5a6e", "c9a8cc", "ffffff"}, colors
    assert "ffffff" in colors  # the body is actually there
    assert "c9a8cc" in colors  # the lilac shading is actually there


def test_overlay_stamp_survives_leaf_over_multichar_text():
    """Regression: a leaf drifting over header text used to crash.

    Rows hold multi-char text fragments (e.g. the header line), but overlays
    index by character column. Stamping a leaf past the fragment count wrote
    out of range -> `list assignment index out of range`.
    """
    from ghost_desk.tui import _stamp_overlay_cells

    blank = ("", " ")
    # Row 0: 18 single-char buddy cells + multi-char header text fragments,
    # exactly like header_fragments builds them.
    rows = [
        [("", " ") ] * 18 + [("class:brand", "ghost desk · haunting"), ("", "   "), ("class:muted", "grok-4.6")],
        [("", " ")] * 18,
    ]
    leaf = [[("fg:#c9a8cc", "❧")]]
    overlays = [(190, 0, leaf), (195, 1, leaf), (5, 0, leaf)]  # far right + buddy zone
    stamped = _stamp_overlay_cells(rows, overlays, 200, blank, 6)
    assert all(len(r) == 200 for r in stamped)
    # The leaf actually landed where it should.
    assert stamped[0][190] == ("fg:#c9a8cc", "❧")
    assert stamped[1][195] == ("fg:#c9a8cc", "❧")
    assert stamped[0][5] == ("fg:#c9a8cc", "❧")
    # Header text survived the explode: characters in order.
    text = "".join(ch for _, ch in stamped[0][18:39])
    assert text == "ghost desk · haunting"


def test_overlay_stamp_clips_out_of_bounds():
    from ghost_desk.tui import _stamp_overlay_cells

    blank = ("", " ")
    rows = [[("", "a"), ("", "bc")]]  # multi-char fragment, visible width 3
    frag = [[("", "X")]]
    # Negative origin, past the right edge, past the bottom: all clipped.
    stamped = _stamp_overlay_cells(rows, [(-5, 0, frag), (99, 0, frag), (0, 9, frag)], 10, blank, 6)
    assert [ch for _, ch in stamped[0]] == list("abc") + [" "] * 7
