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


def test_excluded_trial_reason_is_visible_and_escaped():
    row = sample()
    row["excluded_reason"] = "Concurrent <local checks>"
    page = results_table([row])
    assert "Excluded from reporting series: Concurrent &lt;local checks&gt;" in page


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True])
def test_invalid_ceiling_is_rejected(value):
    from syq_bench.rclone_public import nominal_ceiling

    with pytest.raises(ValueError):
        nominal_ceiling([{"network": {"sender_nic_mbps": value}}])


def test_ceiling_requires_shared_recorded_rating_and_does_not_clamp_speed():
    from syq_bench.rclone_public import nominal_ceiling

    a, b = sample(), sample()
    a.update(network={"sender_nic_mbps": 1000}, bytes=300_000_000, wall_s=2)
    assert nominal_ceiling([a]) == 125
    assert nominal_ceiling([a, b]) is None
    b["network"] = {"sender_nic_mbps": 2000}
    assert nominal_ceiling([a, b]) is None
    page = results_table([a])
    assert "125.0 MB/s" in page
    assert "150.0 MB/s" in page
    assert "left:81.699%" in page


def test_page_only_renders_selected_comparisons_with_sidebar_links(tmp_path):
    import shutil

    from syq_bench.rclone_public import build

    shutil.copytree(ROOT / "site", tmp_path / "site")
    page = build(tmp_path).read_text()
    data = json.loads((ROOT / "site/data/rclone-exploratory.json").read_text())
    for case in data["featured"]:
        assert f'href="#{case["id"]}"' in page
        assert f'id="{case["id"]}"' in page
    assert page.count('class="comparison-case"') == len(data["featured"])
    for text in ('id="appendix"', 'href="#manual"', "needs three runs", "publication controls"):
        assert text not in page
