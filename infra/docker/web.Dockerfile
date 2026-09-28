# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: AGPL-3.0-only
# Web image. Targets: dev (next dev against the bind-mounted repo) and prod
# (Next.js standalone output, non-root). Build context: repository root.

ARG NODE_IMAGE=node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe

FROM ${NODE_IMAGE} AS base
ARG PNPM_VERSION=12.6.0
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install --global "pnpm@${PNPM_VERSION}" && npm cache clean --force

# --- dev: dependencies are installed into the bind mount at container start
FROM base AS dev
ENV HOME=/tmp/home pnpm_config_store_dir=/repo/.cache/pnpm-store
RUN mkdir -p /tmp/home && chmod 1777 /tmp/home
WORKDIR /repo
EXPOSE 3000
CMD ["sh", "-c", "pnpm install --frozen-lockfile && pnpm --filter @pickwise/web dev"]

# --- build
FROM base AS build
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json tsconfig.base.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/api-client/package.json packages/api-client/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY apps/web apps/web
COPY packages packages
RUN pnpm --filter @pickwise/web build

# --- prod
FROM ${NODE_IMAGE} AS prod
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
