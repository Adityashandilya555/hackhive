"""Acceptance tests for #4: read a ResPlan plan into metric layers (slide 4 scale, C2, C4, C19-C24, C27)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping, shape

from hackhive.contracts import Constraints
from hackhive.io import ELIGIBLE, EXCLUDED, get_geometries, read_json, read_layers

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
PLAN_IDS = list(EXPECTED)


def load(plan_id) -> dict:
    return read_json(FIXTURES / "plans" / f"plan-{plan_id}.json")


def synthetic(area=100.0, **geoms) -> dict:
    """A plan whose inner is a 100 × 100-unit square, plus the given geometries."""
    raw = {"id": "SYN", "area": area, "wall_depth": 1.0, "inner": mapping(box(0, 0, 100, 100))}
    raw.update({k: mapping(g) for k, g in geoms.items()})
    return raw


def eligible_area(layers) -> float:
    return sum(p.area for t in ELIGIBLE for p in layers.rooms[t])


def parts(geojson) -> list:
    g = shape(geojson)
    return [p for p in getattr(g, "geoms", [g]) if not p.is_empty]


def test_scale_5924():
    """C19: m_per_unit = sqrt(area / inner.area); eligible area 150.0 m²."""
    raw = load(5924)
    layers = read_layers(raw)
    assert layers.m_per_unit == pytest.approx(math.sqrt(raw["area"] / shape(raw["inner"]).area))
    assert eligible_area(layers) == pytest.approx(150.0, abs=0.1)
    assert layers.wall_depth_m == pytest.approx(raw["wall_depth"] * layers.m_per_unit)


def test_two_living_rooms_576():
    """C2: each living polygon is its own room (no ResPlan living union)."""
    assert len(read_layers(load(576)).rooms["living"]) == 2


def test_bowtie_repaired():
    """C20: a self-intersecting room comes back as valid, non-empty polygons."""
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not bowtie.is_valid
    layers = read_layers(synthetic(bedroom=bowtie))
    out = [p for t in ELIGIBLE + EXCLUDED for p in layers.rooms[t]] + [layers.inner]
    assert len(layers.rooms["bedroom"]) == 2
    assert all(p.is_valid and not p.is_empty for p in out)
    assert eligible_area(layers) == pytest.approx(50 * layers.m_per_unit**2)


def test_doors_repaired_per_part_1293():
    """C2/C20: 1293's door MultiPolygon is invalid as a whole; repairing per part keeps all 10."""
    raw = load(1293)
    assert not shape(raw["door"]).is_valid
    doors = read_layers(raw).door
    assert len(doors) == 10
    assert all(d.is_valid and not d.is_empty for d in doors)


def test_infeasible_room_over_100():
    """C27: an eligible room scaled to 120 m² makes the plan infeasible; 100 m² doesn't."""
    square = box(0, 0, 100, 100)
    big = read_layers(synthetic(area=120.0, bedroom=square))
    assert big.rooms["bedroom"][0].area == pytest.approx(120)
    assert "room > 100 m²" in big.infeasible_reasons
    assert big.infeasible_reasons.count("room > 100 m²") == 1
    assert read_layers(synthetic(area=100.0, bedroom=square)).infeasible_reasons == []


def test_nested_collection():
    """C2: a MultiPolygon nested in a GeometryCollection comes back as its polygons."""
    t1, t2 = Polygon([(0, 0), (1, 0), (0, 1)]), Polygon([(2, 2), (3, 2), (2, 3)])
    got = get_geometries(GeometryCollection([MultiPolygon([t1, t2])]))
    assert len(got) == 2
    assert all(isinstance(g, Polygon) for g in got)


def test_bathroom_excluded():
    """C4: a bathroom is read into rooms["bathroom"] and is not eligible."""
    layers = read_layers(synthetic(bathroom=box(0, 0, 10, 10)))
    assert len(layers.rooms["bathroom"]) == 1
    assert "bathroom" not in ELIGIBLE and "bathroom" in EXCLUDED
    assert eligible_area(layers) == 0


def _out_layers(layers) -> dict[str, list]:
    out = {"inner": [layers.inner], "wall": layers.wall, "door": layers.door}
    out |= {"window": layers.window, "front_door": layers.front_door, **layers.rooms}
    return out


