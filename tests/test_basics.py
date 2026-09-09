import json
import os
import random
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from syq_bench import fixtures as fx
from syq_bench import spec as sp
from syq_bench.cli import main
from syq_bench.remote import Location
from syq_bench.tools import Tool

SPEC = """
name = "t"
[endpoints]
source = "{src}"
destination = "{dst}"
[[tools]]
name = "rsync"
[[tools]]
name = "cp"
[[workloads]]
name = "small"
kind = "small-files"
files = 20
bytes = "200KiB"
[[workloads]]
name = "big"
kind = "large-file"
bytes = "1MiB"
[protocol]
repeats = 2
"""


def test_location_parse():
    assert Location.parse("/tmp/x") == Location(PurePosixPath("/tmp/x"))
    assert Location.parse("u@h:/tmp/x") == Location(PurePosixPath("/tmp/x"), "u@h")
    assert Location.parse("./a:b").host is None
    assert Location.parse("u@h:").path == PurePosixPath(".")


def test_parse_size():
    assert sp.parse_size("4GiB") == 4 * 2**30
    assert sp.parse_size("400M") == 400_000_000
    assert sp.parse_size("1k") == 1000
    assert sp.parse_size(7) == 7
    with pytest.raises(sp.SpecError):
        sp.parse_size("lots")


def test_spec_rejects_delete_and_bad_kind():
    raw = {"endpoints": {"source": "/s", "destination": "/d"}, "workloads": [{"name": "w", "path": "/p"}]}
    with pytest.raises(sp.SpecError, match="--delete"):
        sp.from_dict({**raw, "tools": [{"name": "rsync", "args": ["-a", "--delete"]}]})
    with pytest.raises(sp.SpecError, match="kind"):
        sp.from_dict({**raw, "tools": [{"name": "mine"}]})
    s = sp.from_dict({**raw, "tools": [{"name": "syq-j8", "kind": "syq", "jobs": 8, "binary": "/opt/syq"}]})
    assert s.workloads[0].kind == "path"
    t = Tool(s.tools[0])
    assert t.argv(Location.parse("/s"), Location.parse("h:/d")) == ["/opt/syq", "-a", "-j", "8", "/s/", "h:/d"]


def test_tool_workload_selection_is_validated():
    raw = {
        "endpoints": {"source": "/s", "destination": "/d"},
        "workloads": [{"name": "large", "kind": "large-file", "bytes": "1MiB"}],
    }
    selected = sp.from_dict({**raw, "tools": [{"name": "rsync", "workloads": ["large"]}]})
    assert selected.tools[0].workloads == ("large",)
    for workloads in ([], ["large", "large"], ["unknown"], "large"):
        with pytest.raises(sp.SpecError, match="workloads"):
            sp.from_dict({**raw, "tools": [{"name": "rsync", "workloads": workloads}]})


def test_shipped_qcp_specs_are_tuned_and_large_file_only():
    for name in ("lan.toml", "wan.toml", "wan-loss.toml"):
        spec = sp.load(Path(__file__).parents[1] / "specs" / name)
        qcp = next(tool for tool in spec.tools if tool.kind == "qcp")
        assert qcp.name == "qcp-tuned" and qcp.workloads == ("large-file",)
        assert {"--tx", "--rx", "--rtt", "--remote-port"} <= set(qcp.args)


def test_spec_rejects_unsafe_names_and_source_removal():
    raw = {"endpoints": {"source": "/s", "destination": "/d"}, "tools": [{"name": "rsync"}]}
    for bad in ("../escape", "a/b", ".hidden", "", "x y"):
        with pytest.raises(sp.SpecError, match="plain path component"):
            sp.from_dict({**raw, "workloads": [{"name": bad, "path": "/p"}]})
    for arg in ("--remove-source-files", "--delete-after", "--del", "--delete=yes"):
        with pytest.raises(sp.SpecError, match="harness owns"):
            sp.from_dict(
                {**raw, "tools": [{"name": "rsync", "args": ["-a", arg]}], "workloads": [{"name": "w", "path": "/p"}]}
            )


def test_remove_tree_requires_containment(tmp_path):
    root = Location(tmp_path / "root")
    (tmp_path / "root" / "in").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError):
        Location(tmp_path / "out").remove_tree(inside=root)
    with pytest.raises(ValueError):
        root.remove_tree(inside=root)
    with pytest.raises(ValueError):
        Location(tmp_path / "root" / "in", host="h").remove_tree(inside=root)
    (root / "in").remove_tree(inside=root)
    assert (tmp_path / "out").is_dir() and not (tmp_path / "root" / "in").exists()


def test_timed_keeps_tail_and_heartbeats():
    from syq_bench.runner import _timed

    beats = []
    wall, _u, _s, code, tail = _timed(
        ["sh", "-c", "for i in $(seq 1 120); do echo line$i >&2; done; sleep 0.3; exit 3"], beats.append, 0.1
    )
    assert code == 3 and wall >= 0.3 and beats and "still running" in beats[0]
    assert tail.splitlines() == [f"line{i}" for i in range(21, 121)]


def test_overrides():
    raw = {"protocol": {"repeats": 3}, "tools": [{"name": "syq"}]}
    sp.apply_overrides(raw, ["protocol.repeats=1", "protocol.cache=warm", "name=x", "tools.0.binary=/opt/syq"])
    assert raw == {
        "protocol": {"repeats": 1, "cache": "warm"},
        "name": "x",
        "tools": [{"name": "syq", "binary": "/opt/syq"}],
    }
    with pytest.raises(sp.SpecError, match="index"):
        sp.apply_overrides(raw, ["tools.3.binary=x"])


def test_tools_argv_shape():
    src, dst = Location.parse("/s"), Location.parse("/d")
    rsync = Tool(sp.ToolSpec("rsync", "rsync", "rsync", ("-a",), None))
    cp = Tool(sp.ToolSpec("cp", "cp", "cp", ("-a",), None))
    assert rsync.argv(src, dst) == ["rsync", "-a", "/s/", "/d"]
    assert cp.argv(src, dst) == ["cp", "-a", "/s/.", "/d"]


def test_syq_debug_is_explicit_and_wraps_only_syq():
    raw = {
        "endpoints": {"source": "/s", "destination": "h:/d"},
        "workloads": [{"name": "w", "path": "/p"}],
        "tools": [{"name": "diagnostic", "kind": "syq", "debug": True}],
    }
    tool = Tool(sp.from_dict(raw).tools[0])
    assert tool.argv(Location.parse("/s"), Location.parse("h:/d"))[:3] == ["env", "SYQ_DEBUG=1", "syq"]
    for invalid in ({"name": "rsync", "debug": True}, {"name": "syq", "debug": "true"}):
        with pytest.raises(sp.SpecError, match="debug"):
            sp.from_dict({**raw, "tools": [invalid]})


def test_qcp_argv():
    raw = {
        "endpoints": {"source": "/s", "destination": "h:/d"},
        "workloads": [{"name": "w", "path": "/p"}],
        "tools": [{"name": "qcp", "jobs": 4}],
    }
    t = Tool(sp.from_dict(raw).tools[0])
    assert t.argv(Location.parse("/s/tree"), Location.parse("h:/d")) == ["qcp", "-rpq", "-j", "4", "/s/tree/", "h:/d"]
    assert not t.local_ok and t.remote_ok


def test_ssh_command_reaches_tools_and_harness():
    raw = {
        "endpoints": {"source": "/s", "destination": "h:/d", "ssh": "/x/wrap -v"},
        "workloads": [{"name": "w", "path": "/p"}],
        "tools": [{"name": "syq"}, {"name": "rsync"}, {"name": "qcp"}, {"name": "tar"}],
    }
    spec = sp.from_dict(raw)
    src, dst = Location.parse("/s/t", spec.ssh), Location.parse("h:/d", spec.ssh)
    argvs = {t.name: Tool(t).argv(src, dst) for t in spec.tools}
    assert argvs["syq"] == ["syq", "-a", "-e", "/x/wrap -v", "/s/t/", "h:/d"]
    assert argvs["rsync"] == ["rsync", "-a", "-e", "/x/wrap -v", "/s/t/", "h:/d"]
    assert argvs["qcp"] == ["qcp", "-rpq", "--ssh", "/x/wrap", "-S", "-v", "/s/t/", "h:/d"]
    assert argvs["tar"][:4] == ["bash", "-o", "pipefail", "-c"]
    assert "tar -C /s/t -cf -  . | /x/wrap -v -o BatchMode=yes h 'tar -C /d -xf -'" in argvs["tar"][4]
    assert dst.ssh_argv() == ["/x/wrap", "-v", "-o", "BatchMode=yes", "h"]
    assert (dst / "sub").ssh == "/x/wrap -v"
    # Pull direction packs on the remote.
    pull = Tool(spec.tools[3]).argv(dst, Location.parse("/l", spec.ssh))[4]
    assert pull.startswith("/x/wrap -v -o BatchMode=yes h 'tar -C /d -cf -  .' | tar -C /l -xf -")


