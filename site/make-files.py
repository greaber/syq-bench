#!/usr/bin/env python3
"""Create a fresh benchmark dataset; standard-library Python 3.9+ only.

Preparation is outside timing. This uses buffered writes and does not promise
cold cache. It never deletes a tree or overwrites an existing destination.
"""

import argparse
import os
import random
import shutil
from pathlib import Path


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def create(root, files, size, directories, seed):
    if files <= 0 or size <= 0 or not 0 <= directories <= files:
        raise ValueError("positive file count/size and 0 <= directories <= files required")
    # Keep payload space plus conservative per-entry overhead and a reserve.
    required = files * size + (files + directories) * 16384 + 64 * 1024**2
    if shutil.disk_usage(root.parent).free < required:
        raise ValueError(f"need at least {required} free bytes before preparation")
    root.mkdir(mode=0o700)  # Refuse even an existing empty directory.
    rng = random.Random(seed)
    for i in range(files):
        directory = root / f"d{i % directories:03d}" if directories else root
        directory.mkdir(exist_ok=True)
        target = directory / f"f{i:07d}"
        with target.open("xb") as out:
            left = size
            while left:
                data = rng.randbytes(min(left, 1024**2))
                out.write(data)
                left -= len(data)
        target.chmod(0o644)
        os.utime(target, (1700000000, 1700000000))
        if (i + 1) % 1000 == 0 or i + 1 == files:
            print(f"prepared {i + 1}/{files} files", flush=True)
    for directory in root.iterdir():
        if directory.is_dir():
            directory.chmod(0o755)
            os.utime(directory, (1700000000, 1700000000))
    print(f"created {files * size} payload bytes in {root}; cache is not controlled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--files", type=positive, required=True)
    parser.add_argument("--size", type=positive, required=True, help="bytes per file")
    parser.add_argument("--directories", type=int, default=256, help="0 puts files directly in root")
    parser.add_argument("--seed", type=int, default=9911)
    args = parser.parse_args()
    if not 0 <= args.directories <= args.files:
        parser.error("directories must be between zero and files")
    create(args.root, args.files, args.size, args.directories, args.seed)


if __name__ == "__main__":
    main()
