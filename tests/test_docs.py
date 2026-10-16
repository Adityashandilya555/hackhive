"""#34: `docs/output-format.md` stays true to what the writer and `solve` emit, and the README's
commands run."""

import inspect
import json
import re
import shlex
from pathlib import Path
from typing import get_args

import jsonschema

from hackhive import checker, cli, graph, io, rings, solve, what_if
from hackhive.contracts import SCHEMA_DIR, Bounds, Sidecar, Submission

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "output-format.md").read_text(encoding="utf-8")
PLAN_5924 = ROOT / "tests" / "fixtures" / "plans" / "plan-5924.json"


def _example() -> str:
    return re.search(r"### Example \(fixture 5924\).*?```json\n(.*?)```", DOC, re.S).group(1)


def test_the_example_submission_parses_matches_the_schema_and_passes_check(tmp_path):
    """C24, C48: the doc's example is a real, valid submission for fixture 5924."""
    text = _example()
    Submission.model_validate_json(text)
    schema = json.loads((SCHEMA_DIR / "submission.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(text), schema)
    path = tmp_path / "example.json"
    path.write_text(text, encoding="utf-8")
    assert cli.main(["check", str(PLAN_5924), str(path)]) == 0


def test_the_example_is_rounded_to_4_decimals():
    """T6: the doc shows what the writer writes."""
    assert not re.findall(r"\d\.\d{5,}", _example())


def test_every_status_and_hub_warning_is_documented():
    """C24, C43: a status or hub warning code added to the source fails here until documented."""
    statuses = set(get_args(Sidecar.model_fields["status"].annotation))
    statuses |= set(re.findall(r'status="(\w+)"', inspect.getsource(solve)))
    statuses |= set(re.findall(r'"(\w+)"', inspect.getsource(solve._status)))
    codes = set(re.findall(r'warnings = \["(\w+)"\]', inspect.getsource(rings)))
    assert codes, "no hub warning code found: the grep is stale"
    missing = sorted(s for s in statuses | codes if f"`{s}`" not in DOC)
    assert not missing, f"undocumented in docs/output-format.md: {missing}"


def test_every_sidecar_key_and_proof_is_documented():
    """C43 (#22): the sidecar's keys, `arrangement` included, and every `bounds.proof` value."""
    names = set(Sidecar.model_fields) | set(get_args(Bounds.model_fields["proof"].annotation))
    missing = sorted(n for n in names if not re.search(rf"`{n}(\[\])?`", DOC))
    assert not missing, f"undocumented in docs/output-format.md: {missing}"


def test_the_readme_hackhive_commands_run_on_a_fresh_clone(tmp_path):
    """C22: every `uv run hackhive ...` line in the README runs on the committed fixtures alone
    (`out/` is redirected to a temp dir)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = re.findall(r"^uv run hackhive (.+)$", readme, re.M)
    assert {c.split()[0] for c in commands} == {"solve", "check", "batch"}
    for line in commands:
        argv = [a.replace("out/", f"{tmp_path.as_posix()}/") for a in shlex.split(line)]
        argv = [str(ROOT / a) if a.startswith("tests/") else a for a in argv]
        assert cli.main(argv) == 0, line


def test_the_readme_list_command_runs(capsys):
    """The README's `python -m hackhive.solve --list` line runs on a committed fixture (from #100)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (line,) = re.findall(r"^uv run python -m hackhive\.solve (.+)$", readme, re.M)
    argv = [str(ROOT / a) if a.startswith("tests/") else a for a in shlex.split(line)]
    assert solve.main(argv) == 0
    assert "R01+R02+R05|R03|R04" in capsys.readouterr().out


# (module, the text it writes, how docs/output-format.md names it): every sidecar warning line
WARNINGS = [
    (io, "{n.clearance_m:.2f} m <", "clearance 0.42 m < 0.5 m"),
    (solve, "too narrow for a node at the wall clearance (C38)", "too narrow for a node"),
    (io, 'f"frame: {frame}', "`frame: …`"),
    (io, "lies {out:.0%} outside inner", "lies NN% outside inner"),
    (checker, "recomputed {", "`reported … recomputed …`"),
    (checker, "reported hub_count", "`reported hub_count …`"),
    (solve, "isn't a safe file name", "isn't a safe file name"),
    (what_if, "{WHAT_IF}: ", "`WHAT-IF: …`"),
    (io, "(slide 2: one per room)", "(slide 2: one per room)"),
    (graph, "near-square door, its sides picked by room contact", "near-square door"),
    (graph, "no room found on both sides, so it links nothing", "no room found on both sides"),
    (io, 'f"room > {c.max_area_m2:g} m²"', "`room > 100 m²`"),
]


def test_every_free_text_warning_is_documented():
    """C43, #102 review: each warning line the sidecar can carry still exists in the source and is
    documented. A new warning belongs in this list and in the doc's table."""
    for module, text, doc in WARNINGS:
        assert text in inspect.getsource(module), (module.__name__, text)
        assert doc in DOC, doc
