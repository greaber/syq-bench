# Benchmarking principles

These are the measurement decisions and their rationale as of 2026-09-09.
They guide changes to the harness; the code defines its current behavior.
Recipes describe particular experiments, and Git preserves earlier decisions.

## What we want to learn

Measure where syq helps with real copying jobs, where it loses, and what limits
it. Cover large files, small-file trees, updates and unchanged-folder checks on
local storage, NFS and network paths. Keep automatic syq as the main comparison;
fixed-worker, transport and other tuning settings are separate controls.
Comparators must perform comparable jobs, including metadata preservation.
Dropping work from one tool's command is not a performance improvement.

Prioritize informative comparisons that others can repeat, including on rentable
hardware. Useful results from existing hardware remain valid when their setup
and limitations are disclosed. A manual recipe can be sufficient: reproducing
the relevant conditions matters more than automating every provisioning step.
Do not infer a release regression from measurements on different environments.

## Describe the job, then repeat it fairly

A TOML spec records endpoints, tools and arguments, workload shape and scale,
seed, repetitions, verification, cache policy and optional probes. Store the
resolved spec in every result so later comparisons do not depend on a mutable
recipe file. TOML uses Python's standard library; keeping dependencies small
makes the harness easier to run. Cloud orchestration stays optional and separate
from the measurement protocol.

Use the same source contents and starting destination for every competing tool.
Fresh copies start empty; updates restore the same pre-change state. Interleave
repetitions across tools to reduce drift and cache-order bias. Record additional
campaign ordering or instrumentation rather than claiming a stock harness run
reproduces it. Clean timings run separately from tracing and diagnostic sampling.

Time completed, checksum-verified jobs. Give long runs explicit budgets, retain
failures and timeouts, and report any curtailed repetitions. An interrupted copy
is not evidence of a completed job's speed. Exploratory reports may retain short
or curtailed results; that does not make them suitable for a release chart.

## Timing and cache state

Measure the tool's whole elapsed command time, including startup and connection
setup. Fixture generation and checksum verification are outside that interval.
Post-copy flushing, when requested, is a separate diagnostic: never add it to
copy rankings or speedup claims. Buffered completion is not a durability promise,
and logical copy throughput is not sustained device bandwidth.

Avoid disturbing shared machines. The default cache protocol writes fixtures
with direct I/O where supported and evicts only fixture data pages as needed.
Advisory eviction is not proof of cold physical storage. Record failed eviction,
warm metadata, uncontrolled NFS server caches and other limitations explicitly.
Interleaving improves fairness; it does not eliminate those limitations.

Global cache dropping requires an explicit choice. Ordinary flushing touches
only owned files and directories, never unrelated symlink targets or an entire
filesystem. A warm-cache run is a useful distinct experiment, not a disk-speed
measurement. The recorded policy and observed outcome must agree before a result
can be treated as a controlled comparison.

## Interpreting results

Keep a versioned JSON record per run: resolved spec, tool/source revisions and
binary identities, host and filesystem facts, raw repeat timings, verification,
cache outcomes, probes and uncontrolled conditions. JSON is the source of truth;
reports are derived views, not a replacement for evidence. Missing remote binary
identity stays unknown; matching local hashes do not establish remote identity.

The public copy charts use reference dataset bytes divided by mean command time,
with decimal units and the observed repeat range. For updates, the reference is
the dataset before edits; reuse can raise effective speed without those bytes
crossing the network. Unchanged-folder comparisons use elapsed time. Engineering
reports may use medians; make the statistic clear and never pool different
workloads, environments or independent allocations into one measurement.

A result describes one workload and setup, not a universal winner. Preserve
losses, failed and short cases in the selected captures. The current release page
requires every tool in a selected workload to have three successful, verified
repetitions, matching cache preparation, each lasting at least three seconds.
Select whole workloads, not whichever tool happens to win. These thresholds and
"comparable" labels are practical display rules, not statistical significance
tests. The catalog and renderer implement the current selection rules.

Separate observations from explanations. Storage bandwidth, metadata latency,
network capacity, RTT/loss, per-stream CPU and tool overhead can each dominate.
Use untimed probes and controls to investigate them; a probe is supporting
evidence, not a guaranteed ceiling. Connection reachability or requested options
do not establish the transport actually used. Bind explanatory claims to the
reviewed captures so new results cannot silently inherit an old explanation.

