#!/usr/bin/env python3
"""Build the public site without cloud access."""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

from syq_bench.fly import load_campaign
from syq_bench.public import render_pages, selected_cases
from syq_bench.rclone_public import build as build_rclone_page
from syq_bench.spec import from_dict

ROOT = Path(__file__).resolve().parents[1]


def validate_acceptance(run: dict, scenario: dict, policy: dict) -> None:
    """Fail a release-page build instead of charting a weak comparison."""
    if not policy:
        return
    minimum = policy["minimum_repeat_seconds"]
    repeats = policy["repeats"]
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, (int, float))
        or not math.isfinite(minimum)
        or minimum <= 0
        or type(repeats) is not int
        or repeats <= 0
    ):
        raise ValueError("acceptance requires a positive finite duration and positive integer repeats")
    if run.get("publication", {}).get("syq_revision") != policy["syq_revision"]:
        raise ValueError("release revision differs from capture")
    for rows in selected_cases(run, scenario):
        workload = rows[0].case["workload"]
        expected = {
            tool["name"] for tool in run["spec"]["tools"] if not tool.get("workloads") or workload in tool["workloads"]
        }
        if {row.name for row in rows} != expected or len(rows) != len(expected):
            raise ValueError(f"release acceptance requires every tool: {scenario['id']}/{workload}")
        for row in rows:
            if (
                row.reason
                or row.case.get("curtailed")
                or row.case.get("too_short")
                or len(row.repeats) != repeats
                or {r.get("index") for r in row.repeats} != set(range(repeats))
                or any(r["wall_s"] < minimum for r in row.repeats)
            ):
                raise ValueError(f"release acceptance failed: {scenario['id']}/{row.case['workload']}/{row.name}")


def inputs(root: Path) -> tuple[list[dict], dict]:
    site = root / "site"
    catalog = tomllib.loads((site / "catalog.toml").read_text())
    runs = []
    seen = set()
    selected = set()
    for scenario in catalog["scenarios"]:
        if scenario["id"] in seen:
            raise ValueError(f"duplicate scenario: {scenario['id']}")
        seen.add(scenario["id"])
        if manual := scenario.get("manual_recipe"):
            path = root / manual
            if not path.resolve().is_relative_to((root / "specs").resolve()) or not path.is_file():
                raise ValueError(f"manual recipe must exist under specs: {manual}")
        campaign = load_campaign(root / scenario["campaign"]) if scenario.get("campaign") else None
        for filename in scenario["captures"]:
            path = site / filename
            if path.resolve() in selected:
                raise ValueError(f"duplicate capture: {filename}")
            selected.add(path.resolve())
            if not path.resolve().is_relative_to((site / "data").resolve()):
                raise ValueError(f"capture must be under site/data: {filename}")
            run = json.loads(path.read_text())
            if run["spec"]["name"] != scenario["id"]:
                raise ValueError(f"scenario identity mismatch: {filename}")
            if campaign and run.get("publication", {}).get("syq_revision") != campaign.fly.syq_revision:
                raise ValueError(f"recipe revision differs from capture: {filename}")
            if campaign:
                recorded = from_dict(run["spec"])
                if (recorded.workloads, recorded.tools, recorded.protocol) != (
                    campaign.run_recipe.workloads,
                    campaign.run_recipe.tools,
                    campaign.run_recipe.protocol,
                ):
                    raise ValueError(f"recipe workload, tools or protocol differ from capture: {filename}")
            validate_acceptance(run, scenario, catalog.get("acceptance", {}))
            run["_download"] = filename
            runs.append(run)
        if campaign:
            scenario["_recipe"] = {
                "seconds": campaign.max_duration_seconds,
                "tool_seconds": campaign.run_recipe.protocol.tool_timeout,
                "estimate": campaign.estimate_usd,
                "checked_at": campaign.cost.checked_at,
                "exclusions": campaign.cost.exclusions,
                "revision": campaign.fly.syq_revision,
            }
    return runs, catalog


def build(root: Path = ROOT) -> list[Path]:
    runs, catalog = inputs(root)
    site = root / "site"
    pages = render_pages(runs, catalog, (site / "reproduce-body.html").read_text())
    outputs = []
    for name, page in pages.items():
        target = site / name
        target.write_text(page)
        outputs.append(target)
    outputs.append(build_rclone_page(root))
    return outputs


if __name__ == "__main__":
    for path in build():
        print(f"built {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
