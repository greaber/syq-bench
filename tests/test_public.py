"""Public evidence must stay paired, honest, and available to a new reader."""

import copy
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest

from syq_bench import public
from syq_bench.cli import main
from syq_bench.publication import public_capture
from syq_bench.spec import from_dict

ROOT = Path(__file__).resolve().parents[1]


def run(walls=None, *, date="2026-09-04T12:00:00Z", mode="fresh"):
    walls = walls or {"syq-auto": [10, 12], "syq-j8": [2, 2], "rsync": [40, 44]}
    return {
        "schema": 3,
        "harness_version": "0.1.0",
        "started": date,
        "finished": date,
        "spec": {
            "name": "sample",
            "tools": [{"name": n, "kind": "syq" if n.startswith("syq") else "rsync"} for n in walls],
            "workloads": [{"name": "copy", "kind": "large-file", "bytes": 100_000_000}],
            "protocol": {"cache": "warm", "durable": False},
        },
        "seed": 1,
        "host": {"hostname": "private-machine"},
        "source": "/private/source",
        "destination": "private-remote:/private/destination",
        "tools": {n: {"binary": f"/private/bin/{n}", "version": "0.1.8", "sha256": "a" * 64} for n in walls},
        "filesystems": {"source": "tmpfs", "destination": "tmpfs"},
        "cases": [
            {
                "tool": name,
                "workload": "copy",
                "mode": mode,
                "bytes": 100_000_000,
                "files": 1,
                "repeats": [
                    {"index": i, "wall_s": w, "verified": True, "exit_code": 0, "cache": "warm", "fsync_s": None}
                    for i, w in enumerate(times)
                ],
            }
            for name, times in walls.items()
        ],
    }


CATALOG = {"scenarios": [{"id": "sample", "title": "Example", "primary": "syq-auto", "kind": "cloud"}]}


def test_default_is_explicit_and_faster_tuning_does_not_inflate_the_comparison():
    rows = public.cases(run(), "syq-auto")[0]
    factor, other = public.advantage(rows)
    assert factor == pytest.approx(42 / 11) and other.name == "rsync"
    chart = public.bar_chart(rows)
    assert "syq-j8" not in chart and "9.1 MB/s" in chart
    page = public.render_public([run()], CATALOG)
    assert "3.82× faster than rsync in this run" in page and "syq-j8" in page


@pytest.mark.parametrize("flush", [None, 0, 1000])
def test_post_copy_flush_cannot_change_benchmark_speed_or_ranking(flush):
    data = run({"syq-auto": [10, 10], "rsync": [20, 20]})
    data["spec"]["protocol"]["durable"] = True
    for case in data["cases"]:
        for repeat in case["repeats"]:
            repeat["fsync_s"] = flush if case["tool"] == "syq-auto" else 0
    rows = public.cases(data, "syq-auto")[0]
    assert [row.metric for row in rows] == [10, 5]
    factor, other = public.advantage(rows)
    assert factor == 2 and other.name == "rsync"
    assert "2.00× faster than rsync in this run" in public.render_public([data], CATALOG)


@pytest.mark.parametrize("walls", [{"syq-auto": [10], "rsync": [100]}, {"syq-auto": [100], "rsync": [10]}])
def test_hero_never_turns_one_workloads_result_into_a_general_speedup(walls):
    page = public.render_public([run(walls)], CATALOG)
    hero = page.split('<header class="hero">')[1].split("</header>")[0]
    assert "syq benchmarks" in hero
    assert "×" not in hero and "faster than" not in hero
    assert "10.00× faster" in page


@pytest.mark.parametrize(
    "failure", ["exit", "checksum", "cache", "aborted", "missing", "nan", "inf", "zero", "negative"]
)
def test_invalid_fast_measurement_cannot_be_a_speedup(failure):
    data = run()
    rep = data["cases"][0]["repeats"][0]
    if failure == "exit":
        rep["exit_code"] = 124
    elif failure == "checksum":
        rep["verified"] = False
    elif failure == "cache":
        rep["cache"] = "evict-failed"
    elif failure == "aborted":
        data["aborted"] = "interrupted"
    elif failure == "missing":
        rep["verified"] = None
    else:
        rep["wall_s"] = {"nan": float("nan"), "inf": float("inf"), "zero": 0, "negative": -1}[failure]
    rows = public.cases(data, "syq-auto")[0]
    assert rows[0].mean is None and rows[0].reason
    assert public.advantage(rows) is None
    assert 'class="result-line"' not in public.render_public([data], CATALOG)
    assert rows[0].reason in public.bar_chart(rows)


def test_short_cases_are_visible_without_ranking_or_speedup():
    data = run({"syq-auto": [0.2], "rsync": [0.9]})
    page = public.render_public([data], CATALOG)
    assert "Too short to rank" in page and 'class="result-line"' not in page
    assert "under 2 s" in page


