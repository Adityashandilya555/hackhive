"""Acceptance tests for #5: the plan base layer (walls, doors, windows, rooms, "no node")."""

from pathlib import Path

import matplotlib
import pytest
from matplotlib.colors import to_hex
from shapely.geometry import box

from hackhive.io import load_plan, read_json, read_layers
from hackhive.viz import EXCLUDED_FILL, draw_layers, main, to_png

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (after the backend is chosen)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plans"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def plan_5924():
    return load_plan(FIXTURES / "plan-5924.json")


def _room_patches(ax) -> dict[str, object]:
    return {p.get_gid()[5:]: p for p in ax.patches if (p.get_gid() or "").startswith("room:")}


def _texts(ax) -> list[str]:
    return [t.get_text() for t in ax.texts]


def test_5924_has_a_patch_per_room_and_png_bytes(plan_5924):
    fig = draw_layers(plan_5924)
    ax = fig.axes[0]
    drawn = [r for r in plan_5924.rooms if r.type != "land"]
    assert set(_room_patches(ax)) == {r.id for r in drawn}
    assert len(ax.patches) >= len(drawn)
    png = to_png(fig)
    assert png[:8] == PNG_SIGNATURE


def test_bathroom_is_grey_and_labelled_no_node(plan_5924):
    ax = draw_layers(plan_5924).axes[0]
    patches = _room_patches(ax)
    baths = [r for r in plan_5924.rooms if r.type == "bathroom"]
    assert baths
    for r in baths:
        assert to_hex(patches[r.id].get_facecolor()) == EXCLUDED_FILL
    texts = _texts(ax)
    assert sum("no node" in t for t in texts) >= len(baths)


def test_eligible_rooms_are_not_grey(plan_5924):
    patches = _room_patches(draw_layers(plan_5924).axes[0])
    for r in plan_5924.rooms:
        if r.eligible:
            assert to_hex(patches[r.id].get_facecolor()) != EXCLUDED_FILL


def test_empty_layers_raise():
    with pytest.raises(ValueError):
        draw_layers({})
    with pytest.raises(ValueError):
        draw_layers({"wall": [], "bedroom": []})


def test_y_up_equal_aspect_axes_off_and_every_eligible_room_shows_its_id(plan_5924):
    ax = draw_layers(plan_5924).axes[0]
    assert not ax.yaxis_inverted() and not ax.xaxis_inverted()
    assert ax.get_aspect() == 1.0
    assert not ax.axison
    texts = _texts(ax)
    for r in plan_5924.rooms:
        if r.eligible:
            assert any(t.startswith(r.id) for t in texts), r.id


def test_y_up_puts_a_higher_room_higher_on_the_image():
    """Origin lower-left, y up: the room with larger y is drawn above the other."""
    layers = {"bedroom": [box(0, 0, 4, 3), box(0, 5, 4, 8)], "wall": [box(-0.2, 3, 4.2, 5)]}
    fig = draw_layers(layers)
    ax = fig.axes[0]
    low, high = (ax.transData.transform((2, y))[1] for y in (1.5, 6.5))
    assert high > low  # display y grows upwards in matplotlib


def test_works_in_drawing_units_from_raw_layers():
    """Any units: the #4 metric `Layers` and a plain dict of geometries both draw."""
    raw = read_json(FIXTURES / "plan-2814.json")
    ax = draw_layers(read_layers(raw)).axes[0]
    assert "kitchen_0" in "".join(_texts(ax))
    plain = {"bedroom": [box(0, 0, 400, 300)], "bathroom": [box(400, 0, 600, 300)]}
    ax = draw_layers(plain).axes[0]
    assert any(t.startswith("bedroom_0") for t in _texts(ax))
    assert any("no node" in t for t in _texts(ax))


def test_draws_into_a_given_axes(plan_5924):
    from matplotlib.figure import Figure

    fig = Figure()
    ax = fig.add_subplot()
    assert draw_layers(plan_5924, ax=ax) is fig
    assert _room_patches(ax)


def test_no_pyplot_state_is_left_behind(plan_5924):
    """The app (#7) renders in threads, so the drawing must not touch pyplot's global figures."""
    before = plt.get_fignums()
    to_png(draw_layers(plan_5924))
    assert plt.get_fignums() == before


def test_cli_writes_the_png(tmp_path):
    out = tmp_path / "nested" / "5924-base.png"
    assert main([str(FIXTURES / "plan-5924.json"), str(out)]) == 0
    assert out.read_bytes()[:8] == PNG_SIGNATURE
