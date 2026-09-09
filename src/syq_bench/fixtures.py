"""Synthetic source trees, written cold.

The *shape* of a fixture (file count, sizes, layout) is a deterministic function
of the seed, so two runs with the same seed copy identically shaped trees.
Contents are pseudo-random so compression cannot cheat. Files are written with
O_DIRECT where the filesystem allows, so generating a fixture does not leave it
in the page cache (DESIGN.md, "Cache state without drop_caches"); where it does
not (tmpfs), the pages are evicted with posix_fadvise afterwards.
"""

from __future__ import annotations

import mmap
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
_ALIGN = 4096
_CHUNK = 4 * MIB
MIN_FILE = 1024  # smallest file in a mixed tree


@dataclass(frozen=True)
class Fixture:
    name: str
    description: str
    file_count: int
    total_bytes: int

    def generate(self, root: Path, seed: int = 0) -> bool:
        """Write the tree under root. Returns True if the filesystem took O_DIRECT writes, so the
        tree is born cold (files or tails below one block are evicted with fadvise instead)."""
        root.mkdir(parents=True, exist_ok=True)
        cold = supports_odirect(root)
        self._write(root, seed)
        return cold

    def _write(self, root: Path, seed: int) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class LargeFile(Fixture):
    def _write(self, root: Path, seed: int) -> None:
        write_random(root / "large.bin", self.total_bytes, seed)


@dataclass(frozen=True)
class SmallFiles(Fixture):
    """`file_count` files of equal size, spread over a shallow directory tree."""

    fanout: int = 256

    def _write(self, root: Path, seed: int) -> None:
        size = max(self.total_bytes // self.file_count, 1)
        rng = random.Random(seed)
        for i in range(self.file_count):
            d = root / f"d{i % self.fanout:03d}"
            d.mkdir(parents=True, exist_ok=True)
            write_random(d / f"f{i:07d}", size, rng.getrandbits(64))


@dataclass(frozen=True)
class MixedTree(Fixture):
    """Exactly `file_count` files whose sizes are log-uniform between 1 KiB and 64 MiB, rescaled so
    they sum to `total_bytes`; roughly the shape of a project or home directory."""

    def _write(self, root: Path, seed: int) -> None:
        rng = random.Random(seed)
        sizes = _fit_sizes([2 ** rng.uniform(10, 26) for _ in range(self.file_count)], self.total_bytes, MIN_FILE)
        for i, size in enumerate(sizes):
            depth = rng.randint(1, 4)
            d = root.joinpath(*(f"dir{rng.randint(0, 7)}" for _ in range(depth)))
            d.mkdir(parents=True, exist_ok=True)
            write_random(d / f"file{i:06d}", size, rng.getrandbits(64))


def _fit_sizes(raw: list[float], total: int, floor: int) -> list[int]:
    """Scale `raw` so the integer sizes sum to `total`, none below `floor` (requires total >= floor * len)."""
    if total < floor * len(raw):
        raise ValueError(f"{total} bytes cannot hold {len(raw)} files of at least {floor} B")
    scale = total / sum(raw)
    sizes = [max(int(x * scale), floor) for x in raw]
    order = sorted(range(len(sizes)), key=sizes.__getitem__, reverse=True)
    diff = total - sum(sizes)
    for k in order:  # absorb the rounding difference in the largest files, never below the floor
        take = max(diff, floor - sizes[k]) if diff < 0 else diff
        sizes[k] += take
        diff -= take
        if diff == 0:
            break
    return sizes


def make(kind: str, name: str, total_bytes: int, files: int) -> Fixture:
    if kind == "mixed-tree" and total_bytes < files * MIN_FILE:
        raise ValueError(f"mixed-tree needs at least {MIN_FILE} B per file: {files} files need {files * MIN_FILE} B")
    if kind == "small-files" and total_bytes < files:
        raise ValueError("small-files needs at least 1 B per file")
    cls = {"large-file": LargeFile, "small-files": SmallFiles, "mixed-tree": MixedTree}[kind]
    desc = {
        "large-file": f"one {total_bytes / MIB:.0f} MiB file",
        "small-files": f"{files} files of {total_bytes // max(files, 1)} B",
        "mixed-tree": f"{total_bytes / MIB:.0f} MiB in {files} files, 1 KiB-64 MiB",
    }[kind]
    return cls(name, desc, files if kind != "large-file" else 1, total_bytes)


def supports_odirect(directory: Path) -> bool:
    """Whether files in this directory can be opened O_DIRECT (tmpfs cannot)."""
    probe = directory / ".syq-bench-odirect-probe"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o644)
    except OSError:
        return False
    os.close(fd)
    probe.unlink()
    return True


