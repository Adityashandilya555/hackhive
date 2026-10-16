"""Acceptance tests for #44's pre-#22 part: the final-presentation image of today's result."""

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from hackhive import checker
from hackhive.cli import main
from hackhive.contracts import Constraints
from hackhive.solve import file_stem, solve_file
from hackhive.ui import hub_metrics
from hackhive.viz import draw_presentation, export_presentation, presentation_png

ROOT = Path(__file__).resolve().parents[1]
PLANS = ROOT / "tests" / "fixtures" / "plans"


@pytest.fixture(scope="module")
def r5924():
    return solve_file(PLANS / "plan-5924.json")


@pytest.fixture(scope="module")
def r576():
    return solve_file(PLANS / "plan-576.json")


def _texts(fig, gid: str) -> str:
    return "\n".join(t.get_text() for ax in fig.axes for t in ax.texts if t.get_gid() == gid)


def _png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def test_5924_writes_png_and_svg_at_16_9(r5924, tmp_path):
    written = export_presentation(r5924.plan, r5924, tmp_path)
    stem = file_stem(r5924.sidecar.plan_id)
    png, svg = tmp_path / f"{stem}.presentation.png", tmp_path / f"{stem}.presentation.svg"
    assert written == [png, svg]
    w, h = _png_size(png)
    assert w / h == pytest.approx(16 / 9, rel=0.01)
    root = ET.fromstring(svg.read_bytes())
    _, _, vw, vh = (float(v) for v in root.get("viewBox").split())
    assert vw / vh == pytest.approx(16 / 9, rel=0.01)


def test_png_bytes_are_the_exported_file(r5924, tmp_path):
    png, _ = export_presentation(r5924.plan, r5924, tmp_path)
    assert presentation_png(r5924.plan, r5924) == png.read_bytes()  # the app's download (#44)


def test_5924_summary_and_j_match_the_checker(r5924):
    sub, c = r5924.submission, r5924.sidecar.settings
    m = checker.metrics(sub.hubs, c)
    fig = draw_presentation(r5924.plan, r5924)
    j = _texts(fig, "presentation:J")
    for part in (f"L {m.L:.3f} m", f"X {m.X}", f"O {m.O:.3f} m", f"B {m.B}", f"J {m.J:.3f}"):
        assert part in j
    rows = hub_metrics(r5924.plan, sub, m)
    gap = checker.area_gap(r.area_m2 for r in rows)
    assert f"{gap:.1f} m²" in _texts(fig, "presentation:gap")
    table = [ax.tables[0] for ax in fig.axes if ax.tables][0]
    cells = {(i, j): c.get_text().get_text() for (i, j), c in table.get_celld().items()}
    for i, (row, hub) in enumerate(zip(rows, sub.hubs, strict=True), start=1):
        assert cells[(i, 0)] == hub.hub_id
        assert cells[(i, 2)] == f"{row.area_m2:.1f}"
        assert cells[(i, 3)] == str(row.n_nodes)
        assert cells[(i, 4)] == f"{row.L:.2f}"
        assert cells[(i, 5)] == str(row.B)
    # the left half is #17's overlay of the submitted rings
    assert any((a.get_gid() or "").startswith("zone:") for a in fig.axes[0].patches)


def test_576_slide_says_six_hubs_proven_minimal_against_two(r576):
    badge = _texts(draw_presentation(r576.plan, r576), "presentation:badge")
    assert "6 hubs" in badge
    assert "proven minimal against a lower bound of 2" in badge


def test_five_room_hub_fits_its_cells():
    # a one-hub what-if (cap 250 m²) puts all five 5924 rooms on one row (#107 review)
    r = solve_file(PLANS / "plan-5924.json", Constraints(max_area_m2=250))
    assert len(set(r.submission.hubs[0].assigned_rooms)) == 5
    fig = draw_presentation(r.plan, r)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    table = [ax.tables[0] for ax in fig.axes if ax.tables][0]
    cells = table.get_celld()
    for col in range(6):
        cell = cells[(1, col)]
        box, text = cell.get_window_extent(renderer), cell.get_text().get_window_extent(renderer)
        assert box.x0 <= text.x0 and text.x1 <= box.x1, cell.get_text().get_text()
    assert cells[(0, 0)].get_height() < 0.1  # a one-hub table keeps normal row heights


def test_no_design_writes_nothing(r5924, tmp_path):
    from dataclasses import replace

    assert export_presentation(r5924.plan, replace(r5924, submission=None), tmp_path) == []
    assert not list(tmp_path.iterdir())


def test_cli_present(tmp_path, capsys):
    assert main(["present", str(PLANS / "plan-5924.json"), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "PLAN_5924.presentation.png").is_file()
    assert (tmp_path / "PLAN_5924.presentation.svg").is_file()
    assert "wrote" in capsys.readouterr().out
    assert main(["present", str(tmp_path / "missing.json"), "--out", str(tmp_path)]) == 3
