# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.6.6] - 2026-03-21

### Added
- **"Eliminar seleccionados" button in admin panel.** When one or more rows are
  checked a red "Eliminar seleccionados (N)" button appears in the top bar.
  Clicking it shows a `confirm()` dialog; on confirmation it calls the new
  `POST /db/delete` endpoint which removes the MP3 file from disk and the DB
  row for each selected job. Missing MP3 files are silently ignored. The table
  refreshes automatically on success. Works for any job status.
- New endpoint `POST /db/delete` (`@local_only @login_required`): accepts
  `{"job_ids": [...]}` and returns `{"deleted": N}`.

---

## [1.6.5] - 2026-03-21

### Changed
- **Removed playlist support entirely.** URLs containing both `list=` and `v=`
  parameters now download only the single video (playlist params are stripped
  before passing the URL to yt-dlp). Bare playlist URLs (`?list=PL…` with no
  `v=`) return HTTP 400 with the message
  _"Las listas no están soportadas. Pega la URL de una canción concreta."_

### Removed
- `POST /playlist-zip` endpoint — no longer needed.
- `playlist_url` column removed from ORM model (`models.py`); the physical
  column remains in the SQLite DB but is no longer read or written by the app.
- All playlist UI: confirmation banner, ZIP/individual-mode flows, scrollable
  track list, and associated CSS (`.playlist-links`, `.playlist-track-link`).
- `playlist` column removed from admin panel table and detail view.

---

## [1.6.4] - 2026-03-21

### Added
- **Playlist download modes**: when a playlist URL is submitted, a confirmation banner
  now offers two choices:
  - **Descargar ZIP** — waits for all tracks to finish, then builds and streams a ZIP
    via the new public endpoint `POST /playlist-zip`.
  - **Canción por canción** — polls all jobs as before; on completion shows a scrollable
    list of individual `↓ Title.mp3` download links (max-height 240 px, styled scrollbar)
    plus a "↓ descargar todo .zip" button at the bottom that triggers the same ZIP endpoint.
- `POST /playlist-zip` — new public (rate-limited) endpoint that accepts `{"job_ids": [...]}`
  and returns a ZIP of all completed tracks. Deduplicates filenames to avoid archive
  conflicts. Does not require admin authentication.

### Fixed
- **Progress bar loops back to "analyzing"**: `_progress_hook` in `downloader.py` now
  only advances the progress value — never resets it. FFmpeg post-processing fires new
  `downloading` events with `downloaded_bytes=0` which previously snapped the bar back
  to 0 mid-way through conversion.

---

## [1.6.3] - 2026-03-21

### Fixed
- **Progress bar loops back to "analyzing…" / "almost ready…"**: yt-dlp fires new
  `downloading` hook events with `downloaded_bytes=0` during FFmpeg post-processing,
  causing the progress percentage to reset. `_progress_hook` now only ever moves
  progress forward (monotonic).

---



### Fixed
- **Single video with `list=` param hangs forever**: URLs like `watch?v=X&list=Y` are now
  stripped of `list=`, `index=`, and `start_radio=` params before being passed to yt-dlp.
  Recent yt-dlp versions attempt to resolve playlist metadata even when `noplaylist=True`
  is set, causing the download thread to hang indefinitely. The original URL is still
  stored in the DB (`youtube_url` field) for reference.
- **`watch?v=X&list=Y` always treated as a single video**: `_has_playlist()` in
  `routes.py` and `isPlaylistOnly()` in `app.js` now trigger on **any** `list=` param,
  regardless of whether a `v=` param is also present. Previously, pasting a URL like
  `watch?v=X&list=Y` would silently download only the single video (and then hang);
  now it shows the playlist confirmation banner and expands the full playlist.

---

## [1.6.1] - 2026-03-21

### Fixed
- **Download jobs stuck at "pending" forever** (silent thread crash): after
  `db.session.commit()` SQLAlchemy expires all ORM attributes. The background download
  thread later accessed `record.job_id` and other fields outside a session, triggering a
  `DetachedInstanceError` that was silently swallowed, leaving the job in `pending` state
  permanently. Fixed in `downloader.py` by snapshotting all required record fields into a
  plain `dict` before the `commit()`, and wrapping the error-path DB update in its own
  `try/except` so a secondary DB failure can't mask the original error.

---

## [1.6.0] - 2026-03-21

### Added
- **YouTube playlist support**: pasting a playlist URL (`?list=PL...`) downloads every
  track individually. Each track gets its own DB row with `playlist_url` stored for
  reference. Frontend shows a confirmation banner before starting, then a live
  `Downloading: X / N` counter as tracks complete.
- **`playlist_url` DB column** (`TEXT`, nullable) with automatic inline migration on
  startup. Displayed in the admin table as a `▶` icon link (col 16); full URL also shown
  in the expanded detail row.
- **Country column** in the admin table (col 6, after IP): shows `country_code` from
  MaxMind geo lookup, with city shown on hover.