## Resource ownership

Refuse non-empty destinations and check free space and inodes for the peak
footprint, including snapshots and probes. User-supplied sources are read-only.
Destructive flags and cleanup apply only to trees generated by this run beneath
its scratch destination, never to user data or an endpoint root.

Cloud campaigns need bounded lifetimes, an estimate, and a recovery path before
provisioning. Record resource identity before proceeding and verify ownership
again before removal; preserve recovery state when cleanup fails. Stop owned
process groups on timeout or interruption. Concurrent runs must not overwrite
each other's state or compete for an exclusively scheduled host or network path.
Network emulation stays inside the owned lab, and its results are labelled as
emulated conditions rather than measurements of a real WAN.

## Publishing and maintaining benchmarks

Published comparisons normally identify syq releases; private experiments may
run earlier. Reuse measurements after checking that intervening changes cannot
affect the benchmark, preserving the exact tested source/build identity. Targeted
runs are normal, and refreshing the four homepage comparisons is the default
for a release-page update, not a requirement to rerun the full campaign.
Older results retain their actual release labels. See [PUBLICATION.md](PUBLICATION.md)
for the provenance mapping and publication workflow, including current limits on
mixing release versions in a page.

Publish only reviewed, sanitized captures and portable recipes. Original host
identifiers, logs, operational state and account credentials stay private;
`--public` selects a report layout and does not anonymize its input. Keep enough
build, workload and environmental detail to repeat the procedure, while being
explicit about what was not pinned or controlled.

Generate static reports separately from deployment. GitHub Pages serves the
selected measurements and methodology; source comes from the repository, with
no separate source archive. The site needs no application server or database.
See [README.md](README.md) for usage, [site/README.md](site/README.md) for page
selection, [site/BRANDING.md](site/BRANDING.md) for presentation, and the recipes
under `specs/` and `providers/` for setup. Implementation inventories, experiment
logs and speculative roadmap items do not belong in this document.

## Rclone comparison presentation (2026-09-11)

The separate rclone page combines explicitly labelled screening data with
replacement WAN measurements as they complete; it remains a draft pending
publication review.
Speeds use the same dataset-bytes / mean-command-time calculation and decimal
units as the main page, with seconds and observed repeat ranges also visible.
Keep bars adjacent on a shared scale within each case, followed by a compact
block of exact commands. Describe file sizes, counts, placement and preparation
in plain language; optional reproduction instructions stay outside the main flow.
A linked standalone generator provides an optional procedure; it did not produce
the captured data. The page groups selected comparisons by WAN, LAN, NFS and
local filesystem, with each comparison linked in the sidebar. Unreported trials
remain in the raw data but are not rendered as an appendix or a details page.
Show nominal sender ceilings only from consistent recorded NIC ratings, without
treating per-rail ratings as measured end-to-end throughput.

The reporting campaign requires three successful verified runs per setting in
rotated order. Calibrate the fastest tool to at least ten seconds as a starting
target, preserving the workload's file-size distribution. WAN payload time must
also substantially exceed startup; elapsed duration alone does not establish
that. Replace short runs rather than presenting their setup-heavy rates as
sustained throughput. Preserve old, failed and unfavorable screening data for
inspection, explicitly outside publication acceptance. Matched rail controls and
cache, metadata and authentication review remain necessary before publication.

For the WAN replacement, use memory-resident pseudorandom source files and fresh
remote RAID destinations to isolate the route from source disk throughput.
Record the outgoing NIC speed and congestion control, keep native hashing and
whole-command startup inside timing, and verify SHA256, exact file membership,
sizes and whole-second file mtimes afterward. Larger payloads are calibrated
before freezing settings and collecting three repetitions. Compare rclone's
native SFTP with external OpenSSH sharing, as well as authenticated HTTPS
WebDAV; record local-key authentication and start any shared connection inside
the timer. Do not call a setting globally optimal after a bounded tuning sweep.

WAN startup screening uses a recorded positive finite
upper bound on payload startup at most one fifth of its duration in every repeat.
Use syq's planning log or the first rclone nonzero progress report (including
one second for its timestamp precision). This is a conservative screening rule,
not a subtraction from elapsed time or proof that later coordination is free.
