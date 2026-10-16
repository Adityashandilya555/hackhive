"""The `hackhive` command (#16): `solve` a plan through the submit gate, `check` a submission.
`batch` arrives with #32."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from hackhive.checker import metrics_text, report
from hackhive.contracts import Constraints, Submission, Violation
from hackhive.io import load_plan

# Which open organiser question each strict reading waits on (decision_conventions §6):
# rule 4's same-line retrace is #46 Q5, rule 6's distance to a door or open-passage edge #46 Q3.
STRICT_QUESTION = {4: "Q5", 6: "Q3"}
_QUESTION = re.compile(r"#46 (Q\d+)")


def strict_line(v: Violation) -> str:
    """`STRICT (rule n, #46 Qm): H1: message`: a strict reading the organisers haven't settled."""
    found = _QUESTION.search(v.message)
    q = found.group(1) if found else STRICT_QUESTION.get(v.rule, "?")
    return f"STRICT (rule {v.rule}, #46 {q}): {v.hub_id or 'plan'}: {v.message}"


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):  # "✓", "✗", "²" and "·" would break cp1252 on Windows
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _solve(args: argparse.Namespace) -> int:
    from hackhive.solve import solve_file, submit
    from hackhive.viz import draw_layers, draw_nodes, draw_solution, to_png

    if not args.plan.is_file():
        print(f"no such plan file: {args.plan}", file=sys.stderr)
        return 3
    c = Constraints()
    result = solve_file(args.plan, c)
    written = submit(result, args.out)
    side = result.sidecar
    if result.plan is not None:  # #17's overlay; just the plan and its nodes without a design
        if result.submission is not None:
            fig = draw_solution(result.plan, result.submission, violations=side.violations)
        else:
            fig = draw_layers(result.plan)
            draw_nodes(result.plan, fig.axes[0])
        png = Path(args.out) / f"{side.plan_id}.png"
        png.write_bytes(to_png(fig))
        written.append(png)
    summary = f"{side.plan_id}: {side.status}"
    if side.cost is not None and result.submittable:
        summary += f" · H {side.cost.H} · J {side.cost.J:.3f}"
    if side.reason:
        summary += f" ({side.reason})"
    print(summary)
    for v in side.violations:
        print(strict_line(v) if v.severity == "strict" else f"rule {v.rule} [error] {v.message}")
    for path in written:
        print(f"wrote {path}")
    return 0 if result.submittable else 1


def _check(args: argparse.Namespace) -> int:
    c = Constraints()
    try:
        plan = load_plan(args.plan, c)
        sub = Submission.model_validate_json(args.submission.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as e:
        print(f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}", file=sys.stderr)
        return 3
    if sub.plan_id != plan.plan_id:
        print(f"warning: the submission is for {sub.plan_id}, the plan is {plan.plan_id}")
    r = report(plan, sub, c)
    for line in r.lines:
        print(line)
    print(f"metrics: {metrics_text(r.metrics)}")
    for v in r.violations:
        if v.severity == "error":
            print(f"rule {v.rule} [error] {v.hub_id or 'plan'}: {v.message}")
        else:
            print(strict_line(v))
    print(r.verdict)
    if r.verdict == "invalid":
        return 1
    return 2 if r.violations and args.fail_on_strict else 0


def main(argv: list[str] | None = None) -> int:
    """`hackhive solve <plan.json> --out <dir>` and `hackhive check <plan.json> <submission.json>`.

    solve: exit 0 when a submission was written (strict-only too), 1 when only the sidecar was,
    3 when the plan file doesn't exist. check: exit 1 on any `error`, 0 when valid (strict-only
    too; each strict reading printed `STRICT (rule n, #46 Qm):`), 2 for strict-only under
    `--fail-on-strict`, 3 when a file can't be read.
    """
    _utf8()
    p = argparse.ArgumentParser(prog="hackhive", description="PS4 floor-plan network planner.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("solve", help="design one plan: submission (if valid), sidecar, PNG")
    s.add_argument("plan", type=Path, help="the plan JSON file")
    s.add_argument("--out", type=Path, required=True, help="the output directory")
    s.set_defaults(run=_solve)
    k = sub.add_parser("check", help="check a submission against its plan (slide 3's rules)")
    k.add_argument("plan", type=Path, help="the plan JSON file")
    k.add_argument("submission", type=Path, help="the slide-8 submission JSON file")
    k.add_argument(
        "--fail-on-strict", action="store_true", help="exit 2 when only strict violations exist"
    )
    k.set_defaults(run=_check)
    args = p.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