def test_checksum_is_independent_of_host_collation(tmp_path, monkeypatch):
    # Simulate hosts with opposite collations without requiring a system locale
    # installation. A fixed byte collation must produce the same tree digest.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sorter = bin_dir / "sort"
    sorter.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "items = sys.stdin.buffer.read().split(bytes([0]))[:-1]\n"
        "reverse = os.environ.get('LC_ALL') != 'C' and os.environ['REVERSE_COLLATION'] == '1'\n"
        "sys.stdout.buffer.write(bytes([0]).join(sorted(items, reverse=reverse)) + bytes([0]))\n"
    )
    sorter.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("LC_ALL", raising=False)
    source = tmp_path / "source"
    source.mkdir()
    (source / ".probe").write_bytes(b"")
    (source / "payload").write_bytes(b"content")
    location = Location(source)
    monkeypatch.setenv("REVERSE_COLLATION", "0")
    expected = location.checksum()
    monkeypatch.setenv("REVERSE_COLLATION", "1")
    assert location.checksum() == expected
    (source / "payload").write_bytes(b"changed")
    assert location.checksum() != expected


def test_fixtures_deterministic_shape_and_cold(tmp_path):
    f = fx.make("small-files", "s", 50 * 4096, 50)
    cold_a = f.generate(tmp_path / "a", seed=1)
    f.generate(tmp_path / "b", seed=1)
    a, b = Location(tmp_path / "a"), Location(tmp_path / "b")
    assert a.checksum() == b.checksum()
    assert fx.tree_bytes(tmp_path / "a") == (50, 50 * 4096)
    assert cold_a == fx.supports_odirect(tmp_path)
    assert fx.make("small-files", "tiny", 50, 50).generate(tmp_path / "tiny", seed=1) == cold_a
    assert a.evict_tree() and a.fsync_tree()
    # An unaligned size takes the buffered tail path and still verifies.
    fx.write_random(tmp_path / "odd", 4096 + 123, seed=2)
    assert os.path.getsize(tmp_path / "odd") == 4096 + 123


def test_mixed_tree_exact_shape(tmp_path):
    fx.make("mixed-tree", "m", 3 * 2**20, 40).generate(tmp_path, seed=9)
    assert fx.tree_bytes(tmp_path) == (40, 3 * 2**20)
    for seed in range(20):
        sizes = fx._fit_sizes([2 ** random.Random(seed).uniform(10, 26) for _ in range(10)], 10 * 1024 + 5, 1024)
        assert sum(sizes) == 10 * 1024 + 5 and min(sizes) >= 1024
    with pytest.raises(ValueError):
        fx.make("mixed-tree", "m", 10, 10)


def test_incremental_delta_identical_across_tools(tmp_path):
    spec = tmp_path / "t.toml"
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            'kind = "small-files"', 'kind = "small-files"\nchanged = 0.1'
        )
        + "seed = 5\n"
    )
    out = tmp_path / "r.json"
    # Two tools that both see the delta: rsync twice under different names.
    args = ["--set", "tools.1.name=rsync2", "--set", "tools.1.kind=rsync", "--set", "tools.1.binary=rsync"]
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=2", *args]) == 0
    run = json.loads(out.read_text())
    small = {c["tool"]: c for c in run["cases"] if c["workload"] == "small"}
    for i in range(2):
        a, b = small["rsync"]["repeats"][i], small["rsync2"]["repeats"][i]
        assert (a["delta_seed"], a["changed_files"], a["changed_bytes"]) == (
            b["delta_seed"],
            b["changed_files"],
            b["changed_bytes"],
        )
    assert small["rsync"]["repeats"][0]["delta_seed"] != small["rsync"]["repeats"][1]["delta_seed"]


def test_empty_dir_checks(tmp_path):
    loc = Location(tmp_path / "new")
    assert not loc.exists()
    loc.mkdir()
    assert loc.is_empty_dir()
    (tmp_path / "new" / "f").write_text("x")
    assert not loc.is_empty_dir()
    assert loc.free_bytes() > 0


def test_run_end_to_end(tmp_path, capsys):
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "--dry-run"]) == 0
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    assert run["schema"] == 3 and run["spec"]["protocol"]["repeats"] == 1
    assert {c["tool"] for c in run["cases"]} == {"rsync", "cp"}
    for c in run["cases"]:
        assert c["too_short"] is True
        assert len(c["repeats"]) == 1
        r = c["repeats"][0]
        assert r["exit_code"] == 0 and r["verified"] is True and r["fsync_s"] is not None
        assert r["cache"] == "evict"
    assert "small" in capsys.readouterr().out
    # Scratch is cleaned up: both roots exist and are empty.
    assert not any((tmp_path / "src").iterdir()) and not any((tmp_path / "dst").iterdir())


def test_run_fails_on_verification_mismatch(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    monkeypatch.setattr(Location, "checksum", lambda self: os.urandom(4).hex())
    assert main(["run", str(spec), "-y", "--out", str(tmp_path / "r.json"), "--set", "protocol.repeats=1"]) == 1


def test_path_workload_size_checked_up_front(tmp_path, monkeypatch):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "f").write_bytes(b"x" * 4096)
    spec = tmp_path / "t.toml"
    spec.write_text(
        f'''
[endpoints]
source = "{tmp_path / "src"}"
destination = "{tmp_path / "dst"}"
[[tools]]
name = "cp"
[[workloads]]
name = "mine"
path = "{tmp_path / "user"}"
'''
    )
    monkeypatch.setattr(Location, "free_bytes", lambda self: 100)
    with pytest.raises(SystemExit):
        main(["run", str(spec), "-y"])
    assert not (tmp_path / "dst").exists() or not any((tmp_path / "dst").iterdir())


def test_endpoints_must_be_disjoint(tmp_path):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "f").write_bytes(b"x")
    for src, dst in ((tmp_path / "user", tmp_path / "user" / "dst"), (tmp_path / "user" / "sub", tmp_path / "user")):
        spec = tmp_path / "t.toml"
        spec.write_text(
            f'[endpoints]\nsource = "{tmp_path / "scratch"}"\ndestination = "{dst}"\n'
            f'[[tools]]\nname = "cp"\n[[workloads]]\nname = "mine"\npath = "{src}"\n'
        )
        (tmp_path / "user" / "sub").mkdir(exist_ok=True)
        with pytest.raises(SystemExit):
            main(["run", str(spec), "-y"])
    assert sorted(p.name for p in (tmp_path / "user").iterdir()) == ["f", "sub"]
    spec.write_text(SPEC.format(src=tmp_path / "s", dst=tmp_path / "s" / "d"))
    with pytest.raises(SystemExit):
        main(["run", str(spec), "-y"])


def test_space_guard_counts_blocks_and_only_generated_for_source(tmp_path, monkeypatch):
    # 100k one-byte files need at least 100k blocks, not 100k bytes.
    w = sp.from_dict(
        {
            "endpoints": {"source": "/s", "destination": "/d"},
            "tools": [{"name": "cp"}],
            "workloads": [{"name": "w", "kind": "small-files", "files": 100_000, "bytes": 100_000}],
        }
    ).workloads[0]
    assert w.disk_inodes() == 100_000 + 256 + 1 and w.disk_bytes() == 100_000 + w.disk_inodes() * sp.BLOCK
    mixed = sp.WorkloadSpec("m", "mixed-tree", 10 * 1024, 1, None)
    assert mixed.disk_inodes() == 1 + 4 + 1  # one file may sit four directories deep
    big = sp.WorkloadSpec("m", "mixed-tree", 2**30, 100_000, None)
    assert big.disk_inodes() == 100_000 + 4680 + 1
    assert (
        sp.parse_size("4GiB") + 2 * sp.BLOCK
        == sp.from_dict(
            {
                "endpoints": {"source": "/s", "destination": "/d"},
                "tools": [{"name": "cp"}],
                "workloads": [{"name": "w", "kind": "large-file", "bytes": "4GiB"}],
            }
        )
        .workloads[0]
        .disk_bytes()
    )
    with pytest.raises(sp.SpecError, match="plain path component"):
        sp.from_dict(
            {
                "name": "../x",
                "endpoints": {"source": "/s", "destination": "/d"},
                "tools": [{"name": "cp"}],
                "workloads": [{"name": "w", "path": "/p"}],
            }
        )


