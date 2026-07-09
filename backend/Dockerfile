FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# git is required — the wiki is a git repo and the backend shells out to it.
# postgresql-client-17 provides pg_dump for `app/scripts/backup_to_s3.py`
# (the Helm chart's backup CronJob); it comes from the PGDG repo because
# pg_dump refuses servers newer than itself and Debian bookworm ships v15.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates libjemalloc2 postgresql-common \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/*

# Use jemalloc instead of glibc malloc to reduce memory fragmentation
ENV LD_PRELOAD=libjemalloc.so.2

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e .

COPY app ./app

# Bundled launcher binaries + AgentWikiLauncher.zip — populated by CI
# (docker-build-push.yml + nightly-build.yml) from the
# release-agentwiki-launcher-go workflow's signed/notarized artifact
# before docker build. Served by /api/installer/{app,binary,script}.
COPY static ./static

# Bundled onboarding wiki content. Copied into WIKI_DIR by
# `app/wiki/seed.py` on first boot when the wiki is empty, so fresh
# installs land on a populated instance.
COPY wiki_seed ./wiki_seed

# Bundled starter document templates (RFC, ADR, runbook, etc.).
# Read by `app/wiki/templates.py:seed_starter_templates_if_empty` on
# first boot when the document_templates table is empty.
COPY starter_templates ./starter_templates

EXPOSE 8080

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
