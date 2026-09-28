# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: AGPL-3.0-only
# The tools image: everything a make target needs besides Docker, make and git.
# Python + uv, Node + pnpm, openssl, git, pre-commit and the AWS CLI (for S3 inspection).
# The repo is bind-mounted at /repo; caches live in /repo/.cache so files stay
# owned by the host user.

ARG PYTHON_IMAGE=python:3.13.15-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
ARG NODE_IMAGE=node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}
ARG PNPM_VERSION=12.6.0
ARG PRE_COMMIT_VERSION=4.6.2
ARG AWSCLI_VERSION=1.46.1

RUN apt-get update \
 && apt-get install --no-install-recommends -y git openssl ca-certificates curl make \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install --global "pnpm@${PNPM_VERSION}" \
 && npm cache clean --force

ENV UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_PYTHON_DOWNLOADS=never
RUN uv tool install "pre-commit==${PRE_COMMIT_VERSION}" \
 && uv tool install "awscli==${AWSCLI_VERSION}"

# Runtime caches and the tools venv live under the bind-mounted repo.
ENV HOME=/tmp/home \
    UV_CACHE_DIR=/repo/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/repo/.cache/venv \
    UV_LINK_MODE=copy \
    PRE_COMMIT_HOME=/repo/.cache/pre-commit \
    pnpm_config_store_dir=/repo/.cache/pnpm-store \
    COREPACK_ENABLE_STRICT=0 \
    PICKWISE_ALEMBIC_INI=/repo/apps/backend/alembic.ini
RUN mkdir -p /tmp/home && chmod 1777 /tmp/home \
 && git config --system --add safe.directory '*'

WORKDIR /repo
