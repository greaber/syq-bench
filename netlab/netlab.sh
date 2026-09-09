#!/bin/sh
# A self-contained two-container WAN emulation lab. Needs only ordinary Docker
# access, no sudo: each container gets NET_ADMIN over its OWN network namespace,
# so the netem is confined to the containers' interfaces and never alters a
# host interface's qdisc. (The Docker daemon still creates its usual bridge
# network and veth pairs, as for any container.)
#
#   sh netlab/netlab.sh up                     # build image, start lab-a (client) and lab-b (server)
#   sh netlab/netlab.sh impair "delay 131ms rate 1gbit limit 20000"
#   sh netlab/netlab.sh impair "delay 131ms loss 1% rate 1gbit limit 20000"
#   sh netlab/netlab.sh status                 # tc -s qdisc from both containers
#   sh netlab/netlab.sh run SPEC.toml OUT.json # run the harness inside lab-a against lab-b
#   sh netlab/netlab.sh down                   # remove this checkout's containers, network, and state
#
# Ownership: every `up` mints a random lab token, stores it in netlab/.state/.netlab-state
# (gitignored) and labels the containers and network with it. Every other subcommand, and
# `up` itself when the fixed names are already taken, requires the exact token: a lab started
# from another checkout is never touched, and `up` refuses rather than replacing it. `up` and
# `down` hold a host-wide lock (a Docker container name, which the daemon allocates atomically)
# for their whole critical section, so two checkouts cannot race each other, and every removal
# re-checks the token immediately before it happens. `up` does its checks and the image build
# before anything is removed, and stages the new state (fresh ssh keypair, generated inside the
# image) in a private temporary directory that is deleted on any failure, so a failure before
# the old lab is retired leaves it intact. A failure while the replacement is being started
# leaves no usable lab: run `up` again (the half-started containers carry the new token and are
# replaced). Run results live only where `run`'s OUT argument put them; `up` keeps nothing from
# the old state.
#
# SYQ_BIN, QCP_BIN, RSYNC_BIN bind-mount alternate binaries at /usr/local/bin, shadowing the
# system ones for plain "syq"/"qcp"/"rsync" in specs. The impairment is applied symmetrically
# on each container's eth0 egress, so "delay 131ms" gives a 262 ms RTT and "loss 1%" drops 1 %
# in each direction. Both endpoints share the host's CPU and disk: results compare tools under
# identical impairment; they are not absolute hardware numbers, and the run's recorded network
# state (qdiscs, ping) says netem is active.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
state="$here/.state"
sentinel="$state/.netlab-state"
NET=syqlab
IMG=syq-netlab
A=syq-lab-a
B=syq-lab-b
LOCK=syq-netlab-lock
LABEL=io.syq-bench.netlab

dexec() { c=$1; shift; docker exec "$c" "$@"; }

# Host-wide mutex: creating a container with a fixed name either succeeds or fails atomically.
# Cleanup runs on EXIT only; INT and TERM are turned into an exit first, because POSIX sh would
# otherwise resume the script after the trap and carry on past the released lock.
staged=""
cleanup() {
  docker rm -f "$LOCK" >/dev/null 2>&1 || true
  [ -z "$staged" ] || rm -rf "$staged"  # an abandoned staging directory holds a private key
}
lock() {
  if ! docker create --name "$LOCK" --label "$LABEL=lock" "$img" true >/dev/null 2>&1; then
    echo "another netlab up/down is in progress (if none is, remove the stale lock: docker rm $LOCK)" >&2
    exit 1
  fi
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}
token() { cat "$sentinel" 2>/dev/null || true; }
exists() { docker inspect "$1" >/dev/null 2>&1; }
net_exists() { docker network inspect "$1" >/dev/null 2>&1; }
label_of() { docker inspect -f "{{index .Config.Labels \"$LABEL\"}}" "$1" 2>/dev/null || true; }
netlabel_of() { docker network inspect -f "{{index .Labels \"$LABEL\"}}" "$1" 2>/dev/null || true; }

# Subcommands other than `up` act only on a lab this checkout started: the sentinel token must
# match the label on both containers.
require_owned() {
  t=$(token)
  [ -n "$t" ] || { echo "no lab state in $state; run 'up' first" >&2; exit 1; }
  for c in $A $B; do
    [ "$(label_of "$c")" = "$t" ] || {
      echo "container $c is missing or belongs to another netlab checkout; run 'up' here" >&2; exit 1; }
  done
}

