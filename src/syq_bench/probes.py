"""Cheap environment probes: what could bound a transfer on this setup.

v1 measures local storage only (DESIGN.md, "What limits transfer speed"):
sequential write and read through the page cache bypassed with O_DIRECT, and a
create/stat/unlink loop for metadata latency. Each probe is a few seconds and
touches only the harness's scratch directory. Network and remote probes come
with the remote-source work.
"""

from __future__ import annotations

import mmap
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MIB = 1024 * 1024
SEQ_BYTES = 256 * MIB
META_FILES = 2000


@dataclass
class Probe:
    name: str
    value: float | None  # MB/s for seq_*; files/s for metadata; None if the probe could not run
    unit: str
    detail: str
    where: str = ""  # "source" or "destination": which endpoint's storage this measured


def seq_write(directory: Path, size: int = SEQ_BYTES) -> Probe:
    path = directory / ".probe.seq"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o644)
    except OSError as e:
        return Probe("seq_write", None, "MB/s", f"O_DIRECT unavailable ({e.strerror}); skipped")
    try:
        with mmap.mmap(-1, 4 * MIB) as buf:
            buf[:] = os.urandom(4 * MIB)
            t0 = time.perf_counter()
            done = 0
            while done < size:
                done += os.write(fd, buf)
            os.fsync(fd)
            secs = time.perf_counter() - t0
    except OSError as e:
        path.unlink(missing_ok=True)
        return Probe("seq_write", None, "MB/s", f"write failed ({e.strerror}); skipped")
    finally:
        os.close(fd)
    return Probe("seq_write", size / secs / 1e6, "MB/s", f"{size // MIB} MiB O_DIRECT write + fsync")


def seq_read(directory: Path, size: int = SEQ_BYTES) -> Probe:
    path = directory / ".probe.seq"
    if not path.exists():
        return Probe("seq_read", None, "MB/s", "no probe file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    except OSError as e:
        path.unlink(missing_ok=True)
        return Probe("seq_read", None, "MB/s", f"O_DIRECT unavailable ({e.strerror}); skipped")
    try:
        with mmap.mmap(-1, 4 * MIB) as buf:
            t0 = time.perf_counter()
            done = 0
            while True:
                n = os.readv(fd, [buf])
                if n <= 0:
                    break
                done += n
            secs = time.perf_counter() - t0
    except OSError as e:
        return Probe("seq_read", None, "MB/s", f"read failed ({e.strerror}); skipped")
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)
    return Probe("seq_read", done / secs / 1e6, "MB/s", f"{done // MIB} MiB O_DIRECT read")


def metadata(directory: Path, count: int = META_FILES) -> Probe:
    d = directory / ".probe.meta"
    try:
        d.mkdir(exist_ok=True)
        t0 = time.perf_counter()
        for i in range(count):
            p = d / f"f{i}"
            with open(p, "wb") as f:
                f.write(b"x")
            os.stat(p)
        dfd = os.open(d, os.O_RDONLY)
        os.fsync(dfd)
        os.close(dfd)
        secs = time.perf_counter() - t0
    except OSError as e:
        return Probe("metadata", None, "files/s", f"failed ({e.strerror}); skipped")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return Probe("metadata", count / secs, "files/s", f"create+write+stat of {count} 1-byte files, then dir fsync")


def run_all(directory: Path, where: str = "") -> list[Probe]:
    out = [seq_write(directory), seq_read(directory), metadata(directory)]
    for p in out:
        p.where = where
    return out


def as_dicts(probes: list[Probe]) -> list[dict]:
    return [asdict(p) for p in probes]


