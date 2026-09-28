# Multi-stage Dockerfile for apc-speech
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
# Compile and install wheel dependencies into virtual environment
ENV UV_COMPILE_BYTECODE=1
ENV UV_PROJECT_ENVIRONMENT="/opt/venv"
RUN uv sync --frozen --no-dev


# Runtime Stage
FROM python:3.12-slim AS runtime

WORKDIR /app

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

# Copy application source and configs
COPY --chown=apcuser:apcgroup app/ /app/app/
COPY --chown=apcuser:apcgroup config/ /app/config/
COPY --chown=apcuser:apcgroup schemas/ /app/schemas/
COPY --chown=apcuser:apcgroup openapi.yaml /app/
COPY --chown=apcuser:apcgroup pyproject.toml /app/

# Switch to non-root user
USER apcuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