- **Retroactive geo migration** (`_migrate_geo` background thread in `__init__.py`):
  fills `country_code` and `city` for all existing rows that have an `ip_address` but
  no geo data yet. Runs as a daemon thread on every app start, same pattern as
  `_migrate_hardware`.
- **Analytics fix**: replaced `func.strftime` with `func.date` in the downloads-per-day
  query so Chart.js line chart renders correctly with SQLite's `DateTime` storage format.

### Changed
- Admin table column order: `date | ip | country | title | browser | os | device | model
  | identity | language | fingerprint | bot | playlist | url` (17 columns, colspan 17).
- All admin table column headers renamed to English.
- Detail row labels also renamed to English.
- `POST /download` now always returns `{"job_ids": [...]}` (array). Single-video
  requests return a one-element array; playlist requests return N elements.
- `app.js` updated to handle `job_ids` array; single-video UX is unchanged.
- Mailer notification dict updated: removed stale cookie fields (`fb_fbp`, `fb_fbc`,
  `ga_client`, `ig_did`); added `playlist_url`, `country_code`, `city`, `bot_score`.
- YouTube URL regex in `routes.py` extended to accept `playlist?list=` URLs.

---

## [1.5.0] - 2026-03-21

### Added
- **Analytics dashboard** at `/db/analytics`: summary stats (total, done, errors, success
  rate), line chart of downloads per day (full history), top-10 songs bar chart, top-10
  countries bar chart. Built with Chart.js 4 (CDN), dark theme neon green palette.
  Navigation link added to the admin panel top bar.
- **Bot score** (`app/bot_score.py`): heuristic 0–100 score saved per download.
  Signals: headless/automation keywords in UA (+40), `ua_is_bot` from UA parser (+30),
  absent fingerprint (+20), known headless WebGL renderer (+20), compound signal (+10).
  Displayed in the admin table with colour-coded badge (green / yellow / red).
- **IP geolocation** (`app/geo.py`): MaxMind GeoLite2-City `.mmdb` lookup, saving
  `country_code` and `city` per download. Gracefully disabled when the database file is
  absent. `GEOIP_PATH` env var (default `/app/geoip/GeoLite2-City.mmdb`).
- Three new DB columns (`bot_score INTEGER`, `country_code VARCHAR(2)`, `city VARCHAR(128)`)
  with automatic inline migration on startup.
- **"cookie free · invite me to a coffee ☕"** footer on the public page, linking to the
  PayPal donation campaign.

### Removed
- **All cookie tracking code** — completely removed from the codebase:
  - DB columns dropped from model: `cookies_json`, `fb_fbp`, `fb_fbc`, `ga_client`,
    `ga_session`, `ig_did`
  - `fingerprint.py`: removed `_parse_cookies`, `client_cookies` param, all cookie logic
  - `routes.py`: no longer passes `client_cookies` to `collect()`
  - `app.js`: removed `getCookiesAsObject()` and `_cookieData`; no longer sends cookies
    in the `/download` POST body
  - `index.html`: removed `getCookiesAsObject`, `cookieEnabled`, `doNotTrack` from
    the inline fingerprint script; comment updated
  - `mailer.py`: removed Meta/GA/Instagram rows from notification email
  - Admin table: removed meta/ga/ig columns and cookie-dot indicators
  - Note: the DB columns still exist in older databases — they are simply no longer
    written or displayed.

### Fixed
- **Admin panel checkbox cells** now have `background: #333` matching the ID/date/IP
  cells, instead of the default transparent/white browser rendering.

### Changed
- `docker-compose.yml`: `APP_VERSION` default bumped to `1.5.0`; `GEOIP_PATH` env var
  and `./geoip:/app/geoip:ro` volume added.
- `Dockerfile`: `ARG APP_VERSION` bumped to `1.5.0`.
- `requirements.txt`: added `geoip2==4.8.0`.
- `.env.example`: added `GEOIP_PATH` with setup instructions.

### Operational note — GeoIP setup
The GeoLite2-City database is **not** bundled in the Docker image (60 MB binary, MaxMind
license required). To enable geolocation:
1. Register free at <https://www.maxmind.com/en/geolite2/signup>
2. Download `GeoLite2-City.mmdb`
3. Place it in `./geoip/GeoLite2-City.mmdb` next to `docker-compose.yml`
4. Restart the container — the app picks it up automatically

Without the file, geolocation is silently skipped and `country_code`/`city` remain `NULL`.

---

## [1.4.0] - 2026-03-21

### Added
- **Multi-select + ZIP download** in the admin panel (`/db`): checkbox column (first column),
  select-all scoped to the current page, a "Download ZIP" button in the top bar that appears
  only when ≥ 1 row is selected. ZIP is built server-side by Flask, named
  `yt2mp3-YYYY-MM-DD.zip`. Songs without an MP3 on disk are silently skipped.
  Anti-duplicate filename suffixes handle collisions within the archive.
