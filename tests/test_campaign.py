import json
import sys

import pytest

from syq_bench.cli import cmd_run, parse_args
from syq_bench.resources import measure
from syq_bench.spec import SpecError, from_dict


def raw_spec():
    return {
        "endpoints": {"source": "/src", "destination": "/dst"},
        "tools": [{"name": "new", "kind": "cp"}, {"name": "old", "kind": "cp"}],
        "workloads": [{"name": "files", "kind": "small-files", "files": 3, "bytes": "12KiB"}],
        "protocol": {"repeats": 3, "order": [["new", "old"], ["new"], ["new"]]},
    }


@pytest.mark.parametrize(
    "order",
    [
        [],
        "new",
        [[]],
        [[1]],
        [["new", "new"], ["old"], ["new"]],
        [["new"], ["new"], ["new"]],
        [["unknown"], ["old"], ["new"]],
    ],
)
def test_schedule_rejects_invalid_or_unaccounted_tools(order):
    raw = raw_spec()
    raw["protocol"]["order"] = order
    with pytest.raises(SpecError, match="protocol.order"):
        from_dict(raw)


def test_schedule_smoke_preserves_order_and_counts(tmp_path):
    spec = tmp_path / "run.toml"
    spec.write_text(f'''name = "ordered"
[endpoints]
source = "{tmp_path}/source"
destination = "{tmp_path}/dest"
[[tools]]
name = "new"
kind = "cp"
[[tools]]
name = "old"
kind = "cp"
[[workloads]]
name = "files"
kind = "small-files"
files = 3
bytes = "12KiB"
[protocol]
repeats = 3
order = [["new", "old"], ["new"], ["new"]]
probes = false
durable = false
slow_cutoff = 0
''')
    output = tmp_path / "result.json"
    assert cmd_run(parse_args(["run", str(spec), "--out", str(output), "-y"])) == 0
    run = json.loads(output.read_text())
    new, old = run["cases"]
    assert [r["index"] for r in new["repeats"]] == [0, 1, 2]
    assert [r["round_index"] for r in new["repeats"]] == [0, 1, 2]
    assert [r["index"] for r in old["repeats"]] == [0]
    assert old["curtailed"] is None
    assert all(r["verified"] for c in run["cases"] for r in c["repeats"])
    assert not list((tmp_path / "source").iterdir())
    assert not list((tmp_path / "dest").iterdir())


def test_resource_peak_is_per_command_and_cpu_includes_waited_child(tmp_path):
    high, low = tmp_path / "high.json", tmp_path / "low.json"
    assert (
        measure(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; subprocess.run([sys.executable,'-c',"
                "'x=bytearray(100*1024*1024); sum(range(2000000))'],check=True)",
            ],
            high,
        )
        == 0
    )
    assert measure(["true"], low) == 0
    a, b = json.loads(high.read_text()), json.loads(low.read_text())
    assert a["max_process_rss_bytes"] > 90 * 1024**2
    assert b["max_process_rss_bytes"] < a["max_process_rss_bytes"] * 0.9
    assert a["cpu_s"] > b["cpu_s"]
    assert a["accounting_complete"] and b["accounting_complete"]
    with pytest.raises(FileExistsError):
        measure(["false"], high)
    assert json.loads(high.read_text()) == a


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_resource_timeout_validation(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        measure(["true"], tmp_path / "result.json", timeout)


def test_resource_timeout_retains_failure(tmp_path):
    output = tmp_path / "timeout.json"
    assert measure(["sleep", "60"], output, 0.05) == 124
    record = json.loads(output.read_text())
    assert record["timed_out"] and not record["accounting_complete"]
    assert record["exit_code"] != 0


def test_version_specific_remote_helper_identity():
    from types import SimpleNamespace

    from syq_bench.remote import Location
    from syq_bench.runner import remote_identity
    from syq_bench.tools import Tool

    class Remote:
        def sh(self, script, check):
            assert not check
            assert "command -v /runtime/old/syq" in script
            return SimpleNamespace(returncode=0, stdout="BIN=/runtime/old/syq\nVER=syq 0.5.2\nSHA=" + "a" * 64)

    raw = raw_spec()
    raw["tools"] = [
        {
            "name": name,
            "kind": "syq",
            "binary": f"/local/{name}/syq",
            "args": ["rsync", "-a", "--syq-no-bootstrap", "--rsync-path", f"/runtime/{name}/syq"],
        }
        for name in ("new", "old")
    ]
    new, old = (Tool(t) for t in from_dict(raw).tools)
    assert new.argv(Location.parse("/src"), Location.parse("remote:/dst"))[0] == "/local/new/syq"
    identity = remote_identity(old, Remote())
    assert identity == {"binary": "/runtime/old/syq", "version": "syq 0.5.2", "sha256": "a" * 64}


def test_resource_capture_stops_and_verifies_surviving_workers(tmp_path):
    output = tmp_path / "orphan.json"
    assert measure(["sh", "-c", "sleep 60 & exit 0"], output, 3) == 1
    record = json.loads(output.read_text())
    assert record["surviving_process_group"]
    assert record["process_group_cleanup_verified"]
    assert not record["accounting_complete"]