@pytest.mark.parametrize("mode", ["fresh", "incremental"])
def test_all_charts_use_reference_dataset_speed_and_higher_is_better(mode):
    data = run({"syq-auto": [10, 20], "rsync": [40, 80]}, mode=mode)
    for case in data["cases"]:
        for rep in case["repeats"]:
            rep["changed_bytes"] = 1_000
    rows = public.cases(data, "syq-auto")[0]
    assert rows[0].metric == pytest.approx(100 / 15)
    assert rows[1].metric == pytest.approx(100 / 60)
    chart = public.bar_chart(rows)
    assert "Copy speed · higher is better" not in chart and "6.7 MB/s" in chart and "1.7 MB/s" in chart
    assert "25% of fastest" in chart
    assert "shorter is better" not in chart and "15.0 s average" in chart and "× the time" not in chart
    bars = [float(w) for w in re.findall(r'class="bar [^"]*" style="width:([\d.]+)%', chart)]
    assert bars[0] == pytest.approx(bars[1] * 4, abs=0.02)
    whiskers = re.findall(r'class="whisk" style="left:([\d.]+)%;width:([\d.]+)%', chart)
    # Inverse runtimes, not elapsed-time ranges: first row spans 5–10 MB/s.
    assert [float(v) for v in whiskers[0]] == pytest.approx([5 / 10.2 * 100, 5 / 10.2 * 100], abs=0.01)
    if mode == "incremental":
        assert "size of the whole folder" in chart and "not the bytes sent over the network" in chart


def test_incremental_rsync_win_has_a_longer_bar_and_stays_in_its_case():
    data = run({"syq-auto": [30, 40], "rsync": [5, 7]}, mode="incremental")
    rows = public.cases(data, "syq-auto")[0]
    assert rows[1].metric > rows[0].metric
    page = public.render_public([data], CATALOG)
    assert "rsync is 5.83× faster than syq in this run" in page
    assert "17% of fastest" in page
    assert "Tool time" in public.details_table(rows) and "30.0 s" in public.details_table(rows)


def test_runs_are_never_mixed_and_a_new_failure_does_not_fall_back_to_an_old_win():
    old = run({"syq-auto": [10], "rsync": [100]}, date="2026-09-03T12:00:00Z")
    new = run({"syq-auto": [20], "rsync": [21]})
    page = public.render_public([old, new], CATALOG)
    latest = page.split('<details class="previous">')[0]
    assert "10.00× faster" not in latest
    assert "10.00× faster" in page
    new["cases"][0]["repeats"][0]["exit_code"] = 1
    page = public.render_public([old, new], CATALOG)
    assert 'class="result-line"' not in page.split('<details class="previous">')[0]
    assert "A repeat failed or timed out" in page


def test_remote_identity_warning_is_computed_from_each_capture():
    data = run()
    assert "1 of these runs lack a recorded destination syq hash" in public.render_public([data], CATALOG)
    data["tools"]["syq-auto"]["remote"] = {"sha256": "a" * 64}
    page = public.render_public([data], CATALOG)
    assert "lack a recorded destination syq hash" not in page
    assert "Destination syq hashes are recorded for every published run" not in page


def test_public_capture_drops_private_structures_without_changing_measurements():
    original = run()
    original["_path"] = "/private/result.json"
    original["orchestration"] = {"app": "private-app", "key": "private-secret"}
    original["cases"][0]["argv"] = ["private-command"]
    original["cases"][0]["repeats"][0]["stderr_tail"] = "private-debug"
    original["cases"][0]["repeats"][0]["verified"] = False
    before = copy.deepcopy(original)
    data = public_capture(original, "sample")
    assert "private-" not in json.dumps(data) and "/private/" not in json.dumps(data)
    assert data["cases"][0]["repeats"][0]["wall_s"] == 10
    assert data["cases"][0]["repeats"][0]["verified"] is False
    assert original == before


@pytest.mark.parametrize("requested", ["/private/bin/custom-rsync", "/private/bin/rsync"])
def test_wrong_remote_helper_does_not_become_a_published_identity(requested):
    data = run()
    data["spec"]["tools"][-1]["args"] = [f"--rsync-path={requested}"]
    data["tools"]["rsync"]["remote"] = {"binary": "/usr/bin/rsync", "sha256": "b" * 64, "version": "wrong"}
    sanitized = public_capture(data, "sample")
    assert sanitized["tools"]["rsync"]["remote"]["sha256"] is None
    assert "wrong" not in public.provenance(sanitized)


