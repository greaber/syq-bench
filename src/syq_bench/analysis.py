"""Cross-run analysis over result JSON files: summaries, regression comparison, trends.

Results are compared like-for-like: the key of a case is (spec name, host,
destination host, workload, tool). Two runs with different specs are still
comparable per case, but the report says which spec fields differ.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Below this relative change, a difference is reported as noise.
NOISE = 0.05


@dataclass(frozen=True)
class Key:
    spec: str
    host: str
    source_fs: str  # filesystem type read; a path on NFS and a scratch on ext4 are different series
    dest_host: str
    dest_fs: str
    workload: str
    bytes: int  # a workload's shape is part of its identity: 64 MiB and 4 GiB are not one series
    files: int
    mode: str  # fresh | incremental
    cache: str  # protocol cache mode
    tool: str

    def label(self) -> str:
        return f"{self.spec} / {self.workload} / {self.tool}"

    def shape(self) -> str:
        noun = "file" if self.files == 1 else "files"
        return f"{self.files} {noun}, {self.bytes / 2**20:.0f} MiB, {self.mode}, cache={self.cache}"

    def where(self) -> str:
        return f"{self.host} [{self.source_fs}] → {self.dest_host} [{self.dest_fs}]"


@dataclass
class Summary:
    key: Key
    started: str
    n: int
    median_s: float | None
    min_s: float | None
    max_s: float | None
    rate_mbs: float | None  # median of per-repeat moved-bytes / wall
    fsync_s: float | None
    error: str | None
    skipped: str | None
    too_short: bool
    tool_id: str | None  # sha256 prefix or version
    actual_caches: tuple[str, ...]  # cache preparation recorded on successful repeats
    verified_all: bool = True  # every counted repeat passed the checksum comparison
    failed_repeats: int = 0  # repeats with a nonzero exit or a checksum mismatch (not counted above)
    curtailed: str | None = None  # measured, then not repeated (order-of-magnitude slower)


@dataclass(frozen=True)
class System:
    """One tool configuration and build, suitable for a comparison-table column."""

    tool: str
    version: str | None
    identity: str
    tool_id: str | None
    is_syq: bool
    configuration: str
    remote_unknown: bool
    run_id: str = field(compare=False)


@dataclass(frozen=True)
class Benchmark:
    """The part of a case key that must match before systems are compared."""

    spec: str
    host: str
    source_fs: str
    dest_host: str
    dest_fs: str
    workload: str
    bytes: int
    files: int
    mode: str
    cache: str

    @classmethod
    def from_key(cls, key: Key) -> Benchmark:
        return cls(**{name: getattr(key, name) for name in cls.__dataclass_fields__})

    def shape(self) -> str:
        noun = "file" if self.files == 1 else "files"
        return f"{self.files} {noun}, {self.bytes / 2**20:.0f} MiB, {self.mode}, cache={self.cache}"

    def where(self) -> str:
        return f"{self.host} [{self.source_fs}] → {self.dest_host} [{self.dest_fs}]"


@dataclass
class Measurement:
    """The newest measurement of a system on a benchmark, with older duplicates counted."""

    summary: Summary
    run: dict
    seen: int = 1


@dataclass(frozen=True)
class Standing:
    """A display judgment within one benchmark row; lower wall time is better."""

    verdict: str  # "comparable", "slower", "much-slower", or "n/a"
    ratio: float | None
    leader: bool = False
    fastest: bool = False
    reason: str | None = None


def load(paths: list[Path], sort: bool = True) -> list[dict]:
    """Load result files; sorted oldest first unless the caller's argument order is the meaning."""
    runs = [json.loads(p.read_text()) for p in paths]
    for r, p in zip(runs, paths, strict=True):
        r["_path"] = str(p)
    return sorted(runs, key=lambda r: r["started"]) if sort else runs


def endpoint_host(endpoint: str) -> str:
    head, sep, _ = endpoint.partition(":")
    return head if sep and "/" not in head else "local"


