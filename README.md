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
- `CLOUDFLARE_TUNNEL_TOKEN` — see Cloudflare Tunnel section below
- `ADMIN_EMAIL` — email address where download notifications will be sent
- `SMTP_USER` / `SMTP_PASSWORD` — Gmail address and App Password
- `SMTP_FROM` — sender address shown in the notification email

### 3. Configure Cloudflare Tunnel

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust** → **Networks** → **Tunnels**
2. Create a new tunnel (Cloudflared type)
3. Copy the connector token and set it in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`
4. Under **Public Hostnames** for the tunnel, add:
   - **Subdomain:** `<subdomain>`
   - **Domain:** `<domain>`
   - **Service:** `http://app:5000`

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

---

## Structure

```
app/
  __init__.py      # Flask app factory
  models.py        # SQLAlchemy models (downloads table)
  routes.py        # Endpoints: GET /, POST /download, GET /status/<id>, GET /files/<f>
  downloader.py    # yt-dlp wrapper with background jobs (threading)
  fingerprint.py   # User metadata collection
  mailer.py        # Email notifications via Gmail SMTP
  admin_models.py  # AdminUser, WebAuthnCredential, WebAuthnChallenge models
  admin_routes.py  # WebAuthn endpoints + local-only guard + paginated admin view
  templates/
    index.html     # Main UI
    admin/
      login.html   # Passkey login (three states)
      index.html   # Downloads admin table
static/
  style.css
  app.js
Dockerfile
docker-compose.yml
.github/workflows/build-push.yml
```

---

## Admin panel

Available at `/db`. Protected with **WebAuthn / Passkey** (Face ID, Touch ID, YubiKey, Windows Hello).

- **Registration** is restricted to the local network — it is never reachable from the internet.
- **Authentication** works from any network.
- **Emergency recovery:** run the CLI command directly on the Raspberry Pi to delete all credentials and re-register.

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