def test_html_in_recorded_fields_is_escaped():
    data = run()
    data["cases"][0]["error"] = '<script>alert("x")</script>'
    page = public.render_public([data], CATALOG)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_explanations_are_bound_to_reviewed_captures_and_escaped():
    data = run()
    meta = {
        "explanations": {"copy": {"basis": "likely", "text": "Explanation <not markup>"}},
        "explained_captures": [public.capture_digest(data)],
    }
    rows = public.cases(data, "syq-auto")[0]
    assert "Likely reason" not in public.explanation(rows, meta)
    assert "Explanation &lt;not markup&gt;" in public.explanation(rows, meta)
    data["_download"] = "a-different-url.json"
    assert public.explanation(rows, meta)  # Internal presentation fields are not measurements.
    data["cases"][0]["repeats"][0]["wall_s"] += 1
    assert public.explanation(rows, meta) == ""  # Never reuse a reviewed story for changed data.


def test_new_build_or_failed_comparison_cannot_inherit_an_explanation():
    data = run()
    meta = {
        "explanations": {"copy": {"basis": "control", "text": "The control is faster."}},
        "explained_captures": [public.capture_digest(data)],
    }
    rows = public.cases(data, "syq-auto")[0]
    data["tools"]["syq-auto"]["sha256"] = "b" * 64
    assert public.explanation(rows, meta) == ""
    data["cases"][0]["repeats"][0]["verified"] = False
    meta["explained_captures"] = [public.capture_digest(data)]
    assert public.explanation(rows, meta) == ""  # A hash alone is not sufficient for a valid comparison.


def test_scroll_tracking_is_self_contained_and_does_not_replace_native_navigation():
    page = public.render_public([run()], CATALOG)
    assert 'href="#sample"' in page
    script = (ROOT / "src/syq_bench/public.js").read_text()
    assert f"<script>{script}</script>" in page
    assert 'aria-current="location"' in page
    assert "preventDefault" not in script and "fetch(" not in script and "history." not in script
    assert f"<script>{script}</script>" in public.shell("Method", public.method_body())


def test_public_report_cli_uses_named_default_and_links_back_to_custom_filename(tmp_path):
    data = tmp_path / "run.json"
    data.write_text(json.dumps(run()))
    output = tmp_path / "custom.html"
    assert main(["report", str(data), "--public", "--primary-tool", "syq-auto", "-o", str(output)]) == 0
    assert "3.82× faster" in output.read_text()
    assert "See test setup" in output.read_text()
    assert "Existing hardware" not in output.read_text()
    assert "measurement download" not in output.read_text()
    assert "redacted command inputs" not in output.read_text()
    assert "&quot;workloads&quot;:" in output.read_text()
    assert 'href="custom.html"' in (tmp_path / "custom.html.method.html").read_text()
    for companion in ("method", "reproduce"):
        page = (tmp_path / f"custom.html.{companion}.html").read_text()
        assert 'href="custom.html#sample"' in page
        assert 'href="index.html#' not in page


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("first.html", "second.html"),
        ("same.html", "same.htm"),
        ("method.html", "reproduce.html"),
        ("first #1.html", "second &2.html"),
    ],
)
def test_public_reports_have_independent_companions_and_links(tmp_path, first, second):
    data = tmp_path / "run.json"
    data.write_text(json.dumps(run()))
    previous = {}
    for filename in (first, second):
        assert main(["report", str(data), "--public", "-o", str(tmp_path / filename)]) == 0
        assert all(path.read_bytes() == content for path, content in previous.items())
        own_files = {filename, f"{filename}.method.html", f"{filename}.reproduce.html"}
        for name in own_files:
            page = (tmp_path / name).read_text()
            links = {
                unquote(href.split("#")[0])
                for href in re.findall(r'href="([^"]+)"', page)
                if not href.startswith(("https://", "#"))
            }
            assert links <= own_files
            assert filename in links
            search = json.loads(
                re.search(r'<script id="site-search-index" type="application/json">(.*?)</script>', page)[1]
            )
            assert {unquote(entry["href"]) for entry in search} == own_files
            previous[tmp_path / name] = (tmp_path / name).read_bytes()


def test_public_report_leaves_existing_shared_pages_untouched(tmp_path):
    data = tmp_path / "run.json"
    data.write_text(json.dumps(run()))
    for name in ("method.html", "reproduce.html"):
        (tmp_path / name).write_text(f"Existing {name}")
    assert main(["report", str(data), "--public", "-o", str(tmp_path / "first.html")]) == 0
    for name in ("method.html", "reproduce.html"):
        assert (tmp_path / name).read_text() == f"Existing {name}"


