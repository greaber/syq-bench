#!/bin/sh
set -eu

if [ -z "${BENCH_SSH_PRIVATE_KEY_B64:-}" ] || [ -z "${BENCH_SSH_AUTHORIZED_KEY:-}" ]; then
    echo "benchmark SSH credentials are missing" >&2
    exit 1
fi

printf '%s' "$BENCH_SSH_PRIVATE_KEY_B64" | base64 -d > /root/.ssh/benchmark
printf '%s\n' "$BENCH_SSH_AUTHORIZED_KEY" > /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/benchmark /root/.ssh/authorized_keys

# Fly's root filesystem is intentionally bandwidth-limited. Mount a benchmark-
# owned tmpfs so this campaign measures the private network and tools instead.
mount -t tmpfs -o size=1G,mode=0755 tmpfs /memory
mkdir -p /memory/syq-bench

case "${1:-}" in
    source)
        exec sleep infinity
        ;;
    destination)
        ssh-keygen -A
        # Fly requires services reached over 6PN to listen on the Machine's
        # private address. Docker-based local verification has no Fly address.
        listen_address="${FLY_PRIVATE_IP:-0.0.0.0}"
        iperf3 --server --daemon --bind "$listen_address" --port 5201
        exec /usr/sbin/sshd -D -e -p 2222 \
            -o "ListenAddress=$listen_address" \
            -o PasswordAuthentication=no \
            -o PermitRootLogin=prohibit-password
        ;;
    *)
        echo "usage: syq-bench-node source|destination" >&2
        exit 2
        ;;
esac