case "${1:-}" in
  up)
    old=$(token)
    # Use the immutable image ID from this build everywhere below: another checkout may retag
    # $IMG with a different build at any moment, and a name would silently follow it.
    img=$(docker build -q -t "$IMG" "$here")
    lock
    # 1. Read-only checks. Anything holding our fixed names must carry this checkout's token.
    if [ -d "$state" ] && [ ! -f "$sentinel" ]; then
      echo "refusing: $state exists without a netlab sentinel" >&2; exit 1
    fi
    for c in $A $B; do
      if exists "$c"; then
        [ -n "$old" ] && [ "$(label_of "$c")" = "$old" ] || {
          echo "container $c exists and was not started by this checkout's netlab; refusing" >&2; exit 1; }
      fi
    done
    if net_exists "$NET"; then
      [ -n "$old" ] && [ "$(netlabel_of "$NET")" = "$old" ] || {
        echo "network $NET exists and was not created by this checkout's netlab; refusing" >&2; exit 1; }
    fi
    # 2. Stage the new state (fresh token and keypair) in a private temporary directory.
    new=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    staged=$(mktemp -d "$here/.state.XXXXXX")
    docker run --rm --label "$LABEL=$new" -v "$staged:/lab" "$img" ssh-keygen -q -t ed25519 -N "" -f /lab/id_ed25519
    printf '#!/bin/sh\nexec ssh -i /lab/id_ed25519 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/lab/known_hosts "$@"\n' > "$staged/ssh"
    chmod +x "$staged/ssh"
    printf '%s\n' "$new" > "$staged/.netlab-state"
    # 3. Only now retire the old lab, re-checking the token right before each removal (the lock
    #    already excludes a concurrent up; this is defence in depth), and commit the staged state.
    for c in $A $B; do
      if exists "$c"; then
        [ "$(label_of "$c")" = "$old" ] || { echo "container $c changed hands; refusing" >&2; exit 1; }
        docker rm -f "$c" >/dev/null
      fi
    done
    if net_exists "$NET"; then
      [ "$(netlabel_of "$NET")" = "$old" ] || { echo "network $NET changed hands; refusing" >&2; exit 1; }
      docker network rm "$NET" >/dev/null
    fi
    [ ! -d "$state" ] || rm -rf "$state"
    mv "$staged" "$state"
    staged=""  # committed: no longer ours to delete on exit
    docker network create --label "$LABEL=$new" "$NET" >/dev/null
    for c in $A $B; do
      docker run -d --name "$c" --network "$NET" --cap-add NET_ADMIN --label "$LABEL=$new" \
        -v "$repo/src:/harness/src:ro" -v "$state:/lab" \
        ${SYQ_BIN:+-v "$SYQ_BIN":/usr/local/bin/syq:ro} \
        ${QCP_BIN:+-v "$QCP_BIN":/usr/local/bin/qcp:ro} \
        ${RSYNC_BIN:+-v "$RSYNC_BIN":/usr/local/bin/rsync:ro} \
        "$img" >/dev/null
      docker exec "$c" sh -c 'cat /lab/id_ed25519.pub > /root/.ssh/authorized_keys'
    done
    dexec $A /lab/ssh root@$B true
    echo "lab up: $A (client, harness at /harness) -> $B (server); ssh wrapper /lab/ssh; token ${new%????????????????????????}..."
    ;;
  impair)
    require_owned
    for c in $A $B; do
      dexec $c tc qdisc del dev eth0 root 2>/dev/null || true
      # shellcheck disable=SC2086
      dexec $c tc qdisc add dev eth0 root netem $2
    done
    dexec $A ping -c 3 -q $B | tail -2
    ;;
  clear)
    require_owned
    for c in $A $B; do dexec $c tc qdisc del dev eth0 root 2>/dev/null || true; done
    echo cleared
    ;;
  status)
    require_owned
    for c in $A $B; do echo "== $c"; dexec $c tc -s qdisc show dev eth0; done
    ;;
  run)
    require_owned
    spec=$2; out=$3
    stamp=$(date +%s)
    docker cp "$spec" $A:/lab/spec.toml >/dev/null
    dexec $A mkdir -p /tmp/syq-bench
    rc=0
    dexec $A env PYTHONPATH=/harness/src python3 -m syq_bench.cli run /lab/spec.toml -y --out "/lab/out-$stamp.json" || rc=$?
    # The result file is copied out whatever the exit code: a failed case is still data.
    docker cp "$A:/lab/out-$stamp.json" "$out" >/dev/null
    exit $rc
    ;;
  down)
    t=$(token)
    [ -n "$t" ] || { echo "no lab state in $state; nothing to remove"; exit 0; }
    img=$(docker image inspect -f '{{.Id}}' "$IMG" 2>/dev/null || docker build -q -t "$IMG" "$here")
    lock
    for c in $A $B; do
      if exists "$c"; then
        if [ "$(label_of "$c")" = "$t" ]; then docker rm -f "$c" >/dev/null; else echo "leaving $c: another checkout's lab" >&2; fi
      fi
    done
    if net_exists "$NET"; then
      if [ "$(netlabel_of "$NET")" = "$t" ]; then docker network rm "$NET" >/dev/null; else echo "leaving network $NET: another checkout's lab" >&2; fi
    fi
    rm -rf "$state"
    echo "lab removed"
    ;;
  *) echo "usage: sh $0 up|impair 'NETEM ARGS'|clear|status|run SPEC OUT|down" >&2; exit 2 ;;
esac