def build_module():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts/build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_published_selection_is_valid_and_has_recipe_revision_evidence():
    runs, catalog = build_module().inputs(ROOT)
    assert len(runs) == 15
    assert catalog["acceptance"] == {
        "minimum_repeat_seconds": 3,
        "repeats": 3,
        "syq_revision": "fd2b17c642e62d7ee63f7f3835560a6ceacd0afb",
    }
    for data in runs:
        meta = next(m for m in catalog["scenarios"] if m["id"] == data["spec"]["name"])
        recipe = from_dict(tomllib.loads((ROOT / data["publication"]["manual_recipe"]).read_text()))
        recorded = from_dict(data["spec"])
        assert (recipe.workloads, recipe.tools, recipe.protocol) == (
            recorded.workloads,
            recorded.tools,
            recorded.protocol,
        )
        assert data["publication"]["syq_revision"] == catalog["acceptance"]["syq_revision"]
        for rows in public.selected_cases(data, meta):
            assert {r.name for r in rows} == {
                c["tool"] for c in data["cases"] if c["workload"] == rows[0].case["workload"]
            }
            assert all(r.reason is None and len(r.repeats) == 3 and min(r.walls) >= 3 for r in rows)
    # Omitted failures and short samples remain inspectable, with no chart rankings.
    nfs = next(r for r in runs if r["spec"]["name"] == "private-nfs-write")
    assert any(c["error"] for c in nfs["cases"])
    meta = next(m for m in catalog["scenarios"] if m["id"] == "private-nfs-write")
    assert "small-files" not in meta["workload_selection"]
    xfs = next(r for r in runs if r["spec"]["name"] == "public-xfs-same")
    assert min(xfs["cases"][0]["repeats"][0]["wall_s"], 3) < 3


def test_public_updates_keep_win_loss_and_matching_endpoint_identities():
    runs, catalog = build_module().inputs(ROOT)
    data = next(r for r in runs if r["spec"]["name"] == "public-wan-updates")
    meta = next(m for m in catalog["scenarios"] if m["id"] == "public-wan-updates")
    syq = data["tools"]["syq"]
    assert syq["sha256"] == syq["remote"]["sha256"]
    groups = public.selected_cases(data, meta)
    edit, rewrite, append = [public.advantage(rows)[0] for rows in groups]
    assert edit < 1 < rewrite and append > 1
    page = public.render_public([data], {"scenarios": [meta]})
    assert f"rsync is {1 / edit:.2f}× faster than syq" in page
    assert f"syq is {rewrite:.2f}× faster than rsync" in page
    assert all(len(r.repeats) == 3 and not r.short for rows in groups for r in rows)
    for direction in ("forward", "reverse"):
        lan = next(r for r in runs if r["spec"]["name"] == f"public-lan-{direction}")
        rows = next(rows for rows in public.cases(lan, "syq") if rows[0].case["workload"] == "small-files")
        assert public.advantage(rows)[0] < 1


@pytest.mark.parametrize(
    "damage", ["short", "failed", "unverified", "cache", "missing", "duplicate", "missing-tool", "revision"]
)
def test_release_acceptance_rejects_bad_comparisons(damage):
    data = run({"syq-auto": [3, 4, 5], "rsync": [6, 7, 8]})
    data["publication"] = {"syq_revision": "release"}
    for case in data["cases"]:
        for index, repeat in enumerate(case["repeats"]):
            repeat["index"] = index
    meta = {"id": "sample", "primary": "syq-auto"}
    policy = {"minimum_repeat_seconds": 3, "repeats": 3, "syq_revision": "release"}
    module = build_module()
    module.validate_acceptance(data, meta, policy)  # Three seconds is accepted.
    rep = data["cases"][1]["repeats"][0]  # A weak competitor excludes the whole comparison too.
    if damage == "short":
        rep["wall_s"] = 2.999
    elif damage == "failed":
        rep["exit_code"] = 1
    elif damage == "unverified":
        rep["verified"] = False
    elif damage == "cache":
        rep["cache"] = "failed"
    elif damage == "missing":
        data["cases"][1]["repeats"].pop()
    elif damage == "duplicate":
        rep["index"] = 1
    elif damage == "missing-tool":
        data["cases"].pop()
    else:
        data["publication"]["syq_revision"] = "different"
    with pytest.raises(ValueError):
        module.validate_acceptance(data, meta, policy)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_release_acceptance_rejects_invalid_duration(value):
    with pytest.raises(ValueError, match="positive finite"):
        build_module().validate_acceptance(
            run(), {}, {"minimum_repeat_seconds": value, "repeats": 3, "syq_revision": "release"}
        )


@pytest.mark.parametrize("value", [0, -1, 1.5, float("nan"), float("inf"), True])
def test_release_acceptance_rejects_invalid_repeat_count(value):
    with pytest.raises(ValueError, match="positive integer"):
        build_module().validate_acceptance(
            run(), {}, {"minimum_repeat_seconds": 3, "repeats": value, "syq_revision": "release"}
        )


