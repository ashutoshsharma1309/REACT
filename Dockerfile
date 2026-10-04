# REACT API image (Phase 6). Used unchanged by docker compose (local) and ECS Fargate (AWS).
#   * Base image pinned by digest, Python dependencies pinned by hash (requirements.lock): reproducible builds.
#   * Two stages: build tools never reach the runtime image.
#   * Runs as an unprivileged user. No secrets are baked in: configuration arrives as environment variables
#     at run time (ECS injects DB_USER / DB_PASSWORD from Secrets Manager).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000 \
    STORAGE_LOCAL_DIR=/app/var/artifacts
RUN groupadd --system --gid 10001 react && useradd --system --uid 10001 --gid react --home-dir /app --shell /usr/sbin/nologin react
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=root:root alembic.ini ./
COPY --chown=root:root alembic ./alembic
COPY --chown=root:root app ./app
COPY --chown=root:root scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod 0755 scripts/entrypoint.sh && mkdir -p /app/var/artifacts && chown -R react:react /app/var
USER react
EXPOSE 8000
# Liveness only (the load balancer uses /health too). /ready is for deployment verification.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request,sys; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4); sys.exit(0)"]
ENTRYPOINT ["./scripts/entrypoint.sh"]
