# Multi-stage Dockerfile for apc-speech
# Базовый образ закреплён digest'ом (3.12-slim на момент пина; см. test_images_pinned).
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/0.7.12/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
# Compile and install wheel dependencies into virtual environment
ENV UV_COMPILE_BYTECODE=1
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"
RUN uv sync --frozen --no-dev


# Runtime Stage
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

WORKDIR /app

# Отключаем буферизацию stdout/stderr: без TTY (как в docker compose logs) Python по умолчанию
# блочно буферизует print() - если процесс когда-либо завершится по сигналу (OOM, таймаут
# оркестратора), последние строки лога теряются, что затрудняет диагностику сбоев init-job'ов
# (model-downloader и т.п.) и воркеров.
ENV PYTHONUNBUFFERED=1

# Install runtime system packages: FFmpeg and curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user
RUN groupadd -g 10001 apcgroup && \
    useradd -u 10000 -g apcgroup -s /bin/bash -m apcuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv"
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app:$PYTHONPATH"

# Copy application source and configs
COPY --chown=apcuser:apcgroup app/ /app/app/
COPY --chown=apcuser:apcgroup config/ /app/config/
COPY --chown=apcuser:apcgroup schemas/ /app/schemas/
COPY --chown=apcuser:apcgroup scripts/ /app/scripts/
COPY --chown=apcuser:apcgroup alembic/ /app/alembic/
COPY --chown=apcuser:apcgroup alembic.ini /app/
COPY --chown=apcuser:apcgroup openapi.yaml /app/
COPY --chown=apcuser:apcgroup pyproject.toml /app/

# Switch to non-root user
USER apcuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