@pytest.mark.parametrize("note", [None, "not requested", "failed on /private/path"])
def test_public_flush_note_does_not_claim_a_measurement(note):
    original = run()
    repeat = original["cases"][0]["repeats"][0]
    repeat.update(fsync_s=None, fsync_note=note)
    exported = public_capture(original, "sample")["cases"][0]["repeats"][0]
    assert exported["fsync_s"] is None
    if note in (None, "not requested"):
        assert exported["fsync_note"] == note
    else:
        assert "/private/path" not in exported["fsync_note"]
        assert "see fsync_s for measurement" in exported["fsync_note"]


def test_public_rental_geometry_stays_separate_with_automatic_primary():
    runs, catalog = build_module().inputs(ROOT)
    rentals = [r for r in runs if r["spec"]["name"] in {"public-ext4-same", "public-ext4-cross", "public-xfs-same"}]
    assert len(rentals) == 3
    assert {r["tools"]["syq"]["sha256"] for r in rentals} == {
        "da584b363640f2757f485058103c4be5f906fd8f7fd7e5d582bbcfd98a9ca1ef"
    }
    for data in rentals:
        meta = next(m for m in catalog["scenarios"] if m["id"] == data["spec"]["name"])
        assert meta["primary"] == "syq" and meta["kind"] == "rental"
        assert (ROOT / meta["manual_recipe"]).is_file()
        for rows in public.selected_cases(data, meta):
            assert {r.name for r in public.competitors(rows)} == {"syq", "rsync", "cp"}
        page = public.render_public([data], {"scenarios": [meta]})
        assert "Rented server" in page
        assert "destination syq hash" not in page
        assert data["publication"]["syq_revision"] in page
    assert rentals[0]["spec"]["workloads"] == rentals[1]["spec"]["workloads"]
    assert {"private-ext4-same", "private-nfs-write", "private-nfs-read"} <= {r["spec"]["name"] for r in runs}


def test_published_setup_details_remain_available_without_leading_the_description():
    runs, catalog = build_module().inputs(ROOT)
    cloud = next(s for s in catalog["scenarios"] if s["kind"] == "cloud")
    page = public.render_public(runs, catalog)
    scenario = page.split(f'<section class="scenario" id="{cloud["id"]}">')[1].split('<section class="scenario"')[0]
    lead, rest = scenario.split('<details class="setup">', 1)
    assert "Files stay in memory" in lead
    assert "vCPU" not in lead and "hash" not in lead and "tmpfs" not in lead
    setup = rest.split("</details>", 1)[0]
    assert "4 dedicated vCPUs" in setup
    assert 'class="ladder"' in rest
    assert rest.index('class="ladder"') < rest.index('class="repro-note"')


def test_every_published_case_has_a_reviewed_explanation_and_rewrite_is_a_control():
    runs, catalog = build_module().inputs(ROOT)
    for data in runs:
        meta = next(m for m in catalog["scenarios"] if m["id"] == data["spec"]["name"])
        assert public.capture_digest(data) in meta["explained_captures"]
        for rows in public.selected_cases(data, meta):
            assert public.explanation(rows, meta)
    delta = next(r for r in runs if r["spec"]["name"] == "public-wan-updates")
    meta = next(m for m in catalog["scenarios"] if m["id"] == "public-wan-updates")
    assert meta["workloads"]["full-rewrite"] == "All the contents have changed"
    rewrite = next(w for w in delta["spec"]["workloads"] if w["name"] == "full-rewrite")
    assert rewrite["mutate"] == "rewrite" and rewrite["changed"] == 1
    assert "updating an existing copy" in public.workload_text(public.cases(delta, "syq")[2][0])
    assert "verify every copy with checksums" in public.method_body()


def test_published_explanations_include_parallelism_within_one_file():
    runs, catalog = build_module().inputs(ROOT)
    cases = (
        ("public-wan-forward", "large-file", "parts of the same file", "encrypted TCP"),
        ("public-lan-forward", "large-file", "parts of the same file", "encrypted TCP"),
        ("private-ext4-same", "large-file", "parts of one file at once", "doesn’t always help"),
    )
    for scenario, workload, mechanism, context in cases:
        data = next(r for r in runs if r["spec"]["name"] == scenario)
        meta = next(m for m in catalog["scenarios"] if m["id"] == scenario)
        rows = next(rows for rows in public.cases(data, meta["primary"]) if rows[0].case["workload"] == workload)
        note = public.explanation(rows, meta)
        assert mechanism in note and context in note


