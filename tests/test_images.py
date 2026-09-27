"""Tests for ghost_desk.images: the literal picture path."""

import base64
import os

import pytest

from ghost_desk import images


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("GHOST_DESK_IMG", "KITTY_WINDOW_ID", "TERM_PROGRAM", "TERM"):
        monkeypatch.delenv(key, raising=False)


def test_detect_nothing_by_default():
    assert images.detect_protocol() is None


def test_detect_ignores_terminal_env(monkeypatch):
    # The real picture is opt-in only; terminal sniffing no longer enables it.
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    assert images.detect_protocol() is None
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    assert images.detect_protocol() is None
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert images.detect_protocol() is None
    monkeypatch.setenv("TERM", "xterm-kitty")
    assert images.detect_protocol() is None


def test_detect_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("GHOST_DESK_IMG", "kitty")
    assert images.detect_protocol() == "kitty"
    monkeypatch.setenv("GHOST_DESK_IMG", "iterm2")
    assert images.detect_protocol() == "iterm2"
    monkeypatch.setenv("GHOST_DESK_IMG", "off")
    assert images.detect_protocol() is None


def test_kitty_show_roundtrip():
    seq = images.kitty_show(cols=30, rows=38)
    assert seq.startswith("\x1b_Ga=T,f=100,")
    assert "c=30" in seq and "r=38" in seq
    assert seq.endswith("\x1b\\")
    payload = "".join(part.split(";", 1)[1].rstrip("\x1b\\") for part in seq.split("\x1b_G")[1:])
    assert base64.b64decode(payload) == images.png_bytes()


def test_kitty_delete_format():
    assert images.kitty_delete() == "\x1b_Ga=d,d=i,q=2,i=31\x1b\\"


def test_iterm2_show_roundtrip():
    seq = images.iterm2_show(cols=30, rows=38)
    assert seq.startswith("\x1b]1337;File=")
    assert "inline=1:" in seq
    assert seq.endswith("\x07")
    payload = seq.split("inline=1:", 1)[1].rstrip("\x07")
    assert base64.b64decode(payload) == images.png_bytes()


class FakeSize:
    def __init__(self, rows, columns):
        self.rows = rows
        self.columns = columns


class FakeOutput:
    def __init__(self, rows=34, columns=100):
        self._size = FakeSize(rows, columns)
        self.writes: list[str] = []
        self.flushes = 0

    def get_size(self):
        return self._size

    def write_raw(self, data):
        self.writes.append(data)

    def flush(self):
        self.flushes += 1


def test_install_picture_noop_without_protocol():
    out = FakeOutput()
    real = out.flush
    cleanup = images.install_picture(out, None, {"ghost_width": 30, "ghost_height": 38}, lambda: (False, 0))
    out.flush()
    assert out.writes == []
    assert out.flush == real
    cleanup()


def test_install_picture_paints_once_then_skips():
    out = FakeOutput()
    cleanup = images.install_picture(out, "kitty", {"ghost_width": 30, "ghost_height": 38}, lambda: (False, 0))
    out.flush()
    first_writes = list(out.writes)
    assert any("\x1b[1;1H" in w for w in first_writes)  # top-left buddy: row 1, col 1
    assert any("c=30" in w and "r=38" in w for w in first_writes)
    out.writes.clear()
    out.flush()
    assert out.writes == []  # placement unchanged: picture plane persists
    cleanup()


def test_install_picture_repaints_on_bob_and_cleans_up():
    out = FakeOutput()
    real_flush = out.flush
    tick = {"n": 0}
    cleanup = images.install_picture(
        out, "kitty", {"ghost_width": 30, "ghost_height": 38}, lambda: (True, tick["n"])
    )
    out.flush()
    assert out.writes, "busy tick 0 paints"
    out.writes.clear()
    tick["n"] = 1
    out.flush()
    joined = "".join(out.writes)
    assert "\x1b_Ga=d,d=i" in joined  # old frame deleted before repaint
    assert "\x1b[2;1H" in joined  # bobbed down one row, still top-left
    out.writes.clear()
    cleanup()
    assert out.flush == real_flush  # original flush restored
    assert any("\x1b_Ga=d,d=i" in w for w in out.writes)  # picture deleted on exit


def test_install_picture_too_narrow_skips():
    out = FakeOutput(rows=34, columns=50)
    images.install_picture(out, "kitty", {"ghost_width": 30, "ghost_height": 38}, lambda: (False, 0))
    out.flush()
    assert out.writes == []


def test_boot_paints_picture_when_supported(monkeypatch):
    import ghost_desk.boot as boot
    from ghost_desk.face import BOOT_HEIGHT, BOOT_WIDTH

    monkeypatch.setenv("GHOST_DESK_IMG", "kitty")
    monkeypatch.setattr(boot, "_BOOT_PICTURE", "unset")
    monkeypatch.setattr(boot, "_BOOT_PAINTED", False)
    monkeypatch.setattr(boot, "_boot_picture_fits", lambda: True)
    written: list[str] = []
    monkeypatch.setattr(boot, "_write", written.append)

    boot._paint(["ghost desk"], 0, False)
    blob = "".join(written)
    assert "\x1b_Ga=T,f=100" in blob
    assert f"c={BOOT_WIDTH}" in blob and f"r={BOOT_HEIGHT}" in blob
    assert "\x1b[1;45H" in blob  # ghost column in the boot layout

    written.clear()
    boot._paint(["ghost desk"], 0, False)
    assert "\x1b_Ga=T,f=100" not in "".join(written)  # painted once

    written.clear()
    boot.BootScreen().leave()
    assert "\x1b_Ga=d,d=i" in "".join(written)  # cleaned up on leave


def test_boot_falls_back_without_protocol(monkeypatch):
    import ghost_desk.boot as boot

    monkeypatch.setattr(boot, "_BOOT_PICTURE", "unset")
    monkeypatch.setattr(boot, "_BOOT_PAINTED", False)
    written: list[str] = []
    monkeypatch.setattr(boot, "_write", written.append)
    boot._paint(["ghost desk"], 0, False)
    blob = "".join(written)
    assert "\x1b_G" not in blob  # no kitty sequences
    assert "▀" in blob  # half-block art still painted
