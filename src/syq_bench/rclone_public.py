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


def replacement_note(rows: list[dict]) -> str:
    reasons = []
    if len(rows) != 3 or {r["repeat"] for r in rows} != {0, 1, 2}:
        reasons.append("needs three runs")
    values = measurements(rows)
    if values is None:
        reasons.append("needs successful, verified copies")
    elif min(r["wall_s"] for r in rows) < 10:
        reasons.append("needs a larger workload (fastest run below 10 s)")
    if rows[0]["scenario"].startswith("wan-"):
        reasons.append("needs payload time well beyond startup")
    return "; ".join(reasons) or "three runs recorded; publication controls still require review"


def results_table(rows: list[dict]) -> str:
    groups = defaultdict(list)
    for row in rows:
        groups[row["tool"]].append(row)
    measured = {name: measurements(group) for name, group in groups.items()}
    maximum = max((m[3] for m in measured.values() if m), default=1) * 1.02
    body = ['<div class="copy-results">']
    for name, group in groups.items():
        row = group[0]
        values = measured[name]
        body.append(f'<article class="copy-result"><h4>{escape(backend(row))}</h4>')
        body.append(f'<p class="copy-settings">{escape(settings(row))}</p>')
        if values:
            speed, seconds, low, high = values
            body.append(
                f'<div class="copy-speed"><strong>{rate(speed)}</strong>'
                f"<span>{seconds:.3f} s mean · {len(group)} run(s)</span></div>"
                f'<div class="copy-track" role="img" aria-label="{escape(rate(speed))}; '
                f'observed range {escape(rate(low))} to {escape(rate(high))}">'
                f'<span class="copy-bar {"syq" if row["tool"] == "syq" else ""}" '
                f'style="width:{speed / maximum * 100:.3f}%"></span>'
            )
            if len(group) > 1:
                body.append(
                    f'<span class="copy-range" style="left:{low / maximum * 100:.3f}%;'
                    f'width:{(high - low) / maximum * 100:.3f}%"></span>'
                )
            body.append("</div>")
        else:
            body.append("<p><strong>No verified copy speed</strong></p>")
        body.append(f'<p class="copy-pending">Screening only: {escape(replacement_note(group))}.</p>')
        # Commands are visible beside each result, not behind the methodology link.
        body.append(f'<pre class="copy-command"><code>{escape(shlex.join(row["argv"]))}</code></pre>')
        body.append("<details><summary>Individual speeds, times and checks</summary><ul>")
        for r in group:
            item = measurements([r])
            speed_text = rate(item[0]) + " · " if item else ""
            body.append(f"<li>Run {r['repeat'] + 1}: {speed_text}{escape(timing(r))}. {escape(status(r))}.</li>")
        metadata = row["metadata"]
        scope = metadata["scope"] if metadata else "unrecorded"
        body.append(
            f"</ul><p>Metadata checked: {escape(scope)}. "
            f"Requested cache preparation: {escape(row['protocol']['cache'])}. "
            f"Final durability flush: {'yes' if row['protocol']['durable'] else 'no'}.</p></details></article>"
        )
    body.append("</div>")
    return "".join(body)


def preparation(case: dict, rows: list[dict]) -> str:
    row = rows[0]
    recipe = case["preparation"]
    command = (
        f'python3 make-files.py "$SRC" --files {recipe["files"]} '
        f"--size {recipe['size']} --directories {recipe['directories']} --seed {row['seed']}"
    )
    return (
        '<div class="copy-preparation"><h4>Prepare the data</h4>'
        f"<p>{escape(recipe['original'])}</p>"
        "<p>Simple equivalent workload for the rerun (not the generator used for these old timings):</p>"
        f"<pre><code>{escape(command)}</code></pre>"
        '<p><a href="make-files.py" download>Download make-files.py</a> · '
        '<a href="#manual">Run and check a copy without the harness</a>. '
        "SRC is a new directory under the source filesystem described above. "
        "These counts describe the old screening workload; increase them during calibration "
        "before the three-run campaign.</p>"
        f"<p>Recorded payload: {row['bytes']:,} bytes; {row['files']:,} files. "
        f"Client: {row['host']['cpus']} logical CPUs, "
        f"{escape(row['host']['machine'])}, kernel {escape(row['host']['kernel'])}. "
        "Executable hashes and full protocol fields are in the JSON download.</p></div>"
    )


def build(root: Path) -> Path:
    data = json.loads((root / "site/data/rclone-exploratory.json").read_text())
    body = (root / "site/rclone-body.html").read_text()
    featured = []
    for case in data["featured"]:
        rows = [r for r in data["rows"] if r["scenario"] == case["id"]]
        if not rows:
            raise ValueError(f"Missing case: {case['id']}")
        featured.append(
            f'<section class="comparison-case" id="{escape(case["id"])}">'
            f"<h3>{escape(case['title'])}</h3><p>{escape(case['workload'])}</p>"
            f'<p class="comparison-note">{escape(case["caveat"])}</p>'
            f"{preparation(case, rows)}{results_table(rows)}</section>"
        )
    selected = {c["id"] for c in data["featured"]}
    appendix = []
    for name in dict.fromkeys(r["scenario"] for r in data["rows"]):
        if name in selected:
            continue
        rows = [r for r in data["rows"] if r["scenario"] == name]
        appendix.append(f"<details><summary>{escape(name)}</summary>{results_table(rows)}</details>")
    body = body.replace("<!-- RESULTS -->", "".join(featured)).replace("<!-- APPENDIX -->", "".join(appendix))
    body = body.replace("<!-- LIMITS -->", "<ul>" + "".join(f"<li>{escape(s)}</li>" for s in data["limits"]) + "</ul>")
    nav = "".join(
        f'<a class="nav-item" href="#{anchor}">{label}</a>'
        for anchor, label in [
            ("reading", "Reading these results"),
            ("results", "Measured copies"),
            ("manual", "Simple reproduction"),
            ("settings", "Settings"),
            ("rails", "Eight network rails"),
            ("method", "Method and limits"),
            ("appendix", "Other trials"),
        ]
    )
    target = root / "site/rclone.html"
    target.write_text(shell("syq vs rclone — preliminary results", body, nav, page="rclone.html", all_results=True))
    return target
