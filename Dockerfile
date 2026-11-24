# syntax=docker/dockerfile:1.7

# ---- build stage: resolve and install into a self-contained venv ----
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install ".[server]"

# ---- runtime stage ----
FROM python:3.13-slim AS runtime
ARG VERSION=dev
LABEL org.opencontainers.image.title="Pepite" \
      org.opencontainers.image.description="Free, open-source, local-first prospecting from public data" \
      org.opencontainers.image.source="https://github.com/aymaneprojects/leadcommons" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION}"

RUN groupadd --system --gid 10001 pepite \
    && useradd --system --uid 10001 --gid pepite --home-dir /data --shell /usr/sbin/nologin pepite \
    && mkdir -p /data && chown pepite:pepite /data

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PEPITE_HOME=/data

USER pepite
WORKDIR /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["pepite", "serve", "--host", "0.0.0.0", "--port", "8000"]
