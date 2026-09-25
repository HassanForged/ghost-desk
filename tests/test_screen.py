"""Boot and session screens: photo on the right, log on the left."""

from ghost_desk.face import HOOD, render_ansi, render_blocks


def test_portrait_ansi_is_the_photo_not_a_drawing():
    assert HOOD.is_file()
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
    assert chrome["header"] is False
    still = render_blocks(width=chrome["ghost_width"], height=chrome["ghost_height"], bob=0)
    assert still
