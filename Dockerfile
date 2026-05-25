# Matrix monorepo Dockerfile for Python services.
#
# Parameterized by SERVICE build arg. The same image template is used for
# ingestion, graph, agent, backtest, reflection, labs.
#
# Stages:
#   base    — Python + system deps + uv + watchfiles
#   deps    — only pyproject.toml + uv.lock copied; runs uv sync to cache
#             dependency layer separately from source changes
#   runtime — full source copied, used in prod
#
# In dev, compose mounts ./services/<SERVICE>/src and ./packages/python-shared/src
# directly into this image's workspace so source edits propagate without rebuild.
# watchfiles (installed in base) restarts the process when *.py files change.

ARG PYTHON_IMAGE=python:3.13-slim
ARG SERVICE

# -----------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS base
ARG SERVICE
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/workspace/services/${SERVICE}/.venv \
    PATH="/workspace/services/${SERVICE}/.venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv watchfiles

WORKDIR /workspace

# -----------------------------------------------------------------------------
FROM base AS deps
ARG SERVICE

# Copy the entire shared package (needed at sync time because it's a path dep).
COPY packages/python-shared /workspace/packages/python-shared

# Copy other path deps that some services need (graph imports for agent/labs).
# Cheap; small directories.
COPY services/graph/pyproject.toml /workspace/services/graph/
COPY services/graph/src /workspace/services/graph/src
COPY services/agent/pyproject.toml /workspace/services/agent/
COPY services/agent/src /workspace/services/agent/src

# Service metadata + sync deps. The lockfile pins everything, so this layer
# only rebuilds when pyproject.toml / uv.lock change.
COPY services/${SERVICE}/pyproject.toml services/${SERVICE}/uv.lock /workspace/services/${SERVICE}/

WORKDIR /workspace/services/${SERVICE}
RUN uv sync --frozen --no-install-project

# -----------------------------------------------------------------------------
FROM deps AS runtime
ARG SERVICE

# Now bring in the actual source for the target service. This is the only
# layer that invalidates on source-only changes — keeps rebuilds fast.
COPY services/${SERVICE}/src /workspace/services/${SERVICE}/src

WORKDIR /workspace/services/${SERVICE}
RUN uv sync --frozen

# dev_agent additionally needs Node.js + the Claude Code CLI because
# claude_agent_sdk (Python) spawns the `claude` binary as its transport.
# Other services skip this step (no-op if SERVICE != dev_agent).
USER root
RUN if [ "${SERVICE}" = "dev_agent" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            git \
        && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
        && apt-get install -y --no-install-recommends nodejs \
        && rm -rf /var/lib/apt/lists/* \
        && npm install -g @anthropic-ai/claude-code ; \
    fi

# Generic entrypoint: `uv run python -m <module>`. Compose passes the module
# (e.g. ingestion.main, ingestion.news, agent.main, labs.main) + args.
ENTRYPOINT ["uv", "run", "python", "-m"]
CMD ["--version"]
