# Rclone comparison

The initial campaign compares syq and rsync with rclone on mounted NFS and on
short- and long-distance network routes. These are templates, not measured
results. Use release binaries with recorded versions and SHA-256 identities.
The local adapter was smoke-tested with rclone 1.75.1; network measurements
still require validation on the selected hosts and server implementations.

- `nfs-small.toml`: 100,000 small files, using rclone's local backend through an
  existing NFS mount. No SSH/SFTP server on the NFS server is needed. Run reads
  and writes separately by exchanging the source and destination mount locations.
- `network.toml`: small files and one large file, using SFTP and WebDAV over
  HTTPS. Run separately on each route. Run the harness on the source machine;
  reversing machines measures uploads in the opposite direction. It does not
  test rclone's download implementation. Client-side downloads, updates and
  interruption/recovery are subsequent experiments, outside this first matrix.

Replace all placeholder endpoints and binaries in an ignored copy of the spec.
The network template disables syq bootstrap and names its remote executable;
deploy that pinned binary into an authorized private runtime first.
The harness refuses a non-empty destination, checks free space/inodes, generates
fixtures under its scratch directories, and removes its own workload trees.
Do not point scratch roots at user data. A mounted filesystem's real type and
mount options must be recorded separately; a template's name does not prove NFS.

```sh
uv run syq-bench run /path/to/private-spec.toml --dry-run
uv run syq-bench run /path/to/private-spec.toml --yes --out results/rclone-run.json
```

## Backend configuration

`kind = "rclone"` uses `rclone copy`, never `sync` or `move`. Its default arguments
include `--create-empty-src-dirs`; setting `args` replaces those defaults. Rclone's
normal checksum checking stays enabled. The adapter ignores the user's config
file with `--config /dev/null`; authentication can use a key file, SSH agent or
backend environment variables. Keep credentials out of arguments and specs:
arguments and endpoints are stored in results. Record the non-secret effective
settings in the campaign record, including any environment overrides.

`rclone_backend = "local"` requires local paths, including mounted NFS. For
SFTP choose `sftp` (rclone's internal SSH library) or `sftp-ssh` (external
OpenSSH using the harness's SSH command). The internal client uses the endpoint's
host and user by default, but does not interpret SSH aliases or inherit the
harness's SSH options. Supply `--sftp-host`, `--sftp-port`, `--sftp-user` and key
options in `args` as needed. Server host keys are checked against the local
`~/.ssh/known_hosts` by default. The external OpenSSH variant is a separately
labelled control: remote hashing may start a connection per file.

For `webdav`, set `rclone_url` to an HTTPS URL and `rclone_root` to the absolute
server-side directory exposed at that URL. The destination must lie beneath
that root. Use the matching vendor option and trusted CA certificate. Credentials
belong in the environment, not the URL. Starting a temporary server, exposing a
port, or renting machines requires authorization naming the host/provider and
an estimate plus rollback path. This adapter does not start services or change
firewalls. A campaign should serve only its owned scratch and stop its whole
server process group after capture collection. When using `rclone serve webdav`,
set `--dir-cache-time 0s`: the harness creates and removes scratch directories
through SSH, outside the server's VFS, so its default directory cache can hide
new trees. Record this server option with the results.

Before any upload using internal SFTP or WebDAV, the harness creates a random
marker in the repeat's destination through SSH and reads it through rclone.
Mismatch or failure prevents the copy; the marker is removed before timing.
This binds the configured backend to the owned scratch. It does not validate
all server semantics or defend against malicious concurrent filesystem changes.
Server executable/version, negotiated SSH cipher/extensions, TLS configuration,
and server CPU need a separate campaign capture: the harness records remote
server identity as unknown rather than claiming a remote rclone binary ran.

## Fairness and tuning

The network fixture is regular files. Its comparison targets contents and file
modification times, not Unix archival fidelity: SFTP and WebDAV do not implement
rclone's general metadata framework. Independently inspect required file mtimes
before calling the job equivalent. The stock harness verifies regular-file
contents only; the result explicitly records this limitation. Syq/rsync may do
additional directory-metadata work. On NFS, `--metadata` requests rclone's local
metadata preservation; generated fixtures have no ACL/xattr/hard-link workload.

Keep default rclone verification in primary timings. Its built-in checksumming
can add reads and remote command overhead; an optional diagnostic must name any
changed verification behavior, retain independent content verification, and
not replace the primary result. WebDAV also differs in partial publication and
checksum availability; record that difference instead of claiming rsync-equivalent
integrity from equal file contents after success. Do not disable TLS or use
`--inplace` to improve a headline number.

First collect defaults. If throughput is low, sweep `--transfers` through
4, 8, 16 and 32 on a representative fixture. For SFTP also vary request
concurrency (64, 128, 256) and packet size (32Ki, then up to 255Ki only after
server compatibility verification). Choose a small number of combinations,
then rerun selected settings as separate labelled tools with three repetitions.
Local-to-local multithread copies are disabled by default in rclone; an explicit
`--multi-thread-streams` control can test large mounted-file copies later.

Calibrate scale so each accepted repetition lasts at least three seconds,
preferably tens of seconds for bulk transfers. Keep the same fixture, seed,
starting destination and cache protocol across tools. Interleave repetitions;
do not suppress failures, timeouts, or losses. `slow_cutoff = 0` prevents dropping
repeats merely because another tool was much faster. Record source and destination
mounts, RTT, CPU, link capacity, client/server identities and uncontrolled cache
state. Separate default results, tuned results and diagnostic probes. Buffered
command completion is not a crash-durability guarantee.