def test_overlap_guard_resolves_spellings(tmp_path, monkeypatch):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "f").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "link").symlink_to(tmp_path / "user")
    for dst in ("user/dst", "./user/../user/dst", str(tmp_path / "link" / "dst")):
        spec = tmp_path / "t.toml"
        spec.write_text(
            f'[endpoints]\nsource = "{tmp_path / "scratch"}"\ndestination = "{dst}"\n'
            f'[[tools]]\nname = "cp"\n[[workloads]]\nname = "mine"\npath = "{tmp_path / "user"}"\n'
        )
        with pytest.raises(SystemExit):
            main(["run", str(spec), "-y"])
    assert [p.name for p in (tmp_path / "user").iterdir()] == ["f"]


def test_kill_group_reaches_descendants_that_ignore_sigterm():
    import os
    import signal
    import subprocess
    import time
    import uuid

    from syq_bench.runner import kill_group

    marker = uuid.uuid4().hex
    # Leader exits on SIGTERM; the child traps it and lingers.
    proc = subprocess.Popen(
        ["sh", "-c", f"sh -c 'trap \"\" TERM; sleep 30; : {marker}' & sleep 30"], start_new_session=True
    )
    time.sleep(0.3)
    kill_group(proc)
    time.sleep(0.2)
    assert marker not in os.popen("ps -eo args").read()
    assert signal.SIGTERM  # silence unused-import style checks


def test_tree_bytes_counts_directories(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "f").write_bytes(b"xy")
    assert fx.tree_bytes(tmp_path, with_dirs=True) == (1, 2, 3)
    loc = Location(tmp_path)
    assert loc.same_filesystem(Location(tmp_path / "a"))
    assert loc.free_inodes() is None or loc.free_inodes() > 0


def test_local_fsync_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    monkeypatch.setattr(fx, "fsync_tree", lambda root: (_ for _ in ()).throw(OSError("EINVAL")))
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    reps = [r for c in run["cases"] for r in c["repeats"]]
    assert reps and all(r["fsync_s"] is None and "failed" in r["fsync_note"] for r in reps)
    assert not any((tmp_path / "dst").iterdir())


def test_too_short_uses_true_median():
    from syq_bench.runner import Case, Repeat, Runner

    c = Case("w", "t", [], 1, 1)
    for wall in (1.90, 2.05):
        c.repeats.append(Repeat(0, wall, 0, 0, 0, True, None, None, "evict", "warm"))
    walls = c.good_walls()
    import statistics

    assert statistics.median(walls) < 2.0 and Runner is not None


def test_space_peaks_are_per_workload(tmp_path, monkeypatch):
    """A big path workload and a tiny generated one never coexist, so the check must not add them."""
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "f").write_bytes(b"x" * 6000)
    spec = tmp_path / "t.toml"
    spec.write_text(
        f'[endpoints]\nsource = "{tmp_path / "s"}"\ndestination = "{tmp_path / "d"}"\n[[tools]]\nname = "cp"\n'
        f'[[workloads]]\nname = "mine"\npath = "{tmp_path / "user"}"\n'
        '[[workloads]]\nname = "tiny"\nkind = "large-file"\nbytes = 1\n'
    )
    # Shared filesystem with room for the largest single workload (path: 6000 B + 2 blocks) but not for
    # both peaks summed the old way; the tiny generated one adds only 2 blocks in its own turn.
    monkeypatch.setattr(Location, "free_bytes", lambda self: 40_000)
    monkeypatch.setattr(Location, "free_inodes", lambda self: 10)
    out = tmp_path / "r.json"
    args = ["--set", "protocol.repeats=1", "--set", "protocol.probes=false"]
    assert main(["run", str(spec), "-y", "--out", str(out), *args]) == 0
    with pytest.raises(SystemExit):  # the probes alone need 256 MiB and 2000 inodes
        main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"])
    monkeypatch.setattr(Location, "free_inodes", lambda self: 1)
    with pytest.raises(SystemExit):
        main(["run", str(spec), "-y", "--out", str(out), *args])


def test_interrupt_kills_process_group():
    import os
    import signal
    import threading
    import time
    import uuid

    from syq_bench.runner import _timed

    marker = uuid.uuid4().hex  # appears only in the children's argv, never in this file or the shell

    def interrupt():
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=interrupt, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        _timed(["sh", "-c", f"sleep 30 {marker} & sleep 30; wait"], print, 60)
    time.sleep(0.3)
    assert marker not in os.popen("ps -eo args").read()


def test_harness_step_failure_is_recorded_not_raised(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    calls = {"n": 0}
    real = Location.checksum

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 2:  # the first destination checksum: pretend ssh died
            raise subprocess.CalledProcessError(255, ["ssh"], stderr="Connection closed by remote host")
        return real(self)

    monkeypatch.setattr(Location, "checksum", flaky)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 1
    run = json.loads(out.read_text())
    errs = [c for c in run["cases"] if c["error"]]
    assert len(errs) == 1 and "Connection closed" in errs[0]["error"] and run["finished"]
    assert sum(len(c["repeats"]) for c in run["cases"]) == 4  # every case still ran
    assert not any((tmp_path / "dst").iterdir())
    assert "harness step failed" in capsys.readouterr().out


def test_cleanup_failure_aborts_run(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    real = Location.remove_tree
    calls = {"n": 0}

    def flaky(self, inside):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("EBUSY")
        return real(self, inside)

    monkeypatch.setattr(Location, "remove_tree", flaky)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=2"]) == 1
    run = json.loads(out.read_text())
    assert run["aborted"] and run["finished"]
    assert sum(len(c["repeats"]) for c in run["cases"]) == 1  # stopped after the first copy


def test_warm_cache_mode_keeps_prepopulated_destination_warm(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            'kind = "small-files"', 'kind = "small-files"\nchanged = 0.1'
        )
    )
    evictions = []
    monkeypatch.setattr(Location, "evict_tree", lambda self: evictions.append(str(self.path)) or True)
    out = tmp_path / "r.json"
    args = ["--set", "protocol.repeats=1", "--set", "protocol.cache=warm"]
    assert main(["run", str(spec), "-y", "--out", str(out), *args]) == 0
    assert evictions == []
    run = json.loads(out.read_text())
    assert {r["cache"] for c in run["cases"] for r in c["repeats"]} == {"warm"}


def test_run_skips_tool_outside_its_selected_workloads(tmp_path, capsys):
    spec = tmp_path / "t.toml"
    text = SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
        'name = "rsync"', 'name = "rsync"\nworkloads = ["big"]', 1
    )
    spec.write_text(text)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "--dry-run"]) == 0
    assert "[workloads: big]" in capsys.readouterr().out
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    cases = {(c["workload"], c["tool"]): c for c in run["cases"]}
    skipped = cases[("small", "rsync")]
    assert skipped["repeats"] == [] and skipped["skipped"] == "tool is selected only for: big"
    assert len(cases[("big", "rsync")]["repeats"]) == 1


def test_probes_and_identity(tmp_path):
    from syq_bench import probes as pr

    ps = pr.run_all(tmp_path)
    assert [p.name for p in ps] == ["seq_write", "seq_read", "metadata"]
    assert ps[2].value and ps[2].value > 0
    assert not any(tmp_path.iterdir())
    line = pr.ceiling(pr.as_dicts(ps), 100_000, 400 * 2**20)
    assert line is None or line.startswith(("metadata-bound", "storage-bound"))
    assert pr.ceiling(pr.as_dicts(ps), 1, 2**30, remote=True).startswith("unknown")
    network = [{"name": "network_receive", "value": 125.0, "unit": "MB/s", "where": "path"}]
    assert pr.ceiling(network, 1, 10**9, remote=True) == (
        "network path: 125 MB/s measured receiver goodput -> ~8.0 s floor"
    )
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    assert run["schema"] == 3 and run["filesystems"]["destination"] and run["host"]["kernel"]
    assert len(run["tools"]["rsync"]["sha256"]) == 64 and run["tools"]["rsync"]["binary"].startswith("/")
    assert [p["name"] for p in run["probes"]] == ["seq_write", "seq_read", "metadata"] * 2
    assert {p["where"] for p in run["probes"]} == {"destination", "source"}


