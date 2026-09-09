# syq-bench design

A living document. Each section records what was decided and why, and a
*Status* line says what is built, planned, or research as of its last
revision (dates are given where they matter). When the code and this file
disagree, the change that caused it fixes this file (see `AGENTS.md`).
Milestones and open items live in `current-plans/` (gitignored).

## Public and private development (2026-09-09)

The public repository contains the general-purpose harness, portable recipes,
and reviewed, sanitized measurements. It starts with a clean snapshot; private
experiment history is not part of the public Git history. The existing private
repository remains an active workspace for experiments, operational scripts,
original captures, and investigation notes.

Publish selected changes on a branch based on public history, reviewing file
contents and commit messages before opening a public pull request. Never merge
private branches wholesale into public history. The public repository is the
authority for the released harness; integrate its released changes back into
the private workspace. Credentials and infrastructure identities stay private.
Decryption keys and plaintext secrets stay outside Git in either repository.

The initial public snapshot includes the 15 captures selected by the syq 0.5.2
release catalog. Older, unselected captures remain in the private repository,
keeping the initial public data set focused on the published release. Every
selected capture remains intact, including failed, short, and unfavorable rows.

The release identity, harness mapping and selective refresh workflow is recorded
in [PUBLICATION.md](PUBLICATION.md). Unpublished diagnostic experiments remain
in the private workspace.

## What it is for

One core that measures file-transfer tools on a described setup, with three
uses that differ only in who describes the setup and why:

1. **A user's own hardware.** Does syq help me, by how much, and what is the
   ceiling I am hitting? This is the priority and the smallest useful piece.
2. **syq regression.** Same measurement, two syq builds (baseline vs
   candidate), interleaved so drift does not favour one, with repeats and
   spread. Run before merging perf-touching changes.
3. **Reference numbers.** Same measurement on rented machines whose
   properties are known, producing dated results instead of the numbers
   currently scattered through syq's README.

*Status (2026-09-05)*: (1) built - local, LAN, WAN, and NFS rows have run on
the user's own machines. (2) built (interleaved baseline/candidate,
`syq-bench compare`, `specs/regression.toml`) and used once for a real syq
change. (3) has a provider-tested Fly.io implementation: provisioning,
private Machine-to-Machine SSH, measurement, interruption, and exact-app
cleanup have all run. The first completed Fly run is classified only as
provider qualification because its fresh whole-file tools all saturated the
same path. A short comparative campaign is now configured with the ordinary
fresh-large, fresh-small, block-edit delta, and full-rewrite control semantics,
at separate same- and cross-region scales. Its same-region point completed; the
cross-region point did not survive the campaign deadline. A dedicated bounded
cross-region acceptance recipe has since completed three independently
provisioned, checksum-verified runs in which automatic and fixed-eight syq
substantially beat fixed-one syq and rsync. The result and multi-flow mechanism
now reproduce across isolated allocations and appear in the public page under
review. This is one reproducible WAN acceptance point, not a
replacement for the private benchmark matrix: local-disk, Fly Volume,
NFS-like storage, LAN, and delta-workload reference points remain separate
work. Published numbers come from explicitly selected redacted captures
(see *Results: storage and display*); original private results stay out of git.

## Reference benchmark priorities

**Decision (2026-09-07):** the immediate priority is to demonstrate where syq
offers substantial benefits on publicly rentable infrastructure, with enough
information that a reasonable reader can repeat the comparison and likely see
similar behavior. Useful private-hardware results remain part of the page while
public coverage develops, with their setup and limitations described. Missing
public equivalents are a reason to explore more environments, not to omit those
benefits. Workload-specific losses remain visible alongside wins.

Fly has reproduced one WAN advantage, but has not reproduced all the advantages
seen on private hardware. Explore other providers and resource configurations
for the missing regimes; a result on one provider does not establish behavior
on every storage or network path. Finding informative public results takes
priority over completing a general provisioning backend.

Reproduction detail is a judgment call based on what affects the comparison.
A useful initial recipe describes the provider and relevant machine, storage
and network setup, tool builds and options, workload and scale, measurement
procedure, and known limitations well enough to recreate the conditions.
Manual setup instructions can be sufficient. Automated provisioning, tighter
environment pinning and independent recipe validation improve convenience
and confidence, and can progress alongside experiments; they are not universal
prerequisites for publishing a useful result. The target is a similar comparison,
not identical timings. Verification, honest timing and resource ownership rules
still apply to every run.

