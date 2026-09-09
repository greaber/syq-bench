"""The copy tools under comparison, as command builders.

Every builder returns argv that copies the *contents* of src into dst (rsync
trailing-slash semantics). The spec forbids --delete/--rm in copy args; the
harness owns cleanup.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from syq_bench.remote import Location
from syq_bench.spec import ToolSpec


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def binary(self) -> str:
        return self.spec.binary

    @property
    def remote_ok(self) -> bool:
        return self.kind != "cp"

    @property
    def local_ok(self) -> bool:
        return self.kind not in ("qcp", "tar")  # both exist to move data through ssh

    def runs_workload(self, name: str) -> bool:
        selected = self.spec.workloads
        return selected is None or name in selected

    def argv(self, src: Location, dst: Location) -> list[str]:
        s = self.spec
        remote = src if src.is_remote else dst if dst.is_remote else None
        if s.kind == "cp":
            return [s.binary, *s.args, f"{src.path}/.", str(dst.path)]
        if s.kind == "tar":
            # The folk answer to "rsync is slow": stream a tarball through one ssh session.
            pack = (
                f"{shlex.quote(s.binary)} -C {shlex.quote(str(src.path))} -cf - {' '.join(map(shlex.quote, s.args))} ."
            )
            unpack = f"{shlex.quote(s.binary)} -C {shlex.quote(str(dst.path))} -xf -"
            if src.is_remote:
                pipeline = f"{shlex.join(src.ssh_argv())} {shlex.quote(pack)} | {unpack}"
            elif dst.is_remote:
                pipeline = f"{pack} | {shlex.join(dst.ssh_argv())} {shlex.quote(unpack)}"
            else:
                pipeline = f"{pack} | {unpack}"
            return ["bash", "-o", "pipefail", "-c", pipeline]
        cmd = [s.binary, *s.args]
        if s.kind in ("syq", "qcp") and s.jobs is not None:
            cmd += ["-j", str(s.jobs)]
        if remote is not None and remote.ssh != "ssh":
            if s.kind == "qcp":
                # qcp takes the client as one executable and each extra ssh argument via -S.
                exe, *opts = shlex.split(remote.ssh)
                cmd += ["--ssh", exe]
                for o in opts:
                    cmd += ["-S", o]
            else:
                cmd += ["-e", remote.ssh]
        # qcp 0.9 copies the *contents* of SRC into an existing DST directory (checked against a
        # loopback sshd, with and without a trailing slash), the same as the others; the harness
        # always creates DST first.
        cmd += [src.spec(trailing_slash=True), dst.spec()]
        return ["env", "SYQ_DEBUG=1", *cmd] if s.debug else cmd

    def resolved_binary(self) -> str | None:
        """Absolute path of the local binary, or None if not found."""
        if self.kind == "tar" and shutil.which("bash") is None:
            return None
        if "/" in self.binary:
            return str(Path(self.binary).resolve()) if Path(self.binary).is_file() else None
        return shutil.which(self.binary)