def test_run_refuses_nonempty_destination(tmp_path):
    (tmp_path / "dst").mkdir()
    (tmp_path / "dst" / "keep").write_text("mine")
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    with pytest.raises(SystemExit):
        main(["run", str(spec), "-y"])
    assert (tmp_path / "dst" / "keep").read_text() == "mine"


def test_incremental(tmp_path):
    spec = tmp_path / "t.toml"
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            'kind = "small-files"', 'kind = "small-files"\nchanged = 0.1'
        )
    )
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    small = {c["tool"]: c for c in run["cases"] if c["workload"] == "small"}
    assert "no change detection" in small["cp"]["skipped"] and small["cp"]["repeats"] == []
    r = small["rsync"]
    assert r["mode"] == "incremental" and r["prepopulated_with"] == "cp"
    rep = r["repeats"][0]
    assert rep["changed_files"] == 2 and rep["changed_bytes"] == 2 * 10240
    assert rep["verified"] is True and rep["delta_seed"] is not None
    assert {c["mode"] for c in run["cases"] if c["workload"] == "big"} == {"fresh"}


def test_snapshot_removed_when_a_repeat_fails(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            'kind = "small-files"', 'kind = "small-files"\nchanged = 0.1'
        )
    )
    import subprocess as sp_

    real = sp_.run

    def boom(argv, *a, **k):
        if argv[0] == "cp" and "dst" in str(argv[-1]):
            raise OSError("disk on fire")  # prepopulation into the destination fails after the snapshot exists
        return real(argv, *a, **k)

    monkeypatch.setattr(sp_, "run", boom)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 1
    run = json.loads(out.read_text())
    assert any("disk on fire" in (c["error"] or "") for c in run["cases"])
    assert not any((tmp_path / "src").iterdir()), "snapshot or fixture left behind"
    assert not any((tmp_path / "dst").iterdir())


def test_snapshot_removed_when_change_or_checksum_fails(tmp_path, monkeypatch):
    spec = tmp_path / "t.toml"
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            'kind = "small-files"', 'kind = "small-files"\nchanged = 0.1'
        )
    )
    monkeypatch.setattr(fx, "modify_fraction", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        main(["run", str(spec), "-y", "--out", str(tmp_path / "r.json"), "--set", "protocol.repeats=1"])
    assert not any((tmp_path / "src").iterdir()), "snapshot or fixture left behind"


def test_modify_fraction_changes_content_and_mtime(tmp_path):
    fx.make("small-files", "s", 10 * 4096, 10).generate(tmp_path, seed=3)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in fx.walk_files(tmp_path)}
    n, nbytes = fx.modify_fraction(tmp_path, 0.3, seed=4)
    assert (n, nbytes) == (3, 3 * 4096)
    changed = [p for p in before if p.read_bytes() != before[p][0]]
    assert len(changed) == 3 and all(p.stat().st_mtime_ns > before[p][1] for p in changed)
    assert fx.tree_bytes(tmp_path) == (10, 10 * 4096)


def test_incremental_workload_doubles_scratch_footprint():
    raw = {
        "endpoints": {"source": "/s", "destination": "/d"},
        "tools": [{"name": "rsync"}],
        "workloads": [
            {"name": "a", "kind": "small-files", "files": 100, "bytes": 100_000},
            {"name": "b", "kind": "small-files", "files": 100, "bytes": 100_000, "changed": 0.1},
        ],
    }
    a, b = sp.from_dict(raw).workloads
    assert (a.scratch_factor(), b.scratch_factor()) == (1, 2)


def _two_runs(tmp_path):
    from syq_bench import analysis

    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    outs = []
    for i in range(2):
        out = tmp_path / f"r{i}.json"
        assert (
            main(
                [
                    "run",
                    str(spec),
                    "-y",
                    "--out",
                    str(out),
                    "--set",
                    "protocol.repeats=2",
                    "--set",
                    "protocol.probes=false",
                ]
            )
            == 0
        )
        outs.append(out)
    return outs, analysis


def test_probes_skip_unowned_source_scratch(tmp_path):
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "keep").write_bytes(b"x")
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / ".probe.seq").write_bytes(b"precious")
    spec = tmp_path / "t.toml"
    spec.write_text(
        f'[endpoints]\nsource = "{tmp_path / "scratch"}"\ndestination = "{tmp_path / "dst"}"\n'
        f'[[tools]]\nname = "cp"\n[[workloads]]\nname = "mine"\npath = "{tmp_path / "user"}"\n'
    )
    from syq_bench import runner as rn

    # Pretend the destination is remote for the probe step only, so the probe would target the source scratch.
    orig = rn.Runner.probe

    def probe_src(self):
        local = self.dst_root
        self.dst_root = Location(local.path, "fakehost", local.ssh)
        try:
            return orig(self)
        finally:
            self.dst_root = local

    rn.Runner.probe = probe_src
    out = tmp_path / "r.json"
    try:
        assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    finally:
        rn.Runner.probe = orig
    assert (tmp_path / "scratch" / ".probe.seq").read_bytes() == b"precious"
    run = json.loads(out.read_text())
    assert {p["where"] for p in run["probes"]} <= {"destination"} and any(
        "source storage not probed" in u for u in run["uncontrolled"]
    )
    c = run["cases"][0]
    assert c["source"] == str(tmp_path / "user") and c["source_fs"]


def test_fail_on_regression_counts_broken_after(tmp_path, capsys):
    outs, analysis = _two_runs(tmp_path)
    broken = json.loads(outs[1].read_text())
    for c in broken["cases"]:
        for r in c["repeats"]:
            r["verified"] = False
    outs[1].write_text(json.dumps(broken))
    assert main(["compare", str(outs[0]), str(outs[1]), "--fail-on-regression"]) == 1
    assert "REGRESSION" in capsys.readouterr().out


def test_compare_keeps_argument_order_and_gates_missing_or_aborted(tmp_path, capsys):
    outs, analysis = _two_runs(tmp_path)
    for i, out in enumerate(outs):  # make the timestamps unambiguous
        d = json.loads(out.read_text())
        d["started"] = f"2026-01-0{i + 1}T00:00:00+00:00"
        out.write_text(json.dumps(d))
    older, newer = analysis.load(outs)
    # Reversed order must compare newer -> older, not silently re-sort.
    capsys.readouterr()
    assert main(["compare", str(outs[1]), str(outs[0])]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"before: {outs[1]}") and "BEFORE is newer" in out
    # A case that vanished from the after run, and an aborted after run, both fail the gate.
    after = json.loads(outs[1].read_text())
    after["cases"] = [c for c in after["cases"] if c["workload"] != "big"]
    outs[1].write_text(json.dumps(after))
    assert main(["compare", str(outs[0]), str(outs[1]), "--fail-on-regression"]) == 1
    assert "missing from the after run" in capsys.readouterr().out
    after["aborted"] = "destination cleanup failed"
    outs[1].write_text(json.dumps(after))
    assert "aborted" in " ".join(analysis.gate(older, after))
    # One bad repeat among good ones is not "verified".
    partial = json.loads(outs[0].read_text())
    partial["cases"][0]["repeats"][0]["verified"] = False
    assert not analysis.summaries(partial)[0].verified_all


def test_key_includes_filesystems():
    from syq_bench.analysis import summaries

    run = {
        "spec": {"name": "s"},
        "started": "2026-01-01T00:00:00+00:00",
        "host": {"hostname": "h"},
        "destination": "/d",
        "filesystems": {"source": "ext2/ext3", "destination": "tmpfs"},
        "tools": {},
        "cases": [{"workload": "w", "tool": "t", "bytes": 1, "files": 1, "repeats": [], "source_fs": "nfs"}],
    }
    k = summaries(run)[0].key
    assert (k.source_fs, k.dest_fs) == ("nfs", "tmpfs") and "nfs" in k.where()
    run["cases"][0]["bytes"] = 2
    assert summaries(run)[0].key != k  # a different size is a different series


