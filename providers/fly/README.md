# Fly campaign

The Fly support has two different jobs. `same-region.toml` is a provider
qualification: one fresh large-file copy confirms provisioning, private SSH,
the network ceiling probe, result collection, and cleanup. Its result is not a
useful tool comparison because every tool must send the whole file and the
same-region network path is the bottleneck.

`remote-comparison.toml` is the first comparative suite. It reuses the ordinary
harness's operation modes and file shapes: a fresh large file, fresh small
files, in-place block edits where rsync can use its delta algorithm, and a
whole-file rewrite control. The same run-spec format used by `specs/lan.toml`,
`specs/wan.toml`, and `specs/delta.toml` describes those workloads. Same-region
and Amsterdam-to-Tokyo experiments run concurrently on separate Machine pairs;
the high-RTT point selects a smaller run spec so slow single-SSH paths still
finish within the iteration deadline.

The two `transport-validation` campaigns pin syq revision
`445cd5c7be2255fbc97479d2a29f714cf3974ec1`, a post-IPv6-listener master
revision. They are narrow diagnostics rather than representative benchmark
suites: at a fixed eight connections, they compare encrypted TCP, explicit SSH
transport, and rsync for one fresh file. Double-verbose output and syq's debug
phase/worker timings are retained with each measurement so the selected
transport and setup costs can be checked.

`wan-speedup.toml` is the bounded acceptance experiment for syq's multi-flow
WAN advantage. It sends eight independent 64 MiB files from Amsterdam to Tokyo
and compares automatic syq, fixed one- and eight-connection syq, and rsync. The
512 MiB total gives each connection enough time to leave TCP slow start while a
100-second per-tool deadline keeps every individual measurement below two
minutes. This avoids repeating the invalid 32 MiB single-file scaling: that
file cannot be range-split by syq, so `-j8` has no more payload work than `-j1`.
The one-, four-, and eight-stream iperf probes show whether the provisioned path
had aggregate capacity for parallelism, and the retained syq diagnostics show
whether direct TCP and multiple workers were actually used.

Run this acceptance campaign sequentially. Independent validation found that
two copies running at once on separate Machine pairs still contended for the
same inter-region capacity and pushed the single-stream controls into their
time limits. Its Amsterdam-to-Tokyo route is therefore an unshareable benchmark
resource. `max_parallel = 1` enforces this inside one campaign invocation, and
the declared `exclusive_resources` label prevents separately started campaigns
under the same OS user from overlapping, including launches from separate
worktrees or clones. Every committed campaign using that region pair declares
the same label. Separate OS users and hosts remain uncoordinated and must be
scheduled not to overlap.

Both campaigns give each experiment its own source Machine, destination
Machine, and Fly app. ReFrame may run independent experiments concurrently up
to the campaign's `max_parallel`; no two measurements share a benchmark
Machine. Their current endpoints are 1 GiB tmpfs mounts, which makes these
network/tool-overhead and delta-algorithm comparisons. They do not stand in for
the existing local-disk or NFS rows. Fly Volume and other storage campaigns
need their own explicitly labelled points.

Every remote experiment runs independent one-, four-, and eight-stream iperf3
receiver-goodput probes before the transfers and stores those measured path
ceilings in the result. The four-stream value retains the canonical
`network_receive` name used by reports; the other values reveal when a tool's
effective connection count makes that aggregate ceiling inapplicable.

The campaign creates no Fly Proxy service and requests no public IP. It creates
a random app-scoped SSH key so the source can reach the destination privately;
the key and all resources disappear when the owned app is destroyed.

## Prerequisites

Install `uv`, `flyctl`, and an OpenSSH client (`ssh-keygen`); `ps` is also
required for process-tree cleanup. Dotenvx is optional when credentials are
already exported, and otherwise decrypts the repository's infrastructure
credential file at runtime. The wrapper installs ReFrame from the pinned
`cloud` dependency group when it runs a campaign.

## Credentials

An org-scoped Fly token and the target organization slug are the only Fly
inputs. Supply your own credentials through exported environment variables or
a local, gitignored `.env.infra` encrypted with dotenvx. Its matching private
key stays in the gitignored `.env.keys` or a secret manager. Neither file is
distributed with the public repository.

To create your own encrypted credential file and key:

```bash
cp .env.infra.example .env.infra
chmod 600 .env.infra
$EDITOR .env.infra
dotenvx encrypt --no-native -f .env.infra
chmod 600 .env.keys
```

