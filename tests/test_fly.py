from __future__ import annotations

import json
import os
import shlex
import signal
import stat
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from syq_bench import fly as fly_provider

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_PATH = ROOT / "providers" / "fly" / "campaigns" / "same-region.toml"
COMPARISON_PATH = ROOT / "providers" / "fly" / "campaigns" / "remote-comparison.toml"
TRANSPORT_PATH = ROOT / "providers" / "fly" / "campaigns" / "transport-validation.toml"
TRANSPORT_CROSS_PATH = ROOT / "providers" / "fly" / "campaigns" / "transport-validation-cross-region.toml"
WAN_SPEEDUP_PATH = ROOT / "providers" / "fly" / "campaigns" / "wan-speedup.toml"
VOLUME_PATH = ROOT / "providers" / "fly" / "campaigns" / "volume-qualification.toml"
AMS_NRT_RESOURCE = "fly-private-route-ams-nrt"


def _state(app_id: str | None = "app-id") -> dict:
    return {
        "schema": 1,
        "run_id": "test-run",
        "experiment": "memory",
        "campaign": str(CAMPAIGN_PATH),
        "app_prefix": "syq-bench",
        "app": {"name": "syq-bench-abcdef123456", "id": app_id, "org": "test-org"},
        "phase": "ready",
        "created_at": "2026-09-03T00:00:00+00:00",
        "machines": {"source": "source-id", "destination": "destination-id"},
    }


def _write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state))


def _campaign_with(tmp_path: Path, replacement: tuple[str, str]) -> Path:
    text = CAMPAIGN_PATH.read_text()
    text = text.replace('run_spec = "../spec.toml"', f'run_spec = "{ROOT / "providers/fly/spec.toml"}"')
    text = text.replace('fly_config = "../fly.toml"', f'fly_config = "{ROOT / "providers/fly/fly.toml"}"')
    text = text.replace(*replacement)
    path = tmp_path / "campaign.toml"
    path.write_text(text)
    return path


def test_campaign_separates_network_and_storage_and_estimates_cost() -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)

    assert [(item.network, item.storage) for item in campaign.experiments] == [
        ("same-region-private-6pn", "tmpfs-memory-nondurable"),
    ]
    assert campaign.fly.process_names == ("source", "destination")
    assert campaign.fly.volume_gb == 0
    assert campaign.fly.syq_revision == "35fd73e7220b3aae4079184b48786c18fd1a07ba"
    assert campaign.lifecycle == "experiment"
    assert campaign.max_parallel == 1
    assert campaign.estimate_usd == pytest.approx(0.0370933333)
    assert any("not estimated:" in line for line in fly_provider.plan_lines(campaign))
    assert any("failed cleanup bills" in line for line in fly_provider.plan_lines(campaign))
    assert any("no benchmark Machine is shared" in line for line in fly_provider.plan_lines(campaign))
    assert any("large-file: large-file; 512 MiB" in line for line in fly_provider.plan_lines(campaign))
    assert any("up to 3 timed transfers per experiment" in line for line in fly_provider.plan_lines(campaign))
    assert any("Volumes:       none" in line for line in fly_provider.plan_lines(campaign))


def test_remote_comparison_preserves_fresh_small_file_and_delta_cases() -> None:
    campaign = fly_provider.load_campaign(COMPARISON_PATH)
    workloads = {workload.name: workload for workload in campaign.run_recipe.workloads}
    tools = {tool.name: tool for tool in campaign.run_recipe.tools}

    assert [(e.name, e.source_region, e.destination_region, e.run_recipe.name) for e in campaign.experiments] == [
        ("same-region-memory", "ams", "ams", "fly-remote-comparison"),
        ("cross-region-memory", "ams", "nrt", "fly-remote-cross-region"),
    ]
    assert workloads["fresh-large"].kind == "large-file"
    assert (workloads["fresh-small"].kind, workloads["fresh-small"].files) == ("small-files", 50_000)
    assert (workloads["delta-edited"].changed, workloads["delta-edited"].mutate) == (1.0, "blocks")
    assert (workloads["delta-rewritten"].changed, workloads["delta-rewritten"].mutate) == (1.0, "rewrite")
    assert tools["syq"].workloads is None
    assert tools["rsync"].workloads is None
    assert tools["tar"].workloads == ("fresh-large", "fresh-small")
    cross_workloads = {workload.name: workload for workload in campaign.experiments[1].run_recipe.workloads}
    assert cross_workloads["fresh-large"].bytes == 64 * 2**20
    assert cross_workloads["fresh-small"].files == 2_000
    assert cross_workloads["delta-edited"].bytes == 64 * 2**20
    assert campaign.max_parallel == 2
    assert campaign.compute_estimate_usd == pytest.approx(0.0927333333)
    plan = fly_provider.plan_lines(campaign)
    assert any("up to 14 timed transfers per experiment" in line for line in plan)
    assert any("peak Machines: 5" in line for line in plan)
    assert any("regions=ams->nrt" in line for line in plan)
    assert "COPY providers/fly/specs ./providers/fly/specs" in (ROOT / "providers" / "fly" / "Dockerfile").read_text()


