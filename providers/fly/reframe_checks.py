"""ReFrame definitions for one Fly campaign selected by the launcher."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import reframe as rfm
import reframe.utility.sanity as sn
from reframe.core.builtins import parameter, run_after, sanity_function

from syq_bench.fly import load_campaign

CAMPAIGN = load_campaign(Path(os.environ["SYQ_BENCH_FLY_CAMPAIGN"]))
RESULT_DIR = Path(os.environ["SYQ_BENCH_FLY_RESULT_DIR"])
STATES = json.loads(os.environ["SYQ_BENCH_FLY_STATES"])


@rfm.simple_test
class FlyBenchmark(rfm.RunOnlyRegressionTest):
    """One independently provisioned storage/network point."""

    experiment_name = parameter([experiment.name for experiment in CAMPAIGN.experiments])
    valid_systems = ["*"]
    valid_prog_environs = ["*"]
    local = True
    executable = sys.executable

    @run_after("init")
    def configure_worker(self):
        state = Path(STATES[self.experiment_name])
        self.executable_opts = [
            "-m",
            "syq_bench.fly",
            "_worker",
            shlex.quote(str(CAMPAIGN.path)),
            shlex.quote(str(state)),
            shlex.quote(self.experiment_name),
            shlex.quote(str(RESULT_DIR)),
        ]

    @sanity_function
    def successful(self):
        return sn.assert_eq(self.job.exitcode, 0)
