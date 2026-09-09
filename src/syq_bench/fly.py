"""Fly.io campaign planning and lifecycle support.

The public commands in this module are intentionally small. ReFrame owns the
test matrix and bounded concurrency; this module owns the provider-specific
lifecycle, crash-recovery state, cost plan, and transfer of syq-bench result
JSON.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import importlib.metadata
import json
import math
import os
import re
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from syq_bench.spec import Spec
from syq_bench.spec import load as load_run_spec

STATE_SCHEMA = 1
CAMPAIGN_SCHEMA = 1
APP_SUFFIX_LENGTH = 12
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REGION = re.compile(r"[a-z0-9]+")
_SAFE_SCRATCH = (
    PurePosixPath("/tmp/syq-bench"),
    PurePosixPath("/volume/syq-bench"),
    PurePosixPath("/memory/syq-bench"),
)
_DESTINATION_SSH_PORT = 2222  # Fly's Machine management SSH service owns port 22.
_IPERF_PORT = 5201
_IPERF_OMIT_SECONDS = 2
_IPERF_SECONDS = 5
_IPERF_STREAMS = 4
_IPERF_STREAM_COUNTS = (1, _IPERF_STREAMS, 8)


class CampaignError(ValueError):
    pass


class FlyError(RuntimeError):
    pass


class OwnershipError(FlyError):
    pass


@dataclass(frozen=True)
class Experiment:
    name: str
    network: str
    storage: str
    source: str
    destination: str
    source_region: str
    destination_region: str
    run_spec: Path
    run_recipe: Spec
    execution: str = "remote"


@dataclass(frozen=True)
class Cost:
    checked_at: str
    machine_usd_per_hour: float
    volume_usd_per_gb_month: float
    other_usd: float
    exclusions: str


@dataclass(frozen=True)
class FlyConfig:
    path: Path
    region: str
    process_names: tuple[str, ...]
    machine_descriptions: tuple[str, ...]
    volume_gb: float
    syq_revision: str


@dataclass(frozen=True)
class Campaign:
    path: Path
    name: str
    description: str
    app_prefix: str
    max_duration_seconds: int
    lifecycle: str
    max_parallel: int
    exclusive_resources: tuple[str, ...]
    run_spec: Path
    run_recipe: Spec
    fly: FlyConfig
    experiments: tuple[Experiment, ...]
    cost: Cost

    @property
    def compute_estimate_usd(self) -> float:
        # Moving a process away from fly.toml's primary region briefly creates
        # its replacement before removing the Machine made by fly deploy. Use
        # that transient peak for the whole cap: deliberately conservative,
        # and still simple enough for a reader to reproduce by hand.
        peaks = sorted((self.provision_peak_machines(e) for e in self.experiments), reverse=True)
        active_machines = sum(peaks[: self.max_parallel])
        return active_machines * self.cost.machine_usd_per_hour * self.max_duration_seconds / 3600

    def provision_peak_machines(self, experiment: Experiment) -> int:
        moved = sum(region != self.fly.region for region in (experiment.source_region, experiment.destination_region))
        return len(self.fly.process_names) + moved

    @property
    def volume_estimate_usd(self) -> float:
        # Fly bills volumes by the hour. Round the campaign lifetime up to one
        # billing hour instead of displaying an impossible sub-hour fraction.
        hours = math.ceil(self.max_duration_seconds / 3600)
        return len(self.experiments) * self.fly.volume_gb * self.cost.volume_usd_per_gb_month * hours / (24 * 30)

    @property
    def estimate_usd(self) -> float:
        return self.compute_estimate_usd + self.volume_estimate_usd + self.cost.other_usd


def _required(d: dict[str, Any], key: str, context: str) -> Any:
    if key not in d:
        raise CampaignError(f"{context}: missing {key!r}")
    return d[key]


def _finite_nonnegative(value: Any, context: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as e:
        raise CampaignError(f"{context} must be a number") from e
    if not math.isfinite(parsed) or parsed < 0:
        raise CampaignError(f"{context} must be finite and non-negative")
    return parsed


def _volume_size_gb(value: Any, context: str) -> float:
    if isinstance(value, int | float):
        size = _finite_nonnegative(value, context)
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(gb|gib)?\s*", value.lower())
        if not match:
            raise CampaignError(f"{context} must be a size in GB")
        size = float(match.group(1))
    else:
        raise CampaignError(f"{context} must be a size in GB")
    if size <= 0:
        raise CampaignError(f"{context} must be positive")
    return size


def _load_fly_config(path: Path) -> FlyConfig:
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise CampaignError(f"could not read Fly config {path}: {e}") from e

    allowed = {"primary_region", "kill_signal", "kill_timeout", "build", "processes", "mounts", "vm"}
    unknown = set(raw) - allowed
    if unknown:
        raise CampaignError(f"Fly benchmark config has unsupported top-level fields: {sorted(unknown)}")
    build = raw.get("build")
    if not isinstance(build, dict) or set(build) != {"dockerfile", "args"}:
        raise CampaignError("fly config build must contain exactly dockerfile and args")
    if build["dockerfile"] != "Dockerfile":
        raise CampaignError("fly config must use Dockerfile relative to providers/fly/fly.toml")
    build_args = build["args"]
    if not isinstance(build_args, dict) or set(build_args) != {"SYQ_REV"}:
        raise CampaignError("fly config build args must contain only SYQ_REV")
    syq_revision = str(build_args["SYQ_REV"])
    if not re.fullmatch(r"[0-9a-f]{40}", syq_revision):
        raise CampaignError("fly config SYQ_REV must be a full lowercase Git commit")
    region = str(_required(raw, "primary_region", "fly config"))
    if not _REGION.fullmatch(region):
        raise CampaignError(f"fly config primary_region {region!r} is invalid")
    processes = raw.get("processes")
    if not isinstance(processes, dict) or processes != {"source": "source", "destination": "destination"}:
        raise CampaignError("fly config must declare exactly the source and destination process groups")

    mounts = raw.get("mounts", [])
    if not isinstance(mounts, list) or len(mounts) not in (0, 2):
        raise CampaignError("fly config must declare no volumes or one source and one destination volume mount")
    mounted_processes: set[str] = set()
    volume_gb = 0.0
    for i, mount in enumerate(mounts):
        selected = mount.get("processes")
        if not isinstance(selected, list) or len(selected) != 1 or selected[0] not in processes:
            raise CampaignError(f"fly config mounts[{i}] must select exactly one benchmark process")
        if selected[0] in mounted_processes:
            raise CampaignError(f"fly config has multiple mounts for process {selected[0]!r}")
        mounted_processes.add(selected[0])
        if mount.get("destination") != "/volume":
            raise CampaignError(f"fly config mounts[{i}] destination must be /volume")
        volume_gb += _volume_size_gb(
            _required(mount, "initial_size", f"fly config mounts[{i}]"),
            f"fly config mounts[{i}].initial_size",
        )
    if mounts and mounted_processes != set(processes):
        raise CampaignError("fly config must mount a separate volume on both benchmark processes")

    vms = raw.get("vm", [])
    if not isinstance(vms, list) or not vms:
        raise CampaignError("fly config must declare VM resources explicitly")
    covered: set[str] = set()
    descriptions: list[str] = []
    for i, vm in enumerate(vms):
        selected = vm.get("processes")
        if not isinstance(selected, list) or not selected or not set(selected) <= set(processes):
            raise CampaignError(f"fly config vm[{i}] has invalid processes")
        overlap = covered.intersection(selected)
        if overlap:
            raise CampaignError(f"fly config VM resources overlap for {sorted(overlap)}")
        covered.update(selected)
        size = str(_required(vm, "size", f"fly config vm[{i}]"))
        memory = str(_required(vm, "memory", f"fly config vm[{i}]"))
        descriptions.append(f"{','.join(selected)}={size}/{memory}")
    if covered != set(processes):
        raise CampaignError("fly config must explicitly size both benchmark processes")

    return FlyConfig(path, region, tuple(processes), tuple(descriptions), volume_gb, syq_revision)


def _scratch_path(value: Any, context: str) -> str:
    path = PurePosixPath(str(value))
    if not path.is_absolute() or not any(path.is_relative_to(root) and path != root for root in _SAFE_SCRATCH):
        roots = ", ".join(str(root) for root in _SAFE_SCRATCH)
        raise CampaignError(f"{context} must be strictly below one of: {roots}")
    if ".." in path.parts:
        raise CampaignError(f"{context} must not contain '..'")
    return str(path)


def load_campaign(path: Path) -> Campaign:
    path = path.resolve()
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise CampaignError(f"could not read campaign {path}: {e}") from e

    if raw.get("schema") != CAMPAIGN_SCHEMA:
        raise CampaignError(f"campaign schema must be {CAMPAIGN_SCHEMA}")
    name = str(_required(raw, "name", "campaign"))
    if not _LABEL.fullmatch(name):
        raise CampaignError("campaign name must be a plain path component")
    prefix = str(_required(raw, "app_prefix", "campaign"))
    if not _NAME.fullmatch(prefix) or len(prefix) > 45:
        raise CampaignError("app_prefix must be 2-45 lowercase letters, digits, or hyphens")
    raw_duration = _required(raw, "max_duration_seconds", "campaign")
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, int):
        raise CampaignError("max_duration_seconds must be a positive integer")
    duration = raw_duration
    if duration <= 0:
        raise CampaignError("max_duration_seconds must be a positive integer")
    lifecycle = str(_required(raw, "lifecycle", "campaign"))
    if lifecycle != "experiment":
        raise CampaignError("only lifecycle = 'experiment' is supported: each experiment gets isolated resources")
    raw_max_parallel = _required(raw, "max_parallel", "campaign")
    if isinstance(raw_max_parallel, bool) or not isinstance(raw_max_parallel, int) or raw_max_parallel <= 0:
        raise CampaignError("max_parallel must be a positive integer")
    raw_exclusive_resources = raw.get("exclusive_resources", [])
    if not isinstance(raw_exclusive_resources, list) or any(
        not isinstance(resource, str) or len(resource) > 100 or not _LABEL.fullmatch(resource)
        for resource in raw_exclusive_resources
    ):
        raise CampaignError("exclusive_resources must be a list of plain resource labels")
    if len(raw_exclusive_resources) != len(set(raw_exclusive_resources)):
        raise CampaignError("exclusive_resources must not contain duplicates")

    def relative_file_value(value: Any, context: str) -> Path:
        value = (path.parent / str(value)).resolve()
        if not value.is_file():
            raise CampaignError(f"{context} does not exist: {value}")
        try:
            value.relative_to(_repo_root())
        except ValueError as e:
            raise CampaignError(f"{context} must be inside the repository so the image contains it") from e
        return value

    def relative_file(key: str) -> Path:
        return relative_file_value(_required(raw, key, "campaign"), f"campaign {key}")

    run_spec = relative_file("run_spec")
    try:
        run_recipe = load_run_spec(run_spec)
    except (OSError, ValueError) as e:
        raise CampaignError(f"could not load run spec {run_spec}: {e}") from e
    fly = _load_fly_config(relative_file("fly_config"))

    experiment_rows = raw.get("experiments", [])
    if not isinstance(experiment_rows, list) or not experiment_rows:
        raise CampaignError("campaign needs at least one [[experiments]] entry")
    experiments: list[Experiment] = []
    for i, row in enumerate(experiment_rows):
        context = f"experiments[{i}]"
        exp_name = str(_required(row, "name", context))
        if not _LABEL.fullmatch(exp_name):
            raise CampaignError(f"{context}.name must be a plain path component")
        network = str(_required(row, "network", context))
        storage = str(_required(row, "storage", context))
        if not network or not storage:
            raise CampaignError(f"{context} network and storage labels must be non-empty")
        source_region = str(row.get("source_region", fly.region))
        destination_region = str(row.get("destination_region", fly.region))
        execution = row.get("execution", "remote")
        if execution not in ("local", "remote"):
            raise CampaignError(f"{context}.execution must be 'local' or 'remote'")
        if execution == "local" and source_region != destination_region:
            raise CampaignError(f"{context}: local execution requires matching source and destination regions")
        for label, region in (("source_region", source_region), ("destination_region", destination_region)):
            if not _REGION.fullmatch(region):
                raise CampaignError(f"{context}.{label} {region!r} is invalid")
        if fly.volume_gb and (source_region != fly.region or destination_region != fly.region):
            raise CampaignError(
                f"{context}: cross-region placement with Fly Volumes is not supported yet; "
                "use a volume-free config or keep both Machines in the primary region"
            )
        experiment_run_spec = (
            run_spec if "run_spec" not in row else relative_file_value(row["run_spec"], f"{context}.run_spec")
        )
        try:
            experiment_run_recipe = load_run_spec(experiment_run_spec)
        except (OSError, ValueError) as e:
            raise CampaignError(f"could not load run spec {experiment_run_spec}: {e}") from e
        experiments.append(
            Experiment(
                exp_name,
                network,
                storage,
                _scratch_path(_required(row, "source", context), f"{context}.source"),
                _scratch_path(_required(row, "destination", context), f"{context}.destination"),
                source_region,
                destination_region,
                experiment_run_spec,
                experiment_run_recipe,
                execution,
            )
        )
    names = [experiment.name for experiment in experiments]
    if len(names) != len(set(names)):
        raise CampaignError(f"duplicate experiment names: {names}")

    cost_raw = raw.get("cost")
    if not isinstance(cost_raw, dict):
        raise CampaignError("campaign needs a [cost] estimate")
    cost = Cost(
        str(_required(cost_raw, "checked_at", "cost")),
        _finite_nonnegative(_required(cost_raw, "machine_usd_per_hour", "cost"), "cost.machine_usd_per_hour"),
        _finite_nonnegative(_required(cost_raw, "volume_usd_per_gb_month", "cost"), "cost.volume_usd_per_gb_month"),
        _finite_nonnegative(cost_raw.get("other_usd", 0), "cost.other_usd"),
        str(_required(cost_raw, "exclusions", "cost")),
    )
    return Campaign(
        path,
        name,
        str(raw.get("description", "")),
        prefix,
        duration,
        lifecycle,
        raw_max_parallel,
        tuple(raw_exclusive_resources),
        run_spec,
        run_recipe,
        fly,
        tuple(experiments),
        cost,
    )


def _recipe_plan_lines(recipe: Spec, indent: str = "  ") -> list[str]:
    lines: list[str] = []
    for workload in recipe.workloads:
        if workload.kind == "path":
            detail = f"existing read-only path {workload.path}"
        else:
            detail = f"{workload.bytes / 2**20:g} MiB in {workload.files} file(s)"
        lines.append(f"{indent}workload:      {workload.name}: {workload.kind}; {detail}")
    lines.append(f"{indent}tools:         {', '.join(tool.name for tool in recipe.tools)}")
    transfers = (
        sum(
            1
            for workload in recipe.workloads
            for tool in recipe.tools
            if tool.workloads is None or workload.name in tool.workloads
        )
        * recipe.protocol.repeats
    )
    lines.append(
        f"{indent}measurement:   up to {transfers} timed transfers per experiment; "
        f"{recipe.protocol.repeats} interleaved repeat(s), "
        f"cache={recipe.protocol.cache}, durable={recipe.protocol.durable}, "
        f"verify={recipe.protocol.verify}, probes={recipe.protocol.probes}"
    )
    return lines


def plan_lines(
    campaign: Campaign,
    *,
    instances: dict[str, tuple[Path, str]] | None = None,
) -> list[str]:
    lines = [f"Fly campaign: {campaign.name} - {campaign.description}"]
    lines.append(f"  native config: {campaign.fly.path}")
    lines.append(f"  run spec:      {campaign.run_spec}")
    lines.extend(_recipe_plan_lines(campaign.run_recipe))
    streams = ", ".join(str(value) for value in _IPERF_STREAM_COUNTS)
    if any(e.execution == "remote" for e in campaign.experiments):
        lines.append(
            f"  ceiling probes: remote experiments: {_IPERF_SECONDS} s iperf3 receiver goodput after "
            f"{_IPERF_OMIT_SECONDS} s warm-up with {streams} TCP stream(s)"
        )
    if any(e.execution == "local" for e in campaign.experiments):
        lines.append("  local copies:  both paths on the source Machine; destination Machine idle but still billed")
    lines.append(f"  syq revision:  {campaign.fly.syq_revision}")
    lines.append(f"  deploy region: {campaign.fly.region}")
    copies = len(campaign.experiments)
    active = min(campaign.max_parallel, copies)
    lines.append(
        f"  copies:         {copies} isolated app/Machine-pair copies "
        f"({'; '.join(campaign.fly.machine_descriptions)} per copy)"
    )
    steady_peak = active * len(campaign.fly.process_names)
    provision_peak = sum(
        sorted((campaign.provision_peak_machines(e) for e in campaign.experiments), reverse=True)[:active]
    )
    detail = ""
    if provision_peak != steady_peak:
        detail = f"; {steady_peak} while measuring, plus transient replacements during regional placement"
    lines.append(f"  peak Machines: {provision_peak} ({active} experiment{'s' if active != 1 else ''} at once{detail})")
    if campaign.fly.volume_gb:
        lines.append(f"  Volumes:       {copies * campaign.fly.volume_gb:g} GB across all copies, mounted at /volume")
    else:
        lines.append("  Volumes:       none")
    lines.append("  sharing:       no benchmark Machine is shared between experiments")
    if campaign.exclusive_resources:
        lines.append(
            f"  exclusive:     {', '.join(campaign.exclusive_resources)} "
            "(per-user host lock; separate users and hosts remain uncoordinated)"
        )
    for experiment in campaign.experiments:
        spec_detail = ""
        if experiment.run_spec != campaign.run_spec:
            spec_detail = f"; run_spec={experiment.run_spec}"
        lines.append(
            f"  experiment:    {experiment.name}: execution={experiment.execution}; "
            f"network={experiment.network}; storage={experiment.storage}; "
            f"regions={experiment.source_region}->{experiment.destination_region}{spec_detail}"
        )
        if experiment.run_spec != campaign.run_spec:
            lines.extend(_recipe_plan_lines(experiment.run_recipe, "    "))
    lines.append(f"  lifetime cap:  {campaign.max_duration_seconds:g} s for the whole campaign")
    noun = "app and its Volumes" if campaign.fly.volume_gb else "app and its Machines"
    lines.append(f"  lifecycle:     each {noun} are destroyed after its experiment")
    lines.append(
        f"  cost estimate: ${campaign.estimate_usd:.4f} for the lifetime cap "
        f"(${campaign.compute_estimate_usd:.4f} compute + ${campaign.volume_estimate_usd:.4f} Volumes"
        f" + ${campaign.cost.other_usd:.4f} declared other; rates checked {campaign.cost.checked_at})"
    )
    lines.append(f"  not estimated: {campaign.cost.exclusions}")
    lines.append("  cost caveat:   cleanup time can add cost; failed cleanup bills until the rollback succeeds")
    lines.append("  exposure:      no Fly Proxy services or public IPs; Machines communicate on the private 6PN")
    if instances is None:
        lines.append(
            f"  rollback:      destroy each generated {campaign.app_prefix} app; exact commands print before creation"
        )
    else:
        for experiment in campaign.experiments:
            state_path, app_name = instances[experiment.name]
            lines.append(f"  app ({experiment.name}): {app_name}")
            lines.append(f"  rollback ({experiment.name}): fly apps destroy {app_name} --yes")
            lines.append(f"  recovery ({experiment.name}): ./providers/fly/run destroy {state_path}")
    return lines


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_path(run_id: str) -> Path:
    return _repo_root() / "results" / "fly-state" / f"{run_id}.json"


def _result_dir(run_id: str) -> Path:
    return _repo_root() / "results" / "fly" / run_id


def _resource_lock_dir() -> Path:
    """Return a private per-user directory shared by checkouts on this host."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime)
        if not base.is_absolute():
            raise FlyError("XDG_RUNTIME_DIR must be an absolute path")
        try:
            base_info = base.stat()
        except OSError as e:
            raise FlyError(f"cannot inspect XDG_RUNTIME_DIR {base}: {e}") from e
        if (
            not stat.S_ISDIR(base_info.st_mode)
            or base_info.st_uid != os.getuid()
            or stat.S_IMODE(base_info.st_mode) != 0o700
        ):
            raise FlyError("XDG_RUNTIME_DIR must be a private directory owned by the current user")
        lock_dir = base / "syq-bench-fly-locks"
    else:
        lock_dir = Path(tempfile.gettempdir()) / f"syq-bench-fly-locks-{os.getuid()}"
    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as e:
        raise FlyError(f"cannot create resource-lock directory {lock_dir}: {e}") from e
    try:
        lock_info = lock_dir.lstat()
    except OSError as e:
        raise FlyError(f"cannot inspect resource-lock directory {lock_dir}: {e}") from e
    if (
        not stat.S_ISDIR(lock_info.st_mode)
        or lock_info.st_uid != os.getuid()
        or stat.S_IMODE(lock_info.st_mode) != 0o700
    ):
        raise FlyError(f"resource-lock directory {lock_dir} must be owned by the current user with mode 0700")
    return lock_dir


