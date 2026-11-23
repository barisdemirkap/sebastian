"""Training-loop fixtures (offline, fixture calendar): realistic Turkish use cases per category in `training/*.json`.
Each file is a list of cases in the incident-fixture format ({"name", "setup", "steps"}). Failures found in the owner's real
message log are the `real-*` cases. The principles they pin:
 1. a reference to an existing event, task or list item is resolved, never created again;
 2. a completion statement closes the item and never proposes deleting it;
 3. every clause of a multi-clause message is processed;
 4. 'bu yarina' / 'yarina kalsin' / 'X'e tasi' reschedule, never close;
 5. no stray 'type the number' question when the reference is unambiguous or follows an earlier answer."""
import json
from pathlib import Path

import pytest

import invariants as inv
from sebastian_adapter import make_adapter

FOLDER = Path(__file__).parent / "training"
CASES = [(f"{p.stem}/{c['name']}", c) for p in sorted(FOLDER.glob("*.json")) for c in json.loads(p.read_text(encoding="utf-8"))]


def test_training_set_is_big_enough():
    assert len(CASES) >= 100


@pytest.mark.parametrize("name,case", CASES, ids=[n for n, _ in CASES])
def test_training_case(name, case):
    inv.run_script(make_adapter, case)
