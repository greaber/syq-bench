"""Execute a Spec: generate fixtures, run every (workload, tool) case for N repeats, record raw measurements.

Repeats are interleaved across tools (repeat 1 of every tool, then repeat 2, ...)
so that drift and warm metadata caches do not favour one tool. Each repeat copies
into a fresh destination path; the source is evicted from the page cache before
every repeat when `protocol.cache = "evict"`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import resource
import secrets
import shlex
import signal
import socket
import statistics
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syq_bench import __version__, fixtures, probes
from syq_bench.remote import Location
from syq_bench.spec import Spec, WorkloadSpec
from syq_bench.tools import Tool

SCHEMA_VERSION = 3
# Below this median wall time, startup and timer noise are a visible share of the number. A fast
# tool's rate is then a LOWER BOUND, and its ratio against a competitor above the threshold is still
# valid (the fast tool can only be better than shown). Only when every tool in a workload is under
# the threshold does the comparison itself say little - then the workload should be scaled up.
TOO_SHORT_S = 2.0


@dataclass
class Repeat:
    index: int
    wall_s: float
    user_s: float
    sys_s: float
    exit_code: int
    verified: bool | None  # None: verification not requested
    fsync_s: float | None  # time for the harness to fsync everything the tool wrote; None if not measured
    fsync_note: str | None  # why fsync_s is None
    cache: str  # what was done before this repeat: "evict", "drop", "warm", or "evict-failed"
    metadata_cache: str  # "cold" for the first read of a freshly generated tree, else "warm"
    stderr_tail: str = ""
    changed_files: int = 0  # incremental cases: what differed between source and destination
    changed_bytes: int = 0
    delta_seed: int | None = None  # seed of that change; derived from the run seed and round index
    round_index: int | None = None  # Chronological round; index counts this tool's own repetitions.


@dataclass
class Case:
    workload: str
    tool: str
    argv: list[str]
    bytes: int
    files: int
    source: str = ""  # the tree actually read: a generated fixture under the scratch, or the user's path
    source_fs: str | None = None
    mode: str = "fresh"  # "fresh": empty destination; "incremental": destination pre-populated, source changed
    prepopulated_with: str | None = None
    repeats: list[Repeat] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None  # not run, with the reason (e.g. cp has no incremental mode)
    curtailed: str | None = None  # measured, but remaining repeats were skipped (order-of-magnitude case)
    too_short: bool = False  # median wall < TOO_SHORT_S: rate is a lower bound; see TOO_SHORT_S

    def good(self) -> list[Repeat]:
        return [r for r in self.repeats if r.exit_code == 0 and r.verified is not False]

    def good_walls(self) -> list[float]:
        return [r.wall_s for r in self.good()]

    def moved_bytes(self, r: Repeat) -> int:
        return r.changed_bytes if self.mode == "incremental" else self.bytes


@dataclass
class Run:
    schema: int
    harness_version: str
    started: str
    finished: str | None
    spec: dict[str, Any]  # the spec as loaded, after overrides
    seed: int
    host: dict
    tools: dict[str, dict]  # name -> {binary, version, sha256}; sha256 identifies a source build
    source: str
    destination: str
    filesystems: dict[str, str | None]  # "source"/"destination" -> fs type
    uncontrolled: list[str]  # what the protocol could not control on this run
    probes: list[dict] = field(default_factory=list)
    network: dict | None = None  # qdiscs on both ends and a ping sample when an endpoint is remote
    cases: list[Case] = field(default_factory=list)
    aborted: str | None = None  # set when the run stopped early (destination cleanup failed)

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))


def host_facts() -> dict:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }


def tool_identity(tool: Tool, remote: Location | None = None) -> dict:
    path = tool.resolved_binary()
    ident: dict = {"binary": path or tool.binary, "version": None, "sha256": None}
    if path:
        ident["version"] = Location(Path(".")).tool_version(path)
        with open(path, "rb") as f:
            ident["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
    if remote is not None and tool.remote_ok:
        ident["remote"] = remote_identity(tool, remote)
    return ident


def remote_identity(tool: Tool, remote: Location) -> dict:
    """What ran on the other end: an explicit syq/qcp helper or a binary on the remote PATH.

    Best effort: a missing binary is recorded, not fatal. syq's rsync-compatible interface prefixes
    its no-bootstrap flag, while the native interface does not.
    """
    if tool.kind == "rclone":
        return {
            "binary": None,
            "version": None,
            "sha256": None,
            "note": f"rclone {tool.spec.rclone_backend} server; server identity must be recorded by the campaign",
        }
    args = list(tool.spec.args)
    name = {"syq": "syq", "qcp": "qcp", "rsync": "rsync", "tar": "tar"}[tool.kind]
    explicit = None
    path_flags = (
        ("--syq-path", "--remote-qcp-binary", "--rsync-path") if tool.kind == "syq" else ("--remote-qcp-binary",)
    )
    for i, a in enumerate(args):
        for flag in path_flags:
            if a == flag and i + 1 < len(args):
                explicit = args[i + 1]
            elif a.startswith(flag + "="):
                explicit = a.split("=", 1)[1]
    if explicit:
        name = explicit
    elif tool.kind == "syq" and not {"--no-bootstrap", "--syq-no-bootstrap"}.intersection(args):
        # Without --syq-path, syq bootstraps its own release helper on the remote; its identity is the
        # local release, not whatever 'syq' the remote PATH holds.
        return {
            "binary": None,
            "version": None,
            "sha256": None,
            "note": "managed helper: same release as the local syq",
        }
    q = shlex.quote(name)
    # Labelled lines, so a missing sha256sum or a silent --version cannot shift fields.
    script = (
        f'b=$(command -v {q}) || exit 3; printf "BIN=%s\\n" "$b"; '
        'h=$(sha256sum "$b" 2>/dev/null | cut -d" " -f1) && printf "SHA=%s\\n" "$h"; '
        'v=$("$b" --version 2>&1 | head -1) && printf "VER=%s\\n" "$v"'
    )
    r = remote.sh(script, check=False)
    fields = dict(line.split("=", 1) for line in r.stdout.strip().splitlines() if "=" in line)
    if r.returncode == 3 or "BIN" not in fields:
        return {"binary": name, "version": None, "sha256": None, "note": "not found on the remote PATH"}
    sha = fields.get("SHA") or None
    if sha is not None and not (len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
        sha = None
    out = {"binary": fields["BIN"], "sha256": sha, "version": fields.get("VER") or None}
    if sha is None:
        out["note"] = "sha256sum unavailable on the remote"
    return out


HEARTBEAT_S = 30
STDERR_TAIL_LINES = 100


class CleanupFailed(RuntimeError):
    """A destination the harness owns could not be removed; the run must not continue."""


def kill_group(proc: subprocess.Popen, hard: bool = False) -> None:
    """Terminate the tool and its whole process group, then make sure it is gone. With hard=True the
    group is SIGKILLed immediately (budget enforcement must not wait out a TERM-ignoring tool)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The leader exiting says nothing about descendants that ignore SIGTERM: check the group itself.
    deadline = time.monotonic() + 5
    while _group_alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(proc.pid):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


