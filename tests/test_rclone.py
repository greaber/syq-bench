"""Rclone routing and the pre-write WebDAV scratch binding."""

import shlex
import subprocess
from pathlib import Path

import pytest

from syq_bench import spec as sp
from syq_bench.cli import main, plan_lines
from syq_bench.remote import Location
from syq_bench.runner import remote_identity
from syq_bench.tools import Tool


def spec_for(backend="local", **settings):
    return sp.from_dict(
        {
            "endpoints": {"source": "/source", "destination": "/destination"},
            "tools": [{"name": "rclone", "rclone_backend": backend, **settings}],
            "workloads": [{"name": "small", "kind": "small-files", "files": 10, "bytes": 100}],
        }
    )


def webdav_tool():
    return Tool(spec_for("webdav", rclone_url="https://example.invalid:8443/", rclone_root="/scratch").tools[0])


def test_rclone_local_paths_and_defaults():
    tool = Tool(spec_for().tools[0])
    cmd = tool.argv(Location.parse("./a:b"), Location.parse("/scratch/result"))
    assert cmd == [
        "rclone",
        "copy",
        "--config",
        "/dev/null",
        "--create-empty-src-dirs",
        "--",
        ":local:a:b",
        ":local:/scratch/result",
    ]
    assert tool.local_ok and not tool.remote_ok
    with pytest.raises(ValueError, match="mounted"):
        tool.argv(Location.parse("/source"), Location.parse("server:/scratch"))


def test_sftp_routes_both_directions_through_harness_ssh():
    tool = Tool(spec_for("sftp-ssh", args=["--transfers", "8"]).tools[0])
    local = Location.parse("/source with spaces")
    remote = Location.parse("user@example.invalid:/scratch/a' $b", ssh="ssh -i '/key with spaces'")
    for src, dst in [(local, remote), (remote, local)]:
        cmd = tool.argv(src, dst)
        assert shlex.split(cmd[cmd.index("--sftp-ssh") + 1]) == remote.ssh_argv()
        assert cmd[-2:] == [tool.rclone_path(src), tool.rclone_path(dst)]
    assert tool.remote_ok and not tool.local_ok
    with pytest.raises(ValueError, match="local endpoint"):
        tool.argv(remote, Location.parse("other:/dst"))
    ident = remote_identity(tool, remote)
    assert ident["binary"] is None and ident["sha256"] is None
    assert "server identity" in ident["note"]


@pytest.mark.parametrize(
    "settings",
    [
        {"rclone_backend": "unknown"},
        {"jobs": 8},
        {"rclone_url": "https://example.invalid"},
        {"args": ["--sftp-ssh", "another-ssh"]},
        {"args": ["--webdav-url", "https://elsewhere.invalid"]},
        {"args": ["--config=elsewhere"]},
        {"args": ["--delete-after"]},
    ],
)
def test_invalid_rclone_settings(settings):
    with pytest.raises(sp.SpecError):
        spec_for(**settings)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://example.invalid",
        "https://u:p@example.invalid/",
        "https://example.invalid/?token=x",
        "https://example.invalid/#x",
        "https://example.invalid:0",
        "https://example.invalid:65536",
        123,
    ],
)
def test_webdav_url_validation(url):
    with pytest.raises(sp.SpecError):
        spec_for("webdav", rclone_url=url, rclone_root="/scratch")


@pytest.mark.parametrize("root", [None, "", "relative", "/scratch/../other", 12])
def test_webdav_root_validation(root):
    with pytest.raises(sp.SpecError):
        spec_for("webdav", rclone_url="https://example.invalid/", rclone_root=root)


def test_webdav_routes_only_beneath_served_root():
    tool = webdav_tool()
    cmd = tool.argv(Location.parse("/source"), Location.parse("server:/scratch/case"))
    assert cmd[-2:] == [":local:/source", ":webdav:case"]
    assert cmd[cmd.index("--webdav-url") + 1] == "https://example.invalid:8443/"
    for dst in ["server:/other", "server:/scratch/../other", "server:/scratch-adjacent/case"]:
        with pytest.raises(ValueError, match="outside"):
            tool.argv(Location.parse("/source"), Location.parse(dst))


@pytest.mark.parametrize("outcome", ["match", "wrong", "error", "timeout", "create-failed"])
def test_webdav_binding_is_read_only_until_marker_matches(monkeypatch, outcome):
    tool = webdav_tool()
    state = {}
    removed = []

    def create_marker(self, script):
        if outcome == "create-failed":
            raise subprocess.CalledProcessError(1, "create")
        fields = shlex.split(script.removeprefix("(set -C; ").removesuffix(")"))
        state["token"] = fields[2]
        state["path"] = fields[4]
        assert fields[0:2] == ["printf", "%s"]

    def read_marker(argv, **kwargs):
        assert argv[1] == "cat" and kwargs["timeout"] == 30
        assert "--create-empty-src-dirs" not in argv
        assert argv[-1] == ":webdav:case/" + Path(state["path"]).name
        if outcome == "error":
            raise subprocess.CalledProcessError(1, argv)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        return subprocess.CompletedProcess(argv, 0, stdout=state["token"] if outcome == "match" else "wrong")

    monkeypatch.setattr(Location, "sh", create_marker)
    monkeypatch.setattr(Location, "run", lambda self, argv: removed.append(argv))
    monkeypatch.setattr(subprocess, "run", read_marker)
    if outcome == "match":
        tool.check_destination(Location.parse("server:/scratch/case"))
    else:
        with pytest.raises((OSError, subprocess.SubprocessError)):
            tool.check_destination(Location.parse("server:/scratch/case"))
    assert removed == ([] if outcome == "create-failed" else [["rm", "-f", "--", state["path"]]])