**Page selection (2026-09-09):** the release page uses syq 0.5.2 source
fd2b17c throughout. Hetzner Cloud LAN/WAN, dedicated-server local storage,
and local/NFS storage remain separate comparisons. Automatic
syq is primary; SSH and fixed-policy controls remain inspectable. A displayed
workload must have three successful, checksum-verified repetitions for every
tool, matching cache preparation, and no repetition shorter than three seconds.
Selection is by whole workload, never by which tool wins. The build rejects
selected comparisons that fail this gate or use a different release revision.
Complete captures preserve omitted short or failed cases for inspection; they
are not plotted. The reduced NFS metadata test replaces the timed-out comparison.
Near-instant XFS extent-sharing results are explained without numerical speedups.

The short release page shows four jobs with one chart each: an overseas large
file, a local mixed folder, small files on NFS, and checking an unchanged folder.
The unchanged-folder chart uses elapsed time because no contents are transferred.
The other charts show copy speed. Four is the current selection, not a limit.
The complete rewrite stays on All results because it largely illustrates the same benefit as
the overseas fresh copy. All accepted comparisons, including losses, and
controls remain on All results, accessible from the navigation. The main page
has no separate closing discussion of rsync wins (2026-09-09 editorial choice).
Local storage is described by its setup, not country or ownership; long-distance
routes use countries or broad US regions. Main-page explanations focus on
parallel work and encrypted TCP. Direct local copying and skipping redundant
metadata changes improve syq's own implementation, but these tests do not
establish them as differences from cp or rsync. Do not present them as comparative
advantages without that evidence. The overseas chart also displays the measured
syq-over-SSH control beside default syq and rsync. Its headline keeps the
default-syq/rsync comparison. The explanation distinguishes parallel transfers
(which also work over SSH) from the additional encrypted TCP data path.
Explanations have no "Likely reason" heading. Charts omit the repeated metric
key; hovering or focusing the full bar area explains the range and displays
individual run times and, for speed charts, their corresponding rates.
The supporting benchmark pages (2026-09-09) explain timing, repeated runs and
checksums briefly for new users. Reproduction starts by matching a comparison to
its own environment; there is no single hardware setup shared by all tests.
Repeated average labels and the footer's methodology text/link are omitted;
units remain beside values and averages remain identified in hover summaries.
All results omits shared setup/reproduction boilerplate and redundant settings
explanations; per-test conditions, warnings, measurements and commands remain.
Detailed build pins, template options and campaign diagnostics remain in the
linked recipe and downloadable results. Unchanged-folder charts use elapsed time.

Cross-environment differences are not evidence of a release regression. Clean
command timings disable debug tracing; throughput uses reference bytes divided
by mean elapsed time, with repeat ranges visible. Internal campaign summaries
use medians of repetitions of one workload/tool/environment, never pooled across
scenarios. The manual recipe records build pins, hardware, actual path filesystem
types, cache policy, transport and limits of approximate reproduction. The
source build identifies the published release commit, not a downloaded binary.

**Source distribution (2026-09-08):** the site publishes selected measurements
and methodology, but no downloadable source ZIP. Reproduction instructions
point to the public GitHub repository.
The upcoming public version will be cleaned and may contain only selected
parts of this repository; its scope is a separate decision. Maintaining a
second source distribution through Pages is not needed for that plan.

## Declarative runs

A run is described by a **run spec** (a TOML file; chosen 2026-08-29 because
the standard library parses it, so it costs no dependency, and it reads
well for humans). The CLI is thin: `syq-bench run SPEC` executes it,
`syq-bench run --dry-run SPEC` prints the plan, and a few flags override
individual fields for one-off experiments. Rationale:

- The interesting variation is in *what* to measure (which tools, which
  workloads, which endpoints, how many repeats, which environment probes),
  not in a sequence of steps. A spec names those; the harness derives the
  steps. Flags for every combination would grow without bound.
- A spec is data: it is stored inside every result, so a result is
  reproducible and two results are comparable by diffing their specs. The
  regression and cloud uses are then just specs with two `syq` entries or a
  remote endpoint, not separate commands.
- Ready-made specs (`specs/quick.toml`, `specs/local.toml`, …) are the
  user-facing entry point: "run this one" is easier to explain than a flag
  table, and a user copies one to adapt it.

