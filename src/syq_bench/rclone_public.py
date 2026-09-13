"""Render the separate exploratory comparison from sanitized measurement data."""

from __future__ import annotations

import json
import math
import shlex
import statistics
from collections import defaultdict
from html import escape
from pathlib import Path

from syq_bench.public import rate, shell


def status(row: dict) -> str:
    if row["recorded_status"] == "timeout":
        return "Stopped at time limit; not a completed copy"
    if row["exit_code"] != 0 or row["case_error"] or row["content_verified"] is not True:
        return "Failed; not a completed copy"
    metadata = row["metadata"]
    if metadata is None:
        return "Contents checked; metadata check unrecorded"
    if metadata["mismatches"] != 0:
        return "Metadata mismatch"
    return "Contents and stated metadata checked"


def timing(row: dict) -> str:
    value = row["wall_s"]
    if value is None or (value == 0 and row["exit_code"] != 0):
        return "No copy timing"
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Duration must be finite and positive, or null")
    note = " · under 2 s" if value < 2 else ""
    if row["recorded_status"] == "timeout":
        note += " · time limit"
    elif row["exit_code"] != 0 or row["case_error"]:
        note += " · failed attempt"
    return f"{value:.3f} s{note}"


def backend(row: dict) -> str:
    if row["tool"] == "syq":
        return "syq"
    if any(arg.startswith(":sftp:") for arg in row["argv"]):
        return "rclone / SFTP"
    if any(arg.startswith(":webdav:") for arg in row["argv"]):
        return "rclone / HTTPS WebDAV"
    return "rclone / local filesystem"


def settings(row: dict) -> str:
    args = row["argv"]
    controls = [
        "--syq-connections",
        "--transfers",
        "--multi-thread-streams",
        "--sftp-chunk-size",
        "--sftp-concurrency",
        "--webdav-pacer-min-sleep",
        "--timeout",
    ]
    values = [f"{flag} {args[args.index(flag) + 1]}" for flag in controls if flag in args]
    return " ".join(values) or ("Automatic settings" if row["tool"] == "syq" else "Default performance settings")


def measurements(rows: list[dict]) -> tuple[float, float, float, float] | None:
    """Dataset bytes / mean command time, matching the main public page."""
    if any(status(r) != "Contents and stated metadata checked" for r in rows):
        return None
    for row in rows:
        timing(row)  # Validate successful times before arithmetic.
    payloads = {r["bytes"] for r in rows}
    if len(payloads) != 1 or any(
        isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0 for n in payloads
    ):
        raise ValueError("A speed requires the same positive finite payload in every repeat")
    times = [r["wall_s"] for r in rows]
    mean = statistics.mean(times)
    size = rows[0]["bytes"] / 1e6
    return size / mean, mean, size / max(times), size / min(times)


def nominal_ceiling(rows: list[dict]) -> float | None:
    """Shared recorded sender NIC rating, in decimal MB/s."""
    values = [r.get("network", {}).get("sender_nic_mbps") for r in rows]
    for value in values:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
        ):
            raise ValueError("Sender NIC speed must be finite and positive")
    if not values or None in values or len(set(values)) != 1:
        return None
    return values[0] / 8


def results_table(rows: list[dict]) -> str:
    groups = defaultdict(list)
    for row in rows:
        groups[row["tool"]].append(row)
    measured = {name: measurements(group) for name, group in groups.items()}
    maximum = max((m[3] for m in measured.values() if m), default=1) * 1.02
    ceiling = nominal_ceiling(rows)
    body = []
    if ceiling is not None:
        maximum = max(maximum, ceiling)
        body.append(
            f'<p class="copy-capacity">Nominal sender-link ceiling: {rate(ceiling)} '
            "(dashed line; before protocol overhead).</p>"
        )
    body.append('<div class="copy-chart" aria-label="Copy speeds on a shared scale">')
    commands, details = [], []
    for index, (name, group) in enumerate(groups.items()):
        row, values = group[0], measured[name]
        letter = chr(ord("A") + index)
        label = f"{letter}. {backend(row)}"
        body.append(
            f'<div class="copy-chart-row"><div class="copy-label"><b>{escape(label)}</b>'
            f"<small>{escape(settings(row))}</small></div>"
        )
        if values:
            speed, seconds, low, high = values
            body.append(
                f'<div class="copy-track" role="img" aria-label="{escape(rate(speed))}; '
                f'observed range {escape(rate(low))} to {escape(rate(high))}">'
                f'<span class="copy-bar {"syq" if row["tool"] == "syq" else ""}" '
                f'style="width:{speed / maximum * 100:.3f}%"></span>'
            )
            if ceiling is not None:
                body.append(f'<span class="copy-ceiling" style="left:{ceiling / maximum * 100:.3f}%"></span>')
            if len(group) > 1:
                body.append(
                    f'<span class="copy-range" style="left:{low / maximum * 100:.3f}%;'
                    f'width:{(high - low) / maximum * 100:.3f}%"></span>'
                )
            body.append(
                f'</div><div class="copy-speed"><strong>{rate(speed)}</strong>'
                f"<small>{seconds:.3f} s mean · {len(group)} measured "
                f"{'copy' if len(group) == 1 else 'copies'}</small></div>"
            )
        else:
            body.append('<div class="copy-track"></div><div class="copy-speed">No verified speed</div>')
        body.append("</div>")
        commands.append(
            f'<div class="copy-command-row"><b>{letter}</b>'
            f'<pre class="copy-command"><code>{escape(shlex.join(row["argv"]))}</code></pre></div>'
        )
        details.append(f"<h4>{escape(label)}</h4><ul>")
        for r in group:
            item = measurements([r])
            speed_text = rate(item[0]) + " · " if item else ""
            details.append(f"<li>Run {r['repeat'] + 1}: {speed_text}{escape(timing(r))}. {escape(status(r))}.</li>")
            if r.get("excluded_reason"):
                details.append(f"<li>Excluded from reporting series: {escape(r['excluded_reason'])}</li>")
        scope = row["metadata"]["scope"] if row["metadata"] else "unrecorded"
        details.append(
            f"</ul><p>Metadata checked: {escape(scope)}. "
            f"Requested cache preparation: {escape(row['protocol']['cache'])}. "
            f"Final durability flush: {'yes' if row['protocol']['durable'] else 'no'}.</p>"
        )
    body.append('</div><div class="copy-commands">' + "".join(commands) + "</div>")
    body.append("<details><summary>Individual runs and verification</summary>" + "".join(details) + "</details>")
    return "".join(body)


