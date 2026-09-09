"""Describe data paths without treating a planned TCP route as an observed one."""

from __future__ import annotations

import re


def observed_transports(repeat: dict) -> list[str]:
    """Extract only fixed transport names; never publish addresses or log text."""
    stored = repeat.get("observed_transports")
    if isinstance(stored, list) and stored and all(t in ("tcp", "ssh") for t in stored):
        return sorted(set(stored))
    log = repeat.get("stderr_tail") or ""
    observed = set()
    if re.search(r"^syq: .+: data connection via tcp \S", log, re.MULTILINE):
        observed.add("tcp")
    if re.search(r"^syq: .+: data over ssh \(", log, re.MULTILINE):
        observed.add("ssh")
    return sorted(observed)


def data_path(run: dict, case: dict, repeats: list[dict]) -> tuple[str, str]:
    """A compact label plus its basis, scoped to the given syq repeats."""
    endpoints = [case.get("source") or run.get("source"), run.get("destination")]
    if not all(isinstance(p, str) and p for p in endpoints):
        return "Unrecorded", "Source or destination was not recorded."
    remote = any(":" in p.split("/", 1)[0] for p in endpoints)
    if not remote:
        fs = run.get("filesystems") or {}
        # User-source workloads can bypass the run's source scratch filesystem.
        source_fs = case.get("source_fs") or fs.get("source")
        nfs = any(str(f).lower().startswith("nfs") for f in (source_fs, fs.get("destination")))
        if nfs:
            return "Local copy · NFS", "Local filesystem calls through an NFS mount; NFS carries the network traffic."
        return "Local copy", "Local filesystem calls; the particular kernel copy optimization was not recorded."
    observations = [observed_transports(rep) for rep in repeats]
    modes = sorted({mode for observation in observations for mode in observation})
    if modes:
        label = " + ".join(mode.upper() for mode in modes)
        if any(not observation for observation in observations):
            return label + " · partial record", "Some repeats lack transport evidence. Inspect the individual repeats."
        return label + " · observed", "Data-connection diagnostics recorded these paths, not just a reachability probe."
    spec = next((t for t in run["spec"].get("tools", []) if t["name"] == case["tool"]), {})
    if any(arg in ("--no-tcp", "--syq-no-tcp") for arg in spec.get("args", [])):
        return "SSH · requested", "TCP was disabled in the command; the actual data path was not logged."
    return "Unrecorded", "Actual TCP or SSH use was not recorded. Default settings do not rule out SSH fallback."