A spec names, roughly: the **endpoints** (local paths or `user@host:path`;
the destination must be an empty scratch directory), the **tools** with their
arguments (several entries for the same tool are allowed, e.g. two syq
binaries or `-j` sweeps, a tool may carry a workload-name allowlist, and a syq
entry may retain its opt-in debug timing trace), the
**workloads** (synthetic fixtures with sizes, or a read-only user directory),
the **protocol** (repeats - always interleaved across tools, ordering is
not configurable - cache handling, verification, time budgets), and whether to run the **environment probes**. Everything not
given has a conservative default. (`syq-bench compare` is the cross-run
comparison of two results, not a way to run one.)

*Status (2026-09-02)*: built. Remote *destinations* work over ssh; a remote
*source* (fixtures generated on, or a path read from, the far host) is not
built, so pull-direction and remote-NFS rows are run by starting the
harness on the far host instead.

## What limits transfer speed

The earlier draft said disk is "the usual hidden bottleneck". That is one
case. A transfer is bounded by the slowest of several independent things,
and which one wins changes with the workload and the path:

- **Storage, throughput**: sequential read at the source, sequential write at
  the destination. Cloud network volumes are often the limit (a small gp3 is
  125 MB/s); local NVMe rarely is.
- **Storage, metadata**: create/stat/rename/fsync latency and how well they
  parallelise. This, not bandwidth, decides small-file workloads, and it is
  wildly different between local ext4, NFS, and network block storage.
- **Network, bandwidth**: link rate, and whether one flow can reach it.
- **Network, latency and loss**: with RTT, one TCP stream is capped by
  window/RTT and by ssh's own 2 MB channel window; that is why parallel
  connections help on WAN even when the pipe is idle. Loss compounds it.
- **CPU per stream**: ssh cipher/MAC tops out at a few hundred MB/s per
  process; checksumming and compression add to it. On both ends.
- **Tool overhead**: startup, scan, and per-file protocol round trips.

The harness should therefore measure these **separately**, cheaply and
opportunistically (fio/iperf3 when present; otherwise dd-style writes, a
create/stat loop, a single-stream and N-stream TCP run, an ssh throughput
run, `ping`-style RTT), record them alongside each result, and use them for
two things: name the likely ceiling for each workload in one line, and
explain *why* a tool falls short of it. The v1 diagnosis is that one line;
richer explanation is the section below.

*Status (2026-09-04)*: storage probes (O_DIRECT sequential write and read,
a create/stat loop) run at each harness-owned local endpoint and feed the
ceiling line. The Fly provider runs one-, four-, and eight-stream iperf3
receiver-goodput probes before its tools, discarding a two-second warm-up and
measuring the following five seconds. The warm-up was added after three-second
cross-region samples contradicted the longer transfers they were meant to
bound. It retains the four-stream value as the report's network-path ceiling
and records the other two so a result can be interpreted against the connection
count that actually carried payload.
Generic single- vs N-stream TCP and ssh-throughput probes are not built for
other remote providers; those runs still record both ends' qdiscs and a ping
sample, and say their ceiling is unknown. The hosts' congestion-control setting
is not yet recorded as a host fact although the measurements showed it to be
decisive; that is planned.

## Later: a small model of achievable speed

A plausible hypothesis, and the reason the probes above are worth keeping
separate: a handful of measured parameters — per-end sequential throughput,
metadata op latency and parallelism, bandwidth, RTT, loss, per-stream cipher
CPU — predict the achievable transfer rate for a given workload well enough
to be useful. If so, syq-bench can report "your setup could reach X; syq gets
Y; rsync gets Z; the gap is because …" and, for syq development, point at
which parameter syq is failing to exploit. Most people do not know which of
these properties their setup has, so simply measuring and naming them is
useful on its own. This is a research item after v1, not a requirement: the
model is only as good as the fit against real results, which is what the
stored results are for.

*Status (2026-09-02)*: not started.

## Cache state without `drop_caches`

Decided 2026-08-29. The user's servers are shared and large; a global
`echo 3 > drop_caches` takes a long time there, hurts other people's jobs,
and is impossible on an NFS server the user cannot log into. So the default
protocol does not use it:

- **Born cold**: fixtures are written with `O_DIRECT` (`dd oflag=direct`
  from shell) where the filesystem supports it, so generating a file does
  not leave it in the page cache. The destination is unlinked and recreated
  for every repeat, so nothing there is cached either.
