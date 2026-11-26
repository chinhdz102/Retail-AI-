# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: Build Vue 3 + Vite Kiosk UI (including Silero VAD + static catalog)
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend-builder

WORKDIR /build

# Install frontend dependencies with clean lockfile caching
COPY apps/kiosk-airi/package.json apps/kiosk-airi/package-lock.json ./apps/kiosk-airi/
RUN cd apps/kiosk-airi && npm ci

# Copy frontend source + catalog snapshot so prebuild generates /vad & /catalog-index.json
COPY apps/kiosk-airi ./apps/kiosk-airi
COPY data/thanh_lam_products.json ./data/thanh_lam_products.json

ENV VITE_API_BASE_URL=/api
RUN cd apps/kiosk-airi && npm run build

# ---------------------------------------------------------------------------
# Stage 2: Production Python 3.13 runtime (FastAPI + WS Azure Speech + ONNX)
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    FRONTEND_DIST_DIR=/app/static \
    CATALOG_REPOSITORY=snapshot \
    CATALOG_SNAPSHOT_PATH=data/thanh_lam_products.json \
    PORT=8000

# System libraries required by Azure Speech C++ SDK, ONNX Runtime, PyAV, Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libasound2 \
    libgomp1 \
    libssl3 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# Fast Python package installer
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /usr/local/bin/uv

WORKDIR /app

# Install Python project dependencies + Azure Speech SDK for live WebSocket STT
COPY pyproject.toml uv.lock ./
RUN uv pip install --no-cache -r pyproject.toml \
    && uv pip install --no-cache "azure-cognitiveservices-speech>=1.42,<2"

# Copy backend code, product catalog/knowledge JSON datasets, and optional local ONNX models
# (.dockerignore trims large offline-only models/CSVs/images; COPY data ./data also works on fresh git clones)
COPY backend ./backend
COPY data ./data

# Copy compiled frontend SPA assets from Stage 1
COPY --from=frontend-builder /build/apps/kiosk-airi/dist /app/static

# Non-root runtime user for security
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" >/dev/null || exit 1

CMD ["sh", "-c", "exec uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --ws-ping-interval 20 --ws-ping-timeout 20"]
