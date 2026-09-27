"""Tests for ceremony_fx: visual ceremony against a stubbed phase interface.

Never imports the sibling worktree; phases are stubbed as {kind, text} dicts.
"""

import os

from ghost_desk.ceremony_fx import ceremony_enabled, play_ceremony


def _stub_phases():
    return [
        {"kind": "announce", "text": "time for your upgrade."},
        {"kind": "count", "text": "whispers gathered: 5"},
        {"kind": "wisp", "text": "+ wisp: test-wisp"},
        {"kind": "count", "text": "wisps deepened: 2"},
        {"kind": "deepen", "text": "test-haunt — rewritten from its wisps."},
        {"kind": "close", "text": "the haunting deepens.", "confetti": True},
    ]


def test_ceremony_disabled():
    os.environ["GHOST_DESK_CEREMONY"] = "off"
    try:
        assert not ceremony_enabled()
        frames = []
        play_ceremony(_stub_phases(), render=frames.append, sleep=lambda s: None)
        assert frames == []
    finally:
        del os.environ["GHOST_DESK_CEREMONY"]


def test_ceremony_plays_all_phases():
    os.environ.pop("GHOST_DESK_CEREMONY", None)
    assert ceremony_enabled()
    frames = []
    play_ceremony(
        _stub_phases(),
        render=frames.append,
        sleep=lambda s: None,
        key_pressed=lambda: False,
    )
    assert frames, "expected frames to render"
    # The final frame contains the close copy.
    flat = "".join(ch for row in frames[-1] for _, ch in row)
    assert "the haunting deepens." in flat
    # The announce copy appears.
    assert any(
        "time for your upgrade." in "".join(ch for row in f for _, ch in row)
        for f in frames
    )


def test_ceremony_skip_jumps_to_end():
    os.environ.pop("GHOST_DESK_CEREMONY", None)
    frames = []
    # Skip immediately: only the shimmer steps render before the skip.
    play_ceremony(
        _stub_phases(),
        render=frames.append,
        sleep=lambda s: None,
        key_pressed=lambda: True,
    )
    # Skipped during announce shimmer: no phase text rendered.
    assert frames
    flat_all = " ".join("".join(ch for row in f for _, ch in row) for f in frames)
    assert "the haunting deepens." not in flat_all


def test_ceremony_empty_phases_noop():
    os.environ.pop("GHOST_DESK_CEREMONY", None)
    frames = []
    play_ceremony([], render=frames.append, sleep=lambda s: None)
    assert frames == []
