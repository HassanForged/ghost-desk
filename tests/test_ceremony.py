"""The ceremony: verify-then-announce. Mutate -> verify -> report -> celebrate.

The report comes ONLY from on-disk diffs, never from in-memory claims.
"""

from pathlib import Path

import pytest

from ghost_desk.agent import DeskSession
from ghost_desk.ceremony import (
    UpgradeReport,
    ceremony_enabled,
    collect_upgrades,
    drain_ceremony,
    finish_and_stage,
    phases,
    render_blocking,
    should_celebrate,
    snapshot_skills,
    stage_report,
    take_staged,
)
from ghost_desk.skills import file_whisper, manifest_check


def _turn(root: Path, whispers=(), tool_calls=1) -> "tuple[UpgradeReport, DeskSession]":
    session = DeskSession()
    session.tool_calls_this_turn = tool_calls
    session.pending_whispers = list(whispers)
    return finish_and_stage(root, session=session), session


# --- integrity: verify-then-announce ------------------------------------------


def test_manifest_names_wisp_on_disk_and_in_report(tmp_path):
    root = tmp_path / "skills"
    report, _ = _turn(
        root,
        whispers=[("pdf", "use pdftotext"), ("pdf", "qpdf merges"), ("pdf", "ocrmypdf layers")],
    )
    wisp_path = root / "documents" / "pdf.md"
    assert wisp_path.is_file()  # the upgrade really happened...
    assert report.wisps_manifested == ["pdf"]  # ...and the report names it
    assert should_celebrate(report)


def test_empty_diff_never_celebrates(tmp_path):
    root = tmp_path / "skills"
    before = snapshot_skills(root)
    report = collect_upgrades(before, snapshot_skills(root))
    assert not should_celebrate(report)
    rendered = []
    assert drain_ceremony(root, render=rendered.append) is False
    assert rendered == []


