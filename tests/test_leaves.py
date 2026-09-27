"""Leaves fall slowly, sparsely, and clean up after themselves."""

from ghost_desk.leaves import LEAF_COLORS, LeafField, leaves_enabled


def test_no_leaves_before_first_spawn():
    field = LeafField()
    assert field.tick(100.0, 30, 38) is False
    assert field.leaves == []


def test_leaf_spawns_after_delay():
    field = LeafField()
    field.tick(100.0, 30, 38)
    assert field.tick(103.0, 30, 38) is True
    assert len(field.leaves) == 1


def test_leaves_fall_down():
    field = LeafField()
    field.tick(0.0, 30, 38)
    field.tick(3.0, 30, 38)
    y0 = field.leaves[0].y
    field.tick(4.0, 30, 38)
    assert field.leaves[0].y > y0


def test_leaves_expire_at_the_bottom():
    field = LeafField()
    field.tick(0.0, 30, 10)
    field.tick(3.0, 30, 10)
    assert len(field.leaves) == 1
    field._next_spawn = 1e9  # no more spawns; watch this one fall out
    for step in range(4, 80):
        field.tick(float(step), 30, 10)
    assert field.leaves == []


def test_leaves_stay_capped():
    field = LeafField(max_leaves=2)
    for step in range(0, 240):
        field.tick(step * 0.5, 30, 38)
    assert len(field.leaves) <= 2


def test_default_cap_is_five():
    field = LeafField()
    for step in range(0, 400):
        field.tick(step * 0.5, 30, 38)
    assert len(field.leaves) <= 5


def test_cells_stay_in_bounds_with_fall_colors():
    field = LeafField()
    for step in range(0, 400):
        field.tick(step * 0.5, 30, 38)
        for x, y, color, char in field.cells():
            assert 0 <= x < 30
            assert 0 <= y < 38
            assert color in LEAF_COLORS
            assert char


def test_leaves_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("GHOST_DESK_LEAVES", "off")
    assert leaves_enabled() is False
    monkeypatch.delenv("GHOST_DESK_LEAVES")
    assert leaves_enabled() is True
