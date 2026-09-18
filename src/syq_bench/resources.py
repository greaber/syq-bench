"""Measure one command on this host: python -m syq_bench.resources --out FILE -- COMMAND.

The same standalone file can wrap a remote helper. It never discovers or measures
unrelated processes. Remote helpers and persistent servers require their own capture.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path


def group_running(pgid: int) -> bool:
    """Ignore dead zombies on Linux; they hold no runnable worker or open fixture."""
    if sys.platform.startswith("linux"):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                return True
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def measure(argv: list[str], output: Path, timeout: float | None = None) -> int:
    if not argv:
        raise ValueError("a command is required")
    if timeout is not None and (isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be finite and positive")
    # Exclusive creation protects earlier evidence and concurrent invocations.
    with output.open("x") as stream:
        record = {
            "schema": 1,
            "argv": argv,
            "started_unix_s": time.time(),
            "status": "starting",
            "accounting_complete": False,
            "scope": "local command and descendants whose usage is collected by their parents",
            "limitations": [
                "Remote processes and persistent servers are excluded; measure them separately.",
                "Peak RSS is the largest process high-water mark, not simultaneous process-tree memory.",
                "Process startup can include the launcher memory footprint; small RSS values have that floor.",
                "Block IO counts filesystem operations, not logical payload or network bytes.",
                "Kernel, filesystem-server and unrelated host resource costs are not attributed.",
            ],
        }

        def save() -> None:
            stream.seek(0)
            json.dump(record, stream, indent=2)
            stream.truncate()
            stream.flush()

        save()
        start = time.monotonic()
        pid = os.posix_spawnp(argv[0], argv, os.environ, setpgroup=0)
        record.update(pid=pid, status="running")
        save()
        stopped = None
        kill_at = None

        def stop(signum, _frame=None):
            nonlocal stopped, kill_at
            if stopped is None:
                stopped = signum
                kill_at = time.monotonic() + 5
            try:
                os.killpg(pid, signum)
            except ProcessLookupError:
                pass

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            while True:
                waited, status, usage = os.wait4(pid, os.WNOHANG)
                if waited:
                    break
                now = time.monotonic()
                if timeout is not None and now - start >= timeout and stopped is None:
                    record["timed_out"] = True
                    stop(signal.SIGTERM)
                if kill_at is not None and now >= kill_at:
                    stop(signal.SIGKILL)
                time.sleep(0.02)
            elapsed = time.monotonic() - start
            # A surviving descendant is not a completed job. Remove only this owned group.
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                pass
            else:
                record["surviving_process_group"] = True
                os.killpg(pid, signal.SIGKILL)
            cleanup_deadline = time.monotonic() + 5
            while group_running(pid) and time.monotonic() < cleanup_deadline:
                time.sleep(0.02)
            record["process_group_cleanup_verified"] = not group_running(pid)
            code = os.waitstatus_to_exitcode(status)
            record.update(
                status="finished",
                exit_code=code,
                wall_s=elapsed,
                user_s=usage.ru_utime,
                sys_s=usage.ru_stime,
                cpu_s=usage.ru_utime + usage.ru_stime,
                max_process_rss_bytes=usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                minor_faults=usage.ru_minflt,
                major_faults=usage.ru_majflt,
                input_blocks=usage.ru_inblock,
                output_blocks=usage.ru_oublock,
                voluntary_context_switches=usage.ru_nvcsw,
                involuntary_context_switches=usage.ru_nivcsw,
                accounting_complete=(
                    code == 0
                    and stopped is None
                    and not record.get("surviving_process_group")
                    and record["process_group_cleanup_verified"]
                ),
            )
            if stopped is not None:
                record["interrupted_signal"] = stopped
            save()
            if not record["process_group_cleanup_verified"]:
                return 1
            if record.get("surviving_process_group") and code == 0 and stopped is None:
                return 1
            return 124 if record.get("timed_out") else 128 + stopped if stopped else code if code >= 0 else 128 - code
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return measure(command, args.out, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
