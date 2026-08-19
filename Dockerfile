# ── Stage 1: fetch the geolocation database ──────────────────────────────────
# Runs on the *build* platform so the 59 MB download and gunzip are not
# emulated under QEMU during the arm64 build.
#
# DB-IP City Lite is CC BY 4.0: free, no account, and redistributable inside
# this image — unlike MaxMind GeoLite2, which needs a licence key to download
# and whose licence forbids shipping it in a public image. Both are MMDB, so
# app/geo.py reads either one.
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS geoip

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Try this month first, fall back to last month — DB-IP publishes on the 1st
# and there is a window where the new file is not up yet.
#
# The previous month is "the day before the 1st of this month", not
# "1 month ago": GNU date resolves 2026-03-31 -1 month to 2026-03-03, which
# lands back in the same month and would make the fallback a no-op.
RUN set -eux; \
    this_month="$(date -u +%Y-%m)"; \
    last_month="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m)"; \
    for month in "$this_month" "$last_month"; do \
        url="https://download.db-ip.com/free/dbip-city-lite-$month.mmdb.gz"; \
        if curl -fsSL -o /tmp/geoip.mmdb.gz "$url"; then \
            echo "fetched $url"; \
            break; \
        fi; \
        echo "not available: $url"; \
    done; \
    test -s /tmp/geoip.mmdb.gz; \
    gunzip -c /tmp/geoip.mmdb.gz > /geoip.mmdb; \
    rm /tmp/geoip.mmdb.gz


# ── Stage 2: the application image ───────────────────────────────────────────
FROM python:3.12-slim

# System dependencies: ffmpeg for audio conversion, curl+unzip for Deno install
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Deno — JS runtime required by yt-dlp 2026+ to solve YouTube signature challenges
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Always pull latest yt-dlp to avoid YouTube breakage
RUN pip install --no-cache-dir -U yt-dlp

# Bundled geolocation database. Deliberately NOT /app/geoip — docker-compose
# mounts ./geoip over that path, and an empty host dir would hide this file.
COPY --from=geoip /geoip.mmdb /app/geoip-bundled/dbip-city-lite.mmdb

# Copy application code
COPY app/ ./app/
COPY static/ ./static/
COPY wsgi.py .

# Create directories (will be overridden by volumes in production)
RUN mkdir -p /app/downloads /app/database

EXPOSE 5000

# Build-time arg for commit SHA (passed by GitHub Actions)
ARG GIT_COMMIT=dev
ARG APP_VERSION=5.3.7

ENV FLASK_APP=wsgi.py \
    FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    GIT_COMMIT=${GIT_COMMIT} \
    APP_VERSION=${APP_VERSION}

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "300", "wsgi:app"]
