# yt2mp3

Web app to convert YouTube videos to MP3.
Deployed at `<subdomain>.<domain>` via Cloudflare Tunnel from a Raspberry Pi.

---

## Deployment on Raspberry Pi

### 1. Clone the repository

```bash
git clone https://github.com/davic80/yt2mp3.git
cd yt2mp3
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `SECRET_KEY` — generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_EMAIL` — email address where download notifications will be sent
- `SMTP_USER` / `SMTP_PASSWORD` — Gmail address and App Password
- `SMTP_FROM` — sender address shown in the notification email

### 3. Configure Cloudflare Tunnel

The tunnel is **not** part of `docker-compose.yml`. A single host-network
`cloudflared` container serves every app on the Pi and reaches this one over the
published port `5000`, so that port mapping must stay.

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust** → **Networks** → **Tunnels**
2. Use the existing shared tunnel (or create one, Cloudflared type)
3. Under **Public Hostnames** for the tunnel, add:
   - **Subdomain:** `<subdomain>`
   - **Domain:** `<domain>`
   - **Service:** `http://localhost:5000`

Cloudflare sets `CF-Connecting-IP` on tunnelled requests, which is what the app
uses to tell real visitors apart from local-network (unauthenticated admin)
access — see `app/auth_utils.py`.

### 4. Start

```bash
# First time or after updating the image
docker compose pull

# Start in background
docker compose up -d

# Follow logs
docker compose logs -f
```

### 5. Update to a new version

```bash
docker compose pull && docker compose up -d
```

To pin a specific version:

```bash
IMAGE_TAG=1.2.0 docker compose up -d
```

---

## Local development

```bash
# Install dependencies (requires ffmpeg on the system)
pip install -r requirements.txt

# Start in development mode
FLASK_APP=wsgi.py FLASK_ENV=development flask run

# Or with Docker
docker build -t yt2mp3:dev .
docker run -p 5000:5000 \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/database:/app/database \
  -e SECRET_KEY=dev \
  yt2mp3:dev
```

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Covers YouTube URL parsing (`app/youtube_url.py`) and the `POST /download`
routing. No network access and no real yt-dlp calls — the downloader is
monkeypatched.

---

## Structure

```
app/
  __init__.py      # Flask app factory
  models.py        # SQLAlchemy models (downloads table)
  routes.py        # Endpoints: GET /, POST /download, GET /status/<id>, GET /files/<f>
  downloader.py    # yt-dlp wrapper with background jobs (threading)
  youtube_url.py   # YouTube URL parsing / normalization (stdlib only)
  fingerprint.py   # User metadata collection
  mailer.py        # Email notifications via Gmail SMTP
  admin_routes.py  # Admin panel routes + admin_or_local guard + paginated admin view
  templates/
    index.html     # Main UI
    admin/
      index.html   # Downloads admin table
static/
  style.css
  app.js
tests/
  test_youtube_url.py    # URL parsing
  test_download_route.py # POST /download routing
Dockerfile
docker-compose.yml
.github/workflows/build-push.yml
```

---

## Admin panel

Available at `/db`. Local network requests (RFC-1918 / loopback) have unrestricted access.
Remote users must be logged in with `is_admin=True` on their User record.

- **User management** at `/db/users` — toggle `is_admin` and `is_enabled` flags per user.
- **Analytics** at `/db/analytics`.
- **Emergency access:** connect to the local network or SSH to the Raspberry Pi.

---

## Geolocation

Country and city are resolved from the visitor IP against an MMDB database.
Two are consulted, in order:

1. **`GEOIP_PATH`** (default `/app/geoip/GeoLite2-City.mmdb`) — drop a MaxMind
   GeoLite2-City file into the mounted `./geoip` directory to use it. MaxMind
   needs a free account and a license key, and its licence forbids
   redistributing the file, so it cannot ship in the image.
2. **Bundled DB-IP City Lite** — baked into the image at build time from
   [db-ip.com](https://db-ip.com/db/download/ip-to-city-lite) (CC BY 4.0, no
   account required) and refreshed on every image build. Nothing to set up.

The bundled copy lives at `/app/geoip-bundled/`, deliberately **not** under
`/app/geoip` — docker-compose mounts `./geoip` over that path, and an empty
host directory would hide it.

If neither database is present the app runs normally with geolocation disabled.

---

## CI/CD

Every push to `main` triggers a GitHub Action that:

1. Builds the Docker image for `linux/amd64` and `linux/arm64`
2. Pushes it to `ghcr.io/davic80/yt2mp3:latest`

Every `v*` tag additionally creates a GitHub Release with the relevant CHANGELOG section and Docker image metadata.

---

## Database

SQLite at `/app/database/yt2mp3.db` (mounted as a volume).

`downloads` table — relevant fields:
- Request metadata (IP, parsed User-Agent, Accept-Language, Referrer)
- Browser fingerprint (canvas, WebGL, fonts, screen, timezone)
- Tracking cookies (Meta `_fbp`/`_fbc`, Google Analytics `_ga`, Instagram `ig_did`)
- Job state and path of the downloaded file
