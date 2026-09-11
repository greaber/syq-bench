"""Render the separate exploratory comparison from sanitized measurement data."""

from __future__ import annotations

import json
import math
import shlex
from collections import defaultdict
from html import escape
from pathlib import Path

from syq_bench.public import shell


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


def results_table(rows: list[dict]) -> str:
    groups = defaultdict(list)
    for row in rows:
        groups[row["tool"]].append(row)
    body = [
        '<div class="comparison-scroll" tabindex="0" role="region" aria-label="Result table">'
        '<table class="comparison-table"><thead><tr><th scope="col">Tool / backend</th>'
        '<th scope="col">Performance settings</th><th scope="col">Elapsed time, every run</th>'
        '<th scope="col">Checks</th></tr></thead><tbody>'
    ]
    for group in groups.values():
        row = group[0]
        times = "<br>".join(escape(timing(r)) for r in group)
        checks = "<br>".join(escape(s) for s in dict.fromkeys(status(r) for r in group))
        body.append(
            f'<tr><th scope="row">{escape(backend(row))}</th>'
            f'<td data-label="Settings"><code>{escape(settings(row))}</code></td>'
            f'<td class="times" data-label="Elapsed time, every run">{times}</td>'
            f'<td data-label="Checks">{checks}</td></tr>'
        )
    body.append("</tbody></table></div><details><summary>Commands, workload and verification details</summary>")
    body.append(
        "<p>Paths, accounts and endpoint addresses are replaced with placeholders. "
        "Commands show the recorded flags, including diagnostic logging where enabled. "
        "SSH_WRAPPER selects the test route and authentication; it is not a performance setting.</p>"
    )
    for group in groups.values():
        row = group[0]
        body.append(f"<h4>{escape(row['tool'])}</h4><pre><code>{escape(shlex.join(row['argv']))}</code></pre>")
        metadata = row["metadata"]
        scope = metadata["scope"] if metadata else "unrecorded"
        body.append(
            f"<p>Recorded payload: {row['bytes']:,} bytes; {row['files']:,} files. "
            f"Seed: {row['seed']}. Metadata scope: {escape(scope)}. "
            f"Requested cache preparation: {escape(row['protocol']['cache'])}. "
            f"Final durability flush requested: {row['protocol']['durable']}.</p>"
        )
    host = rows[0]["host"]
    filesystems = rows[0]["filesystems"]
    body.append(
        f"<p>Client host: {host['cpus']} logical CPUs, {escape(host['machine'])}, "
        f"kernel {escape(host['kernel'])}. Recorded filesystem types: "
        f"{escape(filesystems['source'])} → {escape(filesystems['destination'])}. "
        "The filesystem probe may report an ext-family filesystem as “ext2/ext3”. "
        "Tool version strings and executable SHA-256 hashes are in the JSON download.</p>"
    )
    body.append("</details>")
    return "".join(body)


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
            f"{results_table(rows)}</section>"
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
            ("settings", "Settings"),
            ("rails", "Eight network rails"),
            ("method", "Method and limits"),
            ("appendix", "Other trials"),
        ]
    )
    target = root / "site/rclone.html"
    target.write_text(shell("syq vs rclone — preliminary results", body, nav, page="rclone.html", all_results=True))
    return target
