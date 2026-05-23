# Postgres 16 with both Apache AGE (graph) and pgvector (vector search).
# Base: apache/age PG16 — already has AGE compiled in.
# We layer pgvector source build on top.

FROM apache/age:release_PG16_1.6.0

USER root

# Build deps for pgvector
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        postgresql-server-dev-16 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install pgvector
ARG PGVECTOR_VERSION=v0.8.0
RUN git clone --depth 1 --branch ${PGVECTOR_VERSION} https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make OPTFLAGS="" \
    && make install \
    && rm -rf /tmp/pgvector

# Strip build deps to keep image lean
RUN apt-get purge -y --auto-remove build-essential git postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

USER postgres
