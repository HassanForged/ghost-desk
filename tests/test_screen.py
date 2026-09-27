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
    assert menu[0] == "which brain"
    assert menu[1:] == [
        "  1  chatgpt subscription",
        "  2  claude subscription",
        "  3  grok subscription",
        "  4  api key",
        "  5  local model",
    ]


def test_session_keeps_the_ghost_on_the_right():
    from ghost_desk.tui import session_chrome

    chrome = session_chrome()
    assert chrome["ghost_side"] == "right"
    assert chrome["header"] is True
    still = render_blocks(width=chrome["ghost_width"], height=chrome["ghost_height"], bob=0)
    assert still


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
