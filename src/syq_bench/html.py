"""A self-contained HTML report over one or more result files: no JavaScript, no external assets.

One run: environment, probes, and per-workload bar charts (median wall with a
min-max whisker, ceiling line from the probes). Several runs: the same for the
newest, then a trend chart per case series over time, so a regression shows
as a step in a line.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from syq_bench import probes
from syq_bench.analysis import (
    Benchmark,
    Key,
    Measurement,
    Standing,
    Summary,
    System,
    comparison_rows,
    current_rows,
    standings,
    summaries,
    trends,
)

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f"]
CSS = """
:root{
  --fg:#1f2430;
  --muted:#66707f;
  --line:#dde2ea;
  --bg:#fbfcfd;
  --card:#f1f4f8;
  --grid:#e3e8ef;
  --ink:#1f2430;
  --note-fg:#7a4a00;
  --note-bg:#fff6df;
  --note-line:#f2d48a;
  --bad:#b3261e;
  --good:#1f7a3a;
  --warn:#8a5700;
  --good-bg:#e8f6eb;
  --warn-bg:#fff4d6;
  --bad-bg:#fdecea;
}
@media (prefers-color-scheme: dark){
:root:not([data-theme="light"]){
    --fg:#e6e9ef;
    --muted:#9aa4b2;
    --line:#2b3240;
    --bg:#12161c;
    --card:#1a2029;
    --grid:#2b3240;
    --ink:#e6e9ef;
    --note-fg:#f2cf7a;
    --note-bg:#2a2210;
    --note-line:#5a4713;
    --bad:#ff7b72;
    --good:#5fcf7f;
    --warn:#f0be5b;
    --good-bg:#14291c;
    --warn-bg:#302610;
    --bad-bg:#321b1b;
  }
}
:root[data-theme="dark"]{
  --fg:#e6e9ef;
  --muted:#9aa4b2;
  --line:#2b3240;
  --bg:#12161c;
  --card:#1a2029;
  --grid:#2b3240;
  --ink:#e6e9ef;
  --note-fg:#f2cf7a;
  --note-bg:#2a2210;
  --note-line:#5a4713;
  --bad:#ff7b72;
  --good:#5fcf7f;
  --warn:#f0be5b;
  --good-bg:#14291c;
  --warn-bg:#302610;
  --bad-bg:#321b1b;
}
body{margin:0 auto;max-width:1100px;padding:24px;color:var(--fg);background:var(--bg)}
body{font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
h1{font-size:22px;margin:0 0 4px;text-wrap:balance}
h2{font-size:17px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:15px;margin:20px 0 6px}
.lede{max-width:850px}
.jump{margin:12px 0 24px}.jump a{color:inherit;margin-right:16px}
.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.04em;text-transform:uppercase}
td.num,th.num{text-align:right}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 16px;margin:8px 0}
.note{color:var(--note-fg);background:var(--note-bg);border:1px solid var(--note-line);border-radius:4px}
.note{padding:6px 10px;margin:6px 0;font-size:13px}
.bad{color:var(--bad)}.good{color:var(--good)}.warn{color:var(--warn)}
.chart{overflow-x:auto}
.summary-strip{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.metric{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:7px 11px}
.metric strong{font-size:17px;margin-right:4px}
.comparison-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;margin:8px 0 22px}
table.comparison{margin:0;min-width:760px}
.comparison th,.comparison td{padding:9px 10px;vertical-align:top}
.comparison thead th{background:var(--card);border-bottom:1px solid var(--line)}
.comparison .case{min-width:190px;position:sticky;left:0;background:var(--bg);z-index:1}
.comparison thead .case{background:var(--card);z-index:2}
.comparison td.result{min-width:135px;border-left:1px solid var(--line)}
.comparison .system{min-width:135px;border-left:1px solid var(--line);text-transform:none;letter-spacing:0}
.comparison .system-name{font-size:13px;color:var(--fg)}
.comparison .syq-system .system-name{color:var(--good)}
.comparison .value{font-size:15px;white-space:nowrap}
.comparison .sub{color:var(--muted);font-size:11px;line-height:1.35;margin-top:2px}
.comparison .badge{display:inline-block;font-size:11px;border-radius:10px;padding:1px 6px;margin-top:4px}
.comparison .badge{border:1px solid currentColor}
.comparison .leader strong{color:var(--good)}
.comparison .syq-good{background:var(--good-bg)}
.comparison .syq-warn{background:var(--warn-bg)}
.comparison .syq-bad{background:var(--bad-bg)}
.comparison .syq-warn .badge{color:var(--warn)}
.comparison .syq-bad .badge,.comparison .invalid .badge{color:var(--bad)}
.comparison .syq-good .badge,.comparison .leader .badge{color:var(--good)}
.comparison .missing{color:var(--muted);text-align:center}
.legend-items{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 18px;color:var(--muted);font-size:12px}
.swatch{display:inline-block;width:11px;height:11px;border:1px solid var(--line);border-radius:2px}
.swatch{vertical-align:-1px;margin-right:4px}
.swatch.good{background:var(--good-bg)}.swatch.warn{background:var(--warn-bg)}.swatch.bad{background:var(--bad-bg)}
svg{display:block;max-width:100%;height:auto;overflow:visible}
svg text{font:12px system-ui,sans-serif;fill:var(--fg)}
svg .grid{stroke:var(--grid)}svg .ink{stroke:var(--ink)}svg .dim{fill:var(--muted)}svg .dimline{stroke:var(--muted)}
.legend span{display:inline-block;margin-right:14px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:4px;vertical-align:-1px}
code{font-size:12px}
@media(max-width:700px){
  body{padding:16px}.comparison .case{position:static}.jump a{display:inline-block;margin-bottom:4px}
}
@media print{
  body{max-width:none}.jump{display:none}.comparison-wrap{overflow:visible}
  table.comparison{min-width:0}.comparison .case{position:static}
}
"""


def esc(v: object) -> str:
    return html.escape(str(v))


def fmt(v: float | None, unit: str = "s", digits: int = 2) -> str:
    return "-" if v is None else f"{v:.{digits}f}{unit}"


# -- charts -----------------------------------------------------------------------------------


def bar_chart(rows: list[Summary], ceiling_s: float | None) -> str:
    """Horizontal bars of median wall per tool, min-max whisker, optional dashed ceiling line."""
    rows = [r for r in rows if r.median_s is not None]
    if not rows:
        return '<p class="muted">no successful repeats</p>'
    label_w, w, bar_h, gap = 110, 700, 22, 8
    xmax = max([r.max_s or r.median_s for r in rows] + ([ceiling_s] if ceiling_s else [])) * 1.08
    scale = (w - label_w - 80) / xmax
    h = len(rows) * (bar_h + gap) + 24
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">']
    for i, r in enumerate(rows):
        y = i * (bar_h + gap) + 4
        x1 = label_w + r.median_s * scale
        color = PALETTE[i % len(PALETTE)]
        out.append(f'<text x="{label_w - 8}" y="{y + bar_h * 0.7}" text-anchor="end">{esc(r.key.tool)}</text>')
        out.append(
            f'<rect x="{label_w}" y="{y}" width="{x1 - label_w:.1f}" height="{bar_h}" fill="{color}" opacity="0.85"/>'
        )
        if r.min_s is not None and r.max_s is not None and r.n > 1:
            xa, xb = label_w + r.min_s * scale, label_w + r.max_s * scale
            ym = y + bar_h / 2
            out.append(f'<line x1="{xa:.1f}" y1="{ym}" x2="{xb:.1f}" y2="{ym}" class="ink" stroke-width="1.5"/>')
            out.append(f'<line x1="{xa:.1f}" y1="{ym - 5}" x2="{xa:.1f}" y2="{ym + 5}" class="ink"/>')
            out.append(f'<line x1="{xb:.1f}" y1="{ym - 5}" x2="{xb:.1f}" y2="{ym + 5}" class="ink"/>')
        rate = f" · {r.rate_mbs:.0f} MB/s" if r.rate_mbs else ""
        xt = max(x1, label_w + (r.max_s or 0) * scale) + 6
        out.append(f'<text x="{xt:.1f}" y="{y + bar_h * 0.7}">{fmt(r.median_s)}{rate}</text>')
    if ceiling_s:
        xc = label_w + ceiling_s * scale
        out.append(f'<line x1="{xc:.1f}" y1="0" x2="{xc:.1f}" y2="{h - 20}" class="dimline" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{xc:.1f}" y="{h - 6}" text-anchor="middle" class="dim">ceiling {fmt(ceiling_s)}</text>')
    out.append("</svg>")
    return "\n".join(out)


@dataclass
class Series:
    key: Key
    points: list[Summary]


def trend_chart(series: list[Series]) -> str:
    """Median wall over run time, one line per tool. X is the run index (equal spacing), labelled by date."""
    pts = [p for s in series for p in s.points if p.median_s is not None]
    if not pts:
        return '<p class="muted">no data</p>'
    dates = sorted({p.started for s in series for p in s.points})
    xi = {d: i for i, d in enumerate(dates)}
    w, h, ml, mr, mt, mb = 760, 240, 60, 20, 12, 44
    ymax = max(p.median_s for p in pts) * 1.1
    xs = lambda i: ml + (i * (w - ml - mr) / max(len(dates) - 1, 1))  # noqa: E731
    ys = lambda v: mt + (h - mt - mb) * (1 - v / ymax)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">']
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{ml}" y1="{ys(v):.1f}" x2="{w - mr}" y2="{ys(v):.1f}" class="grid"/>')
        out.append(f'<text x="{ml - 6}" y="{ys(v) + 4:.1f}" text-anchor="end">{v:.1f}s</text>')
    for d in dates:
        out.append(f'<text x="{xs(xi[d]):.1f}" y="{h - mb + 16}" text-anchor="middle" class="dim">{esc(d[:10])}</text>')
    legend = []
    for n, s in enumerate(series):
        color = PALETTE[n % len(PALETTE)]
        good = [p for p in s.points if p.median_s is not None]
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{xs(xi[p.started]):.1f},{ys(p.median_s):.1f}" for i, p in enumerate(good)
        )
        out.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
        for p in good:
            title = f"{p.key.tool} {p.started[:16]}: median {fmt(p.median_s)}, n={p.n}, {p.tool_id or ''}"
            cx, cy = xs(xi[p.started]), ys(p.median_s)
            out.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3.5" fill="{color}"><title>{esc(title)}</title></circle>'
            )
        legend.append(f'<span><i style="background:{color}"></i>{esc(s.key.tool)}</span>')
    out.append("</svg>")
    return "\n".join(out) + f'<div class="legend">{"".join(legend)}</div>'


# -- comparison overview ----------------------------------------------------------------------


def _short_version(version: str | None) -> str:
    if not version:
        return "version unknown"
    one_line = " ".join(version.split())
    return one_line if len(one_line) <= 32 else one_line[:29] + "…"


def _system_header(system: System) -> str:
    classes = "system syq-system" if system.is_syq else "system"
    build = system.tool_id or "identity unknown"
    if system.remote_unknown:
        build += f"+remote identity unrecorded (run {system.run_id})"
    config = f" · config {system.configuration}" if system.configuration != "{}" else ""
    return (
        f'<th class="{classes}" title="{esc(system.version or "version unknown")} · {esc(build + config)}">'
        f'<div class="system-name">{esc(system.tool)}</div>'
        f'<div class="sub">{esc(_short_version(system.version))}<br><code>{esc(build)}</code></div></th>'
    )


def _result_cell(system: System, measurement: Measurement, standing: Standing) -> str:
    summary = measurement.summary
    classes = ["result"]
    if standing.leader:
        classes.append("leader")
    if system.is_syq:
        classes.append(
            {
                "comparable": "syq-good",
                "slower": "syq-warn",
                "much-slower": "syq-bad",
                "n/a": "invalid",
            }[standing.verdict]
        )
    elif standing.verdict == "n/a":
        classes.append("invalid")

    value = fmt(summary.median_s)
    if summary.too_short and summary.median_s is not None:
        hint = (
            "under 2 s: rate is a lower bound; ranks against a longer result, "
            "but a row where every result is this short stays unranked"
        )
        value = f"{value} <span class=muted title='{hint}'>&lt;2s</span>"
    value = f"<strong>{value}</strong>" if standing.leader else value
    if standing.verdict == "n/a":
        badge = standing.reason or "not comparable"
    elif standing.fastest:
        badge = "fastest rankable"
    elif standing.leader:
        badge = "comparable to fastest"
    elif standing.verdict == "much-slower":
        badge = f"{standing.ratio:.1f}× slower · much slower"
    else:
        badge = f"{standing.ratio:.2f}× slower"
    badge = " ".join(badge.split())
    if len(badge) > 84:
        badge = badge[:81] + "…"

    if summary.min_s is None:
        spread = "no successful range"
    else:
        spread = f"{fmt(summary.min_s)}–{fmt(summary.max_s)} · n={summary.n}"
    rate = f"{summary.rate_mbs:.1f} MB/s · " if summary.rate_mbs is not None else ""
    cache = ""
    if summary.actual_caches != (summary.key.cache,):
        actual = ", ".join(summary.actual_caches) if summary.actual_caches else "unrecorded"
        cache = f"<br>actual cache: {esc(actual)}"
    history = f" · latest of {measurement.seen}" if measurement.seen > 1 else ""
    if summary.curtailed:
        history += f"<br>{esc(summary.curtailed)}"
    return (
        f'<td class="{" ".join(classes)}"><div class="value">{value}</div>'
        f'<div class="sub">{rate}{spread}<br>{esc(summary.started[:10])}{history}{cache}</div>'
        f'<span class="badge">{esc(badge)}</span></td>'
    )


def _system_sort(system: System) -> tuple:
    return (not system.is_syq, system.tool, system.version or "", system.identity, system.configuration)


def _ranked(measurements: dict[System, Measurement]) -> dict[System, Standing]:
    systems = sorted(measurements, key=_system_sort)
    return dict(zip(systems, standings([measurements[s].summary for s in systems]), strict=True))


def _current_table(groups) -> list[str]:
    """One column per tool name; each cell is the newest measurement for that row, build shown inside."""
    parts = []
    for (spec, where), group_rows in sorted(groups.items()):
        group_rows.sort(key=lambda item: (item[0].workload, item[0].bytes, item[0].files, item[0].mode))
        is_syq = {t: sys.is_syq for _, m in group_rows for t, (sys, _m) in m.items()}
        tools = sorted({t for _, m in group_rows for t in m}, key=lambda t: (not is_syq[t], t))
        parts.append(f"<h3>{esc(spec)} <span class=muted>{esc(where)}</span></h3>")
        parts.append('<div class="comparison-wrap"><table class="comparison"><thead><tr><th class="case">case</th>')
        for t in tools:
            cls = "system syq-system" if is_syq[t] else "system"
            parts.append(
                f'<th class="{cls}"><div class="system-name">{esc(t)}</div><div class="sub">newest build</div></th>'
            )
        parts.append("</tr></thead><tbody>")
        for benchmark, by_tool in group_rows:
            order = [t for t in tools if t in by_tool]
            ranked = dict(zip(order, standings([by_tool[t][1].summary for t in order]), strict=True))
            parts.append(
                f'<tr><td class="case"><strong>{esc(benchmark.workload)}</strong>'
                f'<div class="sub">{esc(benchmark.shape())}</div></td>'
            )
            for t in tools:
                if t not in by_tool:
                    parts.append('<td class="result missing" aria-label="not measured">—</td>')
                    continue
                sys, m = by_tool[t]  # the original System: its ordinal and identity are the real ones
                cell = _result_cell(sys, m, ranked[t])
                # Full provenance of the system behind this newest measurement: version, build identity,
                # configuration, and whether the remote end's binary was actually identified.
                ident = sys.tool_id or "identity unknown"
                if sys.remote_unknown:
                    ident += f" + remote identity unrecorded (run {sys.run_id})"
                config = f" · config {sys.configuration}" if sys.configuration != "{}" else ""
                prov = esc(f"{_short_version(sys.version)} {ident}{config}")
                cell = cell.replace(
                    '<span class="badge">',
                    f'<div class="sub provenance"><code>{prov}</code></div><span class="badge">',
                    1,
                )
                parts.append(cell)
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
    return parts


def comparison_section(runs: list[dict]) -> str:
    rows = comparison_rows(runs)
    if not rows:
        return ""
    groups: dict[tuple[str, str], list[tuple[Benchmark, dict[System, Measurement]]]] = {}
    for benchmark, measurements in rows.items():
        groups.setdefault((benchmark.spec, benchmark.where()), []).append((benchmark, measurements))
    current_groups: dict[tuple[str, str], list[tuple[Benchmark, dict[str, tuple[System, Measurement]]]]] = {}
    for benchmark, by_tool in current_rows(rows).items():
        current_groups.setdefault((benchmark.spec, benchmark.where()), []).append((benchmark, by_tool))

    all_systems = {system for measurements in rows.values() for system in measurements}
    # The headline counters describe the overview (newest measurement per tool), not the
    # historical exact-build grid, so they match what the reader sees first.
    alarms = 0
    comparable = 0
    for by_tool in current_rows(rows).values():
        order = list(by_tool)
        for t, standing in zip(order, standings([by_tool[t][1].summary for t in order]), strict=True):
            sys = by_tool[t][0]
            if sys.is_syq and standing.verdict == "much-slower":
                alarms += 1
            if sys.is_syq and standing.verdict == "comparable":
                comparable += 1

    parts = [
        '<section id="comparison"><h2>Benchmark comparison</h2>',
        '<p class="lede">Each row is one named scenario, host pair, filesystem pair, workload shape, mode, and '
        "cache policy; each column is a tool as the spec names it, showing its newest measurement with the build "
        "that produced it. Lower wall time is better. Scenario names define comparison groups, so only "
        "intentionally equivalent runs should share a name. The exact build-by-build grid is below.</p>",
        '<div class="summary-strip">',
        f'<div class="metric"><strong>{len(groups)}</strong>scenario(s)</div>',
        f'<div class="metric"><strong>{len(rows)}</strong>benchmark case(s)</div>',
        f'<div class="metric"><strong>{len(all_systems)}</strong>system build/configuration(s)</div>',
        f'<div class="metric good"><strong>{comparable}</strong>syq result(s) in the leading group (overview)</div>',
        f'<div class="metric {"bad" if alarms else "muted"}"><strong>{alarms}</strong>'
        "syq result(s) at least 2× slower (overview)</div>",
        "</div>",
        '<div class="legend-items">'
        '<span><i class="swatch good"></i>syq comparable to the fastest</span>'
        '<span><i class="swatch warn"></i>syq measurably slower</span>'
        '<span><i class="swatch bad"></i>syq at least 2× slower</span>'
        "<span><strong>bold</strong> = leading group</span></div>",
        '<div class="note">Comparable at this resolution means within 5% by median or overlapping repeat ranges. '
        "It means these data do not support ranking the systems, not that they are proven equal. Failed, partially "
        "verified, and cache-mismatched results stay visible but are excluded from ranking. A result under 2 s "
        "(marked &lt;2s) ranks against a trustworthy longer result - its wall time is real, only its rate "
        "is a lower bound - but a row where every result is under 2 s stays unranked as startup noise.</div>",
    ]
    parts.extend(_current_table(current_groups))
    parts.append(
        '<details class="all-builds"><summary>All builds and configurations (exact-identity grid, for '
        "regression reading; a dash means that build was never run on that row)</summary>"
    )
    for (spec, where), group_rows in sorted(groups.items()):
        group_rows.sort(key=lambda item: (item[0].workload, item[0].bytes, item[0].files, item[0].mode))
        systems = sorted({system for _, measurements in group_rows for system in measurements}, key=_system_sort)
        parts.append(f"<h3>{esc(spec)} <span class=muted>{esc(where)}</span></h3>")
        parts.append('<div class="comparison-wrap"><table class="comparison"><thead><tr><th class="case">case</th>')
        parts.extend(_system_header(system) for system in systems)
        parts.append("</tr></thead><tbody>")
        for benchmark, measurements in group_rows:
            ranked = _ranked(measurements)
            parts.append(
                f'<tr><td class="case"><strong>{esc(benchmark.workload)}</strong>'
                f'<div class="sub">{esc(benchmark.shape())}</div></td>'
            )
            for system in systems:
                measurement = measurements.get(system)
                parts.append(
                    _result_cell(system, measurement, ranked[system])
                    if measurement
                    else '<td class="result missing" aria-label="not measured">—</td>'
                )
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
    parts.append("</details></section>")
    return "\n".join(parts)


# -- sections ----------------------------------------------------------------------------------


def run_section(run: dict) -> str:
    fs = run.get("filesystems", {})
    h = run["host"]
    parts = [
        f'<section class="run"><h2>Run {esc(run["started"])} · <code>{esc(run["spec"].get("name", "?"))}</code></h2>'
    ]
    parts.append('<div class="card">')
    parts.append(f"<div>{esc(run['spec'].get('description', ''))}</div>")
    parts.append(
        f"<div class=muted>{esc(h['hostname'])} · {esc(h.get('platform', ''))} · kernel {esc(h.get('kernel', '?'))} · "
        f"{h.get('cpus', '?')} CPUs · syq-bench {esc(run['harness_version'])} · seed {run['seed']}</div>"
    )
    parts.append(
        f"<div>source <code>{esc(run['source'])}</code> [{esc(fs.get('source') or '?')}] → destination "
        f"<code>{esc(run['destination'])}</code> [{esc(fs.get('destination') or '?')}]</div>"
    )
    parts.append("<table><tr><th>tool</th><th>version</th><th>binary</th><th>sha256</th><th>remote end</th></tr>")
    spec_tools = {tool.get("name"): tool for tool in run["spec"].get("tools", [])}
    remote_run = ":" in run["destination"].split("/", 1)[0] or ":" in run["source"].split("/", 1)[0]
    for name, t in run.get("tools", {}).items():
        rem = t.get("remote")
        spec_tool = spec_tools.get(name) or {}
        no_bootstrap = {"--no-bootstrap", "--syq-no-bootstrap"}.intersection(spec_tool.get("args", []))
        stale_managed_note = (rem or {}).get("note", "").startswith("managed helper:")
        if (
            remote_run
            and spec_tool.get("kind") == "syq"
            and no_bootstrap
            and not (rem or {}).get("sha256")
            and (not rem or stale_managed_note)
        ):
            remote = "remote PATH binary identity not recorded"
        else:
            remote = (
                "-" if not rem else f"{rem.get('version') or rem.get('note') or '?'} {(rem.get('sha256') or '')[:12]}"
            )
        parts.append(
            f"<tr><td>{esc(name)}</td><td>{esc(t.get('version') or '?')}</td>"
            f"<td><code>{esc(t.get('binary'))}</code></td>"
            f"<td><code>{esc((t.get('sha256') or '')[:12])}</code></td><td><code>{esc(remote)}</code></td></tr>"
        )
    parts.append("</table>")
    if not run["spec"].get("protocol", {}).get("verify", True):
        parts.append('<div class="note">verification was off for this run: copies were not checksum-compared</div>')
    if run.get("probes"):
        parts.append("<table><tr><th>where</th><th>probe</th><th class=num>value</th><th>detail</th></tr>")
        for p in run["probes"]:
            v = "n/a" if p["value"] is None else f"{p['value']:.0f} {p['unit']}"
            parts.append(
                f"<tr><td>{esc(p.get('where', ''))}</td><td>{esc(p['name'])}</td><td class=num>{esc(v)}</td>"
                f"<td class=muted>{esc(p['detail'])}</td></tr>"
            )
        parts.append("</table>")
    remote_run = ":" in run["destination"].split("/", 1)[0] or ":" in run["source"].split("/", 1)[0]
    if remote_run and not run.get("network"):
        parts.append('<div class="note">network state was not recorded for this run (older harness)</div>')
    if run.get("network"):
        n = run["network"]
        before = n.get("before", {})
        ping = before.get("ping") or {}
        drops = {k.replace("_netem_dropped_during_run", ""): v for k, v in n.items() if k.endswith("_during_run")}
        missing = before.get("missing") or []
        if drops:
            state = f"netem packets dropped during the run: {esc(drops)}"
        elif missing:
            state = f"<span class=bad>path not fully inspected</span> (missing: {esc(', '.join(missing))})"
        else:
            state = "no netem on either end"
        parts.append(
            f"<div>network: {ping.get('loss_pct', '?')}% ping loss, rtt {ping.get('rtt_avg_ms', '?')} ms; {state}</div>"
        )
        for end in ("local", "remote"):
            q = before.get(f"{end}_qdisc")
            if q and "netem" in q:
                parts.append(
                    f"<details><summary class=muted>{end} qdiscs and filters at start</summary>"
                    f"<pre>{esc(q)}\n{esc(before.get(f'{end}_filters') or '')}</pre></details>"
                )
    for u in run.get("uncontrolled", []):
        parts.append(f'<div class="note">uncontrolled: {esc(u)}</div>')
    parts.append("</div>")

    by_workload: dict[str, list[Summary]] = {}
    for s in summaries(run):
        by_workload.setdefault(s.key.workload, []).append(s)
    network_ceiling = next(
        (
            p["value"]
            for p in run.get("probes", [])
            if p.get("name") == "network_receive" and p.get("value") and p.get("where") == "path"
        ),
        None,
    )
    cases = {c["workload"]: c for c in run["cases"]}
    for workload, rows in by_workload.items():
        c = cases[workload]
        head = f"{workload} <span class=muted>({c['files']} files, {c['bytes'] / 2**20:.0f} MiB"
        if c.get("mode") == "incremental":
            reps = [r for cc in run["cases"] if cc["workload"] == workload for r in cc["repeats"]]
            mib = sum(r.get("changed_bytes", 0) for r in reps) / max(len(reps), 1) / 2**20
            head += f"; re-sync, {mib:.1f} MiB changed per repeat; logical changes, not measured wire bytes"
        head += ")</span>"
        wspec = next((w for w in run["spec"].get("workloads", []) if w.get("name") == workload), {})
        if wspec.get("path") and c.get("source"):
            head += f" <span class=muted>from <code>{esc(c['source'])}</code> [{esc(c.get('source_fs') or '?')}]</span>"
        parts.append(f"<h3>{head}</h3>")
        remote = ":" in run["destination"].split("/", 1)[0] or ":" in run["source"].split("/", 1)[0]
        fresh = c.get("mode", "fresh") == "fresh"
        bound = probes.ceiling(run.get("probes", []), c["files"], c["bytes"], remote) if fresh else None
        ceiling_s = None
        if bound:
            parts.append(f"<div class=muted>likely ceiling: {esc(bound)}</div>")
            try:
                ceiling_s = float(bound.rsplit("~", 1)[1].split()[0])
            except (IndexError, ValueError):
                ceiling_s = None
        ran = [r for r in rows if not r.skipped]
        if ran and all(r.too_short and r.median_s is not None and not r.error for r in ran):
            parts.append(
                '<div class="note">every tool finished under 2 s: the comparison says little; '
                "scale the workload up</div>"
            )
        parts.append(f'<div class="chart">{bar_chart(rows, ceiling_s)}</div>')
        parts.append(
            "<table><tr><th>tool</th><th class=num>median</th><th class=num>min</th><th class=num>max</th>"
            "<th class=num>+fsync</th><th class=num>rate</th><th class=num>n</th><th>status</th></tr>"
        )
        for r in rows:
            status = (
                r.skipped and f"skipped: {r.skipped}" or r.error and f"<span class=bad>{esc(r.error)}</span>" or "ok"
            )
            if status == "ok" and r.failed_repeats:
                status = (
                    f"<span class=bad>{r.failed_repeats} of {r.failed_repeats + r.n} repeats FAILED</span>; rest shown"
                )
            elif status == "ok" and not r.verified_all:
                status = "ok <span class=muted>(not checksum-verified)</span>"
            if r.too_short:
                status += " <span class=muted>(&lt;2 s: rate is a lower bound)</span>"
            if fresh and network_ceiling and r.rate_mbs is not None:
                utilization = 100 * r.rate_mbs / network_ceiling
                status += f" <span class=muted>({utilization:.0f}% of measured network ceiling, end-to-end)</span>"
            parts.append(
                f"<tr><td>{esc(r.key.tool)}</td><td class=num>{fmt(r.median_s)}</td><td class=num>{fmt(r.min_s)}</td>"
                f"<td class=num>{fmt(r.max_s)}</td><td class=num>{fmt(r.fsync_s)}</td>"
                f"<td class=num>{fmt(r.rate_mbs, ' MB/s', 1)}</td><td class=num>{r.n}</td><td>{status}</td></tr>"
            )
        parts.append("</table>")
    parts.append("</section>")
    return "\n".join(parts)


def trend_section(runs: list[dict]) -> str:
    series = trends(runs)
    groups: dict[tuple[str, str, str], list[Series]] = {}
    for key, points in series.items():
        if len(points) < 2:
            continue
        groups.setdefault((key.spec, key.where(), f"{key.workload} ({key.shape()})"), []).append(Series(key, points))
    if not groups:
        return ""
    parts = [
        f'<section id="trends"><h2>Trends across {len(runs)} runs</h2>',
        "<p class=muted>Median wall time per run; hover a point for the tool build.</p>",
    ]
    for (spec, where, workload), ss in groups.items():
        parts.append(f"<h3>{esc(spec)} · {esc(workload)} <span class=muted>{esc(where)}</span></h3>")
        parts.append(f'<div class="chart">{trend_chart(ss)}</div>')
    parts.append("</section>")
    return "\n".join(parts)


def render_html(runs: list[dict]) -> str:
    runs = sorted(runs, key=lambda r: r["started"])
    specs = {run["spec"].get("name", "?") for run in runs}
    title = f"syq-bench · {next(iter(specs))}" if len(specs) == 1 else "syq benchmark results"
    body = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width">',
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>",
        f"<h1>{esc(title)}</h1>",
    ]
    body.append(
        f'<div class="muted lede">{len(runs)} run(s); newest first. '
        "Numbers are medians of successful repeats (checksum-verified unless a run says otherwise); "
        "ranges are min–max.</div>"
    )
    body.append('<nav class="jump"><a href="#comparison">Comparison</a>')
    if len(runs) > 1:
        body.append('<a href="#trends">Trends</a>')
    body.append('<a href="#runs">Run details</a></nav>')
    body.append(comparison_section(runs))
    if len(runs) > 1:
        body.append(trend_section(runs))
    body.append('<section id="runs"><h2>Run details</h2></section>')
    for run in reversed(runs):
        body.append(run_section(run))
    body.append("</body></html>")
    return "\n".join(body)
