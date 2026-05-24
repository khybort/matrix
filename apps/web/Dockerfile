# Matrix dashboard (Next.js 15 App Router).
#
# Stages:
#   deps    — pnpm install with cached lockfile
#   builder — `pnpm build` produces .next/standalone
#   runner  — minimal node image serving the standalone build
#
# Dev mode (compose override) mounts ./src and uses `pnpm dev` for HMR;
# this image is only the prod path.

ARG NODE_IMAGE=node:22-bookworm-slim

# -----------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
RUN corepack enable

# pnpm-lock + package.json sufficient to install; cache layer.
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./apps/web/
COPY package.json pnpm-workspace.yaml ./
WORKDIR /app/apps/web
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --prefer-offline

# -----------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app /app
COPY apps/web /app/apps/web
WORKDIR /app/apps/web
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# -----------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3030 \
    HOSTNAME=0.0.0.0

# Standalone output keeps the runtime tiny.
COPY --from=builder /app/apps/web/.next/standalone /app
COPY --from=builder /app/apps/web/.next/static /app/apps/web/.next/static
COPY --from=builder /app/apps/web/public /app/apps/web/public

EXPOSE 3030
CMD ["node", "apps/web/server.js"]

# -----------------------------------------------------------------------------
# Dev stage: full node_modules, source mounted by compose, HMR via `next dev`.
FROM ${NODE_IMAGE} AS dev
WORKDIR /app/apps/web
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1 \
    PORT=3030 \
    HOSTNAME=0.0.0.0
# package.json + lock from deps stage so pnpm install caches.
COPY --from=deps /app /app
EXPOSE 3030
CMD ["pnpm", "dev"]
