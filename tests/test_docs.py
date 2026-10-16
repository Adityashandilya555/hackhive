"""#34 (pre-#22 part): `docs/output-format.md` stays true to what the writer and `solve` emit."""

import inspect
import json
import re
from pathlib import Path
from typing import get_args

import jsonschema

from hackhive import cli, rings, solve
from hackhive.contracts import SCHEMA_DIR, Sidecar, Submission

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