def test_each_published_syq_bar_has_a_data_path_including_controls():
    runs, catalog = build_module().inputs(ROOT)
    page = public.render_public(runs, catalog)
    assert page.count('class="transport"') == page.count('<div class="name syq"')
    assert "Data path: TCP · observed" in page
    assert "Data path: Local copy · NFS" in page
    assert "Data path: Unrecorded" in page
    assert "Data path: SSH · requested" in page


def test_short_page_has_four_distinct_charts_and_keeps_full_results_available():
    runs, catalog = build_module().inputs(ROOT)
    pages = public.render_pages(runs, catalog)
    main = pages["index.html"].split('<main id="main">')[1].split("</main>")[0]
    full = pages["all-results.html"].split('<main id="main">')[1].split("</main>")[0]
    assert main.count('class="ladder"') == 4
    assert "<h4>" not in main and 'class="controls"' not in main
    assert "Where rsync was faster" not in main
    assert "See all benchmark results →" not in main
    assert 'id="public-wan-updates"' not in main
    assert 'href="all-results.html"' in pages["index.html"]
    assert 'class="result-line">rsync is ' in full
    assert full.count('class="case"') == 34
    assert "Japanese" not in main and "Falkenstein" not in main and "Ashburn" not in main
    for meta in public.overview_catalog(runs, catalog)["scenarios"]:
        data = next(r for r in runs if r["spec"]["name"] == meta["id"])
        rows = public.selected_cases(data, meta)[0]
        # The short page preserves every competitor and the same calculations.
        assert (
            public.bar_chart(
                rows, include_controls=meta.get("chart_controls", []), metric=meta.get("chart_metric", "speed")
            )
            in main
        )
        assert public.result_line(rows) in main


def test_short_page_shows_measured_ssh_control_beside_default_syq_and_rsync():
    runs, catalog = build_module().inputs(ROOT)
    meta = public.overview_catalog(runs, catalog)["scenarios"][0]
    data = next(run for run in runs if run["spec"]["name"] == meta["id"])
    rows = public.selected_cases(data, meta)[0]
    ssh = next(row for row in rows if row.name == "syq-ssh")
    page = public.render_pages(runs, catalog)["index.html"]
    section = page.split('<section class="scenario" id="public-wan-forward">')[1].split("</section>")[0]
    assert re.findall(r'<div class="name [^"]*"[^>]*><b>(.*?)</b>', section) == ["syq", "syq over SSH", "rsync"]
    assert public.rate(ssh.metric) in section
    assert f"{public.seconds(ssh.mean)} average" in section
    assert "Data path: SSH · observed" in section
    # Showing a syq variant does not replace the headline's rsync comparison.
    assert public.result_line(rows) in section
    assert "faster than rsync" in section


@pytest.mark.parametrize("selected", [["missing"], ["rsync"], ["syq-j8", "syq-j8"]])
def test_chart_rejects_unknown_or_duplicate_control_selection(selected):
    with pytest.raises(ValueError, match="distinct syq controls"):
        public.bar_chart(public.cases(run(), "syq-auto")[0], include_controls=selected)


@pytest.mark.parametrize("damage", ["duplicate", "unknown", "omitted"])
def test_overview_cannot_select_unaccepted_or_duplicate_comparisons(damage):
    runs, catalog = build_module().inputs(ROOT)
    choices = catalog["overview"]["comparisons"]
    if damage == "duplicate":
        choices.append(choices[0])
    elif damage == "unknown":
        choices[0]["id"] = "missing"
    else:
        choices[0].update(id="public-xfs-same", workload="large-file")
    with pytest.raises(ValueError):
        public.overview_catalog(runs, catalog)


def test_generated_page_and_links_match_current_inputs():
    assert not (ROOT / "site/downloads/syq-bench-source.zip").exists()
    module = build_module()
    runs, catalog = module.inputs(ROOT)
    pages = public.render_pages(runs, catalog, (ROOT / "site/reproduce-body.html").read_text())
    assert "will be public soon" not in pages["reproduce.html"]
    for filename, page in pages.items():
        assert (ROOT / "site" / filename).read_text() == page
        assert "syq-bench-source.zip" not in page
        assert 'href="https://github.com/greaber/syq-bench"' in page
        header = page.split('<header class="site-header"')[1].split("</header>")[0]
        assert 'href="https://github.com/greaber/syq"' in header
        assert 'href="https://github.com/greaber/syq-bench"' not in header
        sidebar = page.split('<aside id="benchmark-sidebar">')[1].split("</aside>")[0]
        for target in ("index.html", "all-results.html", "reproduce.html", "method.html"):
            assert f'class="page-link" href="{target}"' in sidebar
        assert f'class="page-link" href="{filename}" aria-current="page"' in sidebar
        assert sidebar.count('aria-current="page"') == 1
        assert "scenario-links" in sidebar
        prefix = "" if filename in ("index.html", "all-results.html") else "index.html"
        navigation = catalog if filename == "all-results.html" else public.overview_catalog(runs, catalog)
        for scenario in public.public_scenarios(runs, navigation):
            assert f'href="{prefix}#{scenario["id"]}"' in sidebar
        assert sidebar.count("↗") == 1  # Only Get syq leaves the benchmark site.
        for href in re.findall(r'href="([^"]+)"', page):
            if not href.startswith(("https://", "#")):
                assert (ROOT / "site" / href.split("#")[0]).is_file(), href


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (1, "1 B"),
        (999, "999 B"),
        (1_000, "1 kB"),
        (1_000_000, "1 MB"),
        (83_880_000, "83.9 MB"),
        (512 * 2**20, "536.9 MB"),
        (1_000_000_000, "1 GB"),
        (2**30, "1.07 GB"),
        (1_000_000_000_000, "1 TB"),
        (-1, "—"),
        (float("nan"), "—"),
        (float("inf"), "—"),
    ],
)
def test_public_sizes_use_rounded_decimal_units(value, expected):
    assert public.size(value) == expected


