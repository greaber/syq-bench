These results use syq source commit `fd2b17c642e62d7ee63f7f3835560a6ceacd0afb`, the published v0.5.2 release (signed tag confirmed against GitHub during this campaign), and syq-bench commit `a81f64898a1080f7dceba0d05a6b4a919f8211c5`.

The [harness source](https://github.com/greaber/syq-bench/tree/a81f64898a1080f7dceba0d05a6b4a919f8211c5) matches
the code used for these measurements. The adjacent TOML specifications replace
endpoint names and scratch paths with placeholders. Sanitized measurements are
downloadable from each comparison.

Build syq with Rust 1.94.1 using:

```sh
CARGO_BUILD_JOBS=8 RUSTFLAGS='-C target-feature=+crt-static' cargo build --locked --release --target x86_64-unknown-linux-gnu
```

This campaign used the resulting static GNU binary on every endpoint. Its SHA256 is `da584b363640f2757f485058103c4be5f906fd8f7fd7e5d582bbcfd98a9ca1ef`. This identifies a source build, not an artifact downloaded from a release.

The comparisons use generated incompressible data with seed 204330278, three repetitions per workload/tool/environment, rotating the first tool each repetition. Each timed copy is followed by an untimed content checksum. Destination trees start empty for fresh-copy tests; update tests restore the same pre-change snapshot for each tool. No compression is enabled. There is no global cache eviction and no final flush. Reported throughput is logical bytes per second, not physical storage or network traffic. The release page uses reference dataset bytes divided by mean elapsed time, including for updates. Unchanged-tree charts show elapsed time because no contents are transferred. For append workloads the reference is the original dataset size. These effective rates do not measure bytes sent. The internal campaign report instead uses median elapsed time and changed-byte rates for updates; the views must not be mixed.

Local copies use `syq rsync -a --stats --no-progress --syq-no-bootstrap`, the same command with `--syq-connections 32` as a tuning control, `rsync -a`, and `cp -a`. The automatic/default syq result is the primary release comparison. Explicit `--syq-connections 32` disables automatic worker tuning and the automatic sequential NFS fallback, so it is a configuration comparison, not an isolated worker-count experiment. Fixtures cover one large file, a mixed tree, and many small files. Recorded TOML files specify their sizes and counts.

Network copies run over SSH-authenticated encrypted TCP by default, with an explicit `--syq-no-tcp` control. An explicit `--rsync-path` pins the remote syq executable. The stock harness remote identity probe does not recognize that flag and may report the remote PATH lookup as unavailable; deployment checks, reciprocal runs, and final endpoint hashes independently verify the selected helper binary. All tools use the same SSH endpoint configuration; rsync uses its normal SSH transport. Benchmark TCP ports are restricted to the participating hosts. Untimed iperf3 probes measure link capacity separately, and identical kernel packet counters distinguish TCP data traffic from SSH traffic during timed copies.

Public local hardware is an hourly Hetzner server-auction machine with a Ryzen 9 5950X, 128 GiB RAM, and two dedicated 3.84 TB Samsung data-center NVMe devices. Actual filesystem types and mount options are captured for both paths. Public network endpoints use dedicated-vCPU Hetzner Cloud CCX23 guests with 4 vCPUs and 16 GiB RAM, Debian 13, and tmpfs fixtures. The nearby-server pair is in one German data center; the long-distance pair connects Germany and the US East Coast. Do not assume the backing hardware, network conditions, CPU frequency, kernel, or rsync build is identical between these environments; those facts are recorded where observable.

The private storage comparisons in this campaign use a storage server with two Intel Xeon Platinum 8460Y+ processors, 160 logical CPUs, and the performance governor. Its XFS data volume, ext4 system volume, and existing NFS 4.2 mount are distinct placements. The development host was not timed because other work was using it. The NFS server backing filesystem and cache were not controlled; fixture-page eviction applies to the client. Shared-host activity is recorded in snapshots rather than assumed absent.

XFS same-filesystem copying may share file extents through kernel offload. A very high logical rate in that case is not a claim of equivalent physical disk bandwidth. GNU cp behavior also depends on its recorded version. Charts include only complete workloads where every tool has three successful, checksum-verified repetitions and every repetition lasts at least three seconds, with the requested cache preparation. Downloads retain omitted short or failed cases. Valid unfavorable comparisons remain charted. This gate is a practical publication threshold, not a statistical significance test.

The adjacent TOML templates preserve each published capture’s full workload, tool options, seed and protocol. Replace the source and destination placeholders with distinct, empty scratch directories on the filesystems shown in the results, and replace `/path/to/syq` with the pinned executable. For a remote test, configure the `benchmark-peer` SSH alias and place that executable at the specified remote path too. LAN and WAN runs use tmpfs on both endpoints.

Run a filled-in template from the harness checkout with:

```sh
uv run syq-bench run specs/release-052/public-ext4-same.toml --dry-run
uv run syq-bench run specs/release-052/public-ext4-same.toml --out results/my-ext4-run.json
```

The campaign additionally used an audit wrapper to rotate the initial tool order between repetitions, record GNU time maximum process RSS and untimed system snapshots, and capture fixture manifests. The stock command above reproduces the workload and transfer configuration; it does not reproduce that extra instrumentation or rotating start order. Match the filesystem placement and cache policy when comparing, and repeat enough times to assess the timing range. The public bare-metal disk runs used ext4 with `noatime`; the separate XFS runs used `noatime` and reflink enabled. The server's default powersave governor was retained.

For each named template, match these placements:

| Template prefix | Source → destination |
| --- | --- |
| public-ext4-same | Same ext4 filesystem on one NVMe drive |
| public-ext4-cross | Separate ext4 filesystems on separate NVMe drives |
| public-xfs-same | Same XFS filesystem, reflink enabled |
| private-ext4-same | Same ext4 system volume on the storage server class |
| private-ext4-xfs-cross | ext4 system volume → XFS data volume |
| private-xfs-full | Same XFS data volume |
| private-nfs-write / private-nfs-metadata-25k | XFS data volume → NFS 4.2 mount |
| private-nfs-read | NFS 4.2 mount → tmpfs |
| public-lan-* | tmpfs → tmpfs, same data center |
| public-wan-* | tmpfs → tmpfs, Germany/US East Coast; reverse swaps endpoints |

NFS results describe this service, whose backing storage and server cache are
unknown. A different NFS service may behave differently. Do not infer that XFS
is universally faster than ext4 or that the storage server and rental CPU/storage
setups are equivalent. Their governors, CPU families and tool versions differ.
Use distinct, empty scratch directories and check capacity on each actual mount.
The harness manages generated trees; it does not provision, format or cancel servers.
Consult current provider quotes before renting and confirm cancellation separately.

The page currently omits LAN update comparisons, small unchanged-file screens,
and the very short local mixed-tree/extent-sharing cases. WAN updates, the large
unchanged tree, and ext4 mixed trees cover those broader operations, without
claiming the omitted specific setups were measured at a publishable scale.