- **Evict only the fixture** (`cache = "evict"`, the default) as the
  fallback where `O_DIRECT` is unsupported (tmpfs) and before every repeat
  after the first (which warms the source): `fdatasync` each file and drop
  *its* pages with `posix_fadvise(DONTNEED)`; from shell (the remote side)
  `dd if=FILE iflag=nocache count=0` or `vmtouch -e` when present. No root,
  no effect on anyone else's cache; a loop over files, cheap next to the
  transfer itself.
- **Compare tool completion; keep flushing diagnostic** (revised 2026-09-05):
  benchmark speed is reference bytes divided by the tool's own elapsed time,
  without a harness-added final flush. This measures the copy command people
  actually run, not an additional persistence operation. Buffered writes are
  part of that behavior; the result must not be called sustained device
  throughput or a durability guarantee. `protocol.durable = true` retains
  its existing diagnostic meaning: after the tool exits, the harness fsyncs
  every file and directory *it created* (locally
  `os.fsync` on each path; remotely
  `find DEST \( -type f -o -type d \) -exec sync -- {} +`, since GNU
  `sync PATH...` calls `fsync` on each named path, directories included,
  and never writes to them; the type filter matters because `sync` follows
  symlinks, which could fsync a target outside the scratch tree, and fails
  with EINVAL on FIFOs and other specials) and records its duration separately
  as `fsync_s`. If the walk fails or the platform's `sync` does not take
  paths, the result records `fsync_s = null` with the reason. Never bare
  `sync`, `sync -f`, or `syncfs`: those flush every
  pending write on the whole filesystem, disrupting other users of a shared
  server and counting their I/O in our time. Never use `dd` for this: with
  `of=FILE` it truncates unless `conv=notrunc`, and it cannot fsync a
  directory. This serial walk has its own traversal and syscall costs on large
  trees, so its duration is not simply "bytes still waiting to be written".
  Keep it in raw data and diagnostic details; do not add it to benchmark
  durations, speed charts, rankings, or public speedup claims.
- **Fresh data per run**: fixtures are seeded per run (seed stored in the
  result) so no cache anywhere holds the bytes from an earlier run.
- **What this does not control, and the result must say so**: an NFS or
  other network filesystem's *server*-side cache (only the client's is
  evicted); storage-controller caches; and the **metadata cache** (dentries,
  inodes): `O_DIRECT` and `fadvise` act on file data pages only, so from the
  second repeat on, small-file workloads run with warm source metadata. The
  destination is always a fresh path. Mitigation is fairness rather than
  control: repeats are interleaved across tools (never all of tool A then
  all of tool B), the first repeat of each case is flagged, and the result
  records `metadata_cache = "warm"`. A spec may ask for a fresh source path
  per repeat (regenerate) when the cost is acceptable. For network-bound scenarios (WAN,
  LAN over ssh) source cache state barely matters, which is fine as long as
  the result records it. `cache = "drop"` (global, needs root at each end)
  and `cache = "warm"` (no eviction; measures the tool, not the disk) remain
  as explicit spec choices.

*Status (2026-09-02)*: the default protocol (born-cold fixtures, per-file
eviction, per-path fsync, fresh seed, recorded uncontrolled state) is
built for local sources and remote destinations (eviction by `dd
iflag=nocache`, per-path fsync by GNU `sync PATH`). Not built: remote
fixture generation (see *Declarative runs*) and the per-repeat regenerate
option mentioned above. `cache = "drop"` does run a global `sync` and
drop_caches by explicit request; the prohibition on filesystem-wide sync
applies to the default protocol and to the durable-wall measurement.

## Results: storage and display

Results are the product, not a by-product, and looking at them needs design:

**Public page (2026-09-06).** The public renderer and syq documentation share
Open Sans headings and prose, a blue Manrope syq wordmark, IBM Plex Mono
commands and numbers, white/dark palettes, and the top navigation. Both sites
use shared sidebars with 15px Open Sans links, 16px section headings,
a 300px default width and 24px/12px left/right padding. Width is adjustable
from 240–480px, constrained by the viewport, and saved locally when available. Benchmarks, Reproduce a result and How we measure are
persistent page links; scenario anchors stay visible under Benchmarks on all
three pages, linking back to the results when needed. Main prose uses 20px Open Sans on both sites, while chart labels remain
compact. Both homepages use a prominent blue wordmark above a larger title,
keeping the visual character of the benchmark page while its introduction
describes the comparisons in one line. Buttons link to reproduction and
method details; chart-reading guidance lives in the method page and chart
labels, keeping the opening short. The docs homepage uses the same buttons
to offer installation and routes into its key features.
Contents, Theme and Search use the same labeled header controls. Sidebars
animate on both sites, respecting reduced motion. The benchmark renderer embeds
a three-page search index, so saved reports remain searchable offline.
A toolkit inventory and byte-comparison command make paired updates explicit.
The small shared asset set
lives in `src/syq_bench/` and is copied into syq's mdBook theme; each site builds
without fetching the other repository. This keeps the two existing renderers
independent without introducing a package release process for a handful of
files. `site/BRANDING.md` records the file mapping and update procedure.