def reportable(rows: list[dict], *, capability: bool = False) -> bool:
    """Require verified copies; main comparisons require repeats and competitor duration."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["tool"]].append(row)
    return bool(groups) and all(
        measurements(group) is not None
        and (
            (len(group) == 1 and group[0]["repeat"] == 0)
            if capability
            else (
                len(group) == 3
                and {r["repeat"] for r in group} == {0, 1, 2}
                and (tool == "syq" or min(r["wall_s"] for r in group) >= 10)
            )
        )
        for tool, group in groups.items()
    )


def render(root: Path, data_path: str = "data/rclone-exploratory.json", body_path: str = "rclone-body.html") -> str:
    data = json.loads((root / "site" / data_path).read_text())
    body = (root / "site" / body_path).read_text()
    before, remainder = body.split("<!-- MULTIRAIL -->")
    rail_intro, after = remainder.split("<!-- /MULTIRAIL -->")
    body = before + after
    featured = []
    eligible = set()
    for case in data["featured"]:
        rows = [r for r in data["rows"] if r["scenario"] == case["id"]]
        if not rows:
            raise ValueError(f"Missing case: {case['id']}")
        if reportable(rows, capability=case.get("presentation") == "capability"):
            eligible.add(case["id"])
        featured.append(
            f'<section class="comparison-case" id="{escape(case["id"])}">'
            f"<h3>{escape(case['title'])}</h3><p>{escape(case['workload'])}</p>"
            f'<p class="comparison-note">{escape(case["caveat"])}</p>'
            f"{results_table(rows)}</section>"
        )
    sections = [
        ("lan", "LAN", ["lan-repeat-small"]),
        ("wan", "WAN", ["wan-repeat-one8g", "wan-repeat-many32g"]),
        (
            "nfs",
            "Mounted NFS",
            ["main-write", "nfs-repeat-small-read", "raid-explore-nfs-large-write", "raid-explore-nfs-large-read"],
        ),
        ("local", "Local filesystem", ["raid-explore-local-small", "raid-explore-local-large"]),
        ("rails", "Multi-rail LAN", ["fast-corrected-8g"]),
    ]
    rendered = dict(zip((c["id"] for c in data["featured"]), featured, strict=True))
    titles = {c["id"]: c["title"] for c in data["featured"]}
    if sorted(rendered) != sorted(case for _, _, cases in sections for case in cases):
        raise ValueError("Every featured comparison must appear once in navigation")
    content, links = [], []
    for anchor, label, cases in sections:
        if anchor == "local":
            content.append('<section id="capabilities"><h2>Filesystem and network capabilities</h2>')
            links.append(
                '<a class="nav-item comparison-nav-group" href="#capabilities">Filesystem and network capabilities</a>'
            )
        heading = "h3" if anchor in {"local", "rails"} else "h2"
        content.append(f'<section id="{anchor}"><{heading}>{label}</{heading}>')
        available = [case for case in cases if case in eligible]
        if not available:
            content.append("<p>Results forthcoming.</p>")
        if anchor == "rails" and available:
            content.append(rail_intro)
        nav_class = "comparison-nav-subgroup" if anchor in {"local", "rails"} else "comparison-nav-group"
        links.append(f'<a class="nav-item {nav_class}" href="#{anchor}">{label}</a>')
        for case in available:
            content.append(rendered[case])
            links.append(f'<a class="nav-item comparison-nav-case" href="#{case}">{escape(titles[case])}</a>')
        content.append("</section>")
        if anchor == "rails":
            content.append("</section>")
    body = body.replace("<!-- RESULTS -->", "".join(content))
    nav = "".join(links) + "".join(
        f'<a class="nav-item" href="#{anchor}">{label}</a>'
        for anchor, label in [
            ("method", "Method"),
            ("settings", "Transfer settings"),
        ]
    )
    return shell("syq vs rclone — preliminary results", body, nav, page="rclone.html", all_results=True)


def build(root: Path) -> Path:
    target = root / "site/rclone.html"
    target.write_text(render(root))
    return target