Use an org token because the runner creates and destroys a fresh app. An
app-scoped deploy token cannot create that app. Create a short-lived token with
`fly tokens create org -o <org> -x 24h`; do not paste it into a campaign, run
spec, command line, result, or tracked file. `FLY_ORG` must be the exact slug
shown by `fly orgs list`, not the organization's display name; the runner
checks this before it creates recovery state or billable resources.

Use your own Fly account and least-privilege token.
The wrapper uses `dotenvx run --redact`, so an accidentally echoed exact value
is redacted. Already-exported `FLY_API_TOKEN` and `FLY_ORG` values take
precedence and do not require dotenvx, which is the intended CI path.

## Plan and run

Planning is local, does not need credentials, and does not create anything:

```bash
./providers/fly/run plan providers/fly/campaigns/same-region.toml
./providers/fly/run plan providers/fly/campaigns/remote-comparison.toml
./providers/fly/run plan providers/fly/campaigns/transport-validation.toml
./providers/fly/run plan providers/fly/campaigns/transport-validation-cross-region.toml
./providers/fly/run plan providers/fly/campaigns/wan-speedup.toml
```

The plan shows the exact Machines, storage, region, experiment axes, configured
cost estimate, exclusions, lifetime cap, and rollback. Running asks again
before any Fly resource is created:

```bash
./providers/fly/run run providers/fly/campaigns/same-region.toml
./providers/fly/run run providers/fly/campaigns/remote-comparison.toml
./providers/fly/run run providers/fly/campaigns/transport-validation.toml
./providers/fly/run run providers/fly/campaigns/transport-validation-cross-region.toml
./providers/fly/run run providers/fly/campaigns/wan-speedup.toml
```

`--yes` is available for an already-reviewed automated run. Results land under
`results/fly/<run-id>/`; Fly and ReFrame state remain gitignored because they
contain infrastructure identity. Each experiment has a separate exact-ID
recovery state and cleanup command. Parallel experiments use separate Machine
pairs, so the campaign does not deliberately make them share a CPU, root
filesystem, or Volume; provider-level ambient contention remains possible.

The estimate covers the configured campaign cap. Provider cleanup can take
additional time, and a failed cleanup continues billing until the printed
recovery command succeeds; remote-build charges are also explicitly excluded.

Normal completion and handled failures destroy every exact app. Each state
file records its immutable Fly app ID before Machines are created; cleanup
refuses to delete an app whose current ID does not match. If the local process
is forcibly killed, run every recovery command printed before creation:

```bash
./providers/fly/run destroy results/fly-state/<run-id>.json
```

The first remote image build can be slow. Later deployments can reuse Fly's
builder cache. The qualification recipe runs one 512 MiB large-file workload,
one repeat, and a 20-second hard limit for each tool. The comparison recipe
runs one repeat with a 15-second hard limit for each timed tool invocation. The
whole campaign has an eight-minute deadline and its two infrastructure points
run concurrently.

Result byte counts need careful interpretation. A fresh case records the
logical fixture size. An incremental case additionally records the bytes that
the harness deliberately changed. Neither number is an interface-counter
measurement of bytes sent on the wire. The edited-versus-rewritten comparison
tests delta behavior through verified elapsed times: rsync should avoid sending
unchanged file data in the edited case, while the rewrite control removes that
advantage. The exact seconds are expected to differ from other hosts; the
useful reproduction target is the ordering and relative behavior in the same
bottleneck regime.

## Volume qualification

```bash
./providers/fly/run plan providers/fly/campaigns/volume-qualification.toml
./providers/fly/run run providers/fly/campaigns/volume-qualification.toml
```

