"""Run specs: the TOML file that describes a benchmark run.

A spec names endpoints, tools, workloads, and the protocol; the harness derives
the steps. The parsed spec is stored verbatim (as loaded, plus overrides) in
every result so a result is reproducible and two results are comparable by
diffing their specs. See BENCHMARKING.md, "Describe the job, then repeat it fairly".
"""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

CACHE_MODES = ("evict", "warm", "drop")
FIXTURE_KINDS = ("large-file", "small-files", "mixed-tree", "path")
TOOL_KINDS = ("syq", "rsync", "cp", "qcp", "tar", "rclone")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Arguments that delete or move files. The harness owns every removal.
FORBIDDEN_ARGS = ("--delete", "--del", "--remove-source-files", "--remove-sent-files", "--rm")
_UNITS = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9, "kib": 2**10, "mib": 2**20, "gib": 2**30}


class SpecError(ValueError):
    pass


def parse_size(v: int | str) -> int:
    """'4GiB', '400M', 1024 -> bytes."""
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*([0-9.]+)\s*([kmg]?)(i?)b?\s*", v.lower())
    if not m:
        raise SpecError(f"bad size {v!r}")
    num, prefix, binary = m.groups()
    return int(float(num) * _UNITS[prefix + ("ib" if prefix and binary else "")])


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # one of TOOL_KINDS; decides argv shape
    binary: str
    args: tuple[str, ...]
    jobs: int | None
    workloads: tuple[str, ...] | None = None  # None: every workload; otherwise an explicit allowlist
    debug: bool = False  # run syq with its SYQ_DEBUG phase/worker diagnostics enabled
    rclone_backend: str = "local"  # local (including mounts), sftp, sftp-ssh, or webdav
    rclone_url: str | None = None  # WebDAV URL, credentials supplied through the environment
    rclone_root: str | None = None  # absolute filesystem directory served at rclone_url


BLOCK = 4096  # a file costs at least one block on disk, whatever its size


@dataclass(frozen=True)
class WorkloadSpec:
    name: str
    kind: str  # one of FIXTURE_KINDS
    bytes: int  # generated total; 0 for "path"
    files: int  # generated count; 0 for "path"
    path: str | None  # for kind == "path": an existing, read-only directory (may be host:path)
    changed: float | None = None  # None: fresh copy. 0.0: re-sync, nothing changed. 0.01: 1 % of files changed.
    mutate: str = "rewrite"  # how a changed file changes: "rewrite" (new bytes, delta-hostile),
    # "blocks" (a few 4 KiB blocks edited in place, delta-friendly), "append" (bytes added at the end)

    def disk_bytes(self) -> int:
        """Space a generated tree can occupy: declared bytes, plus a block per file and directory,
        plus one round of append growth (each chosen file grows max(size/100, one block); the source
        is restored between rounds, so one round is the peak)."""
        total = self.bytes + self.disk_inodes() * BLOCK
        if self.changed and self.mutate == "append":
            total += self.bytes // 100 + self.files * BLOCK
        return total

    def disk_inodes(self) -> int:
        """Inodes a generated tree needs: files plus an upper bound on directories. small-files uses a
        flat fanout of 256; mixed-tree nests each file up to 4 deep under 8 names per level, so at most
        4 directories per file and never more than the whole 8 + 8^2 + 8^3 + 8^4 tree; plus the root."""
        if self.kind == "small-files":
            dirs = min(256, self.files)
        elif self.kind == "mixed-tree":
            dirs = min(4 * self.files, sum(8**k for k in range(1, 5)))
        else:
            dirs = 0
        return self.files + dirs + 1

    def dest_factor(self) -> int:
        """Trees the destination holds at once: an incremental repeat prepopulates the full basis and
        the tool then builds temporary/sidecar copies beside it while re-syncing - two trees' worth."""
        return 2 if self.changed is not None else 1

    def scratch_factor(self) -> int:
        """How many copies of the tree live on the source scratch at once: an incremental workload keeps
        a snapshot of its source beside it during each repeat."""
        return 2 if self.changed is not None else 1


@dataclass(frozen=True)
class Protocol:
    repeats: int = 3
    cache: str = "evict"
    durable: bool = True
    verify: bool = True
    probes: bool = True  # local storage probes before the first workload
    tool_timeout: float | None = None  # kill a single tool invocation after this many seconds
    slow_cutoff: float = 10.0  # skip a tool's remaining repeats once it is at least this many times slower
    # than the workload's fastest completed median (0 disables); the first repeat still runs, so the
    # order of magnitude is measured once and recorded as a lower bound instead of re-measured.
    seed: int | None = None  # None: fresh per run, recorded in the result
    order: tuple[tuple[str, ...], ...] | None = None  # Explicit tools in each round; omissions are intentional.


@dataclass(frozen=True)
class Spec:
    name: str
    description: str
    source: str  # scratch where fixtures are generated (local or host:path)
    destination: str  # empty or absent scratch directory (local or host:path)
    ssh: str  # ssh client command used by the harness and passed to every tool (-e / --ssh)
    tools: tuple[ToolSpec, ...]
    workloads: tuple[WorkloadSpec, ...]
    protocol: Protocol
    raw: dict[str, Any] = field(compare=False, repr=False, default_factory=dict)