def test_frame_5924():
    """C24: default output = input × m (= input ÷ scale); flip_y gives y' = (y_max − y) · m."""
    raw = load(5924)
    y_max = shape(raw["inner"]).bounds[3]
    for c, fy in ((Constraints(), lambda y: y), (Constraints(flip_y=True), lambda y: y_max - y)):
        layers = read_layers(raw, c)
        m = layers.m_per_unit
        for key, polys in _out_layers(layers).items():
            src = parts(raw[key]) if key in raw else []
            assert len(polys) == len(src), key
            for got, want in zip(polys, src, strict=True):
                g, w = shapely.get_coordinates(got), shapely.get_coordinates(want)
                assert g.shape == w.shape, key
                assert np.allclose(g[:, 0], w[:, 0] * m, rtol=0, atol=1e-9), key
                assert np.allclose(g[:, 1], fy(w[:, 1]) * m, rtol=0, atol=1e-9), key


def test_origin_shift_is_metres():
    """origin_shift is subtracted after scaling, in metres (#38 owns the organiser frame check)."""
    raw = load(5924)
    base = read_layers(raw)
    shifted = read_layers(raw, Constraints(origin_shift=(1.0, 2.0)))
    for a, b in zip(_out_layers(base).values(), _out_layers(shifted).values(), strict=True):
        for p, q in zip(a, b, strict=True):
            g = shapely.get_coordinates(q)
            assert np.allclose(g, shapely.get_coordinates(p) - (1.0, 2.0), rtol=0, atol=1e-9)


class _NoNetArea(dict):
    """A plan that fails loudly if anything reads net_area (slide 4)."""

    def __getitem__(self, key):
        assert key != "net_area", "net_area was read"
        return super().__getitem__(key)

    def get(self, key, default=None):
        assert key != "net_area", "net_area was read"
        return super().get(key, default)


def test_net_area_never_read():
    """C19, slide 4: the scale uses only area and inner; net_area is never read."""
    raw = load(5924)
    assert "net_area" in raw
    trapped = read_layers(_NoNetArea(raw))
    without = {k: v for k, v in raw.items() if k != "net_area"}
    assert read_layers(without).m_per_unit == trapped.m_per_unit == read_layers(raw).m_per_unit


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_fixture_eligible_area_and_count(plan_id):
    """Every fixture's eligible area and polygon count match the measured profile."""
    layers = read_layers(load(plan_id))
    assert eligible_area(layers) == pytest.approx(EXPECTED[plan_id]["elig_m2"], abs=1e-6)
    assert sum(len(layers.rooms[t]) for t in ELIGIBLE) == EXPECTED[plan_id]["n_nodes"]
    assert layers.unknown_keys == []  # every geometry key was read; "neighbor" is not geometry


@pytest.mark.parametrize("plan_id", PLAN_IDS)
def test_fixture_every_output_polygon_valid(plan_id):
    """C20: valid after scaling too (rounding can make a valid input self-intersect: 1293, 13344)."""
    layers = read_layers(load(plan_id))
    for key, polys in _out_layers(layers).items():
        for i, p in enumerate(polys):
            assert p.is_valid and not p.is_empty, f"{key}[{i}]"


def test_missing_keys_are_empty_layers():
    layers = read_layers(load(5924))
    assert set(layers.rooms) == set(ELIGIBLE + EXCLUDED)
    assert layers.rooms["storage"] == [] and layers.rooms["stair"] == []


def test_unknown_geometry_keys_reported():
    """C1 / slide 3 rule 1: a room under a key we don't know is reported, not silently dropped."""
    raw = synthetic(living_room=box(0, 0, 10, 10))
    raw["neighbor"] = [1, 2]  # not geometry: ignored
    layers = read_layers(raw)
    assert layers.unknown_keys == ["living_room"]
    assert layers.rooms["living"] == []


def test_caller_dict_not_mutated():
    raw = synthetic(balacony=box(0, 0, 10, 10))  # ResPlan's typo key
    before = dict(raw)
    layers = read_layers(raw)
    assert raw == before
    assert len(layers.rooms["balcony"]) == 1


@pytest.mark.parametrize("area", [0, -5, math.nan, math.inf, None])
def test_bad_area_raises(area):
    raw = synthetic(area=1.0)
    if area is None:
        del raw["area"]
    else:
        raw["area"] = area
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)


@pytest.mark.parametrize("wall_depth", [0, -1, math.nan, None])
def test_bad_wall_depth_raises(wall_depth):
    """The organiser format may lack wall_depth (#46 Q1): fail loudly, naming the plan."""
    raw = synthetic()
    if wall_depth is None:
        del raw["wall_depth"]
    else:
        raw["wall_depth"] = wall_depth
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)


def test_empty_inner_raises():
    raw = synthetic()
    raw["inner"] = {"type": "MultiPolygon", "coordinates": []}
    with pytest.raises(ValueError, match="SYN"):
        read_layers(raw)