TIMED_OUT = -124  # exit_code recorded when the harness killed the tool at protocol.tool_timeout


def _timed(
    argv: list[str], log=print, heartbeat_s: float = HEARTBEAT_S, timeout_s: float | None = None
) -> tuple[float, float, float, int, str]:
    """Run argv; return (wall, child user, child sys, exit code, stderr tail).

    stderr is drained continuously and only its last lines are kept, so a verbose tool cannot
    grow memory; a heartbeat line every `heartbeat_s` shows a long run is alive. With `timeout_s`,
    the whole process group is killed at the deadline and the exit code is TIMED_OUT."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.perf_counter()
    # Own process group: on interruption every worker the tool spawned (ssh sessions included) goes with it.
    proc = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace", start_new_session=True
    )
    tail: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip("\n"))
            del tail[:-STDERR_TAIL_LINES]

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    timed_out = False
    budget_wall = None
    try:
        while True:
            step = heartbeat_s
            if timeout_s is not None:
                step = min(step, max(timeout_s - (time.perf_counter() - t0), 0.01))
            try:
                proc.wait(timeout=step)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.perf_counter() - t0
                if timeout_s is not None and elapsed >= timeout_s:
                    log(f"    time budget of {timeout_s:g} s reached; stopping {argv[0].rsplit('/', 1)[-1]}")
                    timed_out = True
                    budget_wall = elapsed
                    kill_group(proc, hard=True)
                    break
                log(f"    ... {argv[0].rsplit('/', 1)[-1]} still running after {elapsed:.0f} s")
    except BaseException:
        kill_group(proc)
        raise
    # A grandchild that survives the tool (an ssh helper inheriting stderr) would block the drain
    # thread forever: make sure the whole group is gone before joining, and never wait on the join.
    if proc.poll() is not None and _group_alive(proc.pid):
        kill_group(proc)
    t.join(timeout=10)
    # For a budget kill, the wall is the budget moment, not the cleanup time after it: the recorded
    # rate is then a true upper bound over exactly the budgeted window.
    wall = budget_wall if budget_wall is not None else time.perf_counter() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    code = TIMED_OUT if timed_out else proc.returncode
    return wall, after.ru_utime - before.ru_utime, after.ru_stime - before.ru_stime, code, "\n".join(tail)


@dataclass
class Workload:
    """A workload resolved to a source tree on disk."""

    spec: WorkloadSpec
    src: Location
    files: int
    bytes: int
    generated: bool  # True: the harness wrote it (and may remove it); False: user path, read-only
    born_cold: bool
    checksum: str | None = None


class Runner:
    def __init__(self, spec: Spec, tools: list[Tool], src_root: Location, dst_root: Location, log=print):
        self.spec = spec
        self.tools = tools
        self.src_root = src_root
        self.dst_root = dst_root
        self.log = log
        self.seed = spec.protocol.seed if spec.protocol.seed is not None else secrets.randbits(32)
        self.abort: CleanupFailed | None = None
        self.run = Run(
            SCHEMA_VERSION,
            __version__,
            datetime.now(UTC).isoformat(timespec="seconds"),
            None,
            spec.raw,
            self.seed,
            host_facts(),
            {t.name: tool_identity(t, dst_root if dst_root.is_remote else None) for t in tools},
            src_root.spec(),
            dst_root.spec(),
            {"source": src_root.fs_type(), "destination": dst_root.fs_type()},
            ["metadata cache (dentries/inodes) is not evicted: generation, checksum, and repeats all warm it"],
        )
        for tool in tools:
            if tool.kind == "rclone":
                backend = tool.spec.rclone_backend
                self.run.uncontrolled.append(
                    f"{tool.name}: rclone {backend}; harness verification checks regular-file contents, not metadata"
                )
                if backend == "sftp-ssh":
                    self.run.uncontrolled.append(
                        f"{tool.name}: SFTP uses external OpenSSH; permissions/ownership preservation is unsupported"
                    )
                elif backend == "sftp":
                    self.run.uncontrolled.append(
                        f"{tool.name}: SFTP uses rclone's internal SSH library; "
                        "scratch mapping must pass a check before copying; "
                        "permissions/ownership preservation is unsupported"
                    )
                elif backend == "webdav":
                    self.run.uncontrolled.append(
                        f"{tool.name}: WebDAV scratch mapping must pass a check before copying; metadata and rename "
                        "semantics depend on the server"
                    )
        if src_root.is_remote or dst_root.is_remote:
            self.run.uncontrolled.append("remote end: fixture pages evicted on the client only")

    # -- workloads ------------------------------------------------------------------------

    def prepare(self, w: WorkloadSpec) -> Workload:
        if w.kind == "path":
            src = Location.parse(w.path, self.spec.ssh)
            if src.is_remote:
                raise NotImplementedError("remote path workloads are not supported yet")
            files, size = fixtures.tree_bytes(Path(src.path))
            return Workload(w, src, files, size, generated=False, born_cold=False)
        src = self.src_root / w.name
        if src.is_remote:
            raise NotImplementedError("generating fixtures on a remote source is not supported yet")
        self.log(f"generating {w.name} ({w.bytes / 2**20:.0f} MiB, {w.files} files) ...")
        fx = fixtures.make(w.kind, w.name, w.bytes, w.files)
        cold = fx.generate(Path(src.path), self.seed)
        if not cold:
            self.run.uncontrolled.append(f"{w.name}: O_DIRECT unsupported here; evicted with fadvise instead")
        files, size = fixtures.tree_bytes(Path(src.path))
        return Workload(w, src, files, size, generated=True, born_cold=cold)

    def release(self, wl: Workload) -> None:
        if wl.generated:
            wl.src.remove_tree(inside=self.src_root)

    # -- cases ----------------------------------------------------------------------------

    def _prepare_cache(self, wl: Workload, first: bool) -> str:
        mode = self.spec.protocol.cache
        if mode == "warm":
            return "warm"
        if mode == "drop":
            ok = self.src_root.drop_caches() and self.dst_root.drop_caches()
            return "drop" if ok else "evict-failed"
        if first and wl.born_cold:
            return "evict"  # nothing to do: the generator never put the data pages in the cache
        return "evict" if wl.src.evict_tree() else "evict-failed"

    def _prepopulate(self, before: Location, dst: Location, case: Case) -> bool:
        """Incremental cases: fill dst with the snapshot of the source taken before this repeat's change.
        Returns False if the destination should have been evicted and was not."""
        if dst.is_remote:
            argv = ["rsync", "-a", "-e", dst.ssh, before.spec(trailing_slash=True), dst.spec()]
        else:
            argv = ["cp", "-a", f"{before.path}/.", str(dst.path)]
        case.prepopulated_with = argv[0]
        subprocess.run(argv, check=True, capture_output=True)
        if self.spec.protocol.cache == "warm":
            return True  # the spec asked for warm caches; the pre-populated destination stays warm too
        return dst.evict_tree()  # "drop" is handled globally afterwards; evicting first costs nothing

    def _restore_source(self, wl: Workload, before: Location) -> None:
        """Put the pre-change tree back after a round: every repeat then starts from the same state,
        and appended bytes cannot compound past the space budget. Also consumes the snapshot."""
        wl.src.remove_tree(inside=self.src_root)
        os.rename(str(before.path), str(wl.src.path))

    def _snapshot_and_change(self, wl: Workload, i: int) -> tuple[Location, int, int, int]:
        """Copy the source aside, then apply this repeat's deterministic change to the source, so every
        tool in the repeat sees the same before (snapshot) and after (source) states."""
        before = self.src_root / f"{wl.spec.name}.before"
        delta_seed = (self.seed * 1_000_003 + i + 1) & 0xFFFFFFFF
        n = nbytes = 0
        try:
            before.mkdir()
            subprocess.run(["cp", "-a", f"{wl.src.path}/.", str(before.path)], check=True, capture_output=True)
            if wl.spec.changed:
                n, nbytes = fixtures.modify_fraction(Path(wl.src.path), wl.spec.changed, delta_seed, wl.spec.mutate)
                if self.spec.protocol.verify:
                    wl.checksum = wl.src.checksum()
        except BaseException:
            before.remove_tree(inside=self.src_root)  # a partial snapshot would block the next run
            raise
        return before, delta_seed, n, nbytes

    def _repeat(
        self, wl: Workload, tool: Tool, case: Case, i: int, first: bool, delta: tuple[Location, int, int, int] | None
    ) -> Repeat:
        """One timed copy. A failing harness step (ssh dropped, checksum command failed) becomes a
        recorded error on the case, never a crash; the destination is removed on every path."""
        dst = self.dst_root / f"{wl.spec.name}.{tool.name}.r{i}"
        argv = tool.argv(wl.src, dst)
        changed: tuple = (0, 0, None)
        wall = user = sys_ = 0.0
        code = -1
        tail = ""
        fsync = note = verified = None
        cache = "not-run"
        try:
            dst.mkdir()
            tool.check_destination(dst)
            dst_evicted = True
            if delta is not None:
                before, delta_seed, n, nbytes = delta
                dst_evicted = self._prepopulate(before, dst, case)
                changed = (n, nbytes, delta_seed)
                first = False
            cache = self._prepare_cache(wl, first)
            if not dst_evicted and cache == "evict":
                cache = "evict-failed"
            wall, user, sys_, code, tail = _timed(argv, self.log, timeout_s=self.spec.protocol.tool_timeout)
            if not self.spec.protocol.durable:
                note = "not requested"
            elif code == 0:
                t0 = time.perf_counter()
                if dst.fsync_tree():
                    fsync = time.perf_counter() - t0
                else:
                    note = "fsync walk failed at destination"
            if self.spec.protocol.verify and code == 0:
                verified = dst.exists() and dst.checksum() == wl.checksum
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            detail = (getattr(e, "stderr", "") or str(e)).strip().splitlines()
            case.error = f"harness step failed: {detail[-1] if detail else e}"
            verified = None if code != 0 else False
        finally:
            try:
                dst.remove_tree(inside=self.dst_root)
            except (subprocess.CalledProcessError, OSError):
                # Continuing would pile up workload-sized leftovers: record this repeat, then stop.
                self.run.uncontrolled.append(f"destination {dst.spec()} was not cleaned up; run aborted")
                self.log(f"    could not remove {dst.spec()}; remove it by hand. Aborting the run.")
                self.abort = CleanupFailed(dst.spec())
        # Data pages can be cold (O_DIRECT, eviction); dentries and inodes were touched by generation
        # and by tree_bytes(), and every later step warms them further. Say so rather than guess.
        meta = "warm"
        return Repeat(i, wall, user, sys_, code, verified, fsync, note, cache, meta, tail, *changed)

    def run_workload(self, w: WorkloadSpec) -> None:
        wl = self.prepare(w)
        try:
            if self.spec.protocol.verify:
                wl.checksum = wl.src.checksum()
            mode = "incremental" if w.changed is not None else "fresh"
            src_fs = wl.src.fs_type()
            cases = {
                t.name: Case(
                    w.name,
                    t.name,
                    t.argv(wl.src, self.dst_root / "DEST"),
                    wl.bytes,
                    wl.files,
                    source=wl.src.spec(),
                    source_fs=src_fs,
                    mode=mode,
                )
                for t in self.tools
            }
            for t in self.tools:
                if not t.runs_workload(w.name):
                    selected = ", ".join(t.spec.workloads or ())
                    cases[t.name].skipped = f"tool is selected only for: {selected}"
                elif mode == "incremental" and t.kind in ("cp", "qcp", "tar"):
                    cases[t.name].skipped = f"{t.kind} copies everything; it has no change detection"
            self.run.cases.extend(cases.values())
            for i in range(self.spec.protocol.repeats):
                delta = self._snapshot_and_change(wl, i) if mode == "incremental" else None
                try:
                    self._round(wl, cases, i, delta)
                finally:
                    if delta is not None:
                        self._restore_source(wl, delta[0])
        finally:
            self.release(wl)

    def _skip_if_hopeless(self, cases: dict[str, Case], case: Case) -> None:
        """Once a tool has shown it is slow_cutoff times slower than the fastest completed median in
        the same workload, measuring it again buys nothing: skip its remaining repeats and keep what
        was measured as the order of magnitude."""
        cutoff = self.spec.protocol.slow_cutoff
        if not cutoff or case.error or case.skipped or case.curtailed:
            return
        walls = case.good_walls()
        if not walls:
            return
        others = [
            statistics.median(c.good_walls())
            for c in cases.values()
            if c is not case and c.good_walls() and not c.skipped
        ]
        if not others:
            return
        fastest = min(others)
        slow = statistics.median(walls)
        # Below the timing floor the numbers are startup noise (see TOO_SHORT_S): a 1 s tool next to
        # a 0.1 s tool is not "an order of magnitude slower", it is two unmeasurable cases.
        order = self.spec.protocol.order
        planned = sum(case.tool in row for row in order) if order else self.spec.protocol.repeats
        if slow >= cutoff * fastest and slow >= TOO_SHORT_S and len(case.repeats) < planned:
            n = len(case.repeats)
            times = "once" if n == 1 else f"{n} times"
            case.curtailed = (
                f"no further repeats: >= {cutoff:g}x slower than the fastest tool here "
                f"({slow:.0f} s vs {fastest:.1f} s); measured {times} as the order of magnitude"
            )
            self.log(f"    {case.tool}: {case.curtailed}")

    def _round(self, wl: Workload, cases: dict[str, Case], i: int, delta) -> None:
        """Run the configured tools in chronological round i."""
        w = wl.spec
        by_name = {t.name: t for t in self.tools}
        order = self.spec.protocol.order
        round_tools = [by_name[name] for name in order[i]] if order else self.tools
        for position, tool in enumerate(round_tools):
            case = cases[tool.name]
            if case.error or case.skipped or case.curtailed:
                continue
            # Only the very first read of a born-cold tree can skip eviction, and verification's
            # checksum pass (when enabled) already took that read.
            first = i == 0 and position == 0 and wl.generated and not self.spec.protocol.verify
            r = self._repeat(wl, tool, case, len(case.repeats), first, delta)
            r.round_index = i
            case.repeats.append(r)
            if self.abort:
                raise self.abort
            if r.exit_code == TIMED_OUT and not case.error:
                budget = self.spec.protocol.tool_timeout
                case.error = f"stopped at the {budget:g} s time budget; treat its rate as an upper bound"
            for c in cases.values():  # a new fast result can retire any earlier slow case, whatever the order
                self._skip_if_hopeless(cases, c)
            rate = case.moved_bytes(r) / r.wall_s / 1e6 if r.wall_s else 0
            status = "ok" if r.exit_code == 0 and r.verified is not False else "FAILED"
            dur = f" +fsync {r.fsync_s:6.2f} s" if r.fsync_s is not None else ""
            self.log(f"  {w.name:12} {tool.name:8} #{i + 1}: {r.wall_s:8.2f} s  {rate:8.1f} MB/s{dur}  {status}")
            if r.exit_code != 0 and not case.error:
                case.error = f"exit {r.exit_code}: {r.stderr_tail}"
            if case.error:
                self.log(f"    {case.error}")

    def failed(self) -> bool:
        return bool(self.run.aborted) or any(
            c.error or any(r.verified is False for r in c.repeats) for c in self.run.cases
        )

    def probe(self) -> None:
        """Storage probes at every local endpoint the harness owns: the destination (always owned) and
        the source scratch when fixtures are generated there. A user's path workload is never probed;
        a remote endpoint is not probed yet, and the result says so."""
        targets = []
        if not self.dst_root.is_remote:
            targets.append(("destination", self.dst_root))
        if not self.src_root.is_remote and any(w.kind != "path" for w in self.spec.workloads):
            targets.append(("source", self.src_root))
        for where, loc in (("destination", self.dst_root), ("source", self.src_root)):
            if (where, loc) not in targets:
                why = "remote" if loc.is_remote else "a user path, read-only"
                self.run.uncontrolled.append(f"{where} storage not probed ({why})")
        for where, loc in targets:
            self.log(f"probing {where} storage at {loc.spec()} ...")
            found = probes.run_all(Path(loc.path), where)
            self.run.probes.extend(probes.as_dicts(found))
            for p in found:
                v = "n/a" if p.value is None else f"{p.value:.0f}"
                self.log(f"  {p.name:10} {v} {p.unit}  ({p.detail})")

    def _remote(self) -> Location | None:
        return self.dst_root if self.dst_root.is_remote else self.src_root if self.src_root.is_remote else None

    def probe_network(self, when: str) -> None:
        """Snapshot the path at the start and the end of the run. Never fatal: a host without ip/tc/ping
        gets an 'uncontrolled' note instead."""
        remote = self._remote()
        if remote is None:
            return
        alias = remote.host.rsplit("@", 1)[-1]
        # The endpoint is usually an ssh alias; the address ssh would connect to is what routes.
        cfg = probes._run([*shlex.split(remote.ssh), "-G", alias]) or ""
        target = next((ln.split(None, 1)[1] for ln in cfg.splitlines() if ln.startswith("hostname ")), alias)
        route = probes._run(["ip", "route", "get", target])
        dev = None
        if route:
            parts = route.split()
            dev = parts[parts.index("dev") + 1] if "dev" in parts else None
        state = probes.netstate(dev, remote.sh, target, ping=(when == "before"))
        state["target"] = target
        if dev is None:
            state["missing"].append("local_dev (route lookup failed)")
        self.run.network = self.run.network or {}
        self.run.network[when] = state
        if when == "before":
            ping = state.get("ping") or {}
            self.log(
                f"network: {ping.get('loss_pct', '?')}% ping loss, rtt {ping.get('rtt_avg_ms', '?')} ms; "
                f"local dev {dev or '?'}"
            )
            for m in state.get("missing", []):
                self.run.uncontrolled.append(f"network state not recorded: {m} unavailable")
            if any("netem" in (state.get(k) or "") for k in ("local_qdisc", "remote_qdisc")):
                self.run.uncontrolled.append("netem is active on the path: emulated impairment, see run.network")
        else:
            before = self.run.network.get("before", {})
            for end in ("local", "remote"):
                a, b = probes.netem_drops(before.get(f"{end}_qdisc")), probes.netem_drops(state.get(f"{end}_qdisc"))
                if a is not None and b is not None:
                    self.run.network[f"{end}_netem_dropped_during_run"] = b - a
                    self.log(f"network: {end} netem dropped {b - a} packets during the run")

    def execute(self) -> Run:
        self.probe_network("before")
        if self.spec.protocol.probes:
            self.probe()
        try:
            for w in self.spec.workloads:
                self.run_workload(w)
        except CleanupFailed as e:
            self.run.aborted = f"destination cleanup failed: {e}"
        self.probe_network("after")
        for c in self.run.cases:
            walls = c.good_walls()
            c.too_short = bool(walls) and statistics.median(walls) < TOO_SHORT_S
        self.run.finished = datetime.now(UTC).isoformat(timespec="seconds")
        return self.run
