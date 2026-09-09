"""Minimal local ReFrame configuration for Fly campaign workers."""

from __future__ import annotations

import os

site_configuration = {
    "systems": [
        {
            "name": "fly-orchestrator",
            "descr": "Local Fly campaign orchestrator",
            "hostnames": [".*"],
            "max_local_jobs": int(os.environ["SYQ_BENCH_FLY_MAX_PARALLEL"]),
            "partitions": [
                {
                    "name": "default",
                    "scheduler": "local",
                    "launcher": "local",
                    "environs": ["fly-worker"],
                }
            ],
        }
    ],
    "environments": [{"name": "fly-worker", "cc": "cc", "cxx": "", "ftn": ""}],
}
