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


def test_speed_uses_bytes_over_mean_time_not_mean_of_speeds():
    from syq_bench.rclone_public import measurements

    a, b = sample(), sample()
    a.update(wall_s=2, bytes=100_000_000)
    b.update(wall_s=8, bytes=100_000_000)
    speed, mean, low, high = measurements([a, b])
    assert (speed, mean, low, high) == (20, 5, 12.5, 50)
    b["exit_code"] = 1
    assert measurements([a, b]) is None


def test_publication_rerun_note_checks_every_repeat():
    from syq_bench.rclone_public import replacement_note

    rows = [sample() for _ in range(3)]
    for i, row in enumerate(rows):
        row.update(repeat=i, wall_s=12)
    assert "three runs recorded" in replacement_note(rows)
    rows[2]["wall_s"] = 0.5
    assert "larger workload" in replacement_note(rows)
    assert "needs three runs" in replacement_note(rows[:1])


def test_copy_command_visible_without_opening_details():
    page = results_table([sample()])
    assert page.index("copy-command") < page.index("<details>")
    assert "GB/s" in page or "MB/s" in page
    assert " s mean" in page


def load_generator():
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_files", ROOT / "site/make-files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_generator_shape_seed_and_refusal_to_overwrite(tmp_path):
    generator = load_generator()
    a, b = tmp_path / "a", tmp_path / "b"
    generator.create(a, 5, 17, 2, 42)
    generator.create(b, 5, 17, 2, 42)
    paths = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert len(paths) == 5
    assert len({p.parent for p in paths}) == 2
    assert all((a / p).stat().st_size == 17 for p in paths)
    assert all((a / p).read_bytes() == (b / p).read_bytes() for p in paths)
    with pytest.raises(FileExistsError):
        generator.create(a, 5, 17, 2, 42)
    assert all((a / p).read_bytes() == (b / p).read_bytes() for p in paths)


def test_generator_checks_space_before_creating_anything(tmp_path, monkeypatch):
    from collections import namedtuple

    generator = load_generator()
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(generator.shutil, "disk_usage", lambda p: usage(100, 99, 1))
    with pytest.raises(ValueError, match="free bytes"):
        generator.create(tmp_path / "data", 1, 1, 0, 42)
    assert not (tmp_path / "data").exists()