def test_transport_validation_pins_ipv6_build_and_records_transport() -> None:
    same = fly_provider.load_campaign(TRANSPORT_PATH)
    cross = fly_provider.load_campaign(TRANSPORT_CROSS_PATH)

    assert same.fly.syq_revision == "445cd5c7be2255fbc97479d2a29f714cf3974ec1"
    assert cross.fly.syq_revision == same.fly.syq_revision
    assert [(e.source_region, e.destination_region) for e in same.experiments] == [("ams", "ams")]
    assert [(e.source_region, e.destination_region) for e in cross.experiments] == [("ams", "nrt")]
    assert same.run_recipe.workloads[0].bytes == 256 * 2**20
    assert cross.run_recipe.workloads[0].bytes == 32 * 2**20
    tools = {tool.name: tool for tool in same.run_recipe.tools}
    assert set(tools) == {"syq", "syq-no-tcp", "rsync"}
    assert "-vv" in tools["syq"].args
    assert "--syq-no-tcp" not in tools["syq"].args
    assert "--syq-no-tcp" in tools["syq-no-tcp"].args
    assert tools["syq"].args[-2:] == ("--syq-connections", "8")
    assert tools["syq"].debug and tools["syq-no-tcp"].debug


def test_wan_speedup_recipe_exposes_parallel_capacity_without_a_large_fixture() -> None:
    campaign = fly_provider.load_campaign(WAN_SPEEDUP_PATH)
    workload = campaign.run_recipe.workloads[0]
    tools = {tool.name: tool for tool in campaign.run_recipe.tools}

    assert campaign.fly.syq_revision == "e0ee86b9962f2b030d8fb87cec30f84fdfe4077f"
    assert [(e.source_region, e.destination_region) for e in campaign.experiments] == [("ams", "nrt")]
    assert (workload.kind, workload.files, workload.bytes) == ("small-files", 8, 512 * 2**20)
    assert set(tools) == {"syq-auto", "syq-j1", "syq-j8", "rsync"}
    assert tools["syq-auto"].debug and tools["syq-j1"].debug and tools["syq-j8"].debug
    assert "--syq-connections" not in tools["syq-auto"].args
    assert tools["syq-j1"].args[-3:-1] == ("--syq-connections", "1")
    assert tools["syq-j8"].args[-3:-1] == ("--syq-connections", "8")
    assert campaign.run_recipe.protocol.repeats == 1
    assert campaign.run_recipe.protocol.tool_timeout == 100
    assert campaign.max_duration_seconds == 660
    assert campaign.exclusive_resources == (AMS_NRT_RESOURCE,)
    assert any("per-user host lock" in line for line in fly_provider.plan_lines(campaign))


def test_volume_qualification_separates_local_io_from_network() -> None:
    campaign = fly_provider.load_campaign(VOLUME_PATH)
    local, remote = campaign.experiments
    assert (local.execution, remote.execution) == ("local", "remote")
    assert campaign.fly.volume_gb == 40
    assert campaign.estimate_usd == pytest.approx(0.5730666667)
    assert campaign.max_duration_seconds == 900
    assert campaign.max_parallel == 2
    assert campaign.fly.syq_revision == "f7f1589e23b8aae01fe458519b95f1d11f5fd15f"
    assert local.run_recipe.workloads == remote.run_recipe.workloads
    assert {w.kind for w in local.run_recipe.workloads} == {"large-file", "mixed-tree", "small-files"}
    for experiment in campaign.experiments:
        protocol = experiment.run_recipe.protocol
        assert protocol.cache == "evict"
        assert protocol.durable and protocol.verify and protocol.probes
        assert protocol.repeats == 1
        assert protocol.tool_timeout == 30
    argv = fly_provider._remote_run_argv(campaign, local, _state(), "/tmp/result.json")
    assert 'endpoints.destination="/volume/syq-bench/destination"' in argv
    assert 'environment.region="ams"' in argv
    assert not any(".internal" in item for item in argv)
    assert any("destination Machine idle but still billed" in line for line in fly_provider.plan_lines(campaign))
    local_only = replace(campaign, experiments=(local,))
    assert not any("iperf3" in line for line in fly_provider.plan_lines(local_only))