def test_comparison_standings_are_cautious_and_show_large_slowdowns():
    from syq_bench.analysis import Key, Summary, standings

    key = Key("s", "h", "ext4", "local", "ext4", "w", 1, 1, "fresh", "evict", "t")

    def result(median, low, high, *, verified=True, too_short=False, actual_caches=("evict",)):
        return Summary(
            key,
            "2026-01-01T00:00:00+00:00",
            3,
            median,
            low,
            high,
            1.0,
            None,
            None,
            None,
            too_short,
            "abc",
            actual_caches,
            verified,
        )

    ranked = standings(
        [
            result(1, 1, 1, verified=False),  # fastest raw number is excluded: it was not fully verified
            result(5, 5, 5, actual_caches=("evict-failed",)),
            result(10, 9, 11),
            result(12, 10.5, 13),  # >5%, but its range overlaps the fastest trustworthy result
            result(15, 14, 16),
            result(21, 20, 22),
            result(2, 2, 2, too_short=True),  # short but real: it ranks, and it is the fastest
        ]
    )
    assert [r.verdict for r in ranked] == [
        "n/a",
        "n/a",
        "much-slower",
        "much-slower",
        "much-slower",
        "much-slower",
        "comparable",
    ]
    assert ranked[6].fastest and ranked[6].leader
    assert ranked[5].ratio == 10.5
    assert "verified" in ranked[0].reason
    assert "cache eviction failed" in ranked[1].reason


def test_compare_and_report(tmp_path, capsys):
    outs, analysis = _two_runs(tmp_path)
    before, after = analysis.load(outs)
    deltas = analysis.compare(before, after)
    assert {(d.key.workload, d.key.tool) for d in deltas} == {
        ("small", "rsync"),
        ("small", "cp"),
        ("big", "rsync"),
        ("big", "cp"),
    }
    assert all(d.verdict in ("noise", "faster", "slower") for d in deltas)
    assert main(["compare", str(outs[0]), str(outs[1])]) == 0
    assert "verdict" in capsys.readouterr().out
    # A manufactured 3x slowdown with disjoint ranges is a regression.
    slow = json.loads(outs[1].read_text())
    for c in slow["cases"]:
        for r in c["repeats"]:
            r["wall_s"] *= 3
    outs[1].write_text(json.dumps(slow))
    assert main(["compare", str(outs[0]), str(outs[1]), "--fail-on-regression"]) == 1
    assert "REGRESSION" in capsys.readouterr().out
    assert analysis.spec_diff({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}}) == ["b.c: 2 -> 3"]
    html_out = tmp_path / "report.html"
    assert main(["report", str(tmp_path), "-o", str(html_out)]) == 0
    page = html_out.read_text()
    assert page.startswith("<!doctype html>") and "<svg" in page and "Trends across 2 runs" in page
    assert "<script" not in page and "http" not in page.split("<body>")[1].lower()
    for word in ("Benchmark comparison", "benchmark case(s)", "rsync", "cp", "small", "big", "sha256"):
        assert word in page

    # A changed binary with the same friendly tool name becomes another system column; repeats of
    # an unchanged binary collapse to the newest measurement instead.
    changed = json.loads(outs[1].read_text())
    changed["tools"]["rsync"]["version"] = "rsync future"
    changed["tools"]["rsync"]["sha256"] = "f" * 64
    rows = analysis.comparison_rows([before, after, changed])
    small = next(measurements for key, measurements in rows.items() if key.workload == "small")
    rsyncs = [measurement for system, measurement in small.items() if system.tool == "rsync"]
    assert sorted(measurement.seen for measurement in rsyncs) == [1, 2]
    tuned = json.loads(json.dumps(after))
    next(tool for tool in tuned["spec"]["tools"] if tool["name"] == "rsync")["args"] = ["-a", "--whole-file"]
    configured = analysis.comparison_rows([after, tuned])
    small = next(measurements for key, measurements in configured.items() if key.workload == "small")
    assert len([system for system in small if system.tool == "rsync"]) == 2

    # A remote result without a recorded remote identity is not the same exact system in another run.
    remote_before, remote_after = json.loads(json.dumps(before)), json.loads(json.dumps(after))
    for run in (remote_before, remote_after):
        run["destination"] = "remote:/d"
        for tool in run["tools"].values():
            tool.pop("remote", None)
    remote_rows = analysis.comparison_rows([remote_before, remote_after])
    small = next(measurements for key, measurements in remote_rows.items() if key.workload == "small")
    remote_rsyncs = [(system, measurement) for system, measurement in small.items() if system.tool == "rsync"]
    assert len(remote_rsyncs) == 2
    assert all(system.remote_unknown and measurement.seen == 1 for system, measurement in remote_rsyncs)
    from syq_bench.html import render_html

    remote_page = render_html([remote_before, remote_after])
    assert "remote identity unrecorded (run " in remote_page

    # Version and diagnostic metadata are useful context, but only a remote SHA is an exact
    # identity. Without it, each run remains separate even when its note is shared.
    unverified_before, unverified_after = (
        json.loads(json.dumps(remote_before)),
        json.loads(json.dumps(remote_after)),
    )
    for version, run in zip(("rsync remote 1", "rsync remote 2"), (unverified_before, unverified_after), strict=True):
        run["tools"]["rsync"]["remote"] = {
            "version": version,
            "note": "sha256sum unavailable",
        }
    unverified_rows = analysis.comparison_rows([unverified_before, unverified_after])
    small = next(measurements for key, measurements in unverified_rows.items() if key.workload == "small")
    unverified_rsyncs = [(system, measurement) for system, measurement in small.items() if system.tool == "rsync"]
    assert len(unverified_rsyncs) == 2
    assert all(system.remote_unknown and measurement.seen == 1 for system, measurement in unverified_rsyncs)

    # Moving result files cannot change public system identities or leak a path-derived token.
    identities = sorted((system.tool, system.identity, system.run_id) for system in small)
    unverified_before["_path"] = "/private/results/guessed-before.json"
    unverified_after["_path"] = "/moved/results/guessed-after.json"
    moved_rows = analysis.comparison_rows([unverified_before, unverified_after])
    moved_small = next(measurements for key, measurements in moved_rows.items() if key.workload == "small")
    moved_identities = sorted((system.tool, system.identity, system.run_id) for system in moved_small)
    assert moved_identities == identities
    assert {system.run_id for system in moved_small} == {"1", "2"}

    known_before, known_after = json.loads(json.dumps(remote_before)), json.loads(json.dumps(remote_after))
    for run in (known_before, known_after):
        run["tools"]["rsync"]["remote"] = {"sha256": "a" * 64, "version": "rsync remote"}
    known_rows = analysis.comparison_rows([known_before, known_after])
    small = next(measurements for key, measurements in known_rows.items() if key.workload == "small")
    known_rsyncs = [(system, measurement) for system, measurement in small.items() if system.tool == "rsync"]
    assert len(known_rsyncs) == 1 and known_rsyncs[0][1].seen == 2

    # A failed eviction remains visible, but cannot become the fastest rankable result.
    cache_failed = json.loads(json.dumps(after))
    case = next(c for c in cache_failed["cases"] if c["workload"] == "small" and c["tool"] == "rsync")
    case["repeats"][0]["cache"] = "evict-failed"
    summary = next(s for s in analysis.summaries(cache_failed) if s.key.workload == "small" and s.key.tool == "rsync")
    assert summary.actual_caches == ("evict", "evict-failed")
    assert "cache eviction failed" in analysis.standings([summary])[0].reason
    assert "actual cache: evict, evict-failed" in render_html([cache_failed])
    for repeat in case["repeats"]:
        repeat["cache"] = "warm"
    mismatch_summary = next(
        s for s in analysis.summaries(cache_failed) if s.key.workload == "small" and s.key.tool == "rsync"
    )
    mismatched = analysis.standings([mismatch_summary])[0]
    assert "does not match requested evict" in mismatched.reason