def test_failed_write_means_no_ceremony(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        assert file_whisper(root, "pdf", note)["filed"]

    def boom(self, *args, **kwargs):
        raise OSError("disk is gone")

    monkeypatch.setattr(Path, "write_text", boom)
    before = snapshot_skills(root)
    manifest_check(root)  # tries to manifest; every write fails verification
    report = collect_upgrades(before, snapshot_skills(root))
    assert not (root / "documents" / "pdf.md").exists()
    assert not should_celebrate(report)
    assert report.wisps_manifested == []


def test_pipeline_twice_second_report_empty(tmp_path):
    root = tmp_path / "skills"
    first, _ = _turn(
        root, whispers=[("pdf", "one"), ("pdf", "two"), ("pdf", "three")]
    )
    assert first.wisps_manifested == ["pdf"]
    # Same whispers again: dedupe means no write, no diff, no ceremony.
    second, _ = _turn(root, whispers=[("pdf", "one")])
    assert not should_celebrate(second)
    assert (root / "documents" / "pdf.md").read_text(encoding="utf-8").count("- one") == 1


def test_report_ignores_in_memory_claims(tmp_path):
    # A report built by hand-claims is not how the pipeline works: only the
    # disk diff can produce these fields through the real path.
    root = tmp_path / "skills"
    before = snapshot_skills(root)
    manifest_check(root)  # nothing filed -> nothing to manifest
    report = collect_upgrades(before, snapshot_skills(root))
    assert report == UpgradeReport()


# --- the pipeline ------------------------------------------------------------


def test_whispers_filed_counted_from_disk(tmp_path):
    root = tmp_path / "skills"
    report, _ = _turn(root, whispers=[("pdf", "a single whisper")])
    assert report.whispers_filed == 1
    assert should_celebrate(report)
    assert "- pdf: a single whisper" in (root / "documents.md").read_text(encoding="utf-8")


# --- the ghost's own whisper markers -------------------------------------------


def test_harvest_whispers_extracts_and_strips():
    from ghost_desk.agent import _harvest_whispers

    session = DeskSession()
    reply = (
        "Done — merged the PDFs.\n"
        "learn this whisper: pdf - qpdf merges pages without rasterizing\n"
        "Anything else?"
    )
    clean = _harvest_whispers(reply, session)
    assert session.pending_whispers == [("pdf", "qpdf merges pages without rasterizing")]
    assert "learn this whisper" not in clean
    assert "Done — merged the PDFs." in clean
    assert "Anything else?" in clean


def test_harvest_whispers_case_insensitive_topic_lowered():
    from ghost_desk.agent import _harvest_whispers

    session = DeskSession()
    _harvest_whispers("Learn This Whisper: PDF - note here", session)
    assert session.pending_whispers == [("pdf", "note here")]


def test_harvested_whispers_file_only_after_real_work(tmp_path):
    # The model whispered, but the turn did no tool calls: held, not filed.
    from ghost_desk.agent import _harvest_whispers

    root = tmp_path / "skills"
    session = DeskSession()
    _harvest_whispers("learn this whisper: pdf - a trick", session)
    session.tool_calls_this_turn = 0
    report = finish_and_stage(root, session=session)
    assert not should_celebrate(report)
    assert session.pending_whispers == [("pdf", "a trick")]
    # Next turn does real work: the held whisper files.
    session.tool_calls_this_turn = 2
    report = finish_and_stage(root, session=session)
    assert report.whispers_filed == 1
    assert should_celebrate(report)


def test_no_tool_activity_no_filing(tmp_path):
    root = tmp_path / "skills"
    report, session = _turn(root, whispers=[("pdf", "held back")], tool_calls=0)
    assert not should_celebrate(report)
    assert not (root / "documents.md").exists()
    assert session.pending_whispers == [("pdf", "held back")]  # kept for later


def test_whisper_folded_into_wisp_deepens_it(tmp_path):
    root = tmp_path / "skills"
    _turn(root, whispers=[("pdf", "one"), ("pdf", "two"), ("pdf", "three")])
    report, _ = _turn(root, whispers=[("pdf", "a fourth")])
    assert report.wisps_deepened == ["pdf"]
    assert report.whispers_filed == 0
    assert should_celebrate(report)


# --- staging: once-only, crash-safe ------------------------------------------


def test_stage_take_is_once_only(tmp_path):
    root = tmp_path / "skills"
    rid = stage_report(root, UpgradeReport(whispers_filed=2))
    assert rid
    first = take_staged(root)
    assert len(first) == 1 and first[0].whispers_filed == 2
    # A crash between take and render still never double-celebrates.
    assert take_staged(root) == []


def test_same_report_staged_twice_shows_once(tmp_path):
    root = tmp_path / "skills"
    report = UpgradeReport(wisps_manifested=["pdf"])
    stage_report(root, report)
    stage_report(root, report)
    assert len(take_staged(root)) == 1


def test_ceremony_env_off(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_DESK_CEREMONY", "off")
    assert not ceremony_enabled()
    root = tmp_path / "skills"
    assert stage_report(root, UpgradeReport(whispers_filed=1)) is None


def test_ceremony_enabled_by_default():
    assert ceremony_enabled()


# --- copy: the phase machine --------------------------------------------------


def test_phase_order_and_copy():
    report = UpgradeReport(
        whispers_filed=2,
        wisps_manifested=["pdf"],
        wisps_deepened=["png"],
        haunts_rewritten=["documents"],
    )
    texts = [phase["text"] for phase in phases(report)]
    assert texts == [
        "time for your upgrade.",
        "whispers gathered: 2",
        "+ wisp: pdf",
        "wisps deepened: 1",
        "documents — rewritten from its wisps.",
        "the haunting deepens.",
    ]
    assert phases(report)[-1]["confetti"] is True


def test_while_away_variant():
    report = UpgradeReport(whispers_filed=1, while_away=True)
    assert phases(report)[0]["text"] == "while you were away — time for your upgrade."


def test_phases_skip_empty_sections():
    report = UpgradeReport(haunts_rewritten=["documents"])
    texts = [phase["text"] for phase in phases(report)]
    assert texts == [
        "time for your upgrade.",
        "documents — rewritten from its wisps.",
        "the haunting deepens.",
    ]


def test_render_blocking_ticks_counts():
    report = UpgradeReport(
        whispers_filed=2, wisps_manifested=["pdf"], wisps_deepened=["png"]
    )
    shown, slept = [], []
    render_blocking(
        report, print_fn=shown.append, sleep_fn=lambda s: slept.append(s)
    )
    assert shown == [
        "time for your upgrade.",
        "whispers gathered: 2",
        "+ wisp: pdf",
        "wisps deepened: 1",
        "the haunting deepens.",
    ]
    assert slept == [0.25, 0.25, 0.25]  # count lines tick up one at a time


def test_should_celebrate_false_on_empty():
    assert not should_celebrate(UpgradeReport())