def _resource_lock_path(resource: str) -> Path:
    return _resource_lock_dir() / f"{resource}.lock"


@contextlib.contextmanager
def _exclusive_campaign_resources(campaign: Campaign):
    """Hold campaign-declared performance resources across provisioning, measurement, and cleanup."""
    locks = []
    try:
        for resource in sorted(campaign.exclusive_resources):
            path = _resource_lock_path(resource)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            lock = os.fdopen(fd, "r+")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                owner = lock.read().strip() or "owner unavailable"
                lock.close()
                raise FlyError(
                    f"exclusive resource {resource!r} is in use by another local campaign ({owner}); "
                    "wait for it to finish instead of overlapping measurements"
                ) from e
            lock.seek(0)
            lock.truncate()
            lock.write(f"pid {os.getpid()}, campaign {campaign.name}")
            lock.flush()
            locks.append(lock)
        yield
    finally:
        for lock in reversed(locks):
            lock.seek(0)
            lock.truncate()
            lock.flush()
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


def _atomic_state(path: Path, state: dict[str, Any], *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_states(campaign: Campaign) -> tuple[str, dict[str, tuple[Path, str]]]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{campaign.name}-{stamp}-{secrets.token_hex(3)}"
    instances: dict[str, tuple[Path, str]] = {}
    created: list[Path] = []
    try:
        for experiment in campaign.experiments:
            app_name = f"{campaign.app_prefix}-{secrets.token_hex(APP_SUFFIX_LENGTH // 2)}"
            path = _state_path(f"{run_id}-{experiment.name}")
            state = {
                "schema": STATE_SCHEMA,
                "run_id": run_id,
                "experiment": experiment.name,
                "campaign": str(campaign.path),
                "app_prefix": campaign.app_prefix,
                "app": {"name": app_name, "id": None, "org": None},
                "phase": "planned",
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "machines": {},
            }
            _atomic_state(path, state, create=True)
            created.append(path)
            instances[experiment.name] = (path, app_name)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return run_id, instances


def _read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise OwnershipError(f"cannot read state file {path}: {e}") from e
    if state.get("schema") != STATE_SCHEMA:
        raise OwnershipError(f"state file {path} has an unsupported schema")
    app = state.get("app")
    prefix = state.get("app_prefix")
    if not isinstance(app, dict) or not isinstance(prefix, str):
        raise OwnershipError(f"state file {path} is missing ownership fields")
    name = app.get("name")
    if not isinstance(name, str) or not name.startswith(f"{prefix}-") or not _NAME.fullmatch(name):
        raise OwnershipError(f"state file {path} has an invalid app name")
    return state


def _fly_binary() -> str:
    binary = shutil.which("fly") or shutil.which("flyctl")
    if binary is None:
        raise FlyError("flyctl is required (the executable may be named 'fly' or 'flyctl')")
    return binary


def _require_token() -> None:
    if not os.environ.get("FLY_API_TOKEN", "").strip():
        raise FlyError("FLY_API_TOKEN is missing; put it in .env.infra or export it")


def _require_provision_context() -> str:
    _require_token()
    org = os.environ.get("FLY_ORG", "").strip()
    if not org:
        raise FlyError("FLY_ORG is missing; put the target organization slug in .env.infra or export it")
    return org


def _fly_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    # FLY_ORG is syq-bench input. Passing it implicitly to flyctl can make
    # otherwise app-scoped cleanup fail before flyctl evaluates --app.
    env.pop("FLY_ORG", None)
    return env


def _run(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout: float = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=_fly_subprocess_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise FlyError(f"command timed out after {timeout:g}s: {shlex.join(argv)}") from e
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {result.returncode}"
        raise FlyError(f"{shlex.join(argv)}: {message}")
    return result


def _run_live(argv: list[str], *, timeout: float) -> None:
    print(f"+ {shlex.join(argv)}", flush=True)
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=_fly_subprocess_env(),
    )
    assert process.stdout is not None
    started = time.monotonic()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                _stop_process_group(process)
                raise FlyError(f"command timed out after {timeout:g}s: {shlex.join(argv)}")
            events = selector.select(timeout=min(1, remaining))
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    print(chunk.decode(errors="replace"), end="", flush=True)
                else:
                    selector.unregister(key.fileobj)
            if process.poll() is not None and not selector.get_map():
                break
    except BaseException:
        if process.poll() is None:
            _stop_process_group(process)
        raise
    finally:
        selector.close()
    if process.returncode:
        raise FlyError(f"command exited {process.returncode}: {shlex.join(argv)}")


def _stop_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    groups = _descendant_process_groups(process.pid)
    # Every caller creates this process with start_new_session=True, so its
    # PID is also the process-group ID even if it exits during this check.
    root_group = process.pid
    groups.add(root_group)
    own_group = os.getpgrp()
    if own_group in groups:
        raise FlyError(f"refusing to signal the runner's own process group {own_group}")

    # A nested command may start its own session (flyctl does, because
    # _run_live() must also be able to stop it independently). Signal those
    # groups before the ReFrame group so they cannot survive as reparented
    # orphans when the campaign deadline fires.
    ordered = sorted(groups - {root_group}) + [root_group]
    for group in ordered:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None and not any(_process_group_exists(group) for group in ordered):
            return
        time.sleep(0.1)

    for group in ordered:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def _descendant_process_groups(root_pid: int) -> set[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,pgid="],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return set()
    if result.returncode:
        return set()
    rows: dict[int, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, parent, group = map(int, fields)
        except ValueError:
            continue
        rows[pid] = (parent, group)

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in rows.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return {rows[pid][1] for pid in descendants if pid in rows}


def _process_group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _json(argv: list[str], *, timeout: float = 120) -> Any:
    result = _run(argv, timeout=timeout)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise FlyError(f"command did not return JSON: {shlex.join(argv)}") from e


def _ci_get(row: dict[str, Any], name: str) -> Any:
    return next((value for key, value in row.items() if key.lower() == name.lower()), None)


def _validate_org_slug(org: str) -> None:
    value = _json([_fly_binary(), "orgs", "list", "--json"])
    if isinstance(value, dict) and all(isinstance(slug, str) for slug in value):
        slugs = set(value)
    elif isinstance(value, list) and all(isinstance(row, dict) for row in value):
        slugs = {str(slug) for row in value if (slug := _ci_get(row, "slug")) is not None}
    else:
        raise FlyError("fly orgs list returned an unexpected JSON shape")
    if org not in slugs:
        raise FlyError("FLY_ORG must be an exact organization slug visible to this token, not its display name")


def _apps() -> list[dict[str, Any]]:
    value = _json([_fly_binary(), "apps", "list", "--json"])
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise FlyError("fly apps list returned an unexpected JSON shape")
    return value


def _find_app(name: str) -> dict[str, Any] | None:
    return next((row for row in _apps() if _ci_get(row, "name") == name), None)


def _app_identity(row: dict[str, Any]) -> str | None:
    value = _ci_get(row, "id")
    return None if value is None else str(value)


def _machine_group(machine: dict[str, Any]) -> str | None:
    config = _ci_get(machine, "config")
    if not isinstance(config, dict):
        return None
    metadata = _ci_get(config, "metadata")
    if not isinstance(metadata, dict):
        return None
    value = _ci_get(metadata, "fly_process_group")
    return None if value is None else str(value)


def _machines(app_name: str) -> list[dict[str, Any]]:
    value = _json([_fly_binary(), "machines", "list", "--app", app_name, "--json"])
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise FlyError("fly machines list returned an unexpected JSON shape")
    return value


def _wait_for_machine_pair(app_name: str, regions: dict[str, str], timeout: float = 300) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    next_report = 0.0
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = _machines(app_name)
        found: dict[str, str] = {}
        for machine in last:
            group = _machine_group(machine)
            state = str(_ci_get(machine, "state") or "").lower()
            machine_region = str(_ci_get(machine, "region") or "")
            machine_id = _ci_get(machine, "id")
            if group in regions and state == "started" and machine_region == regions[group] and machine_id:
                if group in found:
                    break
                found[group] = str(machine_id)
        else:
            if set(found) == {"source", "destination"} and len(last) == 2:
                return found
        now = time.monotonic()
        if now >= next_report:
            summary = ", ".join(
                f"{_machine_group(row) or '?'}:{_ci_get(row, 'state') or '?'}@{_ci_get(row, 'region') or '?'}"
                for row in last
            )
            print(f"waiting for one started source and destination Machine ({summary or 'none'})", flush=True)
            next_report = now + 10
        time.sleep(2)
    raise FlyError(f"Machines were not ready after {timeout:g}s; last response had {len(last)} Machine(s)")


def _benchmark_ssh_argv() -> list[str]:
    return [
        "ssh",
        "-i",
        "/root/.ssh/benchmark",
        "-p",
        str(_DESTINATION_SSH_PORT),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=10",
    ]


def _wait_for_destination_ssh(app_name: str, source_id: str, timeout: float = 120) -> None:
    destination_host = f"destination.process.{app_name}.internal"
    inner_command = shlex.join(
        [
            *_benchmark_ssh_argv(),
            "-o",
            "BatchMode=yes",
            f"root@{destination_host}",
            "true",
        ]
    )
    command = [
        _fly_binary(),
        "ssh",
        "console",
        "--quiet",
        "--app",
        app_name,
        "--machine",
        source_id,
        "--command",
        inner_command,
    ]
    deadline = time.monotonic() + timeout
    next_report = 0.0
    last_detail = "no connection attempt completed"
    while (remaining := deadline - time.monotonic()) > 0:
        result = _run(command, timeout=min(remaining, 30), check=False)
        if result.returncode == 0:
            print("source-to-destination SSH is ready", flush=True)
            return
        detail = (result.stderr or result.stdout).strip().splitlines()
        last_detail = detail[-1] if detail else f"exit {result.returncode}"
        now = time.monotonic()
        if now >= next_report:
            print(f"waiting for source-to-destination SSH ({last_detail})", flush=True)
            next_report = now + 10
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise FlyError(f"source-to-destination SSH was not ready after {timeout:g}s: {last_detail}")


def _ephemeral_ssh_env() -> str:
    with tempfile.TemporaryDirectory(prefix="syq-bench-fly-key-") as directory:
        private = Path(directory) / "id_ed25519"
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)], timeout=30)
        private_b64 = base64.b64encode(private.read_bytes()).decode("ascii")
        public = private.with_suffix(".pub").read_text().strip()
    return f"BENCH_SSH_PRIVATE_KEY_B64={json.dumps(private_b64)}\nBENCH_SSH_AUTHORIZED_KEY={json.dumps(public)}\n"