def test_probe_failures_clean_up_and_ceiling_uses_slowest_end(tmp_path, monkeypatch):
    from syq_bench import probes as pr

    real_open = os.open

    def no_direct_read(path, flags, *a):
        if flags & os.O_DIRECT and not flags & os.O_WRONLY:
            raise OSError(22, "Invalid argument")
        return real_open(path, flags, *a)

    monkeypatch.setattr(os, "open", no_direct_read)
    pr.seq_write(tmp_path)
    p = pr.seq_read(tmp_path)
    assert p.value is None and not (tmp_path / ".probe.seq").exists()
    monkeypatch.undo()
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(5, "I/O error")))
    m = pr.metadata(tmp_path)
    assert m.value is None and "failed" in m.detail and not (tmp_path / ".probe.meta").exists()
    monkeypatch.undo()
    probes = [
        {"name": "seq_write", "value": 1000.0, "unit": "MB/s", "detail": "", "where": "destination"},
        {"name": "seq_read", "value": 10.0, "unit": "MB/s", "detail": "", "where": "source"},
    ]
    line = pr.ceiling(probes, 1, 10**9)
    assert line.startswith("source storage-bound") and "~100.0 s" in line


def test_partial_failure_is_visible_in_reports(tmp_path, capsys):
    outs, analysis = _two_runs(tmp_path)
    run = json.loads(outs[0].read_text())
    run["cases"][0]["repeats"][0]["verified"] = False
    outs[0].write_text(json.dumps(run))
    from syq_bench.html import render_html
    from syq_bench.report import render
    from syq_bench.runner import Case, Repeat, Run

    page = render_html([run])
    assert "1 of 2 repeats FAILED" in page
    r = Run(**{k: v for k, v in run.items() if not k.startswith("_") and k != "cases"})
    r.cases = [Case(**{**c, "repeats": [Repeat(**rep) for rep in c["repeats"]]}) for c in run["cases"]]
    text = render(r)
    assert "1 of 2 repeats FAILED" in text


def test_netem_drop_counters_and_missing_tools(monkeypatch):
    from syq_bench import probes as pr

    dump = (
        "qdisc prio 1: root refcnt 2 bands 4\n Sent 10 bytes 1 pkt (dropped 0, overlimits 0 requeues 0)\n"
        "qdisc netem 40: parent 1:4 limit 1000 loss 1%\n Sent 999 bytes 9 pkt (dropped 7, overlimits 0 requeues 0)\n"
    )
    assert pr.netem_drops(dump) == 7
    assert pr.netem_drops("qdisc fq 0: root\n Sent 1 bytes 1 pkt (dropped 0)") is None
    assert pr.netem_drops(None) is None
    # No ip/tc/ping on this "host": everything is None and listed as missing, nothing raises.
    monkeypatch.setattr(pr, "_run", lambda argv: None)

    class R:
        returncode = 127
        stdout = ""

    state = pr.netstate("eth0", lambda script, check: R(), "user@far")
    assert state["local_qdisc"] is None and "remote_qdisc" in state["missing"] and "ping" in state["missing"]


def test_remote_identity_labelled_fields():
    from syq_bench.runner import remote_identity
    from syq_bench.spec import ToolSpec
    from syq_bench.tools import Tool

    class Loc:
        host = "h"

        def __init__(self, out, rc=0):
            self.out, self.rc = out, rc

        def sh(self, script, check=True):
            class R:
                pass

            r = R()
            r.returncode, r.stdout = self.rc, self.out
            return r

    t = Tool(ToolSpec("rsync", "rsync", "rsync", ("-a",), None))
    ok = remote_identity(t, Loc("BIN=/usr/bin/rsync\nSHA=" + "a" * 64 + "\nVER=rsync 3.2.7\n"))
    assert ok == {"binary": "/usr/bin/rsync", "sha256": "a" * 64, "version": "rsync 3.2.7"}
    no_sha = remote_identity(t, Loc("BIN=/usr/bin/rsync\nVER=rsync 3.2.7\n"))
    assert no_sha["sha256"] is None and no_sha["version"] == "rsync 3.2.7" and "sha256sum" in no_sha["note"]
    assert remote_identity(t, Loc("", rc=3))["note"].startswith("not found")

    syq_no_bootstrap = Tool(ToolSpec("syq", "syq", "syq", ("rsync", "-a", "--syq-no-bootstrap"), None))
    remote_syq = remote_identity(
        syq_no_bootstrap,
        Loc("BIN=/usr/local/bin/syq\nSHA=" + "b" * 64 + "\nVER=syq 0.1.8\n"),
    )
    assert remote_syq == {
        "binary": "/usr/local/bin/syq",
        "sha256": "b" * 64,
        "version": "syq 0.1.8",
    }


def test_historical_no_bootstrap_remote_identity_is_not_mislabelled():
    from syq_bench.html import render_html

    run = {
        "schema": 3,
        "harness_version": "0",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": None,
        "spec": {
            "name": "s",
            "protocol": {},
            "tools": [
                {
                    "name": "syq",
                    "kind": "syq",
                    "args": ["rsync", "-a", "--syq-no-bootstrap"],
                }
            ],
        },
        "seed": 1,
        "host": {"hostname": "h"},
        "tools": {
            "syq": {
                "binary": "syq",
                "version": "syq 0.1.8",
                "sha256": "a" * 64,
                "remote": {"note": "managed helper: same release as the local syq"},
            }
        },
        "source": "/s",
        "destination": "far:/d",
        "filesystems": {},
        "uncontrolled": [],
        "probes": [],
        "network": None,
        "cases": [],
    }

    page = render_html([run])

    assert "remote PATH binary identity not recorded" in page
    assert "managed helper: same release" not in page

    run["tools"]["syq"]["remote"] = {
        "version": "syq remote version recorded",
        "note": "sha256sum unavailable on the remote",
    }
    page_with_partial_identity = render_html([run])

    assert "syq remote version recorded" in page_with_partial_identity
    assert "remote PATH binary identity not recorded" not in page_with_partial_identity


def test_missing_network_state_is_not_reported_clean(tmp_path):
    from syq_bench.html import render_html

    run = {
        "schema": 3,
        "harness_version": "0",
        "started": "2026-01-01T00:00:00+00:00",
        "finished": None,
        "spec": {"name": "s", "protocol": {}},
        "seed": 1,
        "host": {"hostname": "h"},
        "tools": {},
        "source": "/s",
        "destination": "far:/d",
        "filesystems": {},
        "uncontrolled": [],
        "probes": [],
        "network": {"before": {"missing": ["local_dev (route lookup failed)"], "ping": None}},
        "cases": [],
    }
    page = render_html([run])
    assert "not fully inspected" in page and "no netem on either end" not in page
    run["network"] = None
    assert "network state was not recorded" in render_html([run])


