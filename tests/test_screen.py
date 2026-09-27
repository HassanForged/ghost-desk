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


def test_session_is_a_centered_single_column():
    from ghost_desk.tui import session_chrome

    chrome = session_chrome()
    assert chrome["header"] is True
    assert chrome["col_width"] == 76
    assert "ghost_side" not in chrome  # no more side pane
    still = render_blocks(width=chrome["ghost_width"], height=chrome["ghost_height"], bob=0)
    assert still


def test_user_messages_render_as_right_aligned_bubbles():
    from ghost_desk.tui import COL_W, _bubble

    fragments = _bubble("hello ghost")
    text = "".join(part for _, part in fragments)
    assert "╭" in text and "╮" in text and "╰" in text and "╯" in text
    for line in text.split("\n"):
        if "╭" in line:
            assert len(line) == COL_W  # right-aligned to the column
            assert line.endswith("╮")


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