def test_volume_directory_scales_only_the_small_file_shape() -> None:
    campaign = fly_provider.load_campaign(VOLUME_PATH.with_name("volume-directory.toml"))
    broad = fly_provider.load_campaign(VOLUME_PATH)
    assert campaign.fly == broad.fly
    assert campaign.run_recipe.tools == broad.run_recipe.tools
    original = next(w for w in broad.run_recipe.workloads if w.name == "small-files")
    workload = campaign.run_recipe.workloads[0]
    assert workload.kind == original.kind
    assert workload.files == 5 * original.files
    assert workload.bytes == 5 * original.bytes
    assert campaign.run_recipe.protocol.repeats == 2
    assert campaign.run_recipe.protocol.tool_timeout == 30
    assert campaign.max_duration_seconds == 600
    assert len(campaign.experiments) == 1
    assert campaign.experiments[0].execution == "local"
    assert campaign.estimate_usd == pytest.approx(0.1938)


@pytest.mark.parametrize("execution", ['"typo"', "0", "false", "[]", "{}"])
def test_campaign_rejects_invalid_execution(tmp_path: Path, execution: str) -> None:
    path = _campaign_with(tmp_path, ('name = "memory"', f'name = "memory"\nexecution = {execution}'))
    with pytest.raises(fly_provider.CampaignError, match="execution must be"):
        fly_provider.load_campaign(path)


def test_local_execution_rejects_cross_region_labels(tmp_path: Path) -> None:
    path = _campaign_with(
        tmp_path,
        ('destination_region = "ams"', 'destination_region = "nrt"\nexecution = "local"'),
    )
    with pytest.raises(fly_provider.CampaignError, match="matching source and destination regions"):
        fly_provider.load_campaign(path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("machine_usd_per_hour = 0.1391", "machine_usd_per_hour = nan", "finite and non-negative"),
        ("max_duration_seconds = 480", "max_duration_seconds = 1.5", "positive integer"),
        ("max_parallel = 1", "max_parallel = 0", "positive integer"),
        (
            'source = "/memory/syq-bench/source"',
            'source = "/memory/syq-bench"',
            "strictly below",
        ),
        ('source_region = "ams"', 'source_region = "not/a/region"', "source_region"),
    ],
)
def test_campaign_rejects_bad_numeric_and_scratch_values(tmp_path: Path, old: str, new: str, message: str) -> None:
    path = _campaign_with(tmp_path, (old, new))
    with pytest.raises(fly_provider.CampaignError, match=message):
        fly_provider.load_campaign(path)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('"not-a-list"', "list of plain resource labels"),
        ('["bad/resource"]', "list of plain resource labels"),
        (f'["{"x" * 101}"]', "list of plain resource labels"),
        ('["duplicate", "duplicate"]', "must not contain duplicates"),
    ],
)
def test_campaign_rejects_bad_exclusive_resources(tmp_path: Path, value: str, message: str) -> None:
    path = _campaign_with(
        tmp_path,
        ("max_parallel = 1", f"max_parallel = 1\nexclusive_resources = {value}"),
    )
    with pytest.raises(fly_provider.CampaignError, match=message):
        fly_provider.load_campaign(path)


def test_state_creation_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    fly_provider._atomic_state(path, _state(), create=True)

    with pytest.raises(FileExistsError):
        fly_provider._atomic_state(path, _state(), create=True)


def test_campaign_exclusive_resource_rejects_a_second_local_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = fly_provider.load_campaign(WAN_SPEEDUP_PATH)
    monkeypatch.setattr(fly_provider, "_resource_lock_path", lambda resource: tmp_path / f"{resource}.lock")

    with fly_provider._exclusive_campaign_resources(campaign):
        with pytest.raises(fly_provider.FlyError, match="another local campaign"):
            with fly_provider._exclusive_campaign_resources(campaign):
                pytest.fail("a second campaign acquired the same exclusive resource")

    with fly_provider._exclusive_campaign_resources(campaign):
        pass


