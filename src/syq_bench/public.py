"""Public comparisons: explicit scenarios, paired runs, and inspectable evidence.

Presentation metadata supplies titles and a default syq entry, never timings or
winners. Every comparison uses tools from the same run. Older allocations stay
available separately, including failures; they are not pooled as identical hosts.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
import statistics
from dataclasses import dataclass
from importlib import resources

from syq_bench.transport import data_path


def esc(value: object) -> str:
    return html.escape(str(value))


def seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} s" if value < 10 else f"{value:.1f} s"


def size(value: int) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return "—"
    for divisor, unit, precision in ((1e12, "TB", 2), (1e9, "GB", 2), (1e6, "MB", 1), (1e3, "kB", 1)):
        if value >= divisor:
            number = f"{value / divisor:.{precision}f}".rstrip("0").rstrip(".")
            return f"{number} {unit}"
    return f"{value:g} B"


def rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.2f} GB/s" if value >= 1000 else f"{value:.1f} MB/s"


def positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def tool_spec(run: dict, name: str) -> dict:
    return next((t for t in run["spec"].get("tools", []) if t["name"] == name), {})


def kind(run: dict, name: str) -> str:
    return tool_spec(run, name).get("kind", name)


@dataclass
class Result:
    run: dict
    case: dict
    primary: str
    display_label: str | None = None

    @property
    def name(self) -> str:
        return self.case["tool"]

    @property
    def is_syq(self) -> bool:
        return kind(self.run, self.name) == "syq"

    @property
    def label(self) -> str:
        return self.display_label or ("syq" if self.name == self.primary else self.name)

    @property
    def repeats(self) -> list[dict]:
        return self.case.get("repeats", [])

    @property
    def reason(self) -> str | None:
        if self.run.get("aborted"):
            return "Run aborted; excluded from comparisons"
        if self.case.get("error") or self.case.get("skipped"):
            return str(self.case.get("error") or self.case["skipped"])
        if not self.repeats:
            return "No completed repeats"
        if not positive(self.case.get("bytes")) or not positive(self.case.get("files")):
            return "Empty or invalid workload size"
        if any(r.get("exit_code") != 0 for r in self.repeats):
            return "A repeat failed or timed out"
        if any(r.get("verified") is not True for r in self.repeats):
            return "Not fully checksum-verified"
        if any(not positive(r.get("wall_s")) for r in self.repeats):
            return "Invalid or missing duration"
        requested = self.run["spec"].get("protocol", {}).get("cache", "evict")
        if any(r.get("cache") != requested for r in self.repeats):
            return "Cache preparation failed or was not recorded"
        return None

    @property
    def walls(self) -> list[float]:
        return [r["wall_s"] for r in self.repeats] if self.reason is None else []

    @property
    def mean(self) -> float | None:
        return statistics.mean(self.walls) if self.walls else None

    @property
    def incremental(self) -> bool:
        return self.case.get("mode", "fresh") != "fresh"

    @property
    def short(self) -> bool:
        return bool(self.case.get("too_short")) or (self.mean is not None and self.mean < 2)

    @property
    def metric(self) -> float | None:
        if self.mean is None:
            return None
        # Use the same reference dataset size for copies and updates. This is
        # effective job throughput, not a measurement of transmitted bytes.
        return self.case["bytes"] / self.mean / 1e6


def cases(run: dict, primary: str, labels: dict | None = None) -> list[list[Result]]:
    groups: dict[tuple, list[Result]] = {}
    for case in run.get("cases", []):
        key = (case["workload"], case.get("mode", "fresh"), case["bytes"], case["files"])
        groups.setdefault(key, []).append(Result(run, case, primary, (labels or {}).get(case["tool"])))
    return list(groups.values())


def selected_cases(run: dict, meta: dict) -> list[list[Result]]:
    """Select whole workloads for display while keeping the full capture downloadable."""
    groups = cases(run, meta.get("primary", "syq"), meta.get("tool_labels"))
    selection = meta.get("workload_selection")
    if selection is None:
        return groups
    available = {rows[0].case["workload"] for rows in groups}
    if (
        not isinstance(selection, list)
        or not selection
        or any(not isinstance(name, str) for name in selection)
        or len(set(selection)) != len(selection)
        or not set(selection) <= available
    ):
        raise ValueError("workload_selection must name distinct workloads present in every selected capture")
    return [rows for rows in groups if rows[0].case["workload"] in selection]


def competitors(rows: list[Result]) -> list[Result]:
    return [r for r in rows if r.name == r.primary or not r.is_syq]


def standing(row: Result, rows: list[Result]) -> tuple[str, str]:
    valid = [r for r in rows if r.mean is not None]
    if row.reason:
        return row.reason, "na"
    if not valid or all(r.short for r in valid):
        return "Too short to rank", "na"
    if len(valid) < 2:
        return "No valid comparator", "na"
    best = min(valid, key=lambda r: r.mean)
    factor = row.mean / best.mean
    overlap = (
        len(row.walls) > 1
        and len(best.walls) > 1
        and max(row.walls) >= min(best.walls)
        and min(row.walls) <= max(best.walls)
    )
    if factor <= 1.05 or (factor <= 1.15 and overlap):
        return ("Fastest measured" if row is best else "Comparable"), "good"
    return f"{100 / factor:.2g}% of fastest", "warn"


def advantage(rows: list[Result]) -> tuple[float, Result] | None:
    drawn = competitors(rows)
    primary = next((r for r in drawn if r.name == r.primary), None)
    others = [r for r in drawn if r.name != r.primary and r.mean is not None]
    if primary is None or primary.mean is None or not others or standing(primary, drawn)[1] == "na":
        return None
    best = min(others, key=lambda r: r.mean)
    return best.mean / primary.mean, best


def bar_chart(rows: list[Result], *, controls: bool = False, labels: dict | None = None) -> str:
    drawn = rows if controls else competitors(rows)
    if not drawn:
        return "<p>No measurements.</p>"
    valid = [r for r in drawn if r.mean is not None]
    extreme = [r.case["bytes"] / min(r.walls) / 1e6 for r in valid]
    xmax = max(extreme, default=1) * 1.02
    out = [
        '<p class="chart-key">Effective speed · higher is better'
        " <span>Dataset size ÷ mean runtime; whiskers show the repeat range.</span></p>"
    ]
    if drawn[0].incremental:
        out.append(
            '<p class="metric-note">Updates can reuse existing data. This rate uses the full reference dataset, '
            "not the bytes sent over the network.</p>"
        )
    out.append('<div class="ladder">')
    for row in drawn:
        text, css = standing(row, drawn)
        value = row.metric
        width = (value or 0) / xmax * 100
        whisk = ""
        if len(row.walls) > 1:
            low, high = row.case["bytes"] / max(row.walls) / 1e6, row.case["bytes"] / min(row.walls) / 1e6
            whisk = (
                f'<span class="whisk" style="left:{low / xmax * 100:.2f}%;'
                f'width:{(high - low) / xmax * 100:.2f}%"></span>'
            )
        label = row.label if not controls else (labels or {}).get(row.name, row.name)
        classes = "syq" if row.is_syq else ""
        version = row.run.get("tools", {}).get(row.name, {}).get("version", "Version unrecorded")
        count = len(row.repeats)
        sub = f"{count} repeat{'s' if count != 1 else ''}"
        if row.short:
            sub += " · under 2 s"
        if row.case.get("curtailed"):
            sub += " · further repeats curtailed"
        shown = rate(value)
        elapsed = f"{seconds(row.mean)} mean" if row.mean is not None else "Elapsed unavailable"
        title = f"{version}; effective speed {shown}; {text}"
        transport = ""
        if row.is_syq:
            path, basis = data_path(row.run, row.case, row.repeats)
            transport = f'<small class="transport" title="{esc(basis)}">Data path: {esc(path)}</small>'
        out.extend(
            [
                f'<div class="name {classes}" title="{esc(version)}"><b>{esc(label)}</b>{transport}</div>',
                f'<div class="track" title="{esc(title)}"><span class="bar {classes}" '
                f'style="width:{width:.2f}%"></span>'
                f"{whisk}<small>{esc(sub)}</small></div>",
                f'<div class="t"><span class="s">{shown}</span><small class="elapsed">{elapsed}</small></div>',
                f'<div class="r"><span class="chip {css}" title="{esc(text)}">{esc(text)}</span></div>',
            ]
        )
    return "".join(out) + "</div>"


def workload_text(row: Result) -> str:
    c = row.case
    noun = "file" if c["files"] == 1 else "files"
    what = f"{c['files']:,} {noun} · {size(c['bytes'])} total"
    if not row.incremental:
        return what + " · fresh copy into an empty destination"
    w = next((w for w in row.run["spec"].get("workloads", []) if w["name"] == c["workload"]), {})
    mutation = {"blocks": "in-place block edits", "append": "appended data", "rewrite": "whole-file rewrites"}.get(
        w.get("mutate", "rewrite"), "edits"
    )
    return what + f" · existing destination · re-sync after {mutation}"


def capture_digest(run: dict) -> str:
    """Bind reviewed explanations to measurement content, not mutable filenames."""
    data = {k: v for k, v in run.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def explanation(rows: list[Result], meta: dict) -> str:
    note = meta.get("explanations", {}).get(rows[0].case["workload"])
    if not note or any(r.reason for r in rows) or advantage(rows) is None:
        return ""
    if capture_digest(rows[0].run) not in meta.get("explained_captures", []):
        return ""  # A new allocation, build or result needs a new interpretation.
    labels = {"control": "The controls show", "likely": "Likely reason", "unknown": "Not yet explained"}
    label = labels[note["basis"]]
    return f'<p class="explanation"><span>{esc(label)}</span> {esc(note["text"])}</p>'


def details_table(rows: list[Result]) -> str:
    out = [
        '<div class="tablewrap"><table class="detail"><caption>Every recorded repeat in this run</caption>'
        "<thead><tr><th>Tool</th><th>Repeat</th><th>Tool time</th><th>Post-copy flush</th><th>Exit</th>"
        "<th>Checksum</th><th>Cache</th><th>syq data path</th></tr></thead><tbody>"
    ]
    for r in rows:
        for repeat in r.repeats:
            path, basis = data_path(r.run, r.case, [repeat]) if r.is_syq else ("—", "")
            out.append(
                f"<tr><td>{esc(r.name)}</td><td>{esc(repeat.get('index', '?'))}</td>"
                f"<td>{seconds(repeat.get('wall_s'))}</td><td>{seconds(repeat.get('fsync_s'))}</td>"
                f"<td>{esc(repeat.get('exit_code'))}</td><td>{esc(repeat.get('verified'))}</td>"
                f"<td>{esc(repeat.get('cache', 'unrecorded'))}</td>"
                f'<td title="{esc(basis)}">{esc(path)}</td></tr>'
            )
    return "".join(out) + "</tbody></table></div>"


def provenance(run: dict) -> str:
    out = [
        '<details class="evidence"><summary>Versions, commands &amp; measurement conditions</summary>',
        '<p class="fine">A version string alone does not identify a development build. Local and remote '
        "hashes are reported separately; missing remote identities stay unrecorded.</p>",
        '<div class="tablewrap"><table class="detail"><thead><tr><th>Tool</th><th>Local version / SHA-256</th>'
        "<th>Remote identity</th><th>Arguments</th></tr></thead><tbody>",
    ]
    for name, tool in run.get("tools", {}).items():
        rem = tool.get("remote") or {}
        remote = (
            f"{rem.get('version') or 'Version unrecorded'} / {rem['sha256']}"
            if rem.get("sha256")
            else "Unrecorded"
            if ":" in run["destination"]
            else "Local operation"
        )
        args = tool_spec(run, name)
        out.append(
            f"<tr><td>{esc(name)}</td><td><code>{esc(tool.get('version'))}<br>"
            f"{esc(tool.get('sha256') or 'Unrecorded')}</code></td>"
            f"<td><code>{esc(remote)}</code></td><td><code>{esc(json.dumps(args))}</code></td></tr>"
        )
    out.append("</tbody></table></div>")
    conditions = {
        k: run.get(k)
        for k in ("started", "seed", "host", "filesystems", "publication", "uncontrolled", "aborted", "probes")
    }
    conditions["spec"] = run["spec"]
    out.append(f'<pre class="spec">{esc(json.dumps(conditions, indent=2))}</pre></details>')
    return "".join(out)


def run_html(run: dict, meta: dict, ordinal: int) -> str:
    primary = meta.get("primary", "syq")
    out = [f'<div class="run-heading"><span class="eyebrow">Run {ordinal} · {esc(run["started"][:10])}</span>']
    if run.get("_download"):
        out.append(f'<a href="{esc(run["_download"])}" download>Download measurements ↓</a>')
    out.append("</div>")
    for rows in selected_cases(run, meta):
        name = rows[0].case["workload"]
        titles = meta.get("workloads", {})
        out.append(
            f'<section class="case"><div '
            f'class="case-head"><h4>{esc(titles.get(name, name.replace("-", " ").capitalize()))}</h4></div>'
            f'<p class="what">{esc(workload_text(rows[0]))}</p>'
        )
        if edge := advantage(rows):
            factor, best = edge
            if factor >= 1.05:
                text = f"syq is {factor:.2f}× faster than {best.label} in this run."
            elif factor <= 1 / 1.05:
                text = f"{best.label} is {1 / factor:.2f}× faster than syq in this run."
            else:
                text = f"syq and {best.label} have speeds within 5% of each other."
            out.append(f'<p class="result-line">{esc(text)}</p>')
        out.append(explanation(rows, meta))
        out.append(bar_chart(rows))
        options = [r for r in rows if r.is_syq and r.name != primary]
        if options:
            out.append(
                '<details class="controls"><summary>Why these results? Explore the syq controls</summary>'
                '<p class="fine">These change the connection or worker settings. The main comparison uses the '
                "scenario’s default syq entry.</p>"
            )
            out.append(
                bar_chart([r for r in rows if r.is_syq], controls=True, labels=meta.get("controls")) + "</details>"
            )
        out.append(f"<details><summary>Inspect all repeats</summary>{details_table(rows)}</details></section>")
    out.append(provenance(run))
    return "".join(out)


def capacity(run: dict) -> str:
    values = {
        p["name"]: p["value"] for p in run.get("probes", []) if p.get("unit") == "MB/s" and positive(p.get("value"))
    }
    names = [
        ("network_receive_1_stream", "1 stream"),
        ("network_receive", "4 streams"),
        ("network_receive_8_stream", "8 streams"),
    ]
    measured = [(label, values[name]) for name, label in names if name in values]
    if not measured:
        return ""
    out = ['<details class="capacity"><summary>Network capacity checks</summary><div class="capacity-values">']
    for label, value in measured:
        out.append(f"<div><span>{label}</span><strong>{rate(value)}</strong></div>")
    out.append(
        '</div><p class="fine">iperf3 receiver goodput before this run. These are measured reference rates, '
        "not a theoretical ceiling or simultaneous wire-byte measurements. The benchmark times include "
        "tool startup and finishing work; iperf3 omits its initial warm-up.</p></details>"
    )
    return "".join(out)


def setup_details(meta: dict, runs: list[dict]) -> str:
    out = []
    if meta.get("setup"):
        out.append(f"<p>{esc(meta['setup'])}</p>")
    if meta.get("kind") == "cloud":
        unknown = sum(
            not (r.get("tools", {}).get(meta.get("primary", "syq"), {}).get("remote") or {}).get("sha256") for r in runs
        )
        identity_note = (
            f" {unknown} of these runs lack a recorded destination syq hash; see each run’s identity table."
            if unknown
            else " Destination syq hashes are recorded for every published run."
        )
        out.append(f"<p>{len(runs)} published run(s), shown separately.{esc(identity_note)}</p>")
    if not out:
        return ""
    return '<details class="setup"><summary>Test setup &amp; limitations</summary>' + "".join(out) + "</details>"


def reproduction(meta: dict) -> str:
    if manual := meta.get("manual_recipe"):
        return (
            '<details class="reproduce"><summary>Run this benchmark yourself <span>↓</span></summary>'
            '<p>Get the source from <a href="https://github.com/greaber/syq-bench">GitHub</a> '
            "(the repository will be available soon), then follow "
            f"<code>{esc(manual)}</code>. It includes the machine and filesystem setup, "
            "pinned build, commands and fixture specs. Supply your own server and empty scratch directories.</p>"
            '<p><a href="reproduce.html#local-storage">Local storage setup →</a></p></details>'
        )
    recipe = meta.get("_recipe")
    if not recipe:
        if meta.get("kind") != "hardware":
            return (
                '<p class="repro-note">Use the recorded spec with your own endpoints and credentials. '
                "This report does not include a provisioned-infrastructure recipe.</p>"
            )
        return ""
    path = meta["campaign"]
    return (
        f'<details class="reproduce" id="reproduce-{esc(meta["id"])}"><summary>Run this '
        f"benchmark yourself <span>↓</span></summary>"
        '<p>Get the source from <a href="https://github.com/greaber/syq-bench">GitHub</a> '
        "(the repository will be available soon). You need Python 3.13+, uv, flyctl, an OpenSSH client, "
        "and your own Fly account. No access to our machines or credentials is needed.</p>"
        '<p><a href="reproduce.html">Setup, credentials &amp; cleanup →</a></p>'
        f'<pre class="spec">cd syq-bench\n./providers/fly/run plan '
        f"{esc(path)}\n./providers/fly/run run {esc(path)}</pre>"
        f'<p class="fine">Campaign cap: {recipe["seconds"] / 60:g} minutes; each tool: '
        f"{recipe['tool_seconds']:g} seconds. "
        f"Configured estimate: ${recipe['estimate']:.4f} for compute and declared storage at the cap "
        f"(rates dated {esc(recipe['checked_at'])}). {esc(recipe['exclusions'])} are excluded. "
        "Cleanup may extend runtime and billing. The local plan shows cost assumptions before anything is created.</p>"
        f'<p class="fine">Pinned syq source: <code>{esc(recipe["revision"])}</code> (development build). '
        "Each new run records the actual installed tool identities.</p></details>"
    )


def stylesheet() -> str:
    root = resources.files("syq_bench")
    css = root.joinpath("fonts", "fonts.css").read_text()
    for filename in (
        "manrope-latin",
        "plexmono-400-latin",
        "plexmono-600-latin",
        "open-sans-400",
        "open-sans-600",
        "open-sans-700",
        "open-sans-800",
    ):
        data = base64.b64encode(root.joinpath("fonts", filename + ".woff2").read_bytes()).decode()
        reference = '{{ resource "fonts/' + filename + '.woff2" }}'
        css = css.replace(reference, f"data:font/woff2;base64,{data}")
    return (
        css
        + root.joinpath("brand.css").read_text()
        + root.joinpath("public.css").read_text()
        + root.joinpath("sidebar.css").read_text()
        + root.joinpath("controls.css").read_text()
    )


def search_index(pages: list[tuple[str, str, str]]) -> str:
    entries = [
        {"href": name, "title": title, "text": " ".join(html.unescape(re.sub(r"<[^>]*>", " ", body)).split())}
        for name, title, body in pages
    ]
    data = json.dumps(entries).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script id="site-search-index" type="application/json">{data}</script>'


def shell(title: str, body: str, nav: str = "", page: str = "index.html") -> str:
    script = "<script>" + resources.files("syq_bench").joinpath("public.js").read_text() + "</script>"
    script = "<script>" + resources.files("syq_bench").joinpath("sidebar.js").read_text() + "</script>" + script
    script += "<script>" + resources.files("syq_bench").joinpath("controls.js").read_text() + "</script>"
    page_links = "".join(
        f'<a class="page-link" href="{href}"'
        + (' aria-current="page"' if page == href else "")
        + f">{label}</a>"
        + (f'<div class="scenario-links">{nav}</div>' if href == "index.html" and nav else "")
        for href, label in (
            ("index.html", "Benchmarks"),
            ("reproduce.html", "Reproduce a result"),
            ("method.html", "How we measure"),
        )
    )
    site_nav = resources.files("syq_bench").joinpath("site-nav.html").read_text()
    site_nav = site_nav.replace('data-site="benchmarks"', 'data-site="benchmarks" aria-current="page"')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        f'<meta name="description" content="Measured file-copy performance, with reproducible '
        f'cloud recipes and results you can inspect."><title>{esc(title)} · syq</title>'
        f'<style>{stylesheet()}</style></head><body><a class="skip" href="#main">Skip to content</a>'
        f'{site_nav}<div class="app">'
        '<aside id="benchmark-sidebar"><div class="sidebar-scrollbox"><nav aria-label="Benchmark pages">'
        f'{page_links}</nav><div class="aside-links">'
        '<a href="https://greaber.github.io/syq/install.html">Get syq ↗</a></div></div></aside>'
        f'<main id="main">{body}<footer><a href="https://github.com/greaber/syq-bench">Benchmarks on GitHub ↗</a>'
        '<span>Measurements and methodology are available for inspection.</span><a href="method.html">Method '
        f"&amp; limitations</a></footer></main></div>{search_index([(page, title, body)])}{script}</body></html>"
    )


def public_scenarios(runs: list[dict], catalog: dict | None) -> list[dict]:
    names = dict.fromkeys(r["spec"]["name"] for r in runs)
    configured = catalog.get("scenarios", []) if catalog else [{"id": n, "title": n} for n in names]
    return [meta for meta in configured if meta["id"] in names]


def scenario_navigation(scenarios: list[dict], prefix: str = "") -> str:
    return "".join(
        f'<a class="nav-item" href="{prefix}#{esc(meta["id"])}"><span '
        f'class="nav-num">{i:02}</span><span>{esc(meta["title"])}</span></a>'
        for i, meta in enumerate(scenarios, 1)
    )


def render_public(runs: list[dict], catalog: dict | None = None) -> str:
    scenarios = public_scenarios(runs, catalog)
    groups = [
        (m, sorted([r for r in runs if r["spec"]["name"] == m["id"]], key=lambda r: r["started"], reverse=True))
        for m in scenarios
    ]
    nav = scenario_navigation(scenarios)
    body = [
        '<header class="hero"><div><h1 class="landing-title" aria-label="syq benchmarks">'
        '<span class="landing-wordmark">syq</span> '
        '<span class="landing-subtitle">benchmarks</span></h1>'
        "<p>Measured copy performance, with reproducible tests.</p>"
        '<nav class="landing-actions" aria-label="Explore benchmarks">'
        '<a class="landing-primary" href="reproduce.html">Reproduce a result</a>'
        '<a href="method.html">How we measure</a></nav></div></header>'
    ]
    for i, (meta, rs) in enumerate(groups, 1):
        cloud = meta.get("kind") == "cloud"
        label = (
            "Public cloud · recipe included"
            if cloud and meta.get("_recipe")
            else "Public cloud · recorded setup"
            if cloud
            else "Public rental · manual recipe included"
            if meta.get("kind") == "rental" and meta.get("manual_recipe")
            else "Public rental · recorded setup"
            if meta.get("kind") == "rental"
            else "Existing hardware · bring your own setup"
            if meta.get("kind") == "hardware"
            else "Recorded setup · see run details"
        )
        body.append(
            f'<section class="scenario" id="{esc(meta["id"])}"><div class="scenario-label"><span>{i:02}</span>'
            f"<span>{esc(label)}</span></div><h2>{esc(meta['title'])}</h2><p "
            f'class="desc">{esc(meta.get("description", ""))}</p>'
        )
        if meta.get("note"):
            body.append(f'<p class="scope-note">{esc(meta["note"])}</p>')
        body.append(setup_details(meta, rs))
        body.append(run_html(rs[0], meta, len(rs)))
        body.append(reproduction(meta))
        body.append(capacity(rs[0]))
        for ordinal, run in reversed(list(enumerate(reversed(rs[1:]), 1))):
            body.append(
                f'<details class="previous"><summary>Earlier run {ordinal} · '
                f"{esc(run['started'][:10])} — inspect measurements</summary>"
                f"{run_html(run, meta, ordinal)}{capacity(run)}</details>"
            )
        body.append("</section>")
    return shell("Benchmarks", "".join(body), nav)


def method_body() -> str:
    return resources.files("syq_bench").joinpath("method-body.html").read_text()


def render_pages(runs: list[dict], catalog: dict | None = None, reproduce_body: str = "") -> dict[str, str]:
    nav = scenario_navigation(public_scenarios(runs, catalog), prefix="index.html")
    pages = {
        "index.html": render_public(runs, catalog),
        "method.html": shell("How we measure", method_body(), nav, page="method.html"),
        "reproduce.html": shell(
            "Reproduce a benchmark",
            reproduce_body
            or "<h1>Reproduce a benchmark</h1><p>Use the recorded spec with your own endpoints and credentials.</p>"
            '<a href="index.html">← Results</a>',
            nav,
            page="reproduce.html",
        ),
    }

    index = search_index(
        [
            (
                name,
                {"index.html": "Benchmarks", "method.html": "How we measure", "reproduce.html": "Reproduce a result"}[
                    name
                ],
                page.split('<main id="main">', 1)[1].split("</main>", 1)[0],
            )
            for name, page in pages.items()
        ]
    )
    return {
        name: re.sub(r'<script id="site-search-index" type="application/json">.*?</script>', lambda _: index, page)
        for name, page in pages.items()
    }
