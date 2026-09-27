"""Identity: the ghost is the product; the brain is an interchangeable part."""

from ghost_desk.tui import header_status


def test_header_idle_says_haunting_not_the_model():
    status = header_status(busy=False)
    assert status == "haunting"
    assert "grok" not in status.lower()
    assert "idle" not in status


def test_header_busy_keeps_the_timer_but_not_the_model():
    status = header_status(busy=True, phase="thinking", elapsed=4)
    assert status == "haunting  ·  thinking 4s"
    assert "grok-4.6" not in status
    assert "gpt" not in status


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