def dest_host(run: dict) -> str:
    return endpoint_host(run["destination"])


def summaries(run: dict) -> list[Summary]:
    out = []
    for c in run["cases"]:
        fs = run.get("filesystems", {})
        key = Key(
            run["spec"].get("name", "?"),
            run["host"]["hostname"],
            c.get("source_fs") or fs.get("source") or "?",
            dest_host(run),
            fs.get("destination") or "?",
            c["workload"],
            c["bytes"],
            c["files"],
            c.get("mode", "fresh"),
            run["spec"].get("protocol", {}).get("cache", "evict"),
            c["tool"],
        )
        good = [r for r in c["repeats"] if r["exit_code"] == 0 and r.get("verified") is not False]
        walls = [r["wall_s"] for r in good]
        moved = [(r.get("changed_bytes", 0) if c.get("mode") == "incremental" else c["bytes"]) for r in good]
        rates = [m / r["wall_s"] / 1e6 for m, r in zip(moved, good, strict=True) if r["wall_s"]]
        fs = [r["fsync_s"] for r in good if r.get("fsync_s") is not None]
        actual_caches = tuple(sorted({r.get("cache", "unknown") for r in good}))
        t = run.get("tools", {}).get(c["tool"], {})
        tool_id = (t.get("sha256") or "")[:12] or t.get("version")
        rem = t.get("remote") or {}
        if rem.get("sha256"):
            tool_id = f"{tool_id}+{rem['sha256'][:12]}"
        out.append(
            Summary(
                key,
                run["started"],
                len(walls),
                statistics.median(walls) if walls else None,
                min(walls) if walls else None,
                max(walls) if walls else None,
                statistics.median(rates) if rates else None,
                statistics.median(fs) if fs else None,
                c.get("error"),
                c.get("skipped"),
                c.get("too_short", False),
                tool_id,
                actual_caches,
                bool(c["repeats"]) and all(r["exit_code"] == 0 and r.get("verified") is True for r in c["repeats"]),
                sum(1 for r in c["repeats"] if r["exit_code"] != 0 or r.get("verified") is False),
                curtailed=c.get("curtailed"),
            )
        )
    return out


def _tool_spec(run: dict, name: str) -> dict:
    return next((t for t in run.get("spec", {}).get("tools", []) if t.get("name") == name), {})


