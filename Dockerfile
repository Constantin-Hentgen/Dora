# syntax=docker/dockerfile:1
# Dora HTTP service. The same image also runs `dora-segments` (see docker-compose.yml).

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is cached as long as the lockfile does not change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra server --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra server --no-editable

FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/Constantin-Hentgen/Dora" \
      org.opencontainers.image.description="Dora: loop routes for running, hiking and cycling, on BRouter and OpenStreetMap" \
      org.opencontainers.image.licenses="MIT"
# uid 1000, like the BRouter image: both containers can share the segments volume.
RUN groupadd --gid 1000 dora \
    && useradd --uid 1000 --gid dora --home-dir /app --no-create-home dora \
    && mkdir -p /app /segments4 \
    && chown dora:dora /app /segments4
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    SEGMENTS_DIR=/segments4
WORKDIR /app
USER dora
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"]
CMD ["uvicorn", "dora_routes.server.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
