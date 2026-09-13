"""The copy tools under comparison, as command builders.

Every builder returns argv that copies the *contents* of src into dst (rsync
trailing-slash semantics). The spec forbids --delete/--rm in copy args; the
harness owns cleanup.
"""

from __future__ import annotations

import secrets
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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
        return self.kind != "cp" and (self.kind != "rclone" or self.spec.rclone_backend != "local")

    @property
    def local_ok(self) -> bool:
        return self.kind not in ("qcp", "tar") and (self.kind != "rclone" or self.spec.rclone_backend == "local")

    def runs_workload(self, name: str) -> bool:
        selected = self.spec.workloads
        return selected is None or name in selected

    def argv(self, src: Location, dst: Location) -> list[str]:
        s = self.spec
        remote = src if src.is_remote else dst if dst.is_remote else None
        if s.kind == "rclone":
            return self.rclone_argv("copy", src, dst)
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

    def rclone_path(self, location: Location) -> str:
        if not location.is_remote:
            # Explicit local backend avoids interpreting colons in local names as a remote.
            return ":local:" + str(location.path)
        if self.spec.rclone_backend in ("sftp", "sftp-ssh"):
            return ":sftp:" + str(location.path)
        if self.spec.rclone_backend == "webdav":
            root = PurePosixPath(self.spec.rclone_root)
            if ".." in location.path.parts or not location.path.is_relative_to(root):
                raise ValueError("WebDAV path is outside rclone_root")
            return ":webdav:" + str(location.path.relative_to(root))
        raise ValueError("rclone local backend requires mounted filesystem paths")

    def rclone_argv(self, command: str, *locations: Location) -> list[str]:
        remotes = [loc for loc in locations if loc.is_remote]
        if len(remotes) > 1:
            raise ValueError("rclone benchmark requires a local endpoint")
        cmd = [self.binary, command, "--config", "/dev/null"]
        if remotes:
            remote = remotes[0]
            if self.spec.rclone_backend == "sftp":
                user, sep, host = remote.host.rpartition("@")
                cmd += ["--sftp-host", host, "--sftp-known-hosts-file", str(Path.home() / ".ssh/known_hosts")]
                if sep:
                    cmd += ["--sftp-user", user]
            elif self.spec.rclone_backend == "sftp-ssh":
                # External OpenSSH preserves the harness's aliases, keys, ports and wrappers.
                cmd += ["--sftp-ssh", shlex.join(remote.ssh_argv())]
            elif self.spec.rclone_backend == "webdav":
                cmd += ["--webdav-url", self.spec.rclone_url]
        elif self.spec.rclone_backend != "local":
            raise ValueError("rclone network backend requires a remote endpoint")
        args = self.spec.args
        if command == "cat":
            # This is a copy-command flag, unlike global/backend tuning flags.
            args = tuple(a for a in args if a.split("=", 1)[0] != "--create-empty-src-dirs")
        return [*cmd, *args, "--", *(self.rclone_path(loc) for loc in locations)]

    def check_destination(self, dst: Location) -> None:
        """Before separately configured backend writes, prove it reaches the SSH-owned tree.

        Only a read uses the unproven endpoint. The random marker lives in this repeat's
        owned destination and is removed before timing and checksum verification.
        """
        if self.kind != "rclone" or self.spec.rclone_backend not in ("sftp", "webdav"):
            return
        if not dst.is_remote:
            raise ValueError("SFTP/WebDAV campaign currently supports uploads only")
        token = secrets.token_hex(32)
        marker = dst / (".syq-bench-endpoint-" + secrets.token_hex(16))
        created = False
        try:
            dst.sh(f"(set -C; printf %s {shlex.quote(token)} > {shlex.quote(str(marker.path))})")
            created = True
            result = subprocess.run(
                self.rclone_argv("cat", marker), capture_output=True, text=True, check=True, timeout=30
            )
            if result.stdout != token:
                raise OSError("rclone endpoint does not expose the owned destination; refusing copy")
        finally:
            if created:
                dst.run(["rm", "-f", "--", str(marker.path)])

    def resolved_binary(self) -> str | None:
        """Absolute path of the local binary, or None if not found."""
        if self.kind == "tar" and shutil.which("bash") is None:
            return None
        if "/" in self.binary:
            return str(Path(self.binary).resolve()) if Path(self.binary).is_file() else None
        return shutil.which(self.binary)
