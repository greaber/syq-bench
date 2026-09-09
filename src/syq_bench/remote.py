"""Everything that touches a remote host lives here.

v1 decision (see DESIGN.md): the remote
runs no Python. Every remote operation is a shell command over ssh. If that
gets unwieldy, this module is the seam to replace with a shipped helper.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from syq_bench import fixtures


@dataclass(frozen=True)
class Location:
    """A local or `user@host:` path."""

    path: PurePosixPath
    host: str | None = None  # "user@host" or None for local
    ssh: str = "ssh"  # ssh client command (a wrapper script may carry port, key, and options)

    @classmethod
    def parse(cls, spec: str, ssh: str = "ssh") -> Location:
        # A colon before any slash means host:path (same heuristic rsync uses).
        head, sep, tail = spec.partition(":")
        if sep and "/" not in head:
            return cls(PurePosixPath(tail or "."), head, ssh)
        return cls(PurePosixPath(spec), None, ssh)

    @property
    def is_remote(self) -> bool:
        return self.host is not None

    def spec(self, trailing_slash: bool = False) -> str:
        p = str(self.path) + ("/" if trailing_slash else "")
        return f"{self.host}:{p}" if self.host else p

    def __truediv__(self, name: str) -> Location:
        return Location(self.path / name, self.host, self.ssh)

    def ssh_argv(self) -> list[str]:
        """The ssh command prefix for this host, e.g. ["ssh", "-o", "BatchMode=yes", "user@host"]."""
        assert self.host is not None
        return [*shlex.split(self.ssh), "-o", "BatchMode=yes", self.host]

    # -- shell execution at this location -------------------------------------------------

    def run(self, argv: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        if self.host is None:
            return subprocess.run(argv, check=check, capture_output=True, text=True)
        return subprocess.run([*self.ssh_argv(), shlex.join(argv)], check=check, capture_output=True, text=True)

    def sh(self, script: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(["sh", "-c", script], check=check)

    # -- the small vocabulary the harness needs ------------------------------------------

    def exists(self) -> bool:
        return self.sh(f"test -e {shlex.quote(str(self.path))}", check=False).returncode == 0

    def is_empty_dir(self) -> bool:
        r = self.sh(f"ls -A {shlex.quote(str(self.path))} | head -1", check=False)
        return r.returncode == 0 and r.stdout.strip() == ""

    def mkdir(self) -> None:
        self.run(["mkdir", "-p", str(self.path)])

    def remove_tree(self, inside: Location) -> None:
        """Remove this path, which must lie strictly inside `inside` (a scratch root the harness owns)."""
        if self.host != inside.host or not self.path.is_relative_to(inside.path) or self.path == inside.path:
            raise ValueError(f"refusing to remove {self.spec()}: not inside scratch root {inside.spec()}")
        self.run(["rm", "-rf", "--", str(self.path)])

    def device_id(self) -> str | None:
        """Identifies the filesystem holding this path (or its nearest existing parent) on its host."""
        q = shlex.quote(str(self.path))
        r = self.sh(f'p={q}; while [ ! -e "$p" ]; do p=$(dirname "$p"); done; stat -c %d "$p"', check=False)
        return r.stdout.strip() or None if r.returncode == 0 else None

    def same_filesystem(self, other: Location) -> bool:
        return self.host == other.host and self.device_id() is not None and self.device_id() == other.device_id()

    def free_inodes(self) -> int | None:
        r = self.sh(f"df -Pi {shlex.quote(str(self.path))} | awk 'NR==2{{print $4}}'", check=False)
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None  # filesystems without inode accounting (btrfs, tmpfs on some kernels) report '-'

    def fs_type(self) -> str | None:
        """Filesystem type at this path ('ext2/ext3', 'nfs', 'tmpfs', ...), or None if unknown."""
        r = self.sh(f"stat -f -c %T {shlex.quote(str(self.path))}", check=False)
        return r.stdout.strip() or None if r.returncode == 0 else None

    def free_bytes(self) -> int:
        r = self.sh(f"df -Pk {shlex.quote(str(self.path))} | awk 'NR==2{{print $4}}'")
        return int(r.stdout.strip()) * 1024

    def drop_caches(self) -> bool:
        """`cache = "drop"` only: global page-cache drop, needs root at this end. Returns success."""
        self.run(["sync"])
        r = self.sh(
            "sysctl -w vm.drop_caches=3 >/dev/null 2>&1 || sudo -n sysctl -w vm.drop_caches=3 >/dev/null 2>&1",
            check=False,
        )
        return r.returncode == 0

    def evict_tree(self) -> bool:
        """Drop the page-cache pages of every regular file under this path (no root). Returns success."""
        if self.host is None:
            try:
                fixtures.evict_tree(Path(self.path))
            except OSError:
                return False
            return True
        q = shlex.quote(str(self.path))
        r = self.sh(
            f"find {q} -type f -print0 | xargs -0 -r -n 64 sh -c "
            "'for f; do dd if=\"$f\" iflag=nocache count=0 status=none || exit 1; done' sh",
            check=False,
        )
        return r.returncode == 0

    def fsync_tree(self) -> bool:
        """fsync every regular file and directory under this path (GNU `sync PATH...`). Returns success."""
        if self.host is None:
            try:
                fixtures.fsync_tree(Path(self.path))
            except OSError:
                return False
            return True
        q = shlex.quote(str(self.path))
        r = self.sh(f"find {q} \\( -type f -o -type d \\) -exec sync -- {{}} +", check=False)
        return r.returncode == 0

    def checksum(self) -> str:
        """Order-independent digest of (relative path, size, sha256) over all regular files."""
        script = (
            f"cd {shlex.quote(str(self.path))} && find . -type f -print0 | sort -z | "
            "xargs -0 sha256sum | sha256sum | cut -d' ' -f1"
        )
        return self.sh(script).stdout.strip()

    def tool_version(self, argv0: str) -> str | None:
        r = self.run([argv0, "--version"], check=False)
        out = (r.stdout or r.stderr).strip().splitlines()
        return out[0] if out else None
