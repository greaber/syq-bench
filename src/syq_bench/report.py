"""Turn a Run into a human table: per workload, each tool's median wall, throughput, and ratio to the first tool."""

from __future__ import annotations

import statistics

from syq_bench import probes
from syq_bench.runner import TOO_SHORT_S, Run


def render(run: Run) -> str:
    spec = run.spec
    lines = [
        f"syq-bench {run.harness_version}  {spec.get('name', '?')}  {run.started}  "
        f"{run.host['hostname']} ({run.host['platform']})"
    ]
    fs = run.filesystems
    lines.append(
        f"source {run.source} [{fs.get('source') or '?'}] -> destination {run.destination} "
        f"[{fs.get('destination') or '?'}]; seed {run.seed}"
    )
    for name, t in run.tools.items():
        sha = f"  sha256 {t['sha256'][:12]}" if t.get("sha256") else ""
        lines.append(f"  {name}: {t.get('version') or 'version unknown'}  ({t.get('binary')}){sha}")
    for p in run.probes:
        v = "n/a" if p["value"] is None else f"{p['value']:.0f} {p['unit']}"
        lines.append(f"  probe {p.get('where', ''):11} {p['name']:10} {v:>14}  {p['detail']}")
    if run.network:
        before = run.network.get("before", {})
        ping = before.get("ping") or {}
        drops = {k: v for k, v in run.network.items() if k.endswith("_during_run")}
        missing = before.get("missing") or []
        state = f"netem drops during run: {drops}" if drops else "no netem seen" if not missing else "not inspected"
        lines.append(
            f"  network: {ping.get('loss_pct', '?')}% ping loss, rtt {ping.get('rtt_avg_ms', '?')} ms; {state}"
            + (f" (missing: {', '.join(missing)})" if missing else "")
        )
    lines.append("")
    by_workload: dict[str, list] = {}
    for c in run.cases:
        by_workload.setdefault(c.workload, []).append(c)
    for workload, cases in by_workload.items():
        base = next((c for c in cases if c.good_walls()), cases[0])
        base_med = statistics.median(base.good_walls()) if base.good_walls() else None
        head = f"{workload}  ({cases[0].files} files, {cases[0].bytes / 2**20:.0f} MiB)"
        if cases[0].mode == "incremental":
            reps = [r for c in cases for r in c.repeats]
            pre = next((c.prepopulated_with for c in cases if c.prepopulated_with), "?")
            if reps:
                files = statistics.mean(r.changed_files for r in reps)
                mib = statistics.mean(r.changed_bytes for r in reps) / 2**20
                head += (
                    f"  re-sync: destination pre-populated with {pre}; "
                    f"{files:.1f} files / {mib:.1f} MiB changed per repeat "
                    "(mean; logical changes, not measured wire bytes)"
                )
        src = cases[0].source
        if src and src != run.source and not src.startswith(run.source.rstrip("/") + "/"):
            head += f"  from {src} [{cases[0].source_fs or '?'}]"
        lines.append(head)
        remote = ":" in run.destination.split("/", 1)[0] or ":" in run.source.split("/", 1)[0]
        fresh = cases[0].mode == "fresh"
        # A fresh copy must move the logical fixture across the path, so its
        # size gives a conservative end-to-end utilization and time floor.
        # Incremental tools can send anything from the changed bytes to most
        # of the basis tree; without wire counters, neither number supports a
        # capacity claim.
        bound = probes.ceiling(run.probes, cases[0].files, cases[0].bytes, remote) if fresh else None
        if bound:
            lines.append(f"  likely ceiling: {bound}")
        notes: set[str] = set()
        network_ceiling = (
            next(
                (
                    p["value"]
                    for p in run.probes
                    if p.get("name") == "network_receive" and p.get("value") and p.get("where") == "path"
                ),
                None,
            )
            if fresh
            else None
        )
        lines.append(
            f"  {'tool':10} {'median':>9} {'min':>9} {'max':>9} {'+fsync':>8} {'rate':>13} {'cpu%':>5} "
            f"{'vs ' + base.tool:>12}"
        )
        for c in cases:
            w = c.good_walls()
            bad = [r for r in c.repeats if r.exit_code != 0 or r.verified is False]
            if c.curtailed:
                notes.add(f"{c.tool}: {c.curtailed}")
            if bad and w:
                notes.add(
                    f"{c.tool}: {len(bad)} of {len(c.repeats)} repeats FAILED (exit or checksum); shown from the rest"
                )
            if c.skipped:
                lines.append(f"  {c.tool:10} skipped: {c.skipped}")
                continue
            if not w:
                lines.append(f"  {c.tool:10} failed: {c.error or 'verification'}")
                continue
            med = statistics.median(w)
            good = c.good()
            cpu = statistics.median((r.user_s + r.sys_s) / r.wall_s for r in good if r.wall_s) * 100
            fs = [r.fsync_s for r in good if r.fsync_s is not None]
            fsync = f"{statistics.median(fs):7.2f}s" if fs else "       -"
            # >1 means this tool took that many times longer than the first tool.
            ratio = f"{med / base_med:11.2f}x" if base_med else "           -"
            rate = statistics.median(c.moved_bytes(r) / r.wall_s for r in good if r.wall_s) / 1e6
            if network_ceiling:
                notes.add(
                    f"{c.tool}: end-to-end rate is {100 * rate / network_ceiling:.0f}% of the measured network "
                    "ceiling (a conservative estimate of transfer-phase utilization)"
                )
            lines.append(
                f"  {c.tool:10} {med:8.2f}s {min(w):8.2f}s {max(w):8.2f}s {fsync} "
                f"{rate:8.1f} MB/s {cpu:4.0f}% {ratio:>12}"
            )
        ran = [c for c in cases if not c.skipped]
        all_short = bool(ran) and all(c.too_short and c.good_walls() and not c.error for c in ran)
        if all_short:
            notes.add(
                f"every tool finished under {TOO_SHORT_S:.0f} s: the comparison says little; scale the workload up"
            )
        else:
            for c in cases:
                if c.too_short:
                    notes.add(
                        f"{c.tool} finished under {TOO_SHORT_S:.0f} s: its rate is a lower bound (ratios remain valid)"
                    )
        # Integrity notes cover every case and repeat, whatever the timing wording above said.
        for c in cases:
            for r in c.repeats:
                if r.cache == "evict-failed":
                    notes.add("cache eviction failed for some repeats; treat as warm")
                if r.cache == "warm":
                    notes.add("warm cache by request")
                if r.verified is None and r.exit_code == 0:
                    notes.add("not verified")
        for n in sorted(notes):
            lines.append(f"  note: {n}")
        lines.append("")
    if run.uncontrolled:
        lines.append("uncontrolled:")
        lines.extend(f"  - {u}" for u in run.uncontrolled)
    return "\n".join(lines)
