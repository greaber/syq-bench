import copy
import json
import math
from pathlib import Path

import pytest

from syq_bench.rclone_public import results_table, status, timing

ROOT = Path(__file__).resolve().parents[1]


def sample():
    return copy.deepcopy(json.loads((ROOT / "site/data/rclone-exploratory.json").read_text())["rows"][0])


def test_failure_cannot_be_presented_as_a_successful_copy():
    row = sample()
    row.update(exit_code=1, content_verified=True, case_error=True, wall_s=0.3)
    assert "Failed" in status(row)
    assert "0.300 s" in timing(row)
    assert "failed attempt" in timing(row)
    row.update(exit_code=-1, wall_s=0)
    assert timing(row) == "No copy timing"
    row.update(recorded_status="timeout", wall_s=60)
    assert "not a completed copy" in status(row)
    assert "time limit" in timing(row)


def test_missing_and_failed_metadata_are_visible():
    row = sample()
    row.update(exit_code=0, case_error=False, content_verified=True, metadata=None)
    assert "unrecorded" in status(row)
    row["metadata"] = {"mismatches": 1}
    assert status(row) == "Metadata mismatch"
    row["content_verified"] = False
    assert "Failed" in status(row)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True])
def test_invalid_success_duration_is_rejected(value):
    row = sample()
    row.update(exit_code=0, wall_s=value)
    with pytest.raises(ValueError):
        timing(row)


def test_all_repetitions_and_unfavorable_settings_remain_visible():
    data = json.loads((ROOT / "site/data/rclone-exploratory.json").read_text())
    rows = [r for r in data["rows"] if r["scenario"] == "main-write"]
    page = results_table(rows)
    for row in rows:
        assert f"{row['wall_s']:.3f} s" in page
    assert "--transfers 256" in page
    assert "--transfers 64" in page
