"""What-ifs (#30): settings that leave the organisers' problem statement, named in words.

A what-if design is shown (app, sidecar, PNG) but never written as a submission (C29, C43,
decision_conventions principle 4): `solve.Result.submittable` is false for any of them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hackhive.contracts import Constraints

WHAT_IF = "WHAT-IF"
_PRESET = Constraints.problem_statement()


def _m2(v: Any) -> str:
    return f"{v:g} m²"


def _m(v: Any) -> str:
    return f"{v:g} m"


# field → how a change reads; a toggle (bool) reads as its text alone, a value with the preset's
_NAMES: dict[str, tuple[str, Callable[[Any], str] | None]] = {
    "max_area_m2": ("area cap", _m2),
    "hub_wall_clearance_m": ("hub to wall ≥", _m),
    "zone_links_via_excluded": ("zones linked through a bathroom or stair", None),
    "route_through_other_rooms": ("cable through bathrooms or other hubs' rooms", None),
    "interior_windows_as_doors": ("windows on interior walls as doors", None),
}


def what_if_changes(c: Constraints) -> list[str]:
    """Every setting `c` changes from the problem statement, in words, in field order. Empty for
    the preset. A setting without a name here still shows, by its field name."""
    out = []
    for name in Constraints.model_fields:
        value, preset = getattr(c, name), getattr(_PRESET, name)
        if value == preset:
            continue
        label, unit = _NAMES.get(name, (None, None))
        if label is None:
            out.append(f"{name} = {value} (problem statement: {preset})")
        elif unit is None:
            out.append(label if value else f"not {label}")
        else:
            out.append(f"{label} {unit(value)} (problem statement: {unit(preset)})")
    return out


def what_if_note(c: Constraints) -> str | None:
    """The sidecar warning for a what-if run, or None for the problem statement."""
    changes = what_if_changes(c)
    if not changes:
        return None
    return f"{WHAT_IF}: {'; '.join(changes)}. Never written as a submission (C29)."
