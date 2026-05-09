# Postgres 17 with two extensions baked in:
#   * pg_textsearch (https://github.com/timescale/pg_textsearch) — BM25
#     search. Must be loaded via `shared_preload_libraries`; that's set in
#     the docker-compose `command:` and would need a `postgresql.conf`
#     edit in any other deployment target.
#   * pgmq (https://github.com/pgmq/pgmq) — Postgres-backed message queue
#     used by app/tasks. Regular extension; CREATE EXTENSION is enough.
#
# Both are C/SQL extensions built with the standard PGXS Makefile, so this
# image only needs `postgresql-server-dev-17` and `build-essential`.
FROM postgres:17

ARG PG_TEXTSEARCH_REF=v1.1.0
ARG PGMQ_REF=v1.11.1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        postgresql-server-dev-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 --branch "$PG_TEXTSEARCH_REF" \
        https://github.com/timescale/pg_textsearch.git \
    && cd pg_textsearch \
    && make -j"$(nproc)" \
    && make install

RUN git clone --depth 1 --branch "$PGMQ_REF" \
        https://github.com/pgmq/pgmq.git \
    && cd pgmq/pgmq-extension \
    && make -j"$(nproc)" \
    && make install

WORKDIR /
RUN apt-get purge -y build-essential git postgresql-server-dev-17 \
    && apt-get autoremove -y \
    && rm -rf /build
