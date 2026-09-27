"""Tests for the isolated state helpers: life, health, intro, crew."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghost_desk.crew import CrewState, MAX_CREW
from ghost_desk.face import classify_activity
from ghost_desk.health import (
    BLOCKS,
    WARNING_LINE,
    ContextHealth,
    context_limit,
    estimate_tokens,
)
from ghost_desk.intro import IntroState, intro_enabled
from ghost_desk.life import GhostLife, SLEEP_AFTER_TICKS


def test_life_blinks_periodically():
    life = GhostLife()
    frames = set()
    for _ in range(30):
        life.tick()
        frames.add(life.frame())
    assert "blink" in frames
    assert "neutral" in frames


def test_life_sleeps_after_idle_and_wakes():
    life = GhostLife()
    for _ in range(SLEEP_AFTER_TICKS):
        life.tick()
    assert life.sleeping
    assert life.frame() == "sleep"
    life.mark_active()
    assert not life.sleeping
    assert life.frame() != "sleep"


def test_life_busy_scans_eyes():
    life = GhostLife()
    life.set_busy(True)
    frames = {life.frame() for _ in range(6) for _ in (life.tick(),)}
    assert "look_left" in frames
    assert "look_right" in frames
    life.set_busy(False)


def test_life_typing_looks_down():
    life = GhostLife()
    life.set_typing(True)
    assert life.frame() == "look_down"
    life.set_typing(False)
    assert life.frame() != "look_down"


def test_life_error_flinch_is_brief():
    life = GhostLife()
    life.mark_error()
    assert life.flinch_dx() != 0
    life.tick()
    life.tick()
    assert life.flinch_dx() == 0


def test_life_bounce_only_after_tool_work():
    life = GhostLife()
    life.mark_success()  # no tool work: no bounce
    assert not life.bouncing()
    life.mark_tool_work()
    life.mark_success()
    assert life.bouncing()
    for _ in range(10):
        life.tick()
    assert not life.bouncing()


def test_health_thresholds():
    health = ContextHealth(model="gpt-4o")  # 128k limit
    cases = [
        (0.0, "#b8a6d9"),
        (0.59, "#b8a6d9"),
        (0.60, "#d9a648"),
        (0.84, "#d9a648"),
        (0.85, "#d94f4f"),
        (1.0, "#d94f4f"),
    ]
    for ratio, color in cases:
        health.used_tokens = int(128_000 * ratio)
        colors = health.block_colors()
        assert len(colors) == BLOCKS == 20
        filled = int(ratio * BLOCKS)
        assert all(c == color for c in colors[:filled])
        assert all(c == "#2e2133" for c in colors[filled:])


def test_health_env_override():
    import os

    os.environ["GHOST_DESK_CONTEXT_LIMIT"] = "50000"
    try:
        assert context_limit("gpt-4o") == 50000
    finally:
        del os.environ["GHOST_DESK_CONTEXT_LIMIT"]
    assert context_limit("gpt-4o") == 128_000
    assert context_limit("unknown-model") == 128_000


def test_health_fallback_estimate():
    health = ContextHealth()
    health.add_turn(text="x" * 4000)
    assert health.used_tokens == 1000  # chars/4
    health.add_turn(reported_tokens=500)
    assert health.used_tokens == 1500  # reported wins


def test_health_warning_once():
    health = ContextHealth(model="gpt-4o")
    health.add_turn(reported_tokens=120_000)
    assert health.take_warning() == WARNING_LINE
    assert health.take_warning() is None  # only once
    # /new resets the latch.
    health.reset()
    health.add_turn(reported_tokens=120_000)
    assert health.take_warning() == WARNING_LINE


def test_health_compaction_lowers_reading():
    health = ContextHealth()
    health.add_turn(reported_tokens=100_000)
    assert health.ratio > 0.5
    health.mark_compacted(10_000)
    assert health.ratio < 0.5


def test_intro_phases_and_skip():
    intro = IntroState()
    assert intro.phase == "beat"
    assert not intro.done
    intro.skip()
    assert intro.done
    assert intro.phase == "ui"


def test_intro_completes_into_ui():
    intro = IntroState()
    phases = set()
    for _ in range(20):
        phases.add(intro.phase)
        intro.tick()
    assert intro.done
    assert intro.phase == "ui"
    assert {"beat", "materialize", "wordmark", "tagline"} <= phases


def test_intro_env_disable(monkeypatch):
    monkeypatch.setenv("GHOST_DESK_INTRO", "off")
    assert intro_enabled() is False
    monkeypatch.delenv("GHOST_DESK_INTRO")
    assert intro_enabled() is True


def test_crew_cap_and_overflow():
    crew = CrewState()
    for i in range(6):
        crew.add(f"w{i}", "reading", f"worker {i}")
    assert len(crew.visible) == MAX_CREW == 4
    assert crew.overflow == 2


def test_crew_completed_dissolves_out():
    crew = CrewState()
    crew.add("w1", "building", "worker 1")
    crew.add("w2", "reading", "worker 2")
    crew.complete("w1")
    assert len(crew.visible) == 2  # still visible while dissolving
    for _ in range(10):
        crew.tick()
    assert len(crew.visible) == 1
    assert crew.visible[0].worker_id == "w2"


def test_classify_activity():
    assert classify_activity("read_file") == "reading"
    assert classify_activity("write_note") == "writing"
    assert classify_activity("shell_exec") == "building"
    assert classify_activity("something_else") == "thinking"


def test_play_intro_disabled():
    import os
    from ghost_desk.intro import play_intro
    os.environ["GHOST_DESK_INTRO"] = "off"
    try:
        calls = []
        play_intro(render=calls.append, sleep=lambda s: None, key_pressed=lambda: False)
        assert calls == []
    finally:
        del os.environ["GHOST_DESK_INTRO"]


def test_play_intro_sequence_and_skip():
    import os
    from ghost_desk.intro import play_intro
    os.environ.pop("GHOST_DESK_INTRO", None)
    # Full play: beat + 12 materialize + 10 wordmark chars + tagline.
    frames = []
    play_intro(render=frames.append, sleep=lambda s: None, key_pressed=lambda: False)
    assert len(frames) == 1 + 12 + 10 + 1, len(frames)
    # The last wordmark frame shows the full pixel wordmark (half-blocks).
    wordmark_rows = frames[-2]
    flat = "".join(ch for row in wordmark_rows for _, ch in row)
    assert "▀" in flat or "▄" in flat
    # The tagline frame carries the tagline text.
    tagline_flat = "".join(ch for row in frames[-1] for _, ch in row)
    assert "one ghost, your machine, your notes." in tagline_flat
    # Skip on first keypress: only the beat renders.
    frames2 = []
    play_intro(render=frames2.append, sleep=lambda s: None, key_pressed=lambda: True)
    assert len(frames2) == 1