- **Per-page selector** (10 / 25 / 50 / 100) in the admin top bar. Selection is preserved
  across AJAX refreshes via a query-string parameter (`per_page`).
- **AJAX auto-refresh** with a live countdown timer in the top bar. The table partial
  (`admin/_table.html`) is fetched from the new `GET /db/table-fragment` endpoint and
  swapped in without a full page reload. Checkbox selections that are still on the page
  are restored after each refresh. Interval is configurable via the `ADMIN_REFRESH_INTERVAL`
  environment variable (default: 300 s). The logout button was removed; a manual Refresh
  button is added next to the countdown.
- **Hardware model detection** (`app/hardware_parser.py`): `detect_hardware()` infers a
  human-readable device/chip description from WebGL renderer, User-Agent, and screen
  resolution (e.g. `"Apple M1 Pro · MacBook Pro"`, `"iPhone 15 (iOS 18.7)"`).
- **Identity hash** (`compute_identity_hash()`): stable 8-character SHA-256 digest of
  WebGL renderer + screen resolution + platform, consistent across page loads on the same
  device/browser combination.
- Two new columns in the `downloads` table: `hardware_model VARCHAR(256)`,
  `identity_hash VARCHAR(16)`. Both columns are displayed in the admin panel.
- **Automatic retroactive DB migration**: on app startup a background thread runs
  `ALTER TABLE downloads ADD COLUMN` for the two new columns. Already-migrated databases
  are handled gracefully (SQLite `ALTER TABLE` errors on duplicate columns are caught and
  ignored).
- `ADMIN_REFRESH_INTERVAL` environment variable (`.env.example`, `docker-compose.yml`).

### Changed
- Admin panel table extracted into a reusable Jinja2 partial `admin/_table.html` to support
  AJAX partial rendering without duplicating markup.
- `colspan` on expandable detail rows updated from 15 → 17 to account for the two new columns.
- Selected rows receive a neon-green left-border highlight for visual feedback.
- `docker-compose.yml`: default `APP_VERSION` bumped to `1.4.0`; `ADMIN_REFRESH_INTERVAL`
  env var added.
- `Dockerfile`: default `ARG APP_VERSION` bumped to `1.4.0`.

---

## [1.3.1] - 2026-03-21

### Fixed
- **Vertical position**: content block now reliably lands at ~40% from the top on all screen
  sizes. Previous formula `clamp(2rem, 15vh, 8rem)` only produced ~15vh of top padding,
  placing the visual center at ~20–25%. Corrected to
  `clamp(3rem, calc(40vh - 150px), 20rem)` (150 px ≈ half the content block height).
  Applies to the public page (`body` in `style.css`) and the admin login card (inline style
  in `login.html`).
- **Public page version badge parity**: badge now shows `v<version> · <commit> · github`
  matching the admin pages, instead of just `v<version>`. Commit hash links to the GitHub
  commit; "github" links to the repository.
- **Version badge links clickable**: `style.css` had `pointer-events: none` on
  `.version-badge` with no override for child anchors. Added `.version-badge a` rule with
  `pointer-events: all` so the commit and repo links are actually clickable.

---

## [1.3.0] - 2026-03-21

### Added
- **Song title column in admin DB table**: each row now shows a clickable link with the
  YouTube video title (neon green, ellipsis at 45 chars). Links to `/files/<job_id>` for
  direct MP3 download. Rows with no title or failed jobs show a muted placeholder.
- **Version badge** (bottom-right, fixed, hidden on mobile < 480 px) on all three pages:
  - Public page: `v<version>`
  - Admin login: `v<version> · <commit> · github`
  - Admin DB panel: `v<version> · <commit> · github`
- **Version + commit injection at build time**: `APP_VERSION` and `GIT_COMMIT` are now
  passed as Docker `ARG`/`ENV` by GitHub Actions and forwarded to Flask via a Jinja2
  `context_processor`. Local deployments fall back to `APP_VERSION=1.3.0` / `GIT_COMMIT=dev`.

### Changed
- **Vertical position**: all pages shifted from vertically centered (50%) to ~40% from
  the top (`align-items: flex-start` + `padding-top: clamp(2rem, 15vh, 8rem)`).
- `docker-compose.yml`: added `APP_VERSION` env var (default `1.3.0`).
- `Dockerfile`: added `ARG GIT_COMMIT` / `ARG APP_VERSION` with `ENV` export.
- `build-push.yml`: added `Derive app version` step and passes `GIT_COMMIT` + `APP_VERSION`
  as `build-args` to the Docker build.

### Known limitation — tracking cookies always empty
`_fbp`, `_fbc`, `_ga`, and `ig_did` will always be blank. These cookies are set by
third-party ad/analytics scripts (Meta Pixel, Google Analytics, Instagram SDK) which are
**not loaded on this site**. Browsers scope cookies per domain, so cookies set by
`facebook.com` or `google.com` on other sites are not readable here. The collection
infrastructure is correct; there is simply nothing to capture without adding the respective
tracking scripts to the frontend.

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
