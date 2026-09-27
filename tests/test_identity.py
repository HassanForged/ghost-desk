"""Identity: the ghost is the product; the brain is an interchangeable part."""

from ghost_desk.tui import header_status


def test_header_idle_says_haunting_not_the_model():
    status = header_status(busy=False)
    assert status == "haunting"
    assert "grok" not in status.lower()
    assert "idle" not in status


def test_header_busy_keeps_the_timer_but_not_the_model():
    status = header_status(busy=True, phase="thinking", elapsed=4)
    assert status == "rattling chains…  4s"
    assert "grok-4.6" not in status
    assert "gpt" not in status


def test_header_busy_is_spooky_but_lowkey():
    status = header_status(busy=True, elapsed=12)
    assert "rattling chains" in status
    assert "haunting" not in status  # idle word stays for idle


def test_header_never_takes_a_model_name():
    import inspect

    assert "model" not in inspect.signature(header_status).parameters


def test_harness_is_gone_from_user_facing_strings():
    from ghost_desk import boot
    from ghost_desk.agent import _BASE
    from ghost_desk.context import _STARTER_SOUL
    from ghost_desk.tui import banner

    blob = "\n".join(
        [
            _BASE,
            _STARTER_SOUL,
            "\n".join(boot._menu()),
            "\n".join(boot._checks()),
            banner().renderable.plain,
            boot.TAGLINE,
        ]
    )
    assert "harness" not in blob.lower()
    assert "hermes" not in blob.lower()
    assert "openclaw" not in blob.lower()


def test_system_prompt_gives_the_ghost_a_voice():
    from ghost_desk.agent import _BASE

    assert "ghost in ghost desk" in _BASE
    assert "writes" in _BASE and "yes" in _BASE
    assert 'Never say "as an AI language model"' in _BASE


def test_tagline_is_the_new_one():
    from ghost_desk.boot import TAGLINE

    assert TAGLINE == "one ghost, your machine, your notes."


def test_little_ghost_routes_default_to_the_bound_brain():
    import os
    from ghost_desk.config import Config
    from ghost_desk.subagents import route

    cfg = Config(provider="xai-oauth", model="grok-4.6", data_dir="/tmp", working_directory="/tmp")
    for kind in ("design", "code", "research"):
        for var in (f"GHOST_{kind.upper()}_API_KEY",):
            os.environ.pop(var, None)
        routed = route(kind, cfg)
        assert routed is cfg or routed.model == "grok-4.6"


_SPOOKY_BLOCKLIST = (
    "haunt", "chain", "fade", "spook", "boo", "eerie", "phantom",
    "crypt", "tomb", "witch", "cackle", "scare",
)


def _permission_prompt_templates():
    from ghost_desk import permissions
    import inspect

    src = inspect.getsource(permissions)
    prompts = []
    for line in src.splitlines():
        if "self.ask(f\"" in line or 'self.ask("' in line:
            prompts.append(line)
    return prompts


def test_permission_prompts_stay_crystal_clear():
    prompts = _permission_prompt_templates()
    assert prompts, "expected to find permission prompt strings"
    for prompt in prompts:
        lowered = prompt.lower()
        for word in _SPOOKY_BLOCKLIST:
            assert word not in lowered, f"spooky word {word!r} in permission prompt: {prompt.strip()}"


def test_soul_has_the_haunting_layer_and_the_core_rules():
    from ghost_desk.context import _STARTER_SOUL

    lowered = _STARTER_SOUL.lower()
    assert "dry first, haunted second" in lowered
    assert "never campy" in lowered
    # core behavioral rules survive the rewrite
    assert "reads are free" in lowered
    assert "writes wait for a yes" in lowered
    assert "never be a generic assistant" in lowered


def test_base_prompt_has_the_haunting_layer_and_the_core_rules():
    from ghost_desk.agent import _BASE

    lowered = _BASE.lower()
    assert "dry first, haunted second" in lowered
    assert "reads are free" in lowered
    assert "show the plan first" in lowered
    assert "do not invent" in lowered


def test_quit_fades_the_ghost():
    from ghost_desk.tui import _slash

    printed = []

    class FakeConsole:
        def print(self, *args, **kwargs):
            printed.append(" ".join(str(a) for a in args))

    assert (
        _slash("/quit", console=FakeConsole(), config=None, memory=None, session=None, skills_root=None)
        == "quit"
    )
    assert any("the ghost fades" in p for p in printed)


def test_new_is_a_fresh_haunting():
    import inspect
    from ghost_desk.tui import _slash

    assert "a fresh haunting." in inspect.getsource(_slash)


def test_error_notes_name_the_dark():
    import inspect
    from ghost_desk import tui

    assert "something moved in the dark: " in inspect.getsource(tui)