This exploratory campaign uses `performance-8x` / 16 GB Machines and 20 GB
Volumes. That is the smallest performance Machine size in Fly's documented
[128 MiB/s Volume tier](https://fly.io/docs/volumes/overview/#volume-limits).
Volumes are host-local NVMe, not NFS or network block storage. Their actual
capacity is probed; the tier name is a published limit, not a measured result.

It runs two points concurrently on separate Machine pairs:

- `execution = "local"`: both paths on the source Machine's one Volume, so
  reads and writes share a device. There is no inter-Machine transfer or iperf probe.
  The existing two-process provisioner still allocates an idle destination
  Machine and Volume, both included in the estimate and destroyed afterward.
- `execution = "remote"` (the default): source and destination on separate
  Volume-backed Machines in the same region. Independent iperf probes measure
  the network. The native harness probes source storage but currently cannot
  probe remote destination storage; the result records that limitation.

Both use the ordinary harness's large-file, mixed-tree, and small-file shapes,
scaled for short runs. Fixture pages are evicted; metadata caches are not.
Checksums are verified, and post-copy fsync time is recorded separately as a
diagnostic. Benchmark speed uses tool-return time only; do not add the flush
to charts, rankings, or speedup claims. A tool can return while dirty output
is still in RAM, so this rate is not a durability guarantee or sustained
storage throughput. The flush walk also includes traversal and syscall costs;
it can help investigate different write patterns but is not the copy workload.

Each timed invocation has a 30-second deadline, with one repeat and a
15-minute whole-campaign cap including deployment. The generous first-build
allowance is not a target test duration. A sub-two-second measurement remains
marked too short. These are qualification results, not accepted public-page
claims: repeat promising cases on a fresh allocation before publication.

`campaigns/volume-directory.toml` scales only the small-file shape to 80,000
files / 320 MiB and two repeats, still with a 30-second tool deadline and a
10-minute campaign cap. It checks whether short directory measurements hold
at a more useful scale. Compare copy-only rates and retain the separate flush
measurements solely for troubleshooting.

## Long-distance incremental updates

`campaigns/wan-updates.toml` prepares the missing high-RTT update comparison:

```bash
./providers/fly/run plan providers/fly/campaigns/wan-updates.toml
./providers/fly/run run providers/fly/campaigns/wan-updates.toml
```

This is a new exploratory public comparison, not a reproduction claim. It
compares automatic syq with rsync, using six 64 MiB files. One workload edits
about 1% of the 4 KiB blocks in every file; the other rewrites every byte.
Both tools receive the same generated basis and modified source in each
repeat. The harness prepopulates a fresh destination with rsync outside the
measurement, then verifies the completed copy outside the measurement.
Compare copy-completion seconds; changed bytes are not measured wire traffic.
Both commands use archive semantics and default compression behavior. The syq
command uses its current rsync-compatible interface; it exercises the same
copy engine as the native CLI. This is a Linux push, not a Mac or download
comparison.

The syq source pin is `aab3dc7e13c7f7abdc580cc3261401aa42771b7c`;
Rust 1.94 in the existing Dockerfile meets that revision's requirement. Both
Machines use the image's syq binary with bootstrap disabled. Retain the local
and remote binary hashes and versions, rsync version, image identity, and
`-vv` / `SYQ_DEBUG` logs in the result. Confirm the selected transport from
those logs: requested TCP alone does not establish that TCP was reachable.
The older same-region update experiment fell back to SSH, so its timing is
not evidence about current TCP update behavior.

The source and destination each have a separate 1 GiB tmpfs. The 384 MiB
fixture leaves room for its snapshot or destination sidecar copies, inode
estimates, and the harness's 10% free-space margin. Workloads run sequentially;
verification streams file contents without making another tree. Do not reuse
the 512 MiB fresh-copy size here: incremental state would exceed that margin.
Warm tmpfs intentionally removes storage from this network/algorithm question.
Ambient route load, metadata caching, and syq's remembered tuning state within
a campaign are not controlled. Independent fresh allocations give a separate
check of that dependence.

There are two interleaved repeats, with no ratio-based repeat curtailment,
a 100-second cap on each timed invocation, and a 1,200-second whole-campaign
cap including deployment, prepopulation, probes, and verification. Preparation
has no separate per-command timeout; the campaign deadline is its outer bound.
Two Machines run the benchmark; regional replacement can briefly require a
third. The plan estimates compute cost and prints exclusions and recovery.
No public IP or Fly Proxy service is requested. The existing route lock keeps
this experiment from overlapping the accepted WAN-speedup campaign on the
same account's Amsterdam-to-Tokyo path under one OS user on this host.

Inspect both wins and losses, checksum failures, timeouts, and too-short flags.
The fixture is large enough to offer concurrent payload work, but only a real
run can establish whether elapsed times are useful. Independent allocations
can show how much results vary. Published comparisons should state the
allocation count, repeat count, and conditions so readers can judge their scope.
Local planning and one Fly allocation completed on 2026-09-07: every copy
verified, both endpoint binary hashes matched, and retained diagnostics showed
actual encrypted TCP data connections. Independent allocation confirmation
remains separate from this initial validation.