def test_every_ams_to_nrt_campaign_uses_the_same_exclusive_resource() -> None:
    for path in (ROOT / "providers" / "fly" / "campaigns").glob("*.toml"):
        campaign = fly_provider.load_campaign(path)
        uses_route = any(
            (experiment.source_region, experiment.destination_region) == ("ams", "nrt")
            for experiment in campaign.experiments
        )
        assert (AMS_NRT_RESOURCE in campaign.exclusive_resources) == uses_route, path


def test_resource_locks_use_a_private_per_user_runtime_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        fly_provider,
        "_repo_root",
        lambda: pytest.fail("resource locks must not depend on a checkout path"),
    )

    first = fly_provider._resource_lock_path(AMS_NRT_RESOURCE)
    second = fly_provider._resource_lock_path(AMS_NRT_RESOURCE)

    assert first == second == tmp_path / "syq-bench-fly-locks" / f"{AMS_NRT_RESOURCE}.lock"
    assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700


def test_resource_locks_reject_an_insecure_runtime_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.chmod(0o755)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    with pytest.raises(fly_provider.FlyError, match="private directory owned by the current user"):
        fly_provider._resource_lock_path(AMS_NRT_RESOURCE)


def test_resource_locks_reject_a_fallback_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_target = tmp_path / "private-target"
    private_target.mkdir(mode=0o700)
    lock_dir = tmp_path / f"syq-bench-fly-locks-{os.getuid()}"
    lock_dir.symlink_to(private_target, target_is_directory=True)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(fly_provider.tempfile, "gettempdir", lambda: str(tmp_path))

    with pytest.raises(fly_provider.FlyError, match="owned by the current user with mode 0700"):
        fly_provider._resource_lock_path(AMS_NRT_RESOURCE)


def test_campaign_creates_one_recovery_state_per_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    monkeypatch.setattr(fly_provider, "_state_path", lambda run_id: tmp_path / f"{run_id}.json")

    run_id, instances = fly_provider.create_states(campaign)

    assert set(instances) == {"memory"}
    assert len({app_name for _, app_name in instances.values()}) == 1
    for experiment, (path, _app_name) in instances.items():
        state = json.loads(path.read_text())
        assert state["run_id"] == run_id
        assert state["experiment"] == experiment
        assert state["phase"] == "planned"


def test_destroy_refuses_an_app_with_a_different_immutable_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    _write_state(path, _state("owned-id"))
    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    monkeypatch.setattr(fly_provider, "_find_app", lambda name: {"Name": name, "ID": "replacement-id"})
    called = []
    monkeypatch.setattr(fly_provider, "_run_live", lambda *args, **kwargs: called.append((args, kwargs)))

    with pytest.raises(fly_provider.OwnershipError, match="refusing deletion"):
        fly_provider.destroy(path)

    assert called == []
    assert json.loads(path.read_text())["phase"] == "ready"


def test_destroy_refuses_existing_app_when_create_did_not_record_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    _write_state(path, _state(None))
    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    monkeypatch.setattr(fly_provider, "_find_app", lambda name: {"Name": name, "ID": "unknown-id"})

    with pytest.raises(fly_provider.OwnershipError, match="no immutable ID"):
        fly_provider.destroy(path)


def test_destroy_verifies_identity_and_absence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    state = _state("owned-id")
    _write_state(path, state)
    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    answers = iter([{"Name": state["app"]["name"], "ID": "owned-id"}, None])
    monkeypatch.setattr(fly_provider, "_find_app", lambda name: next(answers))
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    calls = []
    monkeypatch.setattr(fly_provider, "_run_live", lambda argv, timeout: calls.append((argv, timeout)))

    assert fly_provider.destroy(path) is True

    assert calls == [(["fly", "apps", "destroy", state["app"]["name"], "--yes"], 300)]
    assert json.loads(path.read_text())["phase"] == "destroyed"