The public comparison page leads with the release WAN scenario and
labels rentable and existing hardware by their measured setup. Each comparison
uses tools from one run; independent allocations remain separately inspectable.
The catalog names the default syq entry explicitly, so fixed-worker controls
cannot silently become the main syq result. There is no page-wide speedup claim:
advantages and losses belong to their individual workloads and recorded runs.
Descriptions lead with the copy job; hardware details and collection limitations
remain available in expandable setup notes. The release page is written for a new
user choosing a copying tool (reaffirmed 2026-09-09). Its main text describes
familiar jobs and explains results in plain language. Build pins, acceptance
thresholds, measurement terminology and investigation history belong in the
method, reproduction or expandable setup details, not the introduction. Failures and missing verification
or cache preparation suppress rankings, including when an older run succeeded.
Short per-case explanations distinguish control evidence, likely causes and
unresolved gaps. They are bound to reviewed capture-content hashes so changed
results or builds cannot silently inherit a stale interpretation. The full-rewrite
update case is labelled as a control, not a duplicate fresh-copy experiment:
the destination is already populated and the old files must still be considered.
The sidebar follows scroll position with a blue, accessible current-section
link. A header control toggles the sidebar, which starts closed on phones.
The small inline script makes no network requests and leaves navigation and
disclosures usable when JavaScript is disabled. Anchor scrolling is smooth
on both sites unless the reader requests reduced motion.

Each syq bar also labels its data path, separating observed data connections
from requested settings and missing evidence. Reachability probes alone never
establish TCP use. Mixed paths and partly missing repeat records remain visible.
Local filesystem copies distinguish NFS mounts without claiming that a specific
kernel copy optimization was observed. Public captures preserve only allowlisted
transport names extracted from saved diagnostics, not addresses or raw logs.

Displayed sizes and rates use decimal units (MB/GB and MB/s/GB/s), with
rounded labels and full precision preserved in the downloadable measurements.

Public copy charts show effective speed: recorded reference fixture bytes divided
by mean command time, with range lines calculated from the slowest and fastest
observed repeats. Higher is always better, including for incremental updates.
Reusing unchanged data raises effective speed without implying that those bytes
were transmitted; neither fixture size nor logical changed bytes measure network
traffic. The reference size is the fixture size before edits, even for append
workloads. This consistent scale replaces the previous change of chart direction
between new copies and updates. Raw times remain in the repeat tables. The public
comparison labels treat gaps within 5%, or within 15% with overlapping repeat
ranges, as comparable: this is a display heuristic, not a significance test.
The engineering report and regression analysis retain their existing median
calculations. Neither view pools independent allocations as identical machines.
CLI-generated public reports name their companion pages after the complete
output filename, so separate reports in one directory do not overwrite shared
method or reproduction pages. The published site retains its fixed page names.

An explicit catalog selects redacted measurement JSON in `site/data/`. These
published captures retain timing, verification, cache, workload and tool identity
fields while removing infrastructure identities and private diagnostics; original
results stay gitignored. A deterministic build generates the pages, which link
to the selected measurement captures. Source and recipes are obtained from the
public GitHub repository; Pages does not package a
separate source distribution. Container tags and
distribution packages are not frozen; the recipe pins syq source and new runs
record what was installed. This page does not complete the cloud storage matrix.

*Status (2026-09-02)*: storage, display, and hosting are all built as the
bullets below describe; the SQLite index remains a possibility that nothing
has needed yet.

- **Storage** (amended 2026-08-31: results are not committed — they embed
  hostnames and endpoints, and infrastructure identifiers must not enter git;
  `results/` is gitignored and long-term tracking works over the local
  directory. Published numbers come from a redaction step over local
  results - the tracked `site/` report, see *Hosting* - and, if the
  deferred rented-machine milestone happens, from disposable machines.)
  One JSON file per run under `results/` (versioned schema; the
  spec, harness/syq/tool versions, host facts, probe results, per-repeat raw
  timings, CPU, verification outcome, and what could not be controlled).
  Files are durable, diffable, and need nothing to write. When cross-run
  analysis (trend of one workload across syq versions, filtering by host or
  filesystem) becomes real, load the JSON into SQLite as a derived index;
  the JSON stays the source of truth. Do not start with a database.
