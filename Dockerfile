# The hub in one image (docs/PLAN.md, S4). Every base image is pinned by the
# digest of its multi-arch index, so the same file builds on the Mac (arm64)
# and on the home server (amd64).

# ---- the dashboard ---------------------------------------------------------
# The node image frontend/npm.sh pins (node 24, Alpine). npm runs here, in a
# build stage, never on a host that holds credentials (S3c, C1).
FROM node@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
# The only step with a network: fetching exactly what the lock file says,
# with no install scripts (also in .npmrc; said here so it never hangs on
# where that file sits).
RUN npm ci --ignore-scripts
COPY frontend/ ./
# Building runs every package's code, so it gets no network (S4-7).
RUN --network=none npm run build

# ---- the hub ---------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
# Dependencies first, for the layer cache; --locked fails if uv.lock and
# pyproject.toml disagree instead of resolving anew.
COPY pyproject.toml uv.lock README.md ./
# uv and its download cache are mounted for these steps only, so neither
# lands in a layer.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
# Only these paths: never secrets/ or data/, whatever the build context holds
# (S4-8).
COPY src/ ./src/
COPY --from=web /app/src/xbloom_hub/web ./src/xbloom_hub/web
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
    && useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin hub

ENV PATH="/app/.venv/bin:$PATH" \
    XBLOOM_BIND=0.0.0.0 \
    XBLOOM_DATA_DIR=/data \
    XBLOOM_PROXY_PSK_FILE=/run/secrets/proxy_psk
USER 10001
EXPOSE 8377
# From inside, on loopback: the hub's allowed lists keep 127.0.0.0/8 and
# 127.0.0.1. /api/state is a plain read, so it needs no header. In the image, not
# only in compose.yaml, so a plain `docker run` (the smoke test) has it too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8377/api/state', timeout=3)"]
# The virtualenv's own script; `uv run` would write a cache the read-only root
# cannot hold.
CMD ["xbloom-hub"]
