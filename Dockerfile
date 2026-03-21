FROM python:3.12-slim

# System dependencies: ffmpeg for audio conversion
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Always pull latest yt-dlp to avoid YouTube breakage
RUN pip install --no-cache-dir -U yt-dlp

# Copy application code
COPY app/ ./app/
COPY static/ ./static/
COPY wsgi.py .

# Create directories (will be overridden by volumes in production)
RUN mkdir -p /app/downloads /app/database

EXPOSE 5000

# Build-time arg for commit SHA (passed by GitHub Actions)
ARG GIT_COMMIT=dev
ARG APP_VERSION=2.0.2

ENV FLASK_APP=wsgi.py \
    FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    GIT_COMMIT=${GIT_COMMIT} \
    APP_VERSION=${APP_VERSION}

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "300", "wsgi:app"]
