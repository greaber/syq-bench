"""Make an inspectable public capture from an explicitly selected private run.

This is an allowlist of measurement fields, not a promise to scrub arbitrary
free text. Inspect the returned data before publishing. Logs, shell commands,
cloud state, original host names and paths never pass through this function.
"""

from __future__ import annotations

import copy
from pathlib import PurePosixPath

from syq_bench.transport import observed_transports


def take(source: dict, keys: str) -> dict:
    return {key: copy.deepcopy(source[key]) for key in keys.split() if key in source}


def public_capture(run: dict, name: str) -> dict:
    result = take(run, "schema harness_version started finished seed filesystems")
    remote = ":" in run["destination"].split("/", 1)[0]
    remote_source = ":" in run["source"].split("/", 1)[0]
    result.update(
        source="remote-host:/benchmark/source" if remote_source else "/benchmark/source",
        destination="remote-host:/benchmark/destination" if remote else "/benchmark/destination",
    )
    result["aborted"] = "Run aborted; private diagnostic omitted" if run.get("aborted") else None
    result["host"] = take(run.get("host", {}), "kernel machine cpus python")
    result["host"]["hostname"] = "source-host"
    result["spec"] = take(run["spec"], "protocol workloads")
    result["spec"].update(
        name=name,
        description=name,
        endpoints={"source": result["source"], "destination": result["destination"], "ssh": "ssh"},
    )
    for workload in result["spec"].get("workloads", []):
        if "path" in workload:
            workload["path"] = "/benchmark/user-source"
    result["spec"]["tools"] = []
    for original in run["spec"].get("tools", []):
        tool = take(original, "name kind jobs workloads debug")
        tool["kind"] = original.get("kind", original["name"])
        if "binary" in original:
            tool["binary"] = tool["kind"]
        args = []
        for arg in original.get("args", []):
            if arg.startswith("/"):
                arg = "/redacted/" + PurePosixPath(arg).name
            elif "=" in arg and arg.partition("=")[2].startswith("/"):
                flag, _, path = arg.partition("=")
                arg = flag + "=/redacted/" + PurePosixPath(path).name
            args.append(arg)
        if "args" in original:
            tool["args"] = args
        result["spec"]["tools"].append(tool)
    result["tools"] = {}
    for tool_name, original in run.get("tools", {}).items():
        tool = take(original, "version sha256")
        spec = next((t for t in result["spec"]["tools"] if t["name"] == tool_name), {})
        tool["binary"] = spec.get("kind", tool_name)
        if original.get("remote"):
            rem = take(original["remote"], "version sha256")
            # Older collectors inspected bare rsync even when --rsync-path selected
            # a different helper. Do not publish that unrelated binary as the one used.
            original_spec = next((t for t in run["spec"].get("tools", []) if t["name"] == tool_name), {})
            requested = next(
                (a.partition("=")[2] for a in original_spec.get("args", []) if a.startswith("--rsync-path=")), None
            )
            observed = original["remote"].get("binary")
            matches = bool(observed) and (
                requested == observed or ("/" not in (requested or "") and requested == PurePosixPath(observed).name)
            )
            if requested and not matches:
                rem = {"version": None, "sha256": None}
            if not rem.get("sha256"):
                rem["note"] = "Remote binary identity unrecorded"
            tool["remote"] = rem
        result["tools"][tool_name] = tool
    result["cases"] = []
    for original in run.get("cases", []):
        case = take(original, "workload tool bytes files source_fs mode prepopulated_with too_short")
        case["source"] = result["source"]
        for field in ("error", "skipped", "curtailed"):
            case[field] = f"{field.capitalize()}; private diagnostic omitted" if original.get(field) else None
        case["repeats"] = []
        for rep in original.get("repeats", []):
            repeat = take(
                rep,
                "index wall_s user_s sys_s exit_code verified fsync_s cache metadata_cache "
                "changed_files changed_bytes delta_seed",
            )
            note = rep.get("fsync_note")
            repeat["fsync_note"] = (
                "not requested"
                if note == "not requested"
                else "Private flush diagnostic omitted; see fsync_s for measurement"
                if note
                else None
            )
            spec = next((t for t in result["spec"]["tools"] if t["name"] == case["tool"]), {})
            if spec.get("kind") == "syq" and (observed := observed_transports(rep)):
                repeat["observed_transports"] = observed
            case["repeats"].append(repeat)
        result["cases"].append(case)
    result["probes"] = []
    for original in run.get("probes", []):
        probe = take(original, "name value unit where")
        probe["detail"] = "Measured reference rate; private diagnostic omitted"
        result["probes"].append(probe)
    result["uncontrolled"] = []
    for condition in run.get("uncontrolled", []):
        if condition.startswith("metadata cache"):
            text = "Metadata cache is not evicted; fixture generation and verification warm it."
        elif condition.startswith("remote end"):
            text = "For requested eviction, remote fixture pages are evicted on the client only."
        elif "network" in condition:
            text = "Network state was not fully inspected."
        else:
            text = "An additional uncontrolled condition was recorded; private diagnostic omitted."
        result["uncontrolled"].append(text)
    result["network"] = None
    if run.get("network"):
        result["network"] = {
            phase: {
                "ping": (run["network"].get(phase) or {}).get("ping"),
                "missing": ["Private network details omitted"],
            }
            for phase in ("before", "after")
        }
    # Deliberately retain only public build provenance from provider state.
    orchestration = run.get("orchestration") or {}
    revision = orchestration.get("fly_config", {}).get("build", {}).get("args", {}).get("SYQ_REV")
    if revision:
        result["publication"] = {"syq_revision": revision, "build_type": "development", "provider": "fly.io"}
    return result
