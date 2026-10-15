"""Plain helpers behind the Streamlit app (#7): where plans come from and what the locked preset
says. Nothing here imports streamlit, so all of it is unit-tested without running the app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hackhive.contracts import Constraints, CostWeights, Plan, lower_bound
from hackhive.io import load_plan

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "plans"
RAW_DIR = ROOT / "tests" / "fixtures" / "raw"
RESPLAN_JSONL = ROOT / "data" / "resplan" / "plans.jsonl"  # optional, never committed
DEFAULT_FIXTURE = "5924"


# --------------------------------------------------------------------------- plan sources


def fixture_ids() -> list[str]:
    """The test plans' ids, in numeric order."""
    ids = [p.stem.removeprefix("plan-") for p in FIXTURE_DIR.glob("plan-*.json")]
    return sorted(ids, key=int)


def fixture_label(plan_id: str) -> str:
    """`5924 · gap`: the id plus why the plan is a fixture (from its vault file name)."""
    for p in RAW_DIR.glob(f"plan-{plan_id}-*.json"):
        return f"{plan_id} · {p.stem.removeprefix(f'plan-{plan_id}-').replace('-', ' ')}"
    return plan_id


def fixture_path(plan_id: str) -> Path:
    return FIXTURE_DIR / f"plan-{plan_id}.json"


def _unwrap(raw: Any) -> dict:
    """A plan object; a research-vault file (`label`, `metrics`, `plan`) is unwrapped."""
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    if isinstance(raw.get("plan"), dict) and "wall" not in raw:
        return raw["plan"]
    return raw


def plan_from_bytes(content: bytes) -> Plan:
    """An uploaded plan JSON (a ResPlan plan or a vault file) as a metric `Plan`.
    Anything unreadable raises ValueError with a message fit to show the user."""
    try:
        raw = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a JSON file: {e}") from e
    raw = _unwrap(raw)
    try:
        return load_plan(raw)
    except ValueError:
        raise
    except Exception as e:  # malformed geometry surfaces as many exception types
        raise ValueError(f"not a plan this app can read: {type(e).__name__}: {e}") from e


def resplan_raw(path: Path, plan_id: str | int) -> dict:
    """One plan from a ResPlan JSON-lines file, by its `id`. Raises ValueError if absent."""
    want = str(plan_id).strip()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = _unwrap(json.loads(line))
            if str(raw.get("id")) == want:
                return raw
    raise ValueError(f"plan {want} is not in {path.name}")


# --------------------------------------------------------------------------- the locked preset


def _coef(w: float, symbol: str) -> str:
    return symbol if w == 1 else f"{w:g}{symbol}"


def cost_formula(w: CostWeights) -> str:
    """The cost formula as slide 6 prints it, written from the weights so it can't drift."""
    terms = [
        _coef(w.length, "L"),
        _coef(w.cross, "X"),
        _coef(w.overlap, "O"),
        _coef(w.bend, "B"),
    ]
    return "J = " + " + ".join(terms)


def preset_rows(c: Constraints) -> list[tuple[str, str]]:
    """The rules as (what, value) rows, for the read-only sidebar panel."""
    return [
        ("Hub area cap", f"{c.max_area_m2:g} m²"),
        ("Nodes per hub", f"{c.max_nodes} nodes"),
        ("Cost", cost_formula(c.cost_weights)),
        ("Hub to wall", f"≥ {c.hub_wall_clearance_m:g} m"),
        ("Tolerance", f"{c.tolerance_m * 1000:g} mm"),
        ("Ranking", "fewest hubs, then lowest J, then area gap"),
    ]


def plan_summary(plan: Plan, c: Constraints) -> str:
    """One line under the title: rooms, eligible area, nodes and the hub lower bound."""
    eligible = [r for r in plan.rooms if r.eligible]
    area = sum(r.area_m2 for r in eligible)
    lb = lower_bound(area, len(plan.nodes), c)
    rooms = sum(r.type != "land" for r in plan.rooms)
    return (
        f"{rooms} rooms ({len(eligible)} eligible) · {area:.1f} m² eligible · "
        f"{len(plan.nodes)} nodes · at least {lb} hub{'s' if lb != 1 else ''}"
    )
