# Toolchain image for CI job containers (`container:` in the server workflows).
# With ACTIONS_RUNNER_REQUIRE_JOB_CONTAINER=true every step runs in this image,
# never in the runner container. Versions mirror The-Last-Exam/server/mise.toml;
# bump them together (the server repo enforces the match in
# tools/projectctl/test_images.py).
#
# Build and pin: docs/runbooks/arc-runners.md, "Rebuilding the toolchain image".
# The base is pinned by digest at build time with --build-arg BASE=<ref@digest>.
ARG BASE
FROM ${BASE}

ENV MISE_DATA_DIR=/opt/mise \
    MISE_CONFIG_DIR=/opt/mise/config \
    MISE_YES=1 \
    PATH=/opt/mise/shims:/usr/local/bin:/usr/bin:/bin

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git jq pkg-config libssl-dev unzip \
 && rm -rf /var/lib/apt/lists/*

ARG MISE_VERSION
RUN curl -fsSL "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64" \
      -o /usr/local/bin/mise \
 && chmod 0755 /usr/local/bin/mise

RUN mise use --global \
      go@1.27.1 rust@1.98.1 python@3.14.8 node@24.21.0 \
      protoc@34.1 protoc-gen-go@1.36.11 protoc-gen-go-grpc@1.5.1 \
      gomigrate@4.20.1 \
 && mise reshim \
 && chmod -R a+rX /opt/mise