def _req(d: dict, key: str, ctx: str) -> Any:
    if key not in d:
        raise SpecError(f"{ctx}: missing '{key}'")
    return d[key]


def _name(d: dict, ctx: str) -> str:
    """Names become single path components under the scratch roots, so they must be exactly that."""
    name = str(_req(d, "name", ctx))
    if not _NAME.fullmatch(name) or name in (".", ".."):
        raise SpecError(f"{ctx}: name {name!r} must be a plain path component ([A-Za-z0-9._-], not starting with '.')")
    return name


def _tool(d: dict, i: int) -> ToolSpec:
    ctx = f"tools[{i}]"
    name = _name(d, ctx)
    kind = d.get("kind", name)
    if kind not in TOOL_KINDS:
        raise SpecError(f"{ctx}: kind must be one of {TOOL_KINDS}, got {kind!r} (set 'kind' when 'name' is a label)")
    debug = d.get("debug", False)
    if not isinstance(debug, bool):
        raise SpecError(f"{ctx}.debug must be true or false")
    if debug and kind != "syq":
        raise SpecError(f"{ctx}.debug is supported only for syq")
    default_args = {"qcp": ["-rpq"], "tar": [], "rclone": ["--create-empty-src-dirs"]}.get(kind, ["-a"])
    args = tuple(str(a) for a in d.get("args", default_args))
    bad = [a for a in args if a.split("=", 1)[0] in FORBIDDEN_ARGS or a.startswith("--delete-")]
    if bad:
        raise SpecError(f"{ctx}: {' '.join(bad)} not allowed: the harness owns every removal")
    selected = d.get("workloads")
    if selected is not None:
        if not isinstance(selected, list) or not selected or not all(isinstance(v, str) for v in selected):
            raise SpecError(f"{ctx}.workloads must be a non-empty array of workload names")
        if len(set(selected)) != len(selected):
            raise SpecError(f"{ctx}.workloads contains duplicate names: {selected}")
        selected = tuple(selected)
    backend = d.get("rclone_backend", "local")
    url, root = d.get("rclone_url"), d.get("rclone_root")
    if kind != "rclone" and any(k.startswith("rclone_") for k in d):
        raise SpecError(f"{ctx}: rclone settings require kind = rclone")
    if backend not in ("local", "sftp", "sftp-ssh", "webdav"):
        raise SpecError(f"{ctx}: rclone_backend must be local, sftp, sftp-ssh, or webdav")
    if kind == "rclone":
        if d.get("jobs") is not None:
            raise SpecError(f"{ctx}: use rclone --transfers in args instead of jobs")
        if backend == "webdav":
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                valid_url = parsed and parsed.scheme == "https" and parsed.hostname and parsed.port != 0
            except ValueError:
                valid_url = False
            if not valid_url or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise SpecError(f"{ctx}: rclone_url must be an HTTPS URL without credentials, query, or fragment")
            if not isinstance(root, str) or not root.startswith("/") or ".." in PurePosixPath(root).parts:
                raise SpecError(f"{ctx}: rclone_root must be an absolute filesystem directory without ..")
        elif url is not None or root is not None:
            raise SpecError(f"{ctx}: rclone_url and rclone_root are only for webdav")
        # The adapter owns the operation and endpoint routing, just as the harness owns cleanup.
        reserved = (
            "--sftp-ssh",
            "--sftp-server-command",
            "--sftp-subsystem",
            "--webdav-url",
            "--config",
        )
        if any(a.split("=", 1)[0] in reserved for a in args):
            raise SpecError(f"{ctx}: rclone endpoint/config arguments are managed by the harness")
    return ToolSpec(name, kind, d.get("binary", kind), args, d.get("jobs"), selected, debug, backend, url, root)


def _workload(d: dict, i: int) -> WorkloadSpec:
    ctx = f"workloads[{i}]"
    name = _name(d, ctx)
    kind = d.get("kind", "path" if "path" in d else None)
    if kind not in FIXTURE_KINDS:
        raise SpecError(f"{ctx}: kind must be one of {FIXTURE_KINDS}")
    changed = d.get("changed")
    if changed is not None and not (0 <= float(changed) <= 1):
        raise SpecError(f"{ctx}: changed must be a fraction in [0, 1]")
    if changed is not None and kind == "path":
        raise SpecError(f"{ctx}: 'changed' rewrites source files, which a read-only path workload forbids")
    mutate = d.get("mutate", "rewrite")
    if mutate not in ("rewrite", "blocks", "append"):
        raise SpecError(f"{ctx}: mutate must be rewrite, blocks, or append")
    if kind == "path":
        return WorkloadSpec(name, kind, 0, 0, _req(d, "path", ctx))
    size = parse_size(_req(d, "bytes", ctx))
    files = {"large-file": 1}.get(kind) or int(_req(d, "files", ctx))
    if size < 1 or files < 1:
        raise SpecError(f"{ctx}: bytes and files must be positive")
    if kind == "mixed-tree" and size < files * 1024:
        raise SpecError(f"{ctx}: mixed-tree files are at least 1 KiB, so bytes must be >= {files * 1024}")
    if kind == "small-files" and size < files:
        raise SpecError(f"{ctx}: small-files needs at least one byte per file")
    return WorkloadSpec(name, kind, size, files, None, None if changed is None else float(changed), mutate)


