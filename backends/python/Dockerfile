FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Dependencies first (cached layer), then the package itself. The second sync
# installs the project so the `syncplex` console script exists.
COPY pyproject.toml uv.lock ./

# --no-default-groups excludes dev AND the drive-sync group (whose
# readable-utils dep lives on a private git remote this build can't reach).
RUN uv sync --frozen --no-default-groups --no-install-project

COPY engine/ ./engine/
COPY drive_sync/ ./drive_sync/

RUN uv sync --frozen --no-default-groups

# Venv on PATH so `docker exec -it syncplex_web syncplex users ...` works as
# documented in README "Web UI: login, users & roles" (account management
# happens via exec into this container).
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8788

# Inventory path comes from SYNCPLEX_HOSTS (compose mounts hosts.json at
# /config/hosts.json); API keys/tokens come from the container environment.
# Run the venv entry point directly — `uv run` would re-sync (incl. dev deps)
# on every container start.
CMD ["/app/.venv/bin/syncplex", "web", "--host", "0.0.0.0", "--port", "8788"]
