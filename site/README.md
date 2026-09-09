# Published benchmarks

`catalog.toml` explicitly selects the public captures and supplies scenario
titles, context and the default syq entry. Numbers and comparison status are
computed from the captures in `data/`; do not put measured performance in
the presentation metadata. Chart bars show logical throughput beside mean elapsed seconds; whiskers show
the repeat range. Each chart compares tools from one run. Other
allocations stay separate and inspectable.

Every chart uses effective speed: recorded reference dataset bytes divided by
mean runtime, including updates that reuse existing data. This is not a wire
byte count. Descriptions lead with the copy job; short scope notes remain
visible and technical details go in the expandable `setup` text. Speedups
belong to individual tests and runs, never to a page-wide headline.

Brief `explanations` distinguish control evidence, likely causes and unresolved
gaps. `explained_captures` binds these reviewed interpretations to canonical
capture-content hashes from `public.capture_digest`, excluding internal `_`
metadata. New or changed results do not inherit an old explanation automatically.
Review the evidence before adding a hash. Published measurement inputs are real
captured runs; synthetic records in the test suite are never site inputs.

Build the site:

```bash
uv sync --group dev
uv run python scripts/build_site.py
```

This needs no cloud credentials. Reproduction instructions point to the
public syq-bench GitHub repository. The site does not package or distribute
repository source.

To add a capture, explicitly select it from local results and pass it through
`syq_bench.publication.public_capture`. Inspect every output before adding it
to `data/`: the allowlist removes known private structures, but free-text
fields still require review. Preserve failure, verification, cache and timing
fields. Do not rename a changed workload into an existing comparison or
pool different allocations as one measured environment.

The current page selects only the syq 0.5.2 source revision. Hetzner Cloud,
dedicated-server and Local and NFS storage comparisons stay separate. The build
checks `acceptance` against every tool in each selected whole workload: three
verified repetitions, the requested cache preparation, and at least three seconds
per repetition. Failed and short cases remain in the downloadable captures but
are not charted. The initial public snapshot includes the 15 captures selected
by the release catalog. Older captures remain in the private experiment
repository; they are not inputs to this page.

Manual recipes are under `specs/release-052/`. They explain the independent
remote-helper hash checks and the difference between the rotating campaign
wrapper and a stock harness invocation. The recorded harness revision points to matching measurement source in public
history; later report and harness updates are separate commits.

The generated HTML and selected data are served by the existing Pages workflow
after a reviewed change reaches `master`. For shared fonts, colors and
navigation, see [BRANDING.md](BRANDING.md).

A scenario may set `workload_selection` to display whole workloads from its
captures. This preserves every tool/control for a selected workload and keeps
the complete source capture in the download. Use it when a public comparison
replaces a displayed private case; explain the replacement in the scenario setup.
Unknown, empty or duplicate selections fail the build.

`tool_labels` supplies reader-facing names without changing tool identities,
arguments or the original names in repeat tables and downloads.

The main page has four selected charts, one per section. `overview.comparisons`
selects accepted workloads from the full catalog; all competitors and measured values are retained. `all-results.html` keeps the complete set of accepted
comparisons, alternate syq settings, repetitions, downloads and setup details.
Losses remain with the full results, available through the navigation. The
complete-rewrite chart is there too; the main page uses the overseas fresh copy
to show the bulk-transfer benefit once. Write
its copy for someone new to syq: copying jobs first, technical details on the
other pages. Describe local storage by setup and long-distance routes by country
or broad US region, not by small cities or the owner's machine labels.

An overview comparison can use `chart_controls` to show a named syq variant
alongside its normal competitors. The overseas chart includes `syq-ssh`, labelled
“syq over SSH”; it uses that capture's actual repetitions. The headline still
compares default syq with the fastest non-syq tool. Unknown or duplicate control
names fail rendering, and showing a control cannot remove a competitor.

`chart_metric = "time"` displays elapsed time rather than copy speed for the
unchanged-folder check, where no contents are transferred. Other charts default
to `"speed"`. Hover, focus or tap anywhere on a bar's track to see individual
measurements; Escape dismisses the tooltip. Average labels appear in hover
summaries rather than repeating beside every value. Shared guidance stays on the
supporting pages; All results keeps each comparison’s setup and evidence.
