# Storage preparation (storage team, modules 6 and 7): buckets, lifecycle, policies, accounts, tests.
#   docker build -t storage-prepare:7 storage/prepare
# Python + boto3 (buckets, lifecycle, tests) and the RustFS rc client (accounts and policies),
# downloaded from its GitHub release, checksum verified.
FROM python:3.12-slim

ARG RC_VERSION=0.1.36
ARG RC_SHA256=4a8128911ccad4e7b481f26635a4cfd1ec064412210526e57ad2c748d356f3b7
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
  && cd /tmp \
  && curl -fsSLO "https://github.com/rustfs/cli/releases/download/v${RC_VERSION}/rustfs-cli-linux-amd64-v${RC_VERSION}.tar.gz" \
  && echo "${RC_SHA256}  rustfs-cli-linux-amd64-v${RC_VERSION}.tar.gz" | sha256sum -c - \
  && tar -xzf "rustfs-cli-linux-amd64-v${RC_VERSION}.tar.gz" \
  && install -m 0755 "$(find /tmp -type f -name rc | head -1)" /usr/local/bin/rc \
  && rm -rf /tmp/* && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "boto3==1.43.111"
RUN useradd --create-home storage
USER storage
WORKDIR /app
COPY prepare.py ./
CMD ["python", "prepare.py"]
