FROM rust:1.94-bookworm AS syq-build

ARG SYQ_REV
RUN test -n "$SYQ_REV" \
    && git clone --filter=blob:none https://github.com/greaber/syq.git /src/syq \
    && git -C /src/syq checkout --detach "$SYQ_REV" \
    && test "$(git -C /src/syq rev-parse HEAD)" = "$SYQ_REV"
WORKDIR /src/syq
RUN cargo build --locked --release

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        ca-certificates \
        iperf3 \
        iproute2 \
        iputils-ping \
        openssh-client \
        openssh-server \
        procps \
        rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/syq-bench
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY specs ./specs
COPY providers/fly/spec.toml ./providers/fly/spec.toml
COPY providers/fly/specs ./providers/fly/specs
RUN uv pip install --system --no-cache .

COPY --from=syq-build /src/syq/target/release/syq /usr/local/bin/syq
COPY providers/fly/node-entrypoint.sh /usr/local/bin/syq-bench-node
RUN chmod 0755 /usr/local/bin/syq /usr/local/bin/syq-bench-node \
    && mkdir -p /run/sshd /root/.ssh /tmp/syq-bench /memory/syq-bench \
    && chmod 0700 /root/.ssh

ENV PYTHONUNBUFFERED=1
ENV SYQ_NO_UPDATE_CHECK=1
ENTRYPOINT ["/usr/local/bin/syq-bench-node"]