def _protocol(d: dict) -> Protocol:
    order = d.get("order")
    if order is not None and (
        not isinstance(order, list)
        or not order
        or any(not isinstance(row, list) or not row or any(not isinstance(t, str) for t in row) for row in order)
    ):
        raise SpecError("protocol.order must be a non-empty array of non-empty tool-name arrays")
    p = Protocol(
        repeats=int(d.get("repeats", 3)),
        cache=d.get("cache", "evict"),
        durable=bool(d.get("durable", True)),
        verify=bool(d.get("verify", True)),
        probes=bool(d.get("probes", True)),
        tool_timeout=float(d["tool_timeout"]) if "tool_timeout" in d else None,
        slow_cutoff=float(d.get("slow_cutoff", 10.0)),
        seed=d.get("seed"),
        order=tuple(tuple(row) for row in order) if order is not None else None,
    )
    if p.cache not in CACHE_MODES:
        raise SpecError(f"protocol.cache must be one of {CACHE_MODES}")
    if p.repeats < 1:
        raise SpecError("protocol.repeats must be >= 1")
    if p.tool_timeout is not None and not (math.isfinite(p.tool_timeout) and p.tool_timeout > 0):
        raise SpecError("protocol.tool_timeout must be a positive finite number of seconds")
    if not math.isfinite(p.slow_cutoff) or (p.slow_cutoff != 0 and p.slow_cutoff <= 1):
        raise SpecError("protocol.slow_cutoff must be 0 (disabled) or a finite value above 1")
    if p.order is not None and (len(p.order) != p.repeats or any(len(set(row)) != len(row) for row in p.order)):
        raise SpecError("protocol.order must have one row per repeat, with no duplicate tool within a row")
    return p


def from_dict(raw: dict[str, Any]) -> Spec:
    ep = _req(raw, "endpoints", "spec")
    tools = [_tool(t, i) for i, t in enumerate(raw.get("tools", []))]
    workloads = [_workload(w, i) for i, w in enumerate(raw.get("workloads", []))]
    if not tools or not workloads:
        raise SpecError("spec needs at least one [[tools]] and one [[workloads]] entry")
    for coll, label in ((tools, "tool"), (workloads, "workload")):
        names = [x.name for x in coll]
        if len(set(names)) != len(names):
            raise SpecError(f"duplicate {label} names: {names}")
    workload_names = {w.name for w in workloads}
    for i, tool in enumerate(tools):
        unknown = set(tool.workloads or ()) - workload_names
        if unknown:
            raise SpecError(f"tools[{i}].workloads names unknown workloads: {sorted(unknown)}")
    name = str(raw.get("name", "unnamed"))
    if not _NAME.fullmatch(name):
        raise SpecError(f"name {name!r} must be a plain path component ([A-Za-z0-9._-]); it names the result file")
    protocol = _protocol(raw.get("protocol", {}))
    if protocol.order is not None:
        scheduled = {name for row in protocol.order for name in row}
        if scheduled != {t.name for t in tools}:
            raise SpecError("protocol.order must name every configured tool and no unknown tools")
    return Spec(
        name=name,
        description=raw.get("description", ""),
        source=_req(ep, "source", "endpoints"),
        destination=_req(ep, "destination", "endpoints"),
        ssh=str(ep.get("ssh", "ssh")),
        tools=tuple(tools),
        workloads=tuple(workloads),
        protocol=protocol,
        raw=raw,
    )


def apply_overrides(raw: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """`--set a.b=c` on the raw TOML dict. Values parse as TOML literals, falling back to strings."""
    for item in overrides:
        key, sep, val = item.partition("=")
        if not sep:
            raise SpecError(f"override must be key=value: {item!r}")
        try:
            parsed = tomllib.loads(f"v = {val}")["v"]
        except tomllib.TOMLDecodeError:
            parsed = val
        node: Any = raw
        parts = key.split(".")
        for part in parts[:-1]:
            node = _child(node, part, key, create=True)
        if isinstance(node, list):
            node[_index(node, parts[-1], key)] = parsed
        else:
            node[parts[-1]] = parsed
    return raw


def _index(node: list, part: str, key: str) -> int:
    if not part.isdigit() or int(part) >= len(node):
        raise SpecError(f"override {key}: '{part}' is not an index into a {len(node)}-element array")
    return int(part)


def _child(node: Any, part: str, key: str, create: bool) -> Any:
    if isinstance(node, list):
        return node[_index(node, part, key)]
    if not isinstance(node, dict):
        raise SpecError(f"override {key}: '{part}' is not a table")
    return node.setdefault(part, {}) if create else node[part]


def load(path: Path, overrides: list[str] | None = None) -> Spec:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return from_dict(apply_overrides(raw, overrides or []))
