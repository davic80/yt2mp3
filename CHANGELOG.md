# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.2.1] - 2026-03-20

### Security
- All `/db` routes (panel, login, logout, WebAuthn auth and registration) now require
  local network access (RFC-1918 + loopback). Previously only passkey registration was
  local-only; authentication and the panel itself were reachable from the internet.

### Fixed
- **mailer crash**: `send_download_notification` was passing a SQLAlchemy model instance
  to a background thread. After `db.session.commit()` SQLAlchemy expires all attributes,
  and the mailer thread had no Flask app context to reload them, causing a
  `RuntimeError: Working outside of application context`. Fixed by serialising the record
  to a plain `dict` before spawning the mailer thread.
- **dead code in downloader**: removed `db.session.get(Download, None)` (line 74) which
  always returned `None` and emitted a `SAWarning` on every download.
- **mailer logs invisible**: `logger.debug` skip message promoted to `logger.warning` so
  SMTP misconfiguration is always visible. Added a `StreamHandler` to `stdout` in
  `create_app()` so all `app.*` logger output appears in `docker logs`.

---

## [1.2.0] - 2026-03-20

### Added
- Email notifications on every completed download via **Gmail SMTP** (HTML email, non-blocking)
  - New `app/mailer.py`: sends a styled HTML email with download details (title, IP, browser, OS, device, fingerprint, tracking cookies)
  - New environment variables: `ADMIN_EMAIL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
  - Errors are logged only — a mail failure never affects the download response
- `docker-compose.yml`: exposed port `5000:5000` on the `app` service for local network access (`http://<raspberry-pi-ip>:5000`)

### Changed
- Logo `2` separator color changed from neon green `#39FF14` to muted green `#27a008` across all views (`style.css`, `admin/login.html`, `admin/index.html`)
- `#btn-magic` button gets `margin-top: 0.5rem` for better visual separation from the URL input
- `README.md` rewritten in English; domain-specific references replaced with `<subdomain>.<domain>` placeholders
- `CHANGELOG.md` translated to English (all entries)
- `.env.example` translated to English; SMTP/email variables added

---

## [1.1.0] - 2026-03-20

### Added
- Admin panel at `/db` protected with **Passkey / WebAuthn**
  - Passkey registration (Face ID, Touch ID, YubiKey, Windows Hello) **restricted to local network only** — never reachable from the internet
  - Passwordless authentication with 8-hour signed session
  - Three login page states: register (local, no credentials), authenticate (has credentials), blocked (remote, no credentials — does not reveal that registration exists)
  - Option to add additional passkeys from local network only
  - Emergency recovery via CLI command on the Raspberry Pi
- Paginated downloads table (25 rows/page) with expandable full-detail row
  - Summary columns: ID, date, IP, URL, browser, OS, device, language, fingerprint, tracking cookie indicators
  - Detail row: raw User-Agent, all cookies JSON, fingerprint components, job status, MP3 file
  - Visual indicators (color dots) for presence of Meta `_fbp`/`_fbc`, Google Analytics `_ga`, Instagram `ig_did` cookies
- New database models: `AdminUser`, `WebAuthnCredential`, `WebAuthnChallenge`
- New environment variables: `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`, `SESSION_COOKIE_SECURE`
- Semantic versioning with CHANGELOG, automatic GitHub Releases, and tagged Docker images
- `docker-compose.yml` accepts `IMAGE_TAG` variable to select a specific container version (`IMAGE_TAG=1.0.0 docker compose up -d`)

### Changed
- `docker-compose.yml`: image now uses `${IMAGE_TAG:-latest}` instead of a fixed `:latest`
- `docker-compose.yml`: WebAuthn environment variables added to the `app` service
- GitHub Actions: workflow now creates an automatic GitHub Release on every `v*` tag with a CHANGELOG extract and Docker image metadata

---

## [1.0.0] - 2026-03-20

> Documentation tag — does not produce a Docker image in GHCR.

### Added
- Flask web app to convert YouTube URLs to MP3
- Best-quality audio download via **yt-dlp** + **ffmpeg** (VBR quality 0)
- Background download jobs with `threading`, 1.5 s frontend status polling
- Animated progress bar with states: analyzing → downloading → converting → done
- Direct MP3 download link with video title as `Content-Disposition`
- **Dark mode UI**: `#1c1c1c` background, neon green `#39FF14` input and button, Inter font, "magia" button
- **SQLite** database (SQLAlchemy) with `downloads` table and 20+ metadata fields
- Visitor metadata collection:
  - Real IP via `CF-Connecting-IP` (Cloudflare) with fallback to `X-Forwarded-For`
  - Parsed User-Agent: browser, version, OS, OS version, device type, is_mobile, is_bot
  - `Accept-Language` and `Referer` HTTP headers
- Client-side **browser fingerprinting** (no external dependencies):
  - Canvas fingerprint, WebGL renderer, screen resolution, color depth
  - Timezone (name + offset), hardware concurrency, device memory, max touch points
  - Browser plugin list
  - Aggregated numeric hash as `visitorId`
- **Tracking cookie** capture (non-HttpOnly):
  - Meta Pixel: `_fbp`, `_fbc`
  - Google Analytics: `_ga`, `_ga_*` (session ID)
  - Instagram: `ig_did`
  - All cookies serialized as JSON in the `cookies_json` field
- **Rate limiting** per IP via Flask-Limiter: 10 downloads/hour, 3 downloads/minute
- HTTP 429 response with user-friendly message rendered in the UI
- REST API:
  - `POST /download` — accepts URL + fingerprint + cookies, returns `job_id`
  - `GET /status/<job_id>` — job progress and status
  - `GET /files/<job_id>` — serves the MP3 for direct download
- **Dockerfile**: `python:3.12-slim` + ffmpeg + gunicorn (2 workers, 4 threads, 300 s timeout)
- **Docker Compose**: `app` + `cloudflared` tunnel services, healthcheck, persistent volumes for downloads and database
- **GitHub Actions**: multi-arch build (`linux/amd64` + `linux/arm64`) → `ghcr.io/davic80/yt2mp3`
- Cloudflare Tunnel deployment support
