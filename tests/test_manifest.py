"""Whisper filing, manifesting, and wisp deepening."""

import os
from pathlib import Path

import pytest

from ghost_desk.skills import (
    file_whisper,
    fold_whispers_into_wisp,
    load_child,
    manifest_check,
    manifest_threshold,
    manifest_wisp,
    parse_skill,
)


def _haunt_body(root: Path, haunt: str = "documents") -> str:
    return (root / f"{haunt}.md").read_text(encoding="utf-8")


def _drawer_lines(body: str) -> list[str]:
    """Whisper lines live at column zero; frontmatter children are indented."""
    return [line for line in body.splitlines() if line.startswith("- ")]


def _drawer_has(body: str, topic: str) -> bool:
    return any(line.startswith(f"- {topic}") for line in _drawer_lines(body))


def test_whisper_files_under_broad_haunt(tmp_path):
    root = tmp_path / "skills"
    result = file_whisper(root, "pdf", "use pdftotext for layout")
    assert result == {"haunt": "documents", "wisp": None, "filed": True, "duplicate": False}
    body = _haunt_body(root)
    assert "- pdf: use pdftotext for layout" in body


def test_whisper_dedupes_identical_lines(tmp_path):
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "use pdftotext")
    result = file_whisper(root, "pdf", "use pdftotext")
    assert result["duplicate"] is True
    assert result["filed"] is False
    assert _haunt_body(root).count("- pdf: use pdftotext") == 1


def test_manifest_at_threshold(tmp_path):
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "use pdftotext for layout")
    file_whisper(root, "pdf", "qpdf merges pages")
    assert load_child(root, "pdf") is None  # not yet: only two whispers
    file_whisper(root, "pdf", "ocrmypdf adds a text layer")
    manifest_check(root)
    wisp_path = root / "documents" / "pdf.md"
    assert wisp_path.is_file()
    wisp = parse_skill(wisp_path.read_text(encoding="utf-8"), wisp_path)
    assert wisp.name == "pdf"
    assert wisp.parent == "documents"  # on-disk frontmatter key stays `parent:`
    assert "## Field notes" in wisp.body
    assert "- use pdftotext for layout" in wisp.body
    assert "- qpdf merges pages" in wisp.body
    assert "- ocrmypdf adds a text layer" in wisp.body


def test_manifest_migrates_whispers_out_of_haunt(tmp_path):
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "note one")
    file_whisper(root, "pdf", "note two")
    file_whisper(root, "pdf", "note three")
    file_whisper(root, "email", "unrelated stays")
    manifest_check(root)
    body = _haunt_body(root)
    assert not _drawer_has(body, "pdf")
    assert "- email: unrelated stays" in body
    haunt = parse_skill(body, root / "documents.md")
    assert "pdf" in haunt.children  # children list updated


def test_manifest_wisp_description_distilled(tmp_path):
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "pdftotext preserves layout best")
    file_whisper(root, "pdf", "second note")
    file_whisper(root, "pdf", "third note")
    wisp = manifest_wisp(root, "documents", "pdf")
    assert wisp is not None
    assert wisp.description == "pdftotext preserves layout best"


def test_manifest_returns_none_without_whispers(tmp_path):
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "only one")
    assert manifest_wisp(root, "documents", "pdf") is None
    assert manifest_wisp(root, "nope", "pdf") is None


def test_threshold_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_DESK_MANIFEST_THRESHOLD", "2")
    assert manifest_threshold() == 2
    root = tmp_path / "skills"
    file_whisper(root, "pdf", "one")
    file_whisper(root, "pdf", "two")
    manifest_check(root)
    assert (root / "documents" / "pdf.md").is_file()


def test_threshold_env_garbage_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_DESK_MANIFEST_THRESHOLD", "nope")
    assert manifest_threshold() == 3


def test_new_whisper_folds_into_existing_wisp(tmp_path):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    manifest_check(root)
    result = file_whisper(root, "pdf", "a fourth whisper")
    assert result["wisp"] == "pdf"
    assert result["filed"] is True
    assert not _drawer_has(_haunt_body(root), "pdf")  # never lands in the drawer
    wisp_body = (root / "documents" / "pdf.md").read_text(encoding="utf-8")
    assert "- a fourth whisper" in wisp_body


def test_fold_dedupes_field_notes(tmp_path):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    manifest_check(root)
    assert file_whisper(root, "pdf", "one")["duplicate"] is True
    wisp_body = (root / "documents" / "pdf.md").read_text(encoding="utf-8")
    assert wisp_body.count("- one") == 1


def test_fold_whispers_into_wisp_moves_drawer_leftovers(tmp_path):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    manifest_check(root)
    # Simulate a drawer leftover (e.g. a partial failure left it behind).
    path = root / "documents.md"
    path.write_text(path.read_text(encoding="utf-8") + "- pdf: leftover note\n", encoding="utf-8")
    assert fold_whispers_into_wisp(root, "pdf") is True
    assert not _drawer_has(_haunt_body(root), "pdf")
    assert "- leftover note" in (root / "documents" / "pdf.md").read_text(encoding="utf-8")
    assert fold_whispers_into_wisp(root, "pdf") is False  # idempotent


def test_manifest_check_is_idempotent(tmp_path):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    manifest_check(root)
    before = sorted(p.read_text(encoding="utf-8") for p in root.rglob("*.md"))
    manifest_check(root)
    after = sorted(p.read_text(encoding="utf-8") for p in root.rglob("*.md"))
    assert before == after


def test_wisp_frontmatter_keys_unchanged_on_disk(tmp_path):
    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    manifest_check(root)
    raw = (root / "documents" / "pdf.md").read_text(encoding="utf-8")
    assert "\nparent: documents\n" in raw
    assert "\nchildren:\n" in raw


def test_unknown_topic_files_under_notes(tmp_path):
    root = tmp_path / "skills"
    result = file_whisper(root, "sourdough", "feed it daily")
    assert result["haunt"] == "notes"
    assert "- sourdough: feed it daily" in (root / "notes.md").read_text(encoding="utf-8")
