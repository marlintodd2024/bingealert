FROM python:3.11-slim

ARG BUILD_SHA=unknown
ARG BUILD_TAG=dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    BINGEALERT_BUILD_SHA=${BUILD_SHA} \
    BINGEALERT_BUILD_TAG=${BUILD_TAG}

LABEL org.opencontainers.image.source="https://github.com/marlintodd2024/bingealert"

# tini = proper PID 1 (reaps zombies, forwards signals to uvicorn)
# gcc kept for any source-build wheel fallback on arm64
# sqlite3 binary included for ad-hoc inspection during ops
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        sqlite3 \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so dep changes don't bust the app-code layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user. Host must `chown 1000:1000 ./data` before bringing the
# container up, or the SQLite file write will fail. Documented in README.
RUN groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid 1000 --home-dir /app app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