def test_remote_recipe_adds_axes_but_not_credentials() -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    state = _state()
    argv = fly_provider._remote_run_argv(campaign, campaign.experiments[0], state, "/tmp/result.json")
    rendered = " ".join(argv)

    assert "environment.network" in rendered
    assert "same-region-private-6pn" in rendered
    assert "environment.storage" in rendered
    assert "environment.region" in rendered
    assert "ams-&gt;ams" not in rendered
    assert "ams->ams" in rendered
    assert "destination.process.syq-bench-abcdef123456.internal" in rendered
    assert "-p 2222" in rendered
    assert "FLY_API_TOKEN" not in rendered
    assert "secret-not-printed" not in rendered

    comparison = fly_provider.load_campaign(COMPARISON_PATH)
    cross_argv = fly_provider._remote_run_argv(comparison, comparison.experiments[1], state, "/tmp/result.json")
    assert any("remote-cross-region.toml" in item for item in cross_argv)
    assert "ams->nrt" in " ".join(cross_argv)


def test_wait_for_machine_pair_accepts_distinct_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    machines = [
        {
            "id": "source-id",
            "state": "started",
            "region": "ams",
            "config": {"metadata": {"fly_process_group": "source"}},
        },
        {
            "id": "destination-id",
            "state": "started",
            "region": "nrt",
            "config": {"metadata": {"fly_process_group": "destination"}},
        },
    ]
    monkeypatch.setattr(fly_provider, "_machines", lambda app_name: machines)

    assert fly_provider._wait_for_machine_pair(
        "syq-bench-abcdef123456", {"source": "ams", "destination": "nrt"}, timeout=1
    ) == {"source": "source-id", "destination": "destination-id"}


@pytest.mark.parametrize("local", [False, True])
def test_provision_places_regions_and_only_waits_for_remote_ssh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: bool
) -> None:
    campaign = fly_provider.load_campaign(VOLUME_PATH if local else COMPARISON_PATH)
    state = _state()
    state["campaign"] = str(campaign.path)
    state["experiment"] = "local-volume" if local else "cross-region-memory"
    state["phase"] = "planned"
    state["app"]["id"] = None
    state_path = tmp_path / "state.json"
    _write_state(state_path, state)
    live_calls: list[list[str]] = []
    waited_for: list[dict[str, str]] = []
    ssh_waits: list[tuple] = []

    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    monkeypatch.setenv("FLY_ORG", "test-org")
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(fly_provider, "_run_live", lambda argv, timeout: live_calls.append(argv))
    monkeypatch.setattr(fly_provider, "_find_app", lambda name: {"Name": name, "ID": "owned-id"})
    monkeypatch.setattr(fly_provider, "_ephemeral_ssh_env", lambda: "KEY=value\n")
    monkeypatch.setattr(
        fly_provider,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    def wait_for_pair(app_name: str, regions: dict[str, str], timeout: float) -> dict[str, str]:
        waited_for.append(regions)
        return {"source": "source-id", "destination": "destination-id"}

    monkeypatch.setattr(fly_provider, "_wait_for_machine_pair", wait_for_pair)
    monkeypatch.setattr(fly_provider, "_wait_for_destination_ssh", lambda *args: ssh_waits.append(args))

    fly_provider.provision(campaign, state_path)

    scale_calls = [call for call in live_calls if call[1:3] == ["scale", "count"]]
    expected_scale = [["source=1", "destination=1", "--region", "ams"]]
    if not local:
        expected_scale += [
            ["destination=1", "--region", "nrt", "--app"],
            ["destination=0", "--region", "ams", "--app"],
        ]
    assert [call[3:7] for call in scale_calls] == expected_scale
    assert waited_for == [{"source": "ams", "destination": "ams" if local else "nrt"}]
    assert len(ssh_waits) == (0 if local else 1)
    saved = json.loads(state_path.read_text())
    assert saved["phase"] == "ready"
    assert saved["machines"] == {"source": "source-id", "destination": "destination-id"}


@pytest.mark.parametrize("local", [False, True])
def test_run_experiment_sends_one_direct_remote_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local: bool
) -> None:
    campaign = fly_provider.load_campaign(VOLUME_PATH if local else CAMPAIGN_PATH)
    experiment_name = campaign.experiments[0].name
    state = _state()
    state_path = tmp_path / "state.json"
    _write_state(state_path, state)
    live_calls: list[list[str]] = []

    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(fly_provider, "_run_live", lambda argv, timeout: live_calls.append(argv))
    monkeypatch.setattr(
        fly_provider,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "{}\n", ""),
    )
    monkeypatch.setattr(fly_provider, "_orchestration_record", lambda campaign: {"test": True})

    monkeypatch.setattr(
        fly_provider,
        "_measure_network_ceiling",
        lambda *args: [
            {
                "name": "network_receive",
                "value": 125.0,
                "unit": "MB/s",
                "detail": "test",
                "where": "path",
            }
        ],
    )

    if local:
        monkeypatch.setattr(fly_provider, "_measure_network_ceiling", lambda *args: pytest.fail("local network probe"))

    result = fly_provider.run_experiment(campaign, state_path, experiment_name, tmp_path / "results")

    remote_result = f"/tmp/syq-bench/test-run-{experiment_name}.json"
    expected = fly_provider._remote_run_argv(campaign, campaign.experiments[0], state, remote_result)
    remote_command = live_calls[0][live_calls[0].index("--command") + 1]
    # fly ssh executes a tokenized command directly; it does not provide a
    # shell that interprets compound-command operators.
    assert shlex.split(remote_command) == expected
    assert "&&" not in shlex.split(remote_command)
    assert result == tmp_path / "results" / f"{experiment_name}.json"
    probes = json.loads(result.read_text())["probes"]
    if local:
        assert probes == []
    else:
        assert probes[0]["name"] == "network_receive"