def system(run: dict, summary: Summary, run_id: str) -> System:
    """Identify a column without confusing two builds that share a friendly tool name."""
    tool = run.get("tools", {}).get(summary.key.tool, {})
    version = tool.get("version")
    # Unknown identities must not be silently combined across runs: they might be different binaries.
    identity = summary.tool_id or version or f"unknown@{run['started']}"
    remote_run = dest_host(run) != "local" or endpoint_host(run["source"]) != "local"
    remote = tool.get("remote") or {}
    remote_unknown = remote_run and not remote.get("sha256")
    if remote_unknown:
        identity = f"{identity}+remote:unknown@{run_id}"
    spec = _tool_spec(run, summary.key.tool)
    kind = spec.get("kind", spec.get("name", summary.key.tool))
    configuration = json.dumps(
        {key: value for key, value in spec.items() if key not in {"name", "binary"}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return System(
        summary.key.tool,
        version,
        identity,
        summary.tool_id,
        kind == "syq",
        configuration,
        remote_unknown,
        run_id,
    )


def comparison_rows(runs: list[dict]) -> dict[Benchmark, dict[System, Measurement]]:
    """Newest result per (like-for-like benchmark, exact system build/configuration)."""
    rows: dict[Benchmark, dict[System, Measurement]] = {}
    # Unknown remote identities need a per-run discriminator. Assign a report-local ordinal
    # from result content, never from the private input path attached by load().
    ordered = sorted(
        runs,
        key=lambda run: (
            run["started"],
            json.dumps({key: value for key, value in run.items() if key != "_path"}, sort_keys=True),
        ),
    )
    for ordinal, run in enumerate(ordered, start=1):
        for summary in summaries(run):
            benchmark = Benchmark.from_key(summary.key)
            sys = system(run, summary, str(ordinal))
            old = rows.setdefault(benchmark, {}).get(sys)
            seen = (old.seen if old else 0) + 1
            rows[benchmark][sys] = Measurement(summary, run, seen)
    return rows


def current_rows(
    rows: dict[Benchmark, dict[System, Measurement]],
) -> dict[Benchmark, dict[str, tuple[System, Measurement]]]:
    """The overview form of comparison_rows: one entry per tool *name* (the spec's own label for
    "the same thing"), holding the newest measurement across builds and configurations together
    with the exact System that produced it, so provenance (identity, configuration, unrecorded
    remote, run ordinal) is the original, never reconstructed. The exact-identity grid remains for
    regression reading."""
    out: dict[Benchmark, dict[str, tuple[System, Measurement]]] = {}
    for benchmark, measurements in rows.items():
        by_tool: dict[str, tuple[System, Measurement]] = {}
        for sys, m in measurements.items():
            old = by_tool.get(sys.tool)
            if old is None or m.run["started"] > old[1].run["started"]:
                by_tool[sys.tool] = (sys, m)
        out[benchmark] = by_tool
    return out


def standings(items: list[Summary]) -> list[Standing]:
    """Classify systems relative to the fastest trustworthy result in the same row.

    Min–max overlap or a median within the five-percent noise floor means the
    systems are merely comparable at the resolution of the current data. A
    twofold slowdown is kept as a separate, deliberately alarming state.
    """
    out = [Standing("n/a", None, reason=_unrankable(s)) for s in items]
    eligible = [(i, s) for i, s in enumerate(items) if _unrankable(s) is None]
    if not eligible:
        return out
    if all(s.too_short for _, s in eligible):
        # A short result ranks fine against a longer one, but when every trustworthy result is under
        # the floor the whole row is startup-dominated: keep it visibly weak instead of ranking noise.
        for i, _s in eligible:
            out[i] = Standing(
                "n/a", None, reason="every result under 2 s: comparison says little; scale the workload up"
            )
        return out
    fastest_i, fastest = min(eligible, key=lambda item: item[1].median_s)
    assert fastest.median_s is not None
    for i, summary in eligible:
        assert summary.median_s is not None
        ratio = summary.median_s / fastest.median_s
        overlap = (
            summary.n > 1
            and fastest.n > 1
            and summary.min_s is not None
            and summary.max_s is not None
            and fastest.min_s is not None
            and fastest.max_s is not None
            and not (summary.min_s > fastest.max_s or summary.max_s < fastest.min_s)
        )
        leader = ratio < 1 + NOISE or overlap
        verdict_ = "comparable" if leader else "much-slower" if ratio >= 2 else "slower"
        out[i] = Standing(verdict_, ratio, leader, i == fastest_i)
    return out


def _unrankable(summary: Summary) -> str | None:
    if summary.median_s is None:
        return summary.skipped and f"skipped: {summary.skipped}" or summary.error or "no successful repeats"
    if not summary.verified_all:
        return "not fully checksum-verified"
    expected_cache = (summary.key.cache,)
    if summary.actual_caches != expected_cache:
        actual = ", ".join(summary.actual_caches) if summary.actual_caches else "unrecorded"
        if "evict-failed" in summary.actual_caches:
            return f"cache eviction failed (requested {summary.key.cache})"
        return f"cache state {actual} does not match requested {summary.key.cache}"
    # A too-short median is still a real wall time: ordering by it is valid, and startup noise can
    # only make the fast tool look worse than it is. It is labelled, not disqualified (see Standing).
    return None


@dataclass
class Delta:
    key: Key
    before: Summary
    after: Summary
    ratio: float | None  # after.median / before.median; > 1 is slower
    verdict: str  # "slower", "faster", "noise", "n/a"


def verdict(a: Summary, b: Summary) -> tuple[float | None, str]:
    if a.median_s is None or b.median_s is None:
        return None, "n/a"
    ratio = b.median_s / a.median_s
    overlap = not (b.min_s > a.max_s or b.max_s < a.min_s)
    if abs(ratio - 1) < NOISE or (overlap and a.n > 1 and b.n > 1):
        return ratio, "noise"
    return ratio, "slower" if ratio > 1 else "faster"


def compare(before: dict, after: dict) -> list[Delta]:
    b = {s.key: s for s in summaries(before)}
    return [Delta(s.key, b[s.key], s, *verdict(b[s.key], s)) for s in summaries(after) if s.key in b]


def regressed(deltas: list[Delta]) -> list[Delta]:
    """Cases that got slower, or that worked before and failed/skipped/did not verify after."""
    return [
        d
        for d in deltas
        if d.verdict == "slower"
        or (d.before.median_s is not None and (d.after.median_s is None or not d.after.verified_all))
    ]


def gate(before: dict, after: dict) -> list[str]:
    """Reasons the after-run fails a regression gate: slower or broken cases, cases that vanished,
    an aborted run. Empty means pass."""
    reasons = [f"{d.key.workload}/{d.key.tool}: {d.verdict}" for d in regressed(compare(before, after))]
    after_keys = {s.key for s in summaries(after)}
    for s in summaries(before):
        if s.median_s is not None and s.key not in after_keys:
            reasons.append(f"{s.key.workload}/{s.key.tool}: missing from the after run")
    if after.get("aborted"):
        reasons.append(f"after run aborted: {after['aborted']}")
    return reasons


def spec_diff(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Dotted paths whose values differ between two spec dicts."""
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        path = f"{prefix}{k}"
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(spec_diff(va, vb, path + "."))
        elif va != vb:
            out.append(f"{path}: {va!r} -> {vb!r}")
    return out


def trends(runs: list[dict]) -> dict[Key, list[Summary]]:
    """Every case series across runs, oldest first."""
    series: dict[Key, list[Summary]] = {}
    for run in runs:
        for s in summaries(run):
            series.setdefault(s.key, []).append(s)
    return series


def fmt_s(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}s"


def render_compare(before: dict, after: dict) -> str:
    lines = [f"before: {before['_path']}  ({before['started']})", f"after:  {after['_path']}  ({after['started']})"]
    diff = spec_diff(before["spec"], after["spec"])
    if diff:
        lines.append("spec differences:")
        lines.extend(f"  {d}" for d in diff)
    for name in sorted(set(before.get("tools", {})) | set(after.get("tools", {}))):
        ta, tb = before.get("tools", {}).get(name, {}), after.get("tools", {}).get(name, {})
        for side, ka, kb in (("", ta, tb), (" (remote end)", ta.get("remote") or {}, tb.get("remote") or {})):
            if ka.get("sha256") != kb.get("sha256"):
                a_id, b_id = (ka.get("sha256") or "")[:12], (kb.get("sha256") or "")[:12]
                lines.append(f"tool {name}{side}: {ka.get('version')} {a_id} -> {kb.get('version')} {b_id}")
    lines.append("")
    lines.append(f"  {'workload':14} {'tool':10} {'before':>9} {'after':>9} {'ratio':>7}  verdict")
    for d in compare(before, after):
        ratio = "-" if d.ratio is None else f"{d.ratio:6.2f}x"
        flag = "  <-- REGRESSION" if d in regressed([d]) else ""
        lines.append(
            f"  {d.key.workload:14} {d.key.tool:10} {fmt_s(d.before.median_s):>9} {fmt_s(d.after.median_s):>9} "
            f"{ratio:>7}  {d.verdict}{flag}"
        )
    for reason in gate(before, after):
        if "missing" in reason or "aborted" in reason:
            lines.append(f"  {reason}  <-- REGRESSION")
    return "\n".join(lines)


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Key):
        return obj.label()
    raise TypeError(type(obj))
