# Minimal endpoint for the containerized WAN lab: sshd + the comparison tools'
# runtime needs. Both lab containers use this image; the harness and tool
# binaries are bind-mounted at run time.
# python:3.13 matches the project's requires-python; trixie's glibc (2.41) is new enough for the
# bind-mounted syq/qcp/rsync binaries built on Ubuntu 24.04 (glibc 2.39).
FROM python:3.13-slim-trixie
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openssh-server rsync iproute2 iputils-ping ca-certificates \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
RUN printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' >> /etc/ssh/sshd_config
CMD ["/usr/sbin/sshd", "-D", "-e"]