def test_worker_always_destroys_its_isolated_resources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    calls: list[str] = []
    monkeypatch.setattr(fly_provider, "provision", lambda *args: calls.append("provision"))

    def fail_run(*args: object) -> None:
        calls.append("run")
        raise fly_provider.FlyError("benchmark failed")

    monkeypatch.setattr(fly_provider, "run_experiment", fail_run)
    monkeypatch.setattr(fly_provider, "destroy", lambda *args: calls.append("destroy"))

    with pytest.raises(fly_provider.FlyError, match="benchmark failed"):
        fly_provider._run_worker(campaign, tmp_path / "state.json", "memory", tmp_path / "results")

    assert calls == ["provision", "run", "destroy"]
    assert "benchmark failed" in (tmp_path / "results" / "memory.log").read_text()


def test_reframe_command_uses_bounded_async_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    monkeypatch.setattr(fly_provider.shutil, "which", lambda name: "/mock/reframe" if name == "reframe" else None)

    command = fly_provider._reframe_command(campaign, "run-id", tmp_path)

    assert "--exec-policy=async" in command
    assert "--exec-policy=serial" not in command
    assert "--system=fly-orchestrator" in command
    assert str(ROOT / "providers" / "fly" / "reframe_config.py") in command


def test_worker_logs_are_relayed_with_experiment_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    tmp_path.joinpath("memory.log").write_bytes(b"first\npartial")
    offsets: dict[str, int] = {}
    pending: dict[str, bytes] = {}

    fly_provider._relay_worker_logs(campaign, tmp_path, offsets, pending)
    assert capsys.readouterr().out == "[memory] first\n"
    with tmp_path.joinpath("memory.log").open("ab") as log:
        log.write(b" line\n")
    fly_provider._relay_worker_logs(campaign, tmp_path, offsets, pending, final=True)

    assert capsys.readouterr().out == "[memory] partial line\n"


def test_fly_subprocesses_do_not_inherit_the_orchestration_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    monkeypatch.setenv("FLY_ORG", "display-name-that-flyctl-must-not-read")

    env = fly_provider._fly_subprocess_env()

    assert env["FLY_API_TOKEN"] == "secret-not-printed"
    assert "FLY_ORG" not in env