def test_search_index_escapes_script_delimiters_and_preserves_text():
    payload = '</script><script>alert(1)</script> & "quotes"'
    index = public.search_index([("index.html", payload, "<p>Checksum verification</p>")])
    assert index.count("</script>") == 1
    entries = json.loads(index.split(">", 1)[1].removesuffix("</script>"))
    assert entries[0]["title"] == payload
    assert entries[0]["text"] == "Checksum verification"


def test_shared_toolkit_check_detects_drift(tmp_path):
    source = ROOT / "src/syq_bench"
    mapping = json.loads((source / "site-ui.json").read_text())
    for name, target in mapping.items():
        if (source / name).is_dir():
            shutil.copytree(source / name, tmp_path / target)
        else:
            shutil.copyfile(source / name, tmp_path / target)
    command = [sys.executable, str(source / "check-site-ui.py"), str(source), str(tmp_path)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    (tmp_path / "sidebar.css").write_text("drift")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1 and "sidebar.css" in result.stdout
    (tmp_path / "sidebar.css").unlink()
    assert subprocess.run(command, capture_output=True).returncode == 1


def test_chart_pairs_throughput_with_mean_elapsed_time():
    data = run({"syq-auto": [0.2, 0.4], "rsync": [10, 20]})
    chart = public.bar_chart(public.cases(data, "syq-auto")[0])
    assert '<small class="elapsed">0.30 s</small>' in chart
    assert '<small class="elapsed">15.0 s</small>' in chart
    data["cases"][0]["repeats"][0]["verified"] = False
    chart = public.bar_chart(public.cases(data, "syq-auto")[0])
    assert '<small class="elapsed">Elapsed unavailable</small>' in chart
    assert "0.30 s average" not in chart


def test_display_selection_retains_all_tools_and_original_download():
    data = run()
    additional = copy.deepcopy(data["cases"])
    for case in additional:
        case["workload"] = "replaced"
    data["cases"] += additional
    before = copy.deepcopy(data)
    meta = {"primary": "syq-auto", "workload_selection": ["copy"]}
    selected = public.selected_cases(data, meta)
    assert len(selected) == 1
    assert {row.name for row in selected[0]} == {"syq-auto", "syq-j8", "rsync"}
    assert "Replaced" not in public.run_html(data, meta, 1)
    assert data == before


@pytest.mark.parametrize("selection", [[], ["missing"], ["copy", "copy"], "copy", [1]])
def test_display_selection_rejects_invalid_or_missing_workloads(selection):
    with pytest.raises(ValueError, match="workload_selection"):
        public.selected_cases(run(), {"primary": "syq-auto", "workload_selection": selection})


def test_tool_labels_change_display_only_and_are_escaped():
    data = run()
    before = copy.deepcopy(data)
    meta = {"primary": "syq-auto", "tool_labels": {"rsync": "rsync <control>"}}
    rows = public.selected_cases(data, meta)[0]
    assert rows[-1].name == "rsync" and rows[-1].label == "rsync <control>"
    assert "rsync &lt;control&gt;" in public.bar_chart(rows)
    assert "<td>rsync</td>" in public.details_table(rows)
    assert data == before


def test_time_chart_uses_elapsed_ranges_and_does_not_imply_bytes_transferred():
    rows = public.cases(run({"syq-auto": [10, 20], "rsync": [40, 80]}, mode="incremental"), "syq-auto")[0]
    chart = public.bar_chart(rows, metric="time")
    assert "MB/s" not in chart and "whole folder" not in chart
    assert "15.0 s" in chart and "60.0 s" in chart
    bars = [float(w) for w in re.findall(r'class="bar [^"]*" style="width:([\d.]+)%', chart)]
    assert bars[1] == pytest.approx(bars[0] * 4, abs=0.02)
    whisk = re.findall(r'class="whisk" style="left:([\d.]+)%;width:([\d.]+)%', chart)[0]
    assert [float(w) for w in whisk] == pytest.approx([10 / 81.6 * 100, 10 / 81.6 * 100], abs=0.01)
    assert "Shorter bars mean less time" not in chart


def test_unchanged_overview_shows_time_and_zero_contents_transferred():
    runs, catalog = build_module().inputs(ROOT)
    page = public.render_pages(runs, catalog)["index.html"]
    section = page.split('<section class="scenario" id="public-wan-unchanged-tree">')[1].split("</section>")[0]
    assert "100,000 files" in section and "no file contents transferred" in section
    assert "MB/s" not in section and "average time" in section
    assert "No file data transferred" in section and "Data path:" not in section
    assert "Likely reason" not in page and 'class="chart-key"' not in page


@pytest.mark.parametrize("walls", [[10.123456, 20], [10.123456]])
def test_chart_tooltip_lists_actual_runs_without_explanatory_copy(walls):
    rows = public.cases(run({"syq-auto": walls, "rsync": [40, 80]}), "syq-auto")[0]
    chart = public.bar_chart(rows)
    assert '<button type="button" class="track" aria-label=' in chart
    assert 'class="track" title=' not in chart
    assert "Run 1: 10.1235 s · 9.9 MB/s" in chart
    if len(walls) > 1:
        assert "Run 2: 20 s · 5.0 MB/s" in chart
    assert "Longer bars mean" not in chart and "Speed is file size" not in chart
    assert "The line spans" not in chart and "One measured run" not in chart


def test_failed_chart_does_not_offer_successful_measurements():
    data = run()
    data["cases"][0]["repeats"][0]["verified"] = False
    chart = public.bar_chart(public.cases(data, "syq-auto")[0])
    first = chart.split('class="measurements"')[1].split("</button>")[0]
    assert "Not fully checksum-verified" in first
    assert "Run 1:" not in first and "fastest and slowest" not in first


def test_chart_rejects_unknown_metric():
    with pytest.raises(ValueError, match="chart metric"):
        public.bar_chart(public.cases(run(), "syq-auto")[0], metric="unknown")


@pytest.mark.parametrize(
    "change",
    ["none", "changed_files", "changed_bytes", "missing", "failed", "unverified", "fresh", "unseeded", "empty"],
)
def test_no_file_data_label_requires_verified_unchanged_seeded_repeats(change):
    data = run({"syq-auto": [10, 12]}, mode="incremental")
    case = data["cases"][0]
    case["prepopulated_with"] = "rsync"
    for repeat in case["repeats"]:
        repeat.update(changed_files=0, changed_bytes=0)
    last = case["repeats"][-1]
    if change in {"changed_files", "changed_bytes"}:
        last[change] = 1
    elif change == "missing":
        del last["changed_bytes"]
    elif change == "failed":
        last["exit_code"] = 1
    elif change == "unverified":
        last["verified"] = False
    elif change == "fresh":
        case["mode"] = "fresh"
    elif change == "unseeded":
        del case["prepopulated_with"]
    elif change == "empty":
        case["repeats"] = []
    chart = public.bar_chart(public.cases(data, "syq-auto")[0])
    assert ("No file data transferred" in chart) == (change == "none")
    assert ("Data path: Unrecorded" in chart) == (change != "none")


def test_chart_rows_omit_repeated_average_labels_but_keep_times():
    rows = public.cases(run(), "syq-auto")[0]
    for metric in ("speed", "time"):
        chart = public.bar_chart(rows, metric=metric)
        values = re.findall(r'<div class="t">(.*?)</div>', chart)
        assert values and all("average" not in value for value in values)
        assert "11.0 s" in values[0]
        assert "average" in chart  # Still clear in the hover summary.


def test_published_pages_omit_shared_boilerplate_and_redundant_footer_links():
    runs, catalog = build_module().inputs(ROOT)
    pages = public.render_pages(runs, catalog)
    for page in pages.values():
        footer = page.split("<footer>")[1].split("</footer>")[0]
        assert "Measurements and methodology" not in footer
        assert 'href="method.html"' not in footer
        assert "Benchmarks on GitHub" in footer
    full = pages["all-results.html"].split('<main id="main">')[1].split("</main>")[0]
    assert "These runs change how syq copies" not in full
    assert "A version string alone" not in full
    assert "Local storage setup" not in full and "Run this benchmark yourself" not in full
    assert "Test setup &amp; limitations" in full and "Other syq settings" in full
    assert "See each test run" in full and "Download results" in full
    assert full.count('class="metric-note"') == 3  # Once per update workload, not again inside controls.