def ceiling(probes: list[dict], files: int, total_bytes: int, remote: bool = False) -> str | None:
    """One line naming the likely bound for a workload from the probes, or None if nothing applies.
    The slower of the source's read floor and the destination's write (or metadata) floor wins."""
    if remote:
        network = next(
            (p for p in probes if p.get("name") == "network_receive" and p.get("value") and p.get("where") == "path"),
            None,
        )
        if network is None:
            return "unknown: the path crosses a network and no network probe was run"
        rate = network["value"]
        floor = total_bytes / rate / 1e6
        qualifier = "; metadata/round-trip limits are not measured" if files > 1 else ""
        return f"network path: {rate:.0f} MB/s measured receiver goodput{qualifier} -> ~{floor:.1f} s floor"
    dst = {p["name"]: p for p in probes if p.get("value") and p.get("where", "destination") == "destination"}
    src = {p["name"]: p for p in probes if p.get("value") and p.get("where") == "source"}
    floors: list[tuple[float, str]] = []
    if files > 1 and "metadata" in dst and total_bytes / files < 64 * 1024:
        rate = dst["metadata"]["value"]
        floors.append((files / rate, f"destination metadata-bound: {rate:.0f} files/s"))
    if "seq_write" in dst:
        rate = dst["seq_write"]["value"]
        floors.append((total_bytes / rate / 1e6, f"destination storage-bound: {rate:.0f} MB/s sequential write"))
    if "seq_read" in src:
        rate = src["seq_read"]["value"]
        floors.append((total_bytes / rate / 1e6, f"source storage-bound: {rate:.0f} MB/s sequential read"))
    if not floors:
        return None
    secs, why = max(floors)
    return f"{why} -> ~{secs:.1f} s floor"


def _run(argv: list[str]) -> str | None:
    import subprocess

    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def netstate(local_dev: str | None, remote_sh, host: str, ping: bool = True) -> dict:
    """What the network path looked like: on both ends the qdiscs with their statistics (a netem
    shows its own dropped-packet counter here) and the filters that decide which traffic a netem
    applies to, plus a short ping sample. Snapshotted before and after a run so an emulated-loss
    run cannot pass for a clean one, and a mistargeted filter is visible. Every tool is optional:
    a missing command leaves None, never an error."""
    import re

    out: dict = {
        "local_dev": local_dev,
        "local_qdisc": _run(["tc", "-s", "qdisc", "show", "dev", local_dev]) if local_dev else None,
        "local_filters": _run(["tc", "filter", "show", "dev", local_dev]) if local_dev else None,
        "remote_qdisc": None,
        "remote_filters": None,
        "ping": None,
        "missing": [],
    }
    for k in ("local_qdisc", "local_filters"):
        if local_dev and out[k] is None:
            out["missing"].append(k)
    try:
        r = remote_sh(
            "d=$(ip route get $(echo $SSH_CONNECTION | cut -d' ' -f1) 2>/dev/null | "
            "awk '{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1)}' | head -1); "
            '[ -n "$d" ] && tc -s qdisc show dev "$d" && echo ==FILTERS== && tc filter show dev "$d"',
            False,
        )
        if r.returncode == 0 and "==FILTERS==" in r.stdout:
            q, f = r.stdout.split("==FILTERS==", 1)
            out["remote_qdisc"], out["remote_filters"] = q.strip(), f.strip()
        else:
            out["missing"].append("remote_qdisc")
    except OSError:
        out["missing"].append("remote_qdisc")
    if ping:
        target = host.rsplit("@", 1)[-1]
        text = _run(["ping", "-c", "20", "-i", "0.2", "-q", target])
        m = re.search(r"(\d+)% packet loss", text or "")
        rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", text or "")
        if m:
            out["ping"] = {
                "loss_pct": int(m.group(1)),
                "rtt_avg_ms": float(rtt.group(2)) if rtt else None,
                "count": 20,
                "note": "20 packets: resolution 5 %; use the netem dropped counters for the emulated rate",
            }
        else:
            out["missing"].append("ping")
    return out


def netem_drops(qdisc_text: str | None) -> int | None:
    """Sum of 'dropped N' over netem qdiscs in a `tc -s qdisc show` dump, or None if no netem."""
    import re

    if not qdisc_text:
        return None
    total, found = 0, False
    for block in re.split(r"(?=^qdisc )", qdisc_text, flags=re.M):
        if block.startswith("qdisc netem"):
            found = True
            m = re.search(r"dropped (\d+)", block)
            total += int(m.group(1)) if m else 0
    return total if found else None