- **Display** (built 2026-09-01; overview reshaped 2026-09-02): a terminal
  table for the run that just finished, and a self-contained static HTML
  view over `results/` for everything else. Its overview has one
  like-for-like workload/environment per row and one column per tool *as
  the spec names it*, showing the newest measurement with the build that
  produced it inside the cell (once several builds and spec scales have
  been measured, a column per exact build is mostly empty). The exact
  build-by-build grid stays below it, collapsed, for regression reading;
  there a dash means that build was never run on that row. Within a
  build, the newest measurement is used when it was measured repeatedly. A remote
  run whose remote binary SHA-256 was not recorded is deliberately unique to
  that run; a version string or diagnostic note is context, not an exact
  identity, and matching local hashes do not prove the remote systems matched.
  Unknown remote builds receive report-local ordinals after redaction; result
  paths never contribute to a public identifier.
  Bold means the leading group, not a claim that one noisy median is
  definitively best: medians within 5% or with overlapping repeat ranges are
  labelled comparable. syq cells are green when comparable to the fastest,
  amber when slower, and red at 2x or more; the same conclusions are written
  in each cell so color is not the only signal. Failed, incompletely verified,
  and cache-mismatched (including failed eviction) measurements remain
  visible but do not participate in ranking. A too-short measurement (under
  ~2 s) ranks against a trustworthy longer result - the wall time is real
  and startup noise can only understate the fast tool - with its rate
  labelled a lower bound; only a row where every result is too short stays
  unranked, as startup noise (amended 2026-09-02).
  Run details, probe ceilings, and trends remain on the same page.
- **Hosting** (decided 2026-09-01): keep the report static and make GitHub
  Pages the default publication target; it needs no application server or
  database. Generating and publishing are deliberately separate. The tracked
  `site/index.html` is a derived report whose host aliases, paths, interfaces,
  and addresses have been redacted; a Pages workflow deploys only `site/`.
  Raw results stay gitignored because they contain infrastructure identities.
  Treat a Pages deployment as public unless repository-level Pages access
  control has been deliberately configured.

## Other decisions

- **Separate repo**, not part of syq: own dependencies (uv, rsync, maybe
  tofu), own cadence, most syq users will not install it. syq's README keeps
  a pointer. The two repositories are always sibling directories (see
  `AGENTS.md`).
- **Python 3.13, uv-managed package.** uv installs the interpreter itself;
  `uvx --from git+... syq-bench` is the curl-and-run equivalent. Keep
  dependencies few; each one is fetched before a user's benchmark runs.
  ReFrame and its dependencies are isolated in the `cloud` dependency group,
  so they are fetched only by someone running cloud campaigns, not by an
  ordinary `syq-bench` invocation.
- **v1: the remote runs no Python.** Remote work is shell over ssh (tool
  invocation, fsync of created files, cache eviction, checksum, simple probes), isolated in
  `remote.py` so a remote helper can replace it when remote-side probes need
  more than shell. Not a requirement; revisit when it bites.
- **Comparators**: rsync (what users run today), `cp -a` (local/NFS),
  `tar | ssh | tar` (the folk "faster than rsync" answer), and qcp (added
  2026-08-29: a QUIC-based copier built on Quinn; the closest thing to a
  peer of syq's direct-data-path design, so if it wins on some row syq
  should learn from it). As of qcp 0.9, qcp rows explicitly carry the link's
  bandwidth and RTT hints and use a declared remote UDP range; its defaults
  describe a 100 Mbit/s, 300 ms link rather than auto-tuning. qcp is limited
  to single-file or flat workloads while preserving metadata: after a
  recursive push with `-p`, qcp 0.9 serializes one `SETMETA` request per
  directory, which makes shaped-tree results scale with directory count ×
  RTT. Omitting `-p` would benchmark weaker semantics rather than fix that
  bottleneck. scp and rclone are not tool kinds today; adding one is a
  small argv builder when a spec needs it.