def provision(campaign: Campaign, state_path: Path) -> None:
    org = _require_provision_context()
    fly = _fly_binary()
    state = _read_state(state_path)
    if state.get("phase") != "planned" or state["app"].get("id") is not None:
        raise FlyError(f"state {state_path} is not a fresh planned campaign")
    experiment = next((e for e in campaign.experiments if e.name == state.get("experiment")), None)
    if experiment is None:
        raise FlyError(f"state {state_path} names an experiment outside campaign {campaign.name}")
    app_name = state["app"]["name"]
    budget = campaign.max_duration_seconds
    started = time.monotonic()
    state["started_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _atomic_state(state_path, state)
    print(f"creating owned Fly app {app_name}", flush=True)
    _run_live([fly, "apps", "create", app_name, "--org", org], timeout=min(budget, 120))

    app = _find_app(app_name)
    app_id = _app_identity(app or {})
    if app_id is None:
        raise OwnershipError(
            f"created {app_name}, but could not record its immutable ID; refusing automatic deletion. "
            f"Inspect it, then run: fly apps destroy {app_name} --yes"
        )
    state["app"].update({"id": app_id, "org": org})
    state["phase"] = "created"
    _atomic_state(state_path, state)

    secrets_env = _ephemeral_ssh_env()
    _run([fly, "secrets", "import", "--stage", "--app", app_name], input_text=secrets_env, timeout=120)
    print("uploaded an app-scoped ephemeral SSH key", flush=True)

    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap during setup")
    _run_live(
        [
            fly,
            "deploy",
            str(_repo_root()),
            "--app",
            app_name,
            "--config",
            str(campaign.fly.path),
            "--remote-only",
            "--ha=false",
            "--no-public-ips",
            "--yes",
        ],
        timeout=remaining,
    )
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap during deployment")
    _run_live(
        [
            fly,
            "scale",
            "count",
            "source=1",
            "destination=1",
            "--region",
            campaign.fly.region,
            "--app",
            app_name,
            "--yes",
        ],
        timeout=min(remaining, 300),
    )
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap while scaling")
    # fly deploy creates one Machine per process group in primary_region. To
    # move a group, create its replacement first (scale cannot revive a group
    # with no remaining Machine to clone), then remove the primary-region
    # Machine. Each group still has exactly one Machine while measuring.
    targets = {
        "source": experiment.source_region,
        "destination": experiment.destination_region,
    }
    for group, target in targets.items():
        if target == campaign.fly.region:
            continue
        for count, region in ((1, target), (0, campaign.fly.region)):
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap during regional placement")
            _run_live(
                [
                    fly,
                    "scale",
                    "count",
                    f"{group}={count}",
                    "--region",
                    region,
                    "--app",
                    app_name,
                    "--yes",
                ],
                timeout=min(remaining, 300),
            )
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap while placing Machines")
    machine_ids = _wait_for_machine_pair(app_name, targets, min(remaining, 300))
    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        raise FlyError(f"campaign exceeded its {budget:g}s lifetime cap while waiting for Machines")
    if experiment.execution == "remote":
        _wait_for_destination_ssh(app_name, machine_ids["source"], min(remaining, 120))
    state = _read_state(state_path)
    state["machines"] = machine_ids
    state["phase"] = "ready"
    _atomic_state(state_path, state)
    print(
        f"Fly Machine pair ready in {experiment.source_region}->{experiment.destination_region}",
        flush=True,
    )


def _toml_value(value: str) -> str:
    return json.dumps(value)


def _orchestration_record(campaign: Campaign) -> dict[str, Any]:
    with campaign.path.open("rb") as f:
        campaign_raw = tomllib.load(f)
    with campaign.fly.path.open("rb") as f:
        fly_raw = tomllib.load(f)
    fly_version = _run([_fly_binary(), "version"], timeout=30, check=False)
    provider_cli_version = (fly_version.stdout or fly_version.stderr).strip() or None
    return {
        "framework": {"name": "ReFrame", "version": importlib.metadata.version("reframe-hpc")},
        "provider": {"name": "fly.io", "cli_version": provider_cli_version},
        "campaign": campaign_raw,
        "fly_config": fly_raw,
    }


def _remote_run_argv(
    campaign: Campaign,
    experiment: Experiment,
    state: dict[str, Any],
    remote_result: str,
) -> list[str]:
    app_name = state["app"]["name"]
    destination_host = f"destination.process.{app_name}.internal"
    destination = (
        experiment.destination
        if experiment.execution == "local"
        else f"root@{destination_host}:{experiment.destination}"
    )
    region = (
        experiment.source_region
        if experiment.execution == "local"
        else experiment.source_region + "->" + experiment.destination_region
    )
    ssh = shlex.join(_benchmark_ssh_argv())
    try:
        run_spec_rel = experiment.run_spec.relative_to(_repo_root())
    except ValueError as e:
        raise CampaignError("run_spec must be inside the repository so the image contains it") from e
    name = f"{campaign.name}-{experiment.name}"
    return [
        "syq-bench",
        "run",
        f"/opt/syq-bench/{run_spec_rel}",
        "--set",
        f"name={_toml_value(name)}",
        "--set",
        f"description={_toml_value(campaign.description + ': ' + experiment.name)}",
        "--set",
        f"endpoints.source={_toml_value(experiment.source)}",
        "--set",
        f"endpoints.destination={_toml_value(destination)}",
        "--set",
        f"endpoints.ssh={_toml_value(ssh)}",
        "--set",
        f"environment.provider={_toml_value('fly.io')}",
        "--set",
        f"environment.network={_toml_value(experiment.network)}",
        "--set",
        f"environment.storage={_toml_value(experiment.storage)}",
        "--set",
        f"environment.region={_toml_value(region)}",
        "--out",
        remote_result,
        "--yes",
    ]


def _measure_network_ceiling(app_name: str, source_id: str) -> list[dict[str, Any]]:
    destination_host = f"destination.process.{app_name}.internal"
    probes = []
    for streams in _IPERF_STREAM_COUNTS:
        remote_command = shlex.join(
            [
                "iperf3",
                "--client",
                destination_host,
                "--port",
                str(_IPERF_PORT),
                "--time",
                str(_IPERF_SECONDS),
                "--parallel",
                str(streams),
                "--omit",
                str(_IPERF_OMIT_SECONDS),
                "--json",
            ]
        )
        result = _run(
            [
                _fly_binary(),
                "ssh",
                "console",
                "--quiet",
                "--app",
                app_name,
                "--machine",
                source_id,
                "--command",
                remote_command,
            ],
            timeout=30,
        )
        try:
            payload = json.loads(result.stdout)
            bits_per_second = float(payload["end"]["sum_received"]["bits_per_second"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise FlyError("iperf3 did not return a receiver-goodput measurement") from e
        if not math.isfinite(bits_per_second) or bits_per_second <= 0:
            raise FlyError("iperf3 returned a non-positive or non-finite receiver-goodput measurement")
        value = bits_per_second / 8 / 1_000_000
        print(f"measured private-network ceiling ({streams} stream(s)): {value:.1f} MB/s", flush=True)
        probes.append(
            {
                "name": "network_receive" if streams == _IPERF_STREAMS else f"network_receive_{streams}_stream",
                "value": value,
                "unit": "MB/s",
                "detail": (
                    f"iperf3 receiver goodput over private 6PN; {_IPERF_SECONDS} s measured after "
                    f"{_IPERF_OMIT_SECONDS} s warm-up, {streams} TCP stream(s)"
                ),
                "where": "path",
            }
        )
    return probes


def run_experiment(campaign: Campaign, state_path: Path, experiment_name: str, output_dir: Path) -> Path:
    state = _read_state(state_path)
    if state.get("phase") != "ready":
        raise FlyError(f"campaign infrastructure is not ready (phase={state.get('phase')!r})")
    experiment = next((item for item in campaign.experiments if item.name == experiment_name), None)
    if experiment is None:
        raise CampaignError(f"unknown experiment {experiment_name!r}")
    source_id = state.get("machines", {}).get("source")
    if not source_id:
        raise FlyError("state does not identify the source Machine")
    app_name = state["app"]["name"]
    network_probes = _measure_network_ceiling(app_name, source_id) if experiment.execution == "remote" else []
    # Fly's SSH exec channel tokenizes a command but does not interpret shell
    # operators such as ``&&``.  The image creates this owned directory, so run
    # the benchmark directly instead of composing it with a remote ``mkdir``.
    remote_result = f"/tmp/syq-bench/{state['run_id']}-{experiment.name}.json"
    remote_argv = _remote_run_argv(campaign, experiment, state, remote_result)
    remote_command = shlex.join(remote_argv)
    command = [
        _fly_binary(),
        "ssh",
        "console",
        "--quiet",
        "--app",
        app_name,
        "--machine",
        source_id,
        "--command",
        remote_command,
    ]
    error: BaseException | None = None
    try:
        _run_live(command, timeout=campaign.max_duration_seconds)
    except BaseException as e:
        error = e

    fetch = _run(
        [
            _fly_binary(),
            "ssh",
            "console",
            "--quiet",
            "--app",
            app_name,
            "--machine",
            source_id,
            "--command",
            f"cat {shlex.quote(remote_result)}",
        ],
        timeout=120,
        check=False,
    )
    local_result = output_dir / f"{experiment.name}.json"
    if fetch.returncode == 0:
        try:
            parsed = json.loads(fetch.stdout)
        except json.JSONDecodeError as e:
            raise FlyError(f"downloaded result for {experiment.name} is not valid JSON") from e
        if "orchestration" in parsed:
            raise FlyError(f"downloaded result for {experiment.name} already has orchestration metadata")
        result_probes = parsed.setdefault("probes", [])
        if not isinstance(result_probes, list):
            raise FlyError(f"downloaded result for {experiment.name} has invalid probes metadata")
        result_probes.extend(network_probes)
        parsed["orchestration"] = _orchestration_record(campaign)
        output_dir.mkdir(parents=True, exist_ok=True)
        with local_result.open("x") as f:
            json.dump(parsed, f, indent=2)
            f.write("\n")
        print(f"downloaded raw result: {local_result}", flush=True)
    elif error is None:
        raise FlyError(f"benchmark finished but its result could not be downloaded: {experiment.name}")
    if error is not None:
        raise error
    return local_result


def destroy(state_path: Path) -> bool:
    _require_token()
    state = _read_state(state_path)
    if state.get("phase") == "destroyed":
        return False
    app_name = state["app"]["name"]
    expected_id = state["app"].get("id")
    current = _find_app(app_name)
    if current is None:
        state["phase"] = "destroyed"
        state["destroyed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        _atomic_state(state_path, state)
        return False
    if expected_id is None:
        raise OwnershipError(
            f"state has no immutable ID for existing app {app_name}; refusing automatic deletion. "
            f"Inspect it, then run: fly apps destroy {app_name} --yes"
        )
    current_id = _app_identity(current)
    if current_id != expected_id:
        raise OwnershipError(
            f"app {app_name} now has ID {current_id!r}, not the owned ID {expected_id!r}; refusing deletion"
        )
    _run_live([_fly_binary(), "apps", "destroy", app_name, "--yes"], timeout=300)
    if _find_app(app_name) is not None:
        raise FlyError(f"Fly still reports app {app_name} after destroy")
    state["phase"] = "destroyed"
    state["destroyed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    _atomic_state(state_path, state)
    print(f"destroyed owned Fly app {app_name} (Machines, Volumes, secrets, and image)", flush=True)
    return True


def _run_worker(campaign: Campaign, state_path: Path, experiment_name: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"{experiment_name}.log"
    failure: BaseException | None = None
    cleanup_failure: BaseException | None = None
    with log_path.open("x", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print(f"starting isolated {experiment_name} infrastructure", flush=True)
            try:
                provision(campaign, state_path)
                run_experiment(campaign, state_path, experiment_name, output_dir)
            except BaseException as e:
                failure = e
                print(f"{experiment_name} failed: {e}", file=sys.stderr, flush=True)
            finally:
                try:
                    destroy(state_path)
                except BaseException as e:
                    cleanup_failure = e
                    print(f"{experiment_name} cleanup failed: {e}", file=sys.stderr, flush=True)
                    print(f"recovery: ./providers/fly/run destroy {state_path}", file=sys.stderr, flush=True)
            if failure is None and cleanup_failure is None:
                print(f"completed isolated {experiment_name} experiment", flush=True)
    if cleanup_failure is not None:
        raise cleanup_failure from failure
    if failure is not None:
        raise failure


def _relay_worker_logs(
    campaign: Campaign,
    output_dir: Path,
    offsets: dict[str, int],
    pending: dict[str, bytes],
    *,
    final: bool = False,
) -> None:
    for experiment in campaign.experiments:
        name = experiment.name
        path = output_dir / f"{name}.log"
        try:
            with path.open("rb") as stream:
                stream.seek(offsets.get(name, 0))
                chunk = stream.read()
                offsets[name] = stream.tell()
        except FileNotFoundError:
            continue
        data = pending.get(name, b"") + chunk
        lines = data.split(b"\n")
        if final:
            complete, remainder = lines, b""
        else:
            complete, remainder = lines[:-1], lines[-1]
        pending[name] = remainder
        for line in complete:
            if line:
                print(f"[{name}] {line.decode(errors='replace')}", flush=True)


def _wait_for_reframe(process: subprocess.Popen[Any], campaign: Campaign, output_dir: Path) -> int:
    deadline = time.monotonic() + campaign.max_duration_seconds
    next_status = time.monotonic() + 30
    offsets: dict[str, int] = {}
    pending: dict[str, bytes] = {}
    while process.poll() is None:
        _relay_worker_logs(campaign, output_dir, offsets, pending)
        now = time.monotonic()
        if now >= deadline:
            _stop_process_group(process)
            _relay_worker_logs(campaign, output_dir, offsets, pending, final=True)
            raise FlyError(f"campaign exceeded its {campaign.max_duration_seconds:g}s lifetime cap")
        if now >= next_status:
            print("Fly campaign workers are still running", flush=True)
            next_status = now + 30
        time.sleep(0.5)
    _relay_worker_logs(campaign, output_dir, offsets, pending, final=True)
    return process.wait()


def _reframe_command(campaign: Campaign, run_id: str, output_dir: Path) -> list[str]:
    reframe = shutil.which("reframe")
    if reframe is None:
        raise FlyError("ReFrame is missing; run through ./providers/fly/run so uv installs the cloud group")
    root = _repo_root()
    return [
        reframe,
        "--checkpath",
        str(root / "providers" / "fly" / "reframe_checks.py"),
        "-C",
        str(root / "providers" / "fly" / "reframe_config.py"),
        "--system=fly-orchestrator",
        "--run",
        "--exec-policy=async",
        "--stage",
        str(root / "reframe-stage" / run_id),
        "--output",
        str(root / "reframe-output" / run_id),
        "--report-file",
        str(root / "reframe-output" / run_id / "report.json"),
    ]


def _run_campaign(campaign: Campaign, *, yes: bool) -> int:
    org = _require_provision_context()
    _fly_binary()
    _validate_org_slug(org)
    if shutil.which("ssh-keygen") is None:
        raise FlyError("ssh-keygen is required to create the app-scoped ephemeral benchmark key")
    if shutil.which("ps") is None:
        raise FlyError("ps is required so a timed-out campaign cannot leave child processes running")
    if shutil.which("reframe") is None:
        raise FlyError("ReFrame is missing; run through ./providers/fly/run so uv installs the cloud group")

    with _exclusive_campaign_resources(campaign):
        return _run_campaign_exclusive(campaign, yes=yes)


def _run_campaign_exclusive(campaign: Campaign, *, yes: bool) -> int:
    run_id, instances = create_states(campaign)
    for line in plan_lines(campaign, instances=instances):
        print(line)
    if not yes and input("Create these billable resources and run the campaign? [y/N] ").strip().lower() != "y":
        for state_path, _ in instances.values():
            state_path.unlink()
        return 1

    output_dir = _result_dir(run_id)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "SYQ_BENCH_FLY_CAMPAIGN": str(campaign.path),
            "SYQ_BENCH_FLY_STATES": json.dumps({name: str(state_path) for name, (state_path, _) in instances.items()}),
            "SYQ_BENCH_FLY_RESULT_DIR": str(output_dir),
            "SYQ_BENCH_FLY_MAX_PARALLEL": str(campaign.max_parallel),
        }
    )
    command = _reframe_command(campaign, run_id, output_dir)
    process: subprocess.Popen[Any] | None = None
    returncode = 1
    cleanup_error: BaseException | None = None
    try:
        process = subprocess.Popen(command, env=env, start_new_session=True)
        try:
            returncode = _wait_for_reframe(process, campaign, output_dir)
        except FlyError as e:
            print(str(e), file=sys.stderr)
            returncode = 1
    except KeyboardInterrupt:
        if process is not None:
            _stop_process_group(process)
        print("campaign interrupted; destroying its Fly apps", file=sys.stderr)
        returncode = 130
    finally:
        for state_path, _ in instances.values():
            try:
                destroy(state_path)
            except BaseException as e:
                cleanup_error = e
                print(f"cleanup failed: {e}", file=sys.stderr)
                print(f"recovery: ./providers/fly/run destroy {state_path}", file=sys.stderr)
    if cleanup_error is not None:
        return 1
    return returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="syq-bench-fly")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="validate and print a campaign without accessing Fly")
    plan.add_argument("campaign", type=Path)
    run = sub.add_parser("run", help="create, run, and destroy a campaign")
    run.add_argument("campaign", type=Path)
    run.add_argument("--yes", action="store_true", help="accept the printed cost and resource plan")
    cleanup = sub.add_parser("destroy", help="destroy the exact app recorded in a recovery state file")
    cleanup.add_argument("state", type=Path)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("campaign", type=Path)
    worker.add_argument("state", type=Path)
    worker.add_argument("experiment")
    worker.add_argument("output_dir", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "plan":
            campaign = load_campaign(args.campaign)
            print("\n".join(plan_lines(campaign)))
            return 0
        if args.command == "run":
            return _run_campaign(load_campaign(args.campaign), yes=args.yes)
        if args.command == "_worker":
            _run_worker(
                load_campaign(args.campaign),
                args.state.resolve(),
                args.experiment,
                args.output_dir.resolve(),
            )
            return 0
        destroy(args.state.resolve())
        return 0
    except (CampaignError, FlyError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
