"""The seance: janitorial pass + hash-tracked synthesis."""

from pathlib import Path
from types import SimpleNamespace

from ghost_desk.seance import llm_synthesizer, seance
from ghost_desk.skills import load_all, parse_skill


def _write(root: Path, rel: str, name: str, parent: str = "", children=(), body: str = "body") -> Path:
    from ghost_desk.skills import render_skill, Skill

    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    skill = Skill(name=name, description=f"{name} haunt", parent=parent,
                  children=list(children), body=body)
    path.write_text(render_skill(skill), encoding="utf-8")
    return path


def test_seance_merges_duplicate_wisps(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="haunt body")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="first notes")
    _write(root, "documents/dup.md", "pdf", parent="documents", body="second notes")
    stats = seance(root)
    assert stats["removed"] == 1
    assert not (root / "documents" / "dup.md").exists()
    wisp = parse_skill((root / "documents" / "pdf.md").read_text(encoding="utf-8"))
    assert "first notes" in wisp.body and "second notes" in wisp.body


def test_seance_prunes_orphans_and_empties(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="haunt body")
    _write(root, "ghost.md", "ghost", parent="nope", body="orphan wisp")
    _write(root, "empty.md", "empty", body="   ")
    stats = seance(root)
    assert stats["removed"] == 2
    assert not (root / "ghost.md").exists()
    assert not (root / "empty.md").exists()


def test_seance_folds_stray_drawer_lines_into_wisps(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="- pdf: stray line")
    _write(root, "documents/pdf.md", "pdf", parent="documents",
           body="## Field notes\n- a\n")
    seance(root)
    assert "- pdf" not in (root / "documents.md").read_text(encoding="utf-8").split("---")[-1]
    wisp_body = (root / "documents" / "pdf.md").read_text(encoding="utf-8")
    assert "- stray line" in wisp_body


def test_seance_keeps_haunt_with_empty_body_but_live_wisps(tmp_path):
    # Manifesting can drain a haunt's body; the haunt must survive its wisps.
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="   ")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="## Field notes\n- a\n")
    stats = seance(root)
    assert (root / "documents.md").exists()
    assert (root / "documents" / "pdf.md").exists()
    assert stats["removed"] == 0


def test_seance_relits_children_from_disk(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", children=("stale",), body="haunt body")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="notes")
    seance(root)
    haunt = parse_skill((root / "documents.md").read_text(encoding="utf-8"))
    assert haunt.children == ["pdf"]


def test_synthesis_only_for_changed_wisps(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="old body")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="notes v1")
    calls = []

    def synth(haunt, wisps):
        calls.append(haunt.name)
        return "distilled body"

    stats = seance(root, synthesizer=synth)
    assert stats["synthesized"] == 1
    assert calls == ["documents"]
    assert "distilled body" in (root / "documents.md").read_text(encoding="utf-8")

    # Second seance: wisps unchanged -> the brain is never asked again.
    stats = seance(root, synthesizer=synth)
    assert stats["synthesized"] == 0
    assert calls == ["documents"]

    # Wisps change -> synthesis runs again.
    wisp_path = root / "documents" / "pdf.md"
    wisp_path.write_text(wisp_path.read_text(encoding="utf-8") + "- more\n", encoding="utf-8")
    stats = seance(root, synthesizer=synth)
    assert stats["synthesized"] == 1
    assert calls == ["documents", "documents"]


def test_synthesis_skipped_without_synthesizer(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="old body")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="notes")
    stats = seance(root)
    assert stats["synthesized"] == 0
    assert "old body" in (root / "documents.md").read_text(encoding="utf-8")


def test_synthesis_none_result_is_silent(tmp_path):
    root = tmp_path / "skills"
    _write(root, "documents.md", "documents", body="old body")
    _write(root, "documents/pdf.md", "pdf", parent="documents", body="notes")
    stats = seance(root, synthesizer=lambda haunt, wisps: None)
    assert stats["synthesized"] == 0
    assert "old body" in (root / "documents.md").read_text(encoding="utf-8")


def test_llm_synthesizer_none_without_provider():
    assert llm_synthesizer(SimpleNamespace(provider="", model="")) is None
    assert llm_synthesizer(SimpleNamespace(provider="   ", model="x")) is None


def test_llm_synthesizer_silent_on_client_failure(monkeypatch):
    import ghost_desk.providers as providers

    def boom(_config):
        raise RuntimeError("no network")

    # build_client is imported inside synthesize(); patch the provider module.
    monkeypatch.setattr(providers, "build_client", boom)
    synth = llm_synthesizer(SimpleNamespace(provider="openai", model="gpt-4o-mini", api_key="k"))
    haunt = SimpleNamespace(name="documents", body="b")
    wisp = SimpleNamespace(name="pdf", description="d", body="notes")
    assert synth(haunt, [wisp]) is None


def test_session_end_pipeline_reports_from_disk(tmp_path):
    from ghost_desk.ceremony import session_end, should_celebrate
    from ghost_desk.skills import file_whisper

    root = tmp_path / "skills"
    for note in ("one", "two", "three"):
        file_whisper(root, "pdf", note)
    report = session_end(root, synthesizer=lambda haunt, wisps: "distilled from wisps")
    assert (root / "documents" / "pdf.md").is_file()
    assert report.wisps_manifested == ["pdf"]
    assert report.haunts_rewritten == ["documents"]
    assert "distilled from wisps" in (root / "documents.md").read_text(encoding="utf-8")
    assert should_celebrate(report)

    # Second session end: nothing changed -> silence.
    report2 = session_end(root, synthesizer=lambda haunt, wisps: "distilled from wisps")
    assert not should_celebrate(report2)


def test_load_all_still_skips_malformed(tmp_path):
    root = tmp_path / "skills"
    (root / "bad.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "bad.md").write_text("no frontmatter here", encoding="utf-8")
    _write(root, "documents.md", "documents", body="fine")
    assert [s.name for s in load_all(root)] == ["documents"]
