"""Command line: `syq-bench run SPEC`, `syq-bench report RESULTS...`, `syq-bench compare BEFORE AFTER`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from syq_bench import analysis
from syq_bench.fixtures import tree_bytes
from syq_bench.html import render_html
from syq_bench.probes import META_FILES as PROBE_INODES
from syq_bench.probes import SEQ_BYTES as PROBE_BYTES
from syq_bench.remote import Location
from syq_bench.report import render
from syq_bench.runner import Runner
from syq_bench.spec import BLOCK, Spec, SpecError, load
from syq_bench.tools import Tool

GIB = 2**30


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="syq-bench", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="execute a run spec (see specs/)")
    r.add_argument("spec", type=Path, help="TOML run spec")
    r.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", help="override a spec field, e.g. protocol.repeats=1"
    )
    r.add_argument("--dry-run", "-n", action="store_true", help="print the plan and exit")
    r.add_argument("--out", type=Path, help="write JSON results here (default: results/<spec>-<timestamp>.json)")
    r.add_argument("--yes", "-y", action="store_true", help="do not ask before running")
    rep = sub.add_parser("report", help="write a self-contained HTML report over result files")
    rep.add_argument("results", nargs="+", type=Path, help="result JSON files (or directories of them)")
    rep.add_argument("-o", "--out", type=Path, default=Path("results/report.html"))
    rep.add_argument("--public", action="store_true", help="use the public comparison layout (does not redact data)")
    rep.add_argument("--primary-tool", default="syq", help="default syq entry in the public layout (e.g. syq-auto)")
    cmp_ = sub.add_parser("compare", help="compare two runs case by case and flag regressions")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    cmp_.add_argument("--fail-on-regression", action="store_true", help="exit 1 if any case got slower")
    return p.parse_args(argv)


def _overlaps(a: Location, b: Location) -> bool:
    """True if one path contains the other (same host); a copy must never read what it writes."""
    if a.host != b.host:
        return False
    if a.host is None:  # local: absolute and symlink-free, so spellings cannot hide an overlap
        pa, pb = PurePosixPath(Path(a.path).resolve()), PurePosixPath(Path(b.path).resolve())
    else:
        pa, pb = PurePosixPath(os.path.normpath(str(a.path))), PurePosixPath(os.path.normpath(str(b.path)))
    return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def plan_lines(spec: Spec, tools: list[Tool]) -> list[str]:
    p = spec.protocol
    lines = [f"Plan: {spec.name} - {spec.description}"]
    lines.append(f"  source scratch: {spec.source}")
    lines.append(f"  destination:    {spec.destination}")
    if spec.ssh != "ssh":
        lines.append(f"  ssh command:    {spec.ssh}")
    for w in spec.workloads:
        if w.kind == "path":
            lines.append(f"  workload {w.name:12} existing path {w.path} (read-only)")
        else:
            lines.append(f"  workload {w.name:12} {w.kind}: {w.bytes / 2**20:.0f} MiB in {w.files} file(s)")
    for t in tools:
        selected = "" if t.spec.workloads is None else f" [workloads: {', '.join(t.spec.workloads)}]"
        argv = " ".join(t.argv(Location.parse("SRC"), Location.parse("DST")))
        lines.append(f"  tool     {t.name:12} {argv}{selected}")
    repeats = (
        f"{p.repeats} repeat(s)"
        if not p.slow_cutoff
        else (f"up to {p.repeats} repeat(s) (a tool >= {p.slow_cutoff:g}x slower than the fastest is not re-measured)")
    )
    budget = f"; tool_timeout={p.tool_timeout:g}s" if p.tool_timeout else ""
    lines.append(
        f"  protocol: {repeats}, interleaved; cache={p.cache}; durable={p.durable}; verify={p.verify}; "
        f"seed={'fresh' if p.seed is None else p.seed}{budget}"
    )
    return lines


def cmd_run(a: argparse.Namespace) -> int:
    try:
        spec = load(a.spec, a.set)
    except (SpecError, OSError) as e:
        _die(f"{a.spec}: {e}")
    tools = [Tool(t) for t in spec.tools]
    src_root, dst = Location.parse(spec.source, spec.ssh), Location.parse(spec.destination, spec.ssh)

    for line in plan_lines(spec, tools):
        print(line)
    if a.dry_run:
        return 0

    missing = [t.name for t in tools if t.resolved_binary() is None]
    if missing:
        _die(f"tool binary not found: {', '.join(missing)}")
    if dst.is_remote and any(not t.remote_ok for t in tools):
        _die("cp cannot copy to a remote destination")
    if not dst.is_remote and not src_root.is_remote and any(not t.local_ok for t in tools):
        _die("qcp and tar need a remote endpoint")
    if dst.exists() and not dst.is_empty_dir():
        _die(f"destination {dst.spec()} exists and is not empty; refusing to write into it")
    generated = [w for w in spec.workloads if w.kind != "path"]
    if generated and src_root.exists() and not src_root.is_empty_dir():
        _die(f"source scratch {src_root.spec()} exists and is not empty; refusing to generate into it")
    if _overlaps(src_root, dst):
        _die(f"source scratch {src_root.spec()} and destination {dst.spec()} must be disjoint")

    # Space: workloads run one after another and each releases its scratch before the next, so what
    # must fit is the largest *single* workload's footprint: its source copies (generated only) plus
    # its destination copy, bytes and inodes. Path workloads are sized now, not after the run starts.
    peaks: list[tuple[int, int, int, int]] = []  # (src_bytes, src_inodes, dst_bytes, dst_inodes)
    for w in spec.workloads:
        if w.kind != "path":
            f = w.scratch_factor()
            df = w.dest_factor()
            peaks.append((w.disk_bytes() * f, w.disk_inodes() * f, w.disk_bytes() * df, w.disk_inodes() * df))
            continue
        src = Location.parse(w.path, spec.ssh)
        if src.is_remote:
            _die(f"workload {w.name}: remote path workloads are not supported yet")
        if not Path(src.path).is_dir():
            _die(f"workload {w.name}: {w.path} is not a directory")
        if _overlaps(src, dst) or (generated and _overlaps(src, src_root)):
            _die(f"workload {w.name}: {w.path} overlaps a scratch directory; it must stay read-only")
        files, size, dirs = tree_bytes(Path(src.path), with_dirs=True)
        peaks.append((0, 0, size + (files + dirs) * BLOCK, files + dirs))

    spec_workloads = list(spec.workloads)
    if spec.protocol.probes:
        # Storage probes run one root at a time and clean up after themselves, so each probed root
        # needs one probe's worth; a remote destination and a source scratch nothing is generated in
        # are not probed at all.
        src_probe = (PROBE_BYTES, PROBE_INODES) if generated and not src_root.is_remote else (0, 0)
        dst_probe = (PROBE_BYTES, PROBE_INODES) if not dst.is_remote else (0, 0)
        if src_root.same_filesystem(dst) if generated else False:
            src_probe = (0, 0)  # sequential on one filesystem: the destination's budget covers both
        peaks.append((*src_probe, *dst_probe))
        spec_workloads.append(None)

    dst.mkdir()
    if generated:
        src_root.mkdir()
    shared = generated and src_root.same_filesystem(dst)
    dst_free, dst_inodes_free = dst.free_bytes(), dst.free_inodes()
    src_free = src_root.free_bytes() if generated else 0
    src_inodes_free = src_root.free_inodes() if generated else None
    for w, (sb, si, db, di) in zip(spec_workloads, peaks, strict=True):
        w = w or SimpleNamespace(name="storage probes")
        if shared:
            if dst_free < (sb + db) * 1.1:
                _die(
                    f"{w.name}: source scratch and destination share a filesystem with {dst_free / GIB:.1f} GiB free; "
                    f"need {(sb + db) * 1.1 / GIB:.1f}"
                )
            if dst_inodes_free is not None and dst_inodes_free < (si + di) * 1.1:
                _die(f"{w.name}: shared filesystem has {dst_inodes_free} free inodes; need about {si + di}")
            continue
        if dst_free < db * 1.1:
            _die(f"{w.name}: {dst.spec()} has {dst_free / GIB:.1f} GiB free; need {db * 1.1 / GIB:.1f}")
        if dst_inodes_free is not None and dst_inodes_free < di * 1.1:
            _die(f"{w.name}: {dst.spec()} has {dst_inodes_free} free inodes; need about {di}")
        if sb and src_free < sb * 1.1:
            _die(f"{w.name}: {src_root.spec()} has {src_free / GIB:.1f} GiB free; fixtures need {sb * 1.1 / GIB:.1f}")
        if sb and src_inodes_free is not None and src_inodes_free < si * 1.1:
            _die(f"{w.name}: {src_root.spec()} has {src_inodes_free} free inodes; fixtures need about {si}")
    if not a.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return 1

    runner = Runner(spec, tools, src_root, dst)
    out = a.out or Path("results") / f"{spec.name}-{runner.run.started.replace(':', '')}.json"
    try:
        runner.execute()
    finally:
        out.parent.mkdir(parents=True, exist_ok=True)
        runner.run.dump(out)
    print()
    print(render(runner.run))
    print(f"raw results: {out}")
    return 1 if runner.failed() else 0


def _result_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    if not files:
        _die("no result files")
    return files


def cmd_report(a: argparse.Namespace) -> int:
    runs = analysis.load(_result_files(a.results))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.public:
        import re
        from urllib.parse import quote

        from syq_bench.public import render_pages

        filenames = {
            "index.html": a.out.name,
            "method.html": f"{a.out.name}.method.html",
            "reproduce.html": f"{a.out.name}.reproduce.html",
        }
        catalog = {
            "scenarios": [
                {"id": name, "title": name, "primary": a.primary_tool}
                for name in dict.fromkeys(r["spec"]["name"] for r in runs)
            ]
        }
        for name, page in render_pages(runs, catalog).items():
            # Rewrite all companion links in one pass: a report may itself be
            # named method.html, so sequential replacements could rewrite twice.
            page = re.sub(
                r'href="(index\.html|method\.html|reproduce\.html)(#[^"]*)?"',
                lambda match: f'href="{quote(filenames[match[1]], safe="")}{match[2] or ""}"',
                page,
            )

            def rewrite_search(match):
                entries = json.loads(match[1])
                for entry in entries:
                    entry["href"] = quote(filenames[entry["href"]], safe="")
                data = json.dumps(entries).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                return f'<script id="site-search-index" type="application/json">{data}</script>'

            page = re.sub(
                r'<script id="site-search-index" type="application/json">(.*?)</script>',
                rewrite_search,
                page,
            )
            a.out.with_name(filenames[name]).write_text(page)
    else:
        a.out.write_text(render_html(runs))
    print(f"wrote {a.out} ({len(runs)} run(s))")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    before, after = analysis.load([a.before, a.after], sort=False)  # argument order is the meaning
    print(analysis.render_compare(before, after))
    if before["started"] > after["started"]:
        print("note: BEFORE is newer than AFTER; comparing in the order given")
    return 1 if analysis.gate(before, after) and a.fail_on_regression else 0


def main(argv: list[str] | None = None) -> int:
    # Benchmarks run for minutes and are usually captured with tee: keep progress lines live.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    a = parse_args(argv)
    return {"run": cmd_run, "report": cmd_report, "compare": cmd_compare}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
