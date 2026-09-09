# Two-host local-disk copy experiment

[`specs/fast-lan.toml`](../../specs/fast-lan.toml) compares fresh copies with
syq's automatic worker selection, fixed 1/4/8/16 workers, SSH-only syq, and
rsync. It covers one 8 GiB file, an 8 GiB mixed tree of 8,000 files, and
200,000 small files of 4,194 bytes. Contents are seeded pseudorandom data.
This is a manual host recipe; the repository has no Hetzner lifecycle backend.

The initial Cloud configuration uses two CCX23 instances in FSN1, with four
dedicated vCPUs each, Debian 13 x86, local root storage, and a **spread**
placement group. Larger dedicated-CPU shapes are useful follow-up experiments;
record a changed shape as a separate run. A spread group puts the guests on
different physical hosts. It does not reserve their physical network or disk
bandwidth. See Hetzner's [placement documentation](https://docs.hetzner.com/cloud/placement-groups/overview/)
and [technical FAQ](https://docs.hetzner.com/cloud/technical-details/faq/).

## Prepare the pair

Before allocation, obtain a current account quote for both servers and IPv4
addresses, estimate the intended runtime, and record the deletion procedure.
Save the returned resource IDs and a unique run label outside git. Use newly
allocated hosts and run-owned scratch paths. Do not use another campaign's
machines. Cloud and Robot credentials belong to separate APIs; this recipe
uses Cloud. Console provisioning is sufficient; no token belongs in a spec.

Create one exclusive SSH key and firewall for the run. Permit SSH from the
controller and source host, ICMP for path diagnostics, TCP 5201 for iperf3
between the pair, and TCP 47600–47699 for syq between the pair. Leave these
benchmark ports closed to other addresses. Use explicit IPv4 addresses on
the same-location public path and record the route and MTU. No Volume or
private Network is required for this configuration.

Install Python 3.13+, rsync and iperf3 on both guests. Disable the packaged
iperf3 service before installation; start only the bounded measurement
listeners below. Keep package versions in the run record. Pin the guests'
SSH host keys in a run-specific known-hosts file and record how they were
verified. Give the source an exclusive key for the destination; the
controller's personal SSH agent is unnecessary.

Resolve syq's `origin/master` when starting the campaign, then build that
exact revision in a private checkout. The initial build used Rust 1.94.1:

```bash
RUSTFLAGS='-C target-feature=+crt-static' cargo build --locked --release \
  --target x86_64-unknown-linux-gnu -j 4
```

Put the resulting `syq` on PATH at both ends. Record the commit, compiler,
build command, and both SHA256 hashes; verify the hashes match. The spec's
`--syq-no-bootstrap` makes the deployed remote binary explicit. Also record
the harness commit and the exact spec used. Do not follow a moving branch
between repeats.

Run the harness on the source guest. Keep the controller outside the
measured data path. Record on both guests: OS/kernel, CPU model and count,
CPU affinity, memory, filesystem and mount options, block-device geometry,
routes, interface MTU, qdiscs, tool versions, and provider instance facts.
Sample `/proc/stat`, `/proc/diskstats`, `/proc/meminfo`, and `/proc/net/dev`
with UTC timestamps every second, including a quiet interval. These samples
help detect competing work, CPU steal, and changing resource pressure.

## Qualify the network and storage

Complete controls before starting copies. A same-location allocation alone
does not establish a fast LAN. For each network point, start this single-use
server on the destination, substituting its allocated address:

```bash
timeout -k 5 50 iperf3 -s -1 -B DESTINATION_IP -p 5201 -J
```

Then run the client on the source and save stdout and stderr separately:

```bash
timeout -k 5 40 iperf3 -c DESTINATION_IP -p 5201 -P 1 \
  -t 20 -O 2 -J --get-server-output
```

Repeat with `-P 4` and `-P 8`, then all three with `-R`, and repeat the six
points once more. Start a fresh single-use server for every point and check
both exit statuses and JSON errors. Retain receiver goodput, retransmits,
CPU use, interval data, and server output. Repeat representative points
after the copies. Run controls and copies sequentially: concurrent tests
would consume the bandwidth being measured.

If the link is around 1 Gbit/s or below, retain that qualification result and
do not call it a fast-LAN reproduction. Even a faster route can limit copies;
interpret their rates against its measured capacity and variation.

Separately measure source reads and destination writes on newly generated
owned files. The initial control uses 16 GiB aggregate, 4 MiB blocks,
`O_DIRECT`, and 1/4/8 synchronous workers, with a 60-second cap per point.
Read preparation is outside timing; write timing includes final per-worker
fsync. Check free space and completed byte counts, retain truncated points,
and remove only the control files created by that invocation. Record the
data pattern and any direct-I/O failure. Guest direct I/O does not establish
the state of the host's physical caches. These are short storage diagnostics,
not a disk endurance or sustained maximum-throughput claim.

## Run and interpret the comparisons

Copy the template into an ignored directory on the source and replace all
endpoint/SSH placeholders. Each source and destination scratch directory
must be empty or absent, with enough bytes and inodes for the largest
fixture. The harness checks both. Use a small separate smoke spec first:
one repeat, 256 KiB per workload, and 64 files for each tree. Keep its
results as correctness evidence, separate from performance measurements.

Use fresh, run-specific `XDG_CACHE_HOME` and `XDG_CONFIG_HOME` directories
for the main campaign, retaining them across its repeats. For example,
from the harness checkout on the source, with the edited spec saved as
`current-plans/fast-lan.toml`:

```bash
uv run syq-bench run current-plans/fast-lan.toml --yes \
  --out results/fast-lan.json
```

The template uses three repeats, fixed-order interleaving, checksum
verification, fixture-page eviction, a 120-second per-copy timeout, and no
slow-tool cutoff. It times tool return without adding a final flush.
Metadata caches remain warm; no global cache drop is needed. Fixture
generation and verification are outside copy timing. Small-file generation
fsyncs individual files, so allow setup time and never present an incomplete
fixture as the requested full tree.

Retain every tool, failed verification, timeout, eviction failure, and
too-short result. Record actual transport diagnostics from `SYQ_DEBUG=1`
(enabled in the template); reachable ports alone do not prove TCP was used.
Keep complete stderr separately when investigating worker selection, since
the stock JSON stores only its last 100 lines. Preserve the automatic tuning
cache and distinguish automatic results from the best fixed worker count.
If drift matters, repeat the whole affected comparison in reverse tool
order. A single allocation establishes one observation; independent
allocations are needed to assess reproducibility of the numbers.

Give the whole campaign a finite deadline. After capturing results, logs,
specs, provenance and monitoring data, stop and verify its owned worker
process groups on both guests. Delete both recorded servers and verify
absence through the API. Delete any retained owned primary IPs, then the
owned firewall, placement group and SSH key, and verify all their IDs are
absent. Keep deletion receipts. Billing continues until server deletion;
see Hetzner's [billing FAQ](https://docs.hetzner.com/cloud/billing/faq/).

Raw artifacts contain infrastructure identities and stay under ignored
`results/` or `current-plans/`. This template contains placeholders only.