- **Time budgets and early curtailment** (decided 2026-09-02). Repeats
  exist to resolve small differences; they add nothing to a difference of
  an order of magnitude, and a run that spends 30 minutes re-measuring a
  tool already shown to be 100x slower has wasted the machine. So, by
  default (`protocol.slow_cutoff = 10`), once a tool's median in a
  workload is at least ten times the fastest tool's, its remaining
  repeats are skipped; the measurement stays, ranks normally, and is
  marked "curtailed" with how many times it was measured. Curtailment
  never applies below the timing floor (both numbers would be noise), is
  re-evaluated for every case after each repeat so tool order does not
  matter, and never fires when every planned repeat has already run. A
  separate hard cap, `protocol.tool_timeout`, kills a single invocation's
  whole process group at the deadline and records the rate as an upper
  bound over exactly the budgeted window. Fixed-time-variable-data
  benchmarking was considered and rejected as the general policy: the
  unit of honesty here is the completed, verified job (checksum, durable
  flush, per-file completion cost), which a wall-clock-stopped transfer
  does not have; pure throughput sampling is what the probes are for.
- **Size published comparisons above the timing floor** (2026-09-09). The
  release page requires every tool to take at least three seconds in every
  repetition. Short and failed workloads remain diagnostic evidence, not plotted
  speedups. Where exclusion leaves important coverage missing, use a larger
  workload; increasing bytes alone does not lengthen an extent-sharing copy.
  The general harness retains its short-run labels for exploratory reports.
- **ReFrame orchestrates cloud campaign matrices** (decided 2026-09-03,
  provider-tested on Fly). This is not a claim that
  Python classes are less reproducible than data or that every benchmark must
  use a framework. The existing TOML run spec still describes one measurement.
  A separate TOML campaign describes the provider config, experiment points,
  resource lifetime, and cost assumptions; a small ReFrame test definition
  interprets that data and supplies parameter expansion, bounded concurrency,
  and ordering. The actual timing, cache handling, verification, probes, and
  result JSON remain in syq-bench. ReFrame earns its dependency only if later
  providers and resource graphs continue to need those facilities; this first
  slice is deliberately replaceable.
- **Cloud provisioning starts with Fly Launch, not Terraform/OpenTofu**
  (decided 2026-09-03). The first provider layer uses a native `fly.toml` and
  `flyctl`: one source and one destination process group per app, explicitly
  sized, with no public service or IP. Each experiment gets a fresh app and an
  exclusive Machine pair; ReFrame may run those independent copies in
  parallel up to the campaign's declared limit, then each worker destroys its
  app. Per-experiment state records the immutable app ID before Machines are
  created; normal failure and interruption destroy every app, and crash
  recovery refuses deletion if a current ID differs.
  The first short qualification point uses an explicitly mounted tmpfs at both
  ends and non-durable writes to keep Fly's fixed 8 MiB/s Machine-rootfs limit
  out of the measurement; it measures the private-network ceiling separately
  with iperf3. The Volume qualification campaign (added 2026-09-05) uses
  performance-8x Machines, the smallest performance size in Fly's documented
  128 MiB/s Volume tier. It scales the existing local/LAN large-file,
  mixed-tree, and small-file shapes, with fixture-page eviction, checksums,
  and separately measured post-copy fsync. Its one-repeat, bounded runs are
  exploratory; results are not automatically publication acceptance.
  An experiment's explicit `execution = "local"` keeps both paths on the
  source Machine and skips private-network probing. The same-Volume local
  case shares one device's read/write capacity; the remote case uses separate
  devices. The current two-process provisioner still creates an idle
  destination Machine for local experiments, included in the estimate. This
  reuses the established ownership/cleanup path pending a demonstrated need
  for single-Machine provisioning. A Fly Volume is not NFS or network block
  storage, so its result must not be labelled as either. The provider API boundary remains
  narrow so a later backend can use another provider's native interface without
  changing the run spec. Each result embeds the campaign and Fly config data,
  framework and provider-CLI versions, and exact syq revision alongside the run
  spec; reconstructing the setup does not depend on the original config path.
  The high-RTT acceptance campaign keeps its region-pair experiment serial.
  Two simultaneous copies on exclusive Machine pairs exhibited correlated
  inter-region capacity loss and caused the single-stream controls to hit their
  time limits; a following isolated allocation returned to the earlier regime.
  The route is therefore an unshareable resource for this experiment, not just
  the Machines. `max_parallel` coordinates experiments inside one campaign
  process; every campaign using that region pair declares the same exclusive
  resource label. Its per-user runtime lock spans the whole
  provision-measure-cleanup lifecycle and is shared across worktrees and clones
  on the host. Separate OS users and hosts remain outside that lock and must not
  overlap this route.