def test_mutate_modes(tmp_path):
    fx.make("small-files", "s", 4 * 2**20, 4).generate(tmp_path, seed=3)
    files = sorted(fx.walk_files(tmp_path))
    before = {p: p.read_bytes() for p in files}
    n, nbytes = fx.modify_fraction(tmp_path, 1.0, seed=4, mutate="blocks")
    assert n == 4 and 0 < nbytes <= 4 * 3 * 4096  # ~1 % of 256 blocks -> 2-3 blocks per file
    for p in files:
        after = p.read_bytes()
        assert len(after) == len(before[p]) and after != before[p]
        diff = sum(1 for i in range(0, len(after), 4096) if after[i : i + 4096] != before[p][i : i + 4096])
        assert 1 <= diff <= 4
    n, nbytes = fx.modify_fraction(tmp_path, 0.5, seed=5, mutate="append")
    assert n == 2 and nbytes == 2 * (2**20 // 100)  # 1 % of each 1 MiB file
    with pytest.raises(sp.SpecError):
        sp.from_dict(
            {
                "endpoints": {"source": "/s", "destination": "/d"},
                "tools": [{"name": "rsync"}],
                "workloads": [{"name": "w", "kind": "large-file", "bytes": 1024, "changed": 0.5, "mutate": "nope"}],
            }
        )


def test_source_restored_between_rounds_and_append_bounded(tmp_path):
    # Files large enough that 1 % exceeds the 4 KiB floor: if restoration were missing, the appended
    # size would compound and each repeat's changed_bytes would grow.
    spec = tmp_path / "t.toml"
    spec.write_text(
        f'''
[endpoints]
source = "{tmp_path / "src"}"
destination = "{tmp_path / "dst"}"
[[tools]]
name = "rsync"
[[workloads]]
name = "big"
kind = "small-files"
files = 2
bytes = "2MiB"
changed = 1.0
mutate = "append"
[protocol]
repeats = 3
'''
    )
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out)]) == 0
    run = json.loads(out.read_text())
    case = next(c for c in run["cases"] if c["tool"] == "rsync")
    per_repeat = [r["changed_bytes"] for r in case["repeats"]]
    base = 2 * (2**20 // 100)  # 1 % of each 1 MiB file, above the 4 KiB floor
    # Without restoration repeat 2 would append 1 % of 1.01 MiB (10590 per file), not 10485.
    assert per_repeat == [base, base, base]
    assert not any((tmp_path / "src").iterdir())


def test_append_growth_budgeted():
    w = sp.from_dict(
        {
            "endpoints": {"source": "/s", "destination": "/d"},
            "tools": [{"name": "rsync"}],
            "workloads": [
                {"name": "a", "kind": "small-files", "files": 100_000, "bytes": 100_000},
                {
                    "name": "b",
                    "kind": "small-files",
                    "files": 100_000,
                    "bytes": 100_000,
                    "changed": 1.0,
                    "mutate": "append",
                },
            ],
        }
    ).workloads
    plain, appending = w
    # 100k one-byte files appending the 4 KiB minimum each need ~400 MB more than the plain tree.
    assert appending.disk_bytes() - plain.disk_bytes() >= 100_000 * sp.BLOCK
    # Incremental destinations hold the basis plus a full temporary copy: two trees' worth.
    assert appending.dest_factor() == 2 and plain.dest_factor() == 1


def test_too_short_wording():
    import dataclasses
    import json as j

    from syq_bench.html import render_html
    from syq_bench.report import render
    from syq_bench.runner import Case, Repeat, Run

    def mk(walls_by_tool):
        base = {
            "schema": 3,
            "harness_version": "0",
            "started": "2026-01-01T00:00:00+00:00",
            "finished": None,
            "spec": {"name": "s", "protocol": {}},
            "seed": 1,
            "host": {"hostname": "h", "platform": "p"},
            "tools": {},
            "source": "/s",
            "destination": "/d",
            "filesystems": {},
            "uncontrolled": [],
        }
        r = Run(**base)
        for tool, wall in walls_by_tool.items():
            c = Case("w", tool, [], 2**30, 1, too_short=wall < 2.0)
            c.repeats = [Repeat(0, wall, 0, 0, 0, True, None, None, "evict", "warm")]
            r.cases.append(c)
        return r

    mixed = render(mk({"syq": 1.2, "rsync": 6.7}))
    assert "lower bound" in mixed and "ratios remain valid" in mixed and "says little" not in mixed
    allshort = render(mk({"syq": 0.5, "rsync": 0.8}))
    assert "says little" in allshort and "scale the workload up" in allshort
    capacity = mk({"syq": 10.0})
    capacity.cases[0].bytes = 1_000_000_000
    capacity.probes = [{"name": "network_receive", "value": 100.0, "unit": "MB/s", "detail": "test", "where": "path"}]
    assert "syq: end-to-end rate is 100% of the measured network ceiling" in render(capacity)
    capacity_page = render_html([j.loads(j.dumps(dataclasses.asdict(capacity)))])
    assert "100% of measured network ceiling, end-to-end" in capacity_page
    capacity.probes = []
    assert "of the measured network ceiling" not in render(capacity)
    incremental = mk({"rsync": 3.0})
    incremental.cases[0].mode = "incremental"
    incremental.cases[0].prepopulated_with = "rsync"
    incremental.cases[0].repeats[0].changed_files = 1
    incremental.cases[0].repeats[0].changed_bytes = 4096
    incremental.probes = [
        {"name": "network_receive", "value": 100.0, "unit": "MB/s", "detail": "test", "where": "path"}
    ]
    incremental_text = render(incremental)
    assert "logical changes, not measured wire bytes" in incremental_text
    assert "likely ceiling" not in incremental_text
    assert "of the measured network ceiling" not in incremental_text
    incremental_page = render_html([j.loads(j.dumps(dataclasses.asdict(incremental)))])
    assert "logical changes, not measured wire bytes" in incremental_page
    assert "likely ceiling" not in incremental_page
    assert "of measured network ceiling" not in incremental_page
    page = render_html([j.loads(j.dumps(dataclasses.asdict(mk({"syq": 1.2, "rsync": 6.7}))))])
    assert "lower bound" in page and "says little" not in page
    allshort_page = render_html([j.loads(j.dumps(dataclasses.asdict(mk({"syq": 0.5, "rsync": 0.8}))))])
    assert "says little" in allshort_page
    # A fast success next to a failed comparator is NOT "every tool finished"; integrity notes survive
    # the all-short wording.
    r = mk({"syq": 0.5, "rsync": 0.8})
    r.cases[1].error = "exit 23: boom"
    r.cases[1].repeats[0].exit_code = 23
    r.cases[0].repeats[0].cache = "evict-failed"
    text = render(r)
    assert "says little" not in text and "eviction failed" in text
    page = render_html([j.loads(j.dumps(dataclasses.asdict(r)))])
    assert "says little" not in page
    short_with_warm = mk({"syq": 0.5, "rsync": 0.8})
    short_with_warm.cases[0].repeats[0].cache = "evict-failed"
    assert "eviction failed" in render(short_with_warm)


def test_comparison_page_ranks_short_winner():
    from syq_bench.analysis import Summary, standings
    from syq_bench.spec import WorkloadSpec  # noqa: F401  (import guard for the branch layout)

    def summ(tool, med, short):
        from syq_bench.analysis import Key

        key = Key("s", "h", "ext4", "local", "ext4", "w", 1, 1, "fresh", "evict", tool)
        return Summary(
            key,
            "2026-01-01",
            2,
            med,
            med,
            med,
            1.0,
            None,
            None,
            None,
            short,
            "id",
            actual_caches=("evict",),
            verified_all=True,
            failed_repeats=0,
        )

    st = standings([summ("syq", 1.2, True), summ("rsync", 6.7, False)])
    assert st[0].fastest and st[0].verdict == "comparable" and st[1].ratio > 5
    # All-short row: ranking would be startup noise; the row stays visibly weak instead.
    st = standings([summ("syq", 0.5, True), summ("rsync", 0.8, True)])
    assert all(x.verdict == "n/a" and "says little" in x.reason for x in st)


def test_tool_timeout_and_slow_cutoff(tmp_path, monkeypatch):
    from syq_bench.runner import TIMED_OUT, _timed

    # The time budget kills the whole group and reports TIMED_OUT.
    wall, _u, _s, code, _t = _timed(["sh", "-c", "sleep 30"], print, heartbeat_s=60, timeout_s=0.4)
    assert code == TIMED_OUT and wall < 5

    # A tool 10x slower than the fastest is measured once, then not repeated - but still reported.
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    from syq_bench import runner as rn

    real = rn._timed

    def slow_cp(argv, log=print, heartbeat_s=30, timeout_s=None):
        wall, u, s_, code, tail = real(argv, log, heartbeat_s, timeout_s)
        if argv[0] == "cp":
            wall += 100.0  # pretend cp took ~100 s
        return wall, u, s_, code, tail

    monkeypatch.setattr(rn, "_timed", slow_cp)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=3"]) == 0
    run = json.loads(out.read_text())
    cp_case = next(c for c in run["cases"] if c["tool"] == "cp" and c["workload"] == "small")
    assert cp_case["curtailed"] and "no further repeats" in cp_case["curtailed"]
    assert len(cp_case["repeats"]) == 1  # measured once
    rsync_case = next(c for c in run["cases"] if c["tool"] == "rsync" and c["workload"] == "small")
    assert len(rsync_case["repeats"]) == 3
    from syq_bench.report import render as render_text
    from syq_bench.runner import Case, Repeat, Run

    r = Run(**{k: v for k, v in run.items() if not k.startswith("_") and k != "cases"})
    r.cases = [Case(**{**c, "repeats": [Repeat(**rep) for rep in c["repeats"]]}) for c in run["cases"]]
    text = render_text(r)
    assert "no further repeats" in text and "order of magnitude" in text


def test_cutoff_is_order_independent(tmp_path, monkeypatch):
    """A slow tool listed BEFORE the fast one is retired as soon as the fast result exists."""
    spec = tmp_path / "t.toml"
    # cp first, rsync second; cp is the slow one.
    spec.write_text(
        SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst").replace(
            '[[tools]]\nname = "rsync"\n[[tools]]\nname = "cp"', '[[tools]]\nname = "cp"\n[[tools]]\nname = "rsync"'
        )
    )
    from syq_bench import runner as rn

    real = rn._timed

    def slow_cp(argv, log=print, heartbeat_s=30, timeout_s=None):
        wall, u, s_, code, tail = real(argv, log, heartbeat_s, timeout_s)
        return (wall + 100.0 if argv[0] == "cp" else wall), u, s_, code, tail

    monkeypatch.setattr(rn, "_timed", slow_cp)
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=2"]) == 0
    run = json.loads(out.read_text())
    cp_case = next(c for c in run["cases"] if c["tool"] == "cp" and c["workload"] == "small")
    assert len(cp_case["repeats"]) == 1 and cp_case["curtailed"]


