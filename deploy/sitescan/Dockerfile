# syntax=docker/dockerfile:1.7
# The site scan bot on its own: the same code as the app, without the web server extras.
# Build from the repository root:  docker build -f deploy/sitescan/Dockerfile -t pepite-sitescan .

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install .

FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.title="Pepite site scan" \
      org.opencontainers.image.source="https://github.com/aymaneprojects/leadcommons" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
RUN groupadd --system --gid 10001 sitescan \
    && useradd --system --uid 10001 --gid sitescan --home-dir /data --shell /usr/sbin/nologin sitescan \
    && mkdir -p /data && chown sitescan:sitescan /data
COPY --from=build /opt/venv /opt/venv
COPY deploy/sitescan/run.sh /usr/local/bin/sitescan-run
RUN chmod 0755 /usr/local/bin/sitescan-run
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PEPITE_HOME=/data
USER sitescan
WORKDIR /data
VOLUME /data
CMD ["sitescan-run"]