def write_random(path: Path, size: int, seed: int) -> bool:
    """Write `size` pseudo-random bytes. Returns True if the aligned part bypassed the page cache."""
    rng = random.Random(seed)
    aligned = size - size % _ALIGN
    written = 0
    cold = False
    if aligned:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o644)
        except OSError:
            fd = -1
        if fd >= 0:
            try:
                with mmap.mmap(-1, _CHUNK) as buf:  # page-aligned, as O_DIRECT requires
                    while written < aligned:
                        n = min(_CHUNK, aligned - written)
                        buf[:n] = rng.randbytes(n)
                        os.write(fd, memoryview(buf)[:n])
                        written += n
                cold = True
            finally:
                os.close(fd)
    # Unaligned tail (or everything, when O_DIRECT is unavailable): buffered, then evicted.
    with open(path, "ab" if written else "wb") as f:
        while written < size:
            n = min(_CHUNK, size - written)
            f.write(rng.randbytes(n))
            written += n
        f.flush()
        os.fsync(f.fileno())
        evict_fd(f.fileno())
    return cold


def evict_fd(fd: int) -> None:
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)


def walk_files(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                yield p


def evict_tree(root: Path) -> int:
    """Drop the page-cache pages of every regular file under root. Returns file count."""
    n = 0
    for p in walk_files(root):
        fd = os.open(p, os.O_RDONLY)
        try:
            os.fdatasync(fd)
            evict_fd(fd)
        finally:
            os.close(fd)
        n += 1
    return n


def fsync_tree(root: Path) -> None:
    """fsync every regular file and directory under root (never follows symlinks)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in [*files, "."]:
            p = os.path.join(dirpath, name)
            if os.path.islink(p) or not (os.path.isfile(p) or os.path.isdir(p)):
                continue
            fd = os.open(p, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def modify_fraction(root: Path, fraction: float, seed: int, mutate: str = "rewrite") -> tuple[int, int]:
    """Change `fraction` of the regular files under root (at least one), advancing mtime so a
    size+mtime quick check sees the change. Returns (files changed, bytes changed), where bytes
    changed counts what actually differs, not file sizes:

    - "rewrite": the whole file gets new bytes of the same size. A delta algorithm cannot help.
    - "blocks":  ~1 % of each chosen file's 4 KiB blocks (at least one) get new bytes, in place.
      This is rsync's home turf: its rolling-checksum delta sends only the changed blocks, while a
      tool without delta transfer resends the whole file.
    - "append":  1 % of the file's size (at least 4 KiB) is appended; sizes change.
    """
    files = sorted(walk_files(root))
    if not files:
        return 0, 0
    rng = random.Random(seed)
    chosen = rng.sample(files, max(1, round(len(files) * fraction)))
    total = 0
    now = time.time_ns()
    for p in chosen:
        st = p.stat()
        if mutate == "rewrite":
            write_random(p, st.st_size, rng.getrandbits(64))
            total += st.st_size
        elif mutate == "blocks":
            nblocks = max(st.st_size // _ALIGN, 1)
            edit = max(round(nblocks * 0.01), 1)
            with open(p, "r+b") as f:
                for b in sorted(rng.sample(range(nblocks), min(edit, nblocks))):
                    f.seek(b * _ALIGN)
                    n = min(_ALIGN, st.st_size - b * _ALIGN)
                    f.write(rng.randbytes(n))
                    total += n
                f.flush()
                os.fsync(f.fileno())
                evict_fd(f.fileno())
        elif mutate == "append":
            n = max(st.st_size // 100, _ALIGN)
            with open(p, "ab") as f:
                left = n
                while left > 0:
                    step = min(left, _CHUNK)
                    f.write(rng.randbytes(step))
                    left -= step
                f.flush()
                os.fsync(f.fileno())
                evict_fd(f.fileno())
            total += n
        os.utime(p, ns=(now, max(now, st.st_mtime_ns + 1_000_000_000)))
    return len(chosen), total


def tree_bytes(root: Path, with_dirs: bool = False):
    """(file_count, total_bytes[, dir_count]) of a tree; used for path workloads and free-space checks."""
    count = total = dirs = 0
    for dirpath, subdirs, files in os.walk(root):
        dirs += len(subdirs)
        for f in files:
            st = os.lstat(os.path.join(dirpath, f))
            count += 1
            total += st.st_size
    return (count, total, dirs) if with_dirs else (count, total)