def test_network_plan_has_actual_transport():
    spec = spec_for("sftp")
    # The local endpoints cannot silently turn an SFTP experiment into a local copy.
    with pytest.raises(ValueError, match="remote endpoint"):
        plan_lines(spec, [Tool(spec.tools[0])])


def test_invalid_webdav_route_fails_before_scratch_creation(tmp_path):
    src = tmp_path / "source"
    config = tmp_path / "spec.toml"
    config.write_text(f'''
[endpoints]
source = "{src}"
destination = "server:/wrong-root"
[[tools]]
name = "rclone"
rclone_backend = "webdav"
rclone_url = "https://example.invalid/"
rclone_root = "/scratch"
[[workloads]]
name = "small"
kind = "small-files"
files = 10
bytes = 100
''')
    with pytest.raises(SystemExit) as exc:
        main(["run", str(config), "--yes"])
    assert exc.value.code == 2
    assert not src.exists()


def test_internal_sftp_keeps_engine_and_checks_endpoint(monkeypatch):
    tool = Tool(spec_for("sftp", args=["--sftp-host", "actual.example", "--sftp-port", "2222"]).tools[0])
    remote = Location.parse("person@ssh-alias:/scratch/case", ssh="ssh -i /special/key")
    cmd = tool.argv(Location.parse("/source"), remote)
    assert "--sftp-ssh" not in cmd
    assert cmd[cmd.index("--sftp-user") + 1] == "person"
    assert cmd[cmd.index("--sftp-known-hosts-file") + 1] == str(Path.home() / ".ssh/known_hosts")
    assert cmd[-6:-2] == ["actual.example", "--sftp-port", "2222", "--"]
    created, removed = [], []
    monkeypatch.setattr(Location, "sh", lambda self, script: created.append(script))
    monkeypatch.setattr(Location, "run", lambda self, argv: removed.append(argv))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="wrong"))
    with pytest.raises(OSError, match="owned destination"):
        tool.check_destination(remote)
    assert len(created) == len(removed) == 1


def test_failed_binding_prevents_timing_and_marks_result_failed(monkeypatch, tmp_path):
    from syq_bench import runner as runner_module
    from syq_bench.runner import Case, Runner

    spec = spec_for()
    tool = Tool(spec.tools[0])
    src, dst = Location(tmp_path / "src"), Location(tmp_path / "dst")
    src.mkdir()
    dst.mkdir()
    runner = Runner(spec, [tool], src, dst, log=lambda *args: None)
    wl = runner.prepare(spec.workloads[0])

    def fail(self, location):
        raise OSError("endpoint mismatch")

    monkeypatch.setattr(Tool, "check_destination", fail)
    monkeypatch.setattr(runner_module, "_timed", lambda *a, **kw: pytest.fail("copy must not start"))
    case = Case("small", "rclone", [], 100, 10)
    try:
        repeat = runner._repeat(wl, tool, case, 0, False, None)
        assert repeat.exit_code == -1 and repeat.verified is None and repeat.wall_s == 0
        assert "endpoint mismatch" in case.error
        assert not any(Path(dst.path).iterdir())
    finally:
        runner.release(wl)


def test_campaign_templates():
    root = Path(__file__).parents[1] / "specs" / "rclone"
    for path in root.glob("*.toml"):
        spec = sp.load(path)
        assert spec.protocol.verify and spec.protocol.repeats == 3 and spec.protocol.slow_cutoff == 0
        text = "\n".join(plan_lines(spec, [Tool(t) for t in spec.tools]))
        assert "rclone" in text
        assert all(w.changed is None for w in spec.workloads)


@pytest.mark.parametrize("args", [("--rsync-path", "/runtime/syq copy"), ("--rsync-path=/runtime/syq copy",)])
def test_campaign_syq_remote_identity_uses_explicit_rsync_path(monkeypatch, args):
    tool = Tool(sp.ToolSpec("syq", "syq", "syq", ("rsync", "-rt", "--syq-no-bootstrap", *args), None))

    def probe(self, script, check):
        assert "command -v '/runtime/syq copy'" in script
        return subprocess.CompletedProcess([], 0, stdout=f"BIN=/runtime/syq copy\nSHA={'a' * 64}\nVER=syq pinned\n")

    monkeypatch.setattr(Location, "sh", probe)
    ident = remote_identity(tool, Location.parse("server:/scratch"))
    assert ident["binary"] == "/runtime/syq copy" and ident["sha256"] == "a" * 64