@pytest.mark.parametrize(
    "response",
    [
        {"correct-slug": "Organization Display Name"},
        [{"Slug": "correct-slug", "Name": "Organization Display Name"}],
    ],
)
def test_org_preflight_requires_the_slug(response: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(fly_provider, "_json", lambda argv: response)

    fly_provider._validate_org_slug("correct-slug")
    with pytest.raises(fly_provider.FlyError, match="exact organization slug"):
        fly_provider._validate_org_slug("Organization Display Name")


def test_destination_readiness_is_checked_from_the_source_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(fly_provider.time, "sleep", lambda seconds: None)
    responses = iter(
        [
            subprocess.CompletedProcess([], 255, "", "not ready\n"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return next(responses)

    monkeypatch.setattr(fly_provider, "_run", fake_run)

    fly_provider._wait_for_destination_ssh("syq-bench-abcdef123456", "source-id", timeout=1)

    assert len(calls) == 2
    assert calls[0][calls[0].index("--machine") + 1] == "source-id"
    inner = calls[0][calls[0].index("--command") + 1]
    inner_argv = shlex.split(inner)
    assert inner_argv[inner_argv.index("-p") + 1] == "2222"
    assert inner_argv[-2:] == [
        "root@destination.process.syq-bench-abcdef123456.internal",
        "true",
    ]


def test_network_ceiling_uses_receiver_goodput(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        payload = {"end": {"sum_received": {"bits_per_second": 800_000_000}}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(fly_provider, "_run", fake_run)

    probes = fly_provider._measure_network_ceiling("syq-bench-abcdef123456", "source-id")

    assert [probe["name"] for probe in probes] == [
        "network_receive_1_stream",
        "network_receive",
        "network_receive_8_stream",
    ]
    assert all(probe["value"] == 100.0 for probe in probes)
    assert probes[1]["detail"] == (
        "iperf3 receiver goodput over private 6PN; 5 s measured after 2 s warm-up, 4 TCP stream(s)"
    )
    assert calls[0][calls[0].index("--machine") + 1] == "source-id"
    commands = [shlex.split(call[call.index("--command") + 1]) for call in calls]
    assert [command[command.index("--parallel") + 1] for command in commands] == ["1", "4", "8"]
    assert all(command[command.index("--time") + 1] == "5" for command in commands)
    assert all(command[command.index("--omit") + 1] == "2" for command in commands)


@pytest.mark.parametrize("bits_per_second", [0, -1, float("nan"), float("inf")])
def test_network_ceiling_rejects_invalid_rates(bits_per_second: float, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"end": {"sum_received": {"bits_per_second": bits_per_second}}}
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(
        fly_provider,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""),
    )

    with pytest.raises(fly_provider.FlyError, match="non-positive or non-finite"):
        fly_provider._measure_network_ceiling("syq-bench-abcdef123456", "source-id")


def test_orchestration_record_embeds_recipes_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign = fly_provider.load_campaign(CAMPAIGN_PATH)
    monkeypatch.setattr(fly_provider, "_fly_binary", lambda: "fly")
    monkeypatch.setattr(
        fly_provider,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "fly v1.2.3\n", ""),
    )
    monkeypatch.setattr(
        fly_provider.importlib.metadata,
        "version",
        lambda package: "4.10.3" if package == "reframe-hpc" else pytest.fail(f"unexpected package: {package}"),
    )

    record = fly_provider._orchestration_record(campaign)
    rendered = json.dumps(record)

    assert record["campaign"]["experiments"][0]["network"] == "same-region-private-6pn"
    assert record["fly_config"]["build"]["dockerfile"] == "Dockerfile"
    assert record["fly_config"]["build"]["args"]["SYQ_REV"] == campaign.fly.syq_revision
    assert record["framework"] == {"name": "ReFrame", "version": "4.10.3"}
    assert record["provider"] == {"name": "fly.io", "cli_version": "fly v1.2.3"}
    assert "FLY_API_TOKEN" not in rendered


def test_live_command_has_a_hard_deadline() -> None:
    started = time.monotonic()
    with pytest.raises(fly_provider.FlyError, match="timed out"):
        fly_provider._run_live([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
    assert time.monotonic() - started < 2


def test_live_command_timeout_stops_a_nested_process_group(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child-pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    try:
        with pytest.raises(fly_provider.FlyError, match="timed out"):
            fly_provider._run_live([sys.executable, "-c", script], timeout=0.2)
        child_pid = int(child_pid_path.read_text())
        with pytest.raises(ProcessLookupError):
            os.killpg(child_pid, 0)
    finally:
        if child_pid_path.exists():
            child_pid = int(child_pid_path.read_text())
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_destroy_needs_only_the_fly_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    state = _state()
    state["phase"] = "destroyed"
    _write_state(path, state)
    monkeypatch.delenv("FLY_API_TOKEN", raising=False)
    monkeypatch.delenv("FLY_ORG", raising=False)

    with pytest.raises(fly_provider.FlyError, match="FLY_API_TOKEN"):
        fly_provider.destroy(path)

    monkeypatch.setenv("FLY_API_TOKEN", "secret-not-printed")
    assert fly_provider.destroy(path) is False