def test_budget_wall_is_the_budget_moment(monkeypatch):
    from syq_bench.runner import TIMED_OUT, _timed

    # A TERM-ignoring child must not stretch the recorded wall past the budget.
    wall, _u, _s, code, _t = _timed(["sh", "-c", "trap '' TERM; sleep 30"], print, heartbeat_s=60, timeout_s=0.3)
    assert code == TIMED_OUT and wall < 1.0


def test_curtailment_metadata_is_never_false(tmp_path, monkeypatch):
    """repeats=1: every planned repeat ran, so nothing is 'curtailed' however slow a tool was."""
    from syq_bench import runner as rn

    real = rn._timed

    def slow_cp(argv, log=print, heartbeat_s=30, timeout_s=None):
        wall, u, s_, code, tail = real(argv, log, heartbeat_s, timeout_s)
        return (wall + 100.0 if argv[0] == "cp" else wall), u, s_, code, tail

    monkeypatch.setattr(rn, "_timed", slow_cp)
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=1"]) == 0
    run = json.loads(out.read_text())
    assert all(c["curtailed"] is None for c in run["cases"])
    base = {
        "endpoints": {"source": "/s", "destination": "/d"},
        "tools": [{"name": "rsync"}],
        "workloads": [{"name": "w", "kind": "large-file", "bytes": 1024}],
    }
    # Sub-unit cutoffs would curtail the fastest tool; they are rejected. Zero disables.
    with pytest.raises(sp.SpecError, match="slow_cutoff"):
        sp.from_dict({**base, "protocol": {"slow_cutoff": 0.5}})
    assert sp.from_dict({**base, "protocol": {"slow_cutoff": 0}}).protocol.slow_cutoff == 0


def test_plan_and_html_preserve_control_values():
    from syq_bench.analysis import Key, Measurement, Standing, Summary, System
    from syq_bench.cli import plan_lines
    from syq_bench.html import _result_cell
    from syq_bench.tools import Tool

    spec = sp.from_dict(
        {
            "endpoints": {"source": "/s", "destination": "/d"},
            "tools": [{"name": "rsync"}],
            "workloads": [{"name": "w", "kind": "large-file", "bytes": 1024}],
            "protocol": {"slow_cutoff": 1.5, "tool_timeout": 0.3, "repeats": 3},
        }
    )
    text = "\n".join(plan_lines(spec, [Tool(t) for t in spec.tools]))
    assert "up to 3 repeat(s)" in text and ">= 1.5x" in text and "tool_timeout=0.3s" in text
    key = Key("s", "h", "ext4", "local", "ext4", "w", 1, 1, "fresh", "evict", "cp")
    summary = Summary(
        key,
        "2026-01-01",
        2,
        100.0,
        99.0,
        101.0,
        1.0,
        None,
        None,
        None,
        False,
        "id",
        actual_caches=("evict",),
        curtailed="no further repeats: >= 10x slower (100 s vs 1.0 s); measured 2 times as the order of magnitude",
    )
    system = System("cp", None, "id", "id", False, "", False, "run")
    cell = _result_cell(system, Measurement(summary, {}, 1), Standing("much-slower", 100.0))
    assert "measured 2 times" in cell and "measured once" not in cell


def test_curtailment_respects_timing_floor_and_validation(tmp_path, monkeypatch):
    from syq_bench import runner as rn

    real = rn._timed

    def stretched(argv, log=print, heartbeat_s=30, timeout_s=None):
        wall, u, s_, code, tail = real(argv, log, heartbeat_s, timeout_s)
        # cp "takes" 1.0 s, rsync 0.1 s: 10x apart but both under the floor - no curtailment.
        return (1.0 if argv[0] == "cp" else 0.1), u, s_, code, tail

    monkeypatch.setattr(rn, "_timed", stretched)
    spec = tmp_path / "t.toml"
    spec.write_text(SPEC.format(src=tmp_path / "src", dst=tmp_path / "dst"))
    out = tmp_path / "r.json"
    assert main(["run", str(spec), "-y", "--out", str(out), "--set", "protocol.repeats=2"]) == 0
    run = json.loads(out.read_text())
    assert all(c["curtailed"] is None for c in run["cases"])
    base = {
        "endpoints": {"source": "/s", "destination": "/d"},
        "tools": [{"name": "rsync"}],
        "workloads": [{"name": "w", "kind": "large-file", "bytes": 1024}],
    }
    for bad in (
        {"tool_timeout": float("inf")},
        {"tool_timeout": float("nan")},
        {"slow_cutoff": 1},
        {"slow_cutoff": float("inf")},
    ):
        with pytest.raises(sp.SpecError):
            sp.from_dict({**base, "protocol": bad})
    assert sp.from_dict({**base, "protocol": {"slow_cutoff": 1.5, "tool_timeout": 0.3}}).protocol.slow_cutoff == 1.5


def test_comparison_overview_collapses_builds_to_tool(tmp_path):
    """Two builds of syq on the same benchmark: the overview shows one syq column with the newest, and the
    exact grid still has both."""
    from syq_bench.analysis import comparison_rows, current_rows
    from syq_bench.html import render_html

    outs, analysis = _two_runs(tmp_path)
    runs = analysis.load(outs)
    # Pretend the second run used a different rsync build (identity differs) and is newer.
    runs[1]["tools"]["rsync"]["sha256"] = "b" * 64
    runs[1]["started"] = "2026-02-01T00:00:00+00:00"
    runs[0]["started"] = "2026-01-01T00:00:00+00:00"
    rows = comparison_rows(runs)
    exact_rsync_cols = {s for m in rows.values() for s in m if s.tool == "rsync"}
    assert len(exact_rsync_cols) == 2
    cur = current_rows(rows)
    for benchmark, by_tool in cur.items():
        assert set(by_tool) == {"rsync", "cp"}
        sys, m = by_tool["rsync"]
        assert m.run["started"].startswith("2026-02")  # newest wins
        assert sys in rows[benchmark]  # the original System, never a rebuilt one
    page = render_html(runs)
    overview = page[page.find('id="comparison"') : page.find('<details class="all-builds"')]
    assert 'aria-label="not measured"' not in overview
    assert "bbbbbbbbbbbb" in overview  # the newest build's identity is shown in the cell
    # Provenance in the overview cell covers configuration and an unidentified remote end.
    runs[1]["spec"]["tools"][0]["args"] = ["-a", "--whole-file"]
    runs[1]["destination"] = "far:/d"
    page2 = render_html(runs)
    ov2 = page2[page2.find('id="comparison"') : page2.find('<details class="all-builds"')]
    assert "--whole-file" in ov2 and "remote identity unrecorded (run 2)" in ov2  # the real ordinal, not 1
    # Headline counters describe the overview, not the historical grid.
    strip = page2[page2.find('<div class="summary-strip">') : page2.find('<div class="legend-items">')]
    assert "(overview)" in strip and "syq result(s) in the leading group" in strip


def test_overview_uses_kind_not_name_for_syq(tmp_path):
    import re

    from syq_bench.html import render_html

    outs, analysis = _two_runs(tmp_path)
    run = analysis.load(outs)[0]
    # A tool named 'candidate' whose kind is syq must get syq styling and counting; rsync named
    # 'syq-copy' must not. Rename cases and spec entries consistently.
    for c in run["cases"]:
        c["tool"] = {"rsync": "syq-copy", "cp": "candidate"}[c["tool"]]
    run["tools"] = {"syq-copy": run["tools"]["rsync"], "candidate": run["tools"]["cp"]}
    run["spec"]["tools"] = [{"name": "syq-copy", "kind": "rsync"}, {"name": "candidate", "kind": "syq"}]
    page = render_html([run])
    overview = page[page.find('id="comparison"') : page.find('<details class="all-builds"')]
    headers = re.findall(r'<th class="([^"]*)"><div class="system-name">([^<]*)</div>', overview)
    assert ("system syq-system", "candidate") in headers and ("system", "syq-copy") in headers