- **Fast-LAN qualification** (2026-09-07). The manual Hetzner Cloud recipe
  uses two same-location guests with local root storage and spread placement.
  It qualifies their actual network and guest storage before comparing fresh
  large-file, mixed-tree and small-file copies. Automatic syq, fixed worker
  counts, SSH-only syq and rsync remain separate entries. Network controls
  and copies run sequentially, with the harness on the source guest. A shared
  advertised host uplink does not establish per-guest bandwidth, and one
  allocation does not establish repeatability across allocations. The recipe
  and placeholder spec are durable; provisioning helpers and raw artifacts
  remain private experiment state. This adds no Hetzner lifecycle backend.
- **Infrastructure credentials share one encrypted file** (decided
  2026-09-03, revised 2026-09-05). The Fly campaign accepts `FLY_API_TOKEN`
  plus the non-secret `FLY_ORG` context. Hetzner dedicated-server credentials
  use `HETZNER_ROBOT_USER` and `HETZNER_ROBOT_PASSWORD`, a separate Robot
  Webservice login rather than a Cloud API token or the main account login.
  These credentials are prepared for the next provider; Hetzner provisioning
  is not implemented yet. The public repository supplies only `.env.infra.example`.
  Reproducers bring their own least-privilege provider credentials through
  exported variables or a local, gitignored `.env.infra` encrypted with their
  own key. The private experiment repository may retain encrypted credentials;
  decryption keys remain outside Git in `.env.keys` or a secret manager. Credentials never
  enter campaign specs, Fly config, command arguments, state, or result JSON.
  The wrapper uses dotenvx redaction when it loads `.env.infra`.
- **Emulated networks** (decided 2026-09-02): a two-container Docker lab
  (`netlab/`) applies symmetric `tc netem` delay, loss, and rate inside the
  containers' own network namespaces. The guarantee is that the netem is
  confined to the containers' interfaces and never alters a host
  interface's qdisc; the lab still needs ordinary Docker access, and the
  daemon creates a bridge network and veth pairs as any container run does.
  It exists to compare tools under identical, controlled impairment. Its
  first use, at 262 ms RTT, a 1 Gbit cap, uniformly random loss of 0.1 %
  and 1 %, and a single large file, showed the congestion algorithm rather
  than the transport deciding the outcome; other loss models, rates, or
  small-file workloads were not tested. Both ends share one machine, so it
  is never a source of absolute numbers. Real links stay the reference.

*Status (2026-09-05)*: built: separate repo, Python 3.13/uv, shell-only
remote, the comparators rsync/cp/tar/qcp, the near-instant rule, and the
emulated-network lab (the lab and time budgets merged 2026-09-02), plus the
first Fly/ReFrame campaign, including provider provisioning, private SSH,
bounded parallel workers, cleanup, and its provider-specific iperf3 ceiling
probe. On demand, not built: scp and rclone tool kinds, fio and generic
iperf3 probes. Three independently provisioned Fly WAN-speedup runs have exposed
the intended multi-flow advantage and its fixed-one/fixed-many control; the
redacted publication artifact is generated and under review. A complete
shortened cloud reference matrix has not yet been built or reviewed, so Fly
does not yet replace the private benchmark setup.

## Safety guards

- Destination must be empty or absent; refuse otherwise.
- Check free space and inodes against the peak footprint of each workload
  (source copies, snapshots, destination trees, probes) before starting.
- Removal rule (decided 2026-08-29): the harness passes `--rm`, `--delete`,
  or `rm -rf` only a path it generated itself under a scratch root in this
  run, never a user path, never an endpoint root. `syq --rm` and `--delete`
  may be benchmarked under that rule; neither has a tool kind yet.
- User path workloads are read-only. A maximum size with results marked as
  extrapolated is planned, not built: today a path workload is copied whole.
- Print the plan (tools, sizes, hosts, repeat policy) before running;
  `--yes` to skip. Not built: a duration estimate, and showing whether
  probes will run. Anything that costs money or opens ports prints the
  estimate and the rollback path first. The Fly campaign does print its
  configured cost estimate, exclusions, lifetime cap, every exact-app
  rollback, and every recovery-state command before creating the apps; its
  rates are reviewable dated inputs, not a live provider quote.

*Status (2026-09-02)*: the removal rule, empty-destination refusal, and the
space/inode preflight are built and tested; the size cap for user paths
and the plan additions above are planned.
