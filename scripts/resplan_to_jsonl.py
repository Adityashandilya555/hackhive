#!/usr/bin/env python3
"""Decode ResPlan.pkl into JSONL: one plan per line, geometries as GeoJSON. No arbitrary pickle code runs.

usage: uv run --no-project --with shapely --with numpy python -I scripts/resplan_to_jsonl.py \
           data/resplan/ResPlan.pkl data/resplan/plans.jsonl

`pickletools` shows the file only references the three globals in ALLOWED (checked 2026-10-15).
The unpickler refuses everything else, so a tampered pickle aborts instead of executing.
"""

import json
import pickle
import sys

import numpy as np
import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

ALLOWED = {("shapely.io", "from_wkb"), ("numpy._core.multiarray", "scalar"), ("numpy", "dtype")}


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED:
            raise pickle.UnpicklingError(f"blocked global {module}.{name}")
        return super().find_class(module, name)


def to_json(v):
    if isinstance(v, BaseGeometry):
        return mapping(v)
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, dict):
        return {k: to_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_json(x) for x in v]
    return v


def main(src, dst):
    with open(src, "rb") as f:
        plans = SafeUnpickler(f).load()
    with open(dst, "w") as out:
        for i, plan in enumerate(plans):
            out.write(json.dumps({"index": i, **to_json(plan)}) + "\n")

    # self-check: line count, and every 500th plan's geometries round-trip exactly
    with open(dst) as f:
        lines = f.readlines()
    assert len(lines) == len(plans), (len(lines), len(plans))
    for i in range(0, len(plans), 500):
        back = json.loads(lines[i])
        for k, v in plans[i].items():
            if isinstance(v, BaseGeometry):
                assert shapely.equals_exact(shape(back[k]), v, 0), (i, k)
    print(f"{len(plans)} plans -> {dst}; keys of plan 0: {sorted(plans[0])}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
