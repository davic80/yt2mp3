# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [4.5.0] - 2026-03-23

### Changed
- **Auth: Auth0 → Google OAuth directo.** Eliminada la dependencia de Auth0.
  La autenticación ahora usa Google OAuth 2.0 directamente a través de Authlib
  (`https://accounts.google.com/.well-known/openid-configuration`). Solo Google
  como proveedor (Facebook eliminado).
- **URL de la app: `yt2mp3.f1madrid.win`.** El callback de OAuth y el origen
  de sesión apuntan ahora a `https://yt2mp3.f1madrid.win`. El logout redirige
  a `yt2mp3.f1madrid.win` (sin pasar por el endpoint de Auth0).
- **Env vars renombradas** en `docker-compose.yml`:
  - `AUTH0_DOMAIN` / `AUTH0_CLIENT_ID` / `AUTH0_CLIENT_SECRET` / `AUTH0_CALLBACK_URL`
    → `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_CALLBACK_URL`
  - `WEBAUTHN_ORIGIN` default: `https://yt2mp3.f1madrid.win`

### Removed
- Todo el código de Auth0 en `auth_routes.py` y `__init__.py`.

### Notes — configuración en Google Cloud Console
1. Crear un proyecto en https://console.cloud.google.com/
2. APIs & Services → Credentials → Create OAuth 2.0 Client ID (tipo: Web application)
3. Authorized redirect URIs: `https://yt2mp3.f1madrid.win/auth/callback`
4. Authorized JavaScript origins: `https://yt2mp3.f1madrid.win`
5. Copiar Client ID y Client Secret al `.env` de la Raspberry:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   GOOGLE_CALLBACK_URL=https://yt2mp3.f1madrid.win/auth/callback
   WEBAUTHN_ORIGIN=https://yt2mp3.f1madrid.win
   SESSION_COOKIE_SECURE=true
   ```

---

## [4.0.0] - 2026-03-22

### Added
- **Persistent player bar (SPA architecture).** Audio now keeps playing when
  navigating between pages. A single `shell.html` outer shell owns the topbar,
  `#page-content` swap area, `<audio>` element, and player bar — none of these
  are ever destroyed on navigation.
- **`static/player.js`** — global `window.Player` module. Owns the `<audio>` DOM
  node and all playback logic (`playTrack`, `togglePlay`, `prevTrack`,
  `nextTrack`, `toggleShuffle`, `cycleRepeat`, `toggleMute`, `loadTracks`,
  `setQueue`, `onTrackChange`, `offTrackChange`, `getState`).
- **`static/spa.js`** — SPA navigation engine. Intercepts internal `<a>` clicks,
  fetches `?fragment=1` from the server, swaps `#page-content` innerHTML, calls
  `runScripts()` to re-execute inline scripts, updates `history.pushState`, and
  highlights the active topbar link. Back/forward via `popstate` is also handled.
  Auth redirects (`/auth/…`) trigger a full-page reload so the login flow works.
- **Fragment templates** (`app/templates/fragments/`):
  - `home.html` — download form + fingerprint script.
  - `mis_descargas.html` — downloads table with ▶ play button per row calling
    `window.Player.playTrack(jobId)`.
  - `player.html` — sidebar + track list, calls `Player.loadTracks()` and
    registers `Player.onTrackChange()` for row highlighting.
- **Auth zone** moved from `index.html` body into the persistent shell topbar.
- **Active topbar-link** highlighting via `data-path` attribute and
  `.topbar-link-active` CSS class, updated on every SPA navigation.
- **Version badge** repositioned with `bottom: calc(var(--player-h) + .5rem)`
  so it clears the persistent player bar.

### Changed
- `app/routes.py`, `app/mis_descargas_routes.py`, `app/player_routes.py`:
  page routes now return `shell.html` on first load and `fragments/*.html` when
  `?fragment=1` is present (SPA subsequent navigations).
- `static/app.js` refactored: download form logic wrapped in `initDownloadForm()`
  with null guards, exposed as `window._initDownloadForm` for re-init after SPA
  swaps. `initAuthZone()` runs once on shell load only.

### Removed
- `app/templates/index.html` — replaced by `shell.html` + `fragments/home.html`.
- `app/templates/mis_descargas.html` — replaced by `fragments/mis_descargas.html`.
- `app/templates/player/index.html` — replaced by `fragments/player.html`.

---

## [3.2.0] - 2026-03-22

### Added
- **Video-ID deduplication.** Before invoking yt-dlp, the downloader now checks
  whether a completed download with the same YouTube `video_id` (and a confirmed
  `audio_hash`) already exists on disk. If found, the new `Download` row reuses
  `file_path`, `file_name`, `title`, `file_size`, and `audio_hash` from the
  existing record — no network request, no conversion, instant progress bar.
- **`video_id` column on `downloads`** (`VARCHAR(32)`, nullable). Extracted from
  the cleaned URL (handles `watch?v=`, `youtu.be/`, `shorts/`, `embed/`) in
  `routes.py` via `_extract_video_id()` and stored before the background thread
  starts.
- **`audio_hash` column on `downloads`** (`VARCHAR(64)`, nullable). SHA-256 hex
  digest of the MP3 file, computed in 1 MB chunks after every new download via
  `_sha256()` in `downloader.py`. Used as a sentinel to confirm the original
  download completed fully.
- **Reference-counted file deletion** (already in `mis_descargas_routes.py`):
  the physical MP3 is only removed when no other `downloads` row references the
  same `file_path`, so shared files are never orphaned.

### Changed
- `start_download()` now accepts an optional `video_id=` keyword argument and
  passes it to `_run_download` via `kwargs=` so the dedup check can run in the
  background thread without touching the DB from the request thread.
- `APP_VERSION` default bumped to `3.2.0` in `__init__.py`, `Dockerfile`, and
  `docker-compose.yml`.
- Inline migrations in `__init__.py` extended with `ALTER TABLE downloads ADD
  COLUMN video_id` and `ALTER TABLE downloads ADD COLUMN audio_hash`.

---

## [3.1.0] - 2026-03-22

### Added
- **`/mis-descargas`** — personal download history page for logged-in users.
  - Full list of the user's completed downloads with title, size, and date.
  - **Rename** any track inline (sanitised for filesystem safety); updates both
    `title` and `file_name` in the DB so the download link uses the new name.
  - **Delete** a track record. File on disk is removed only when no other
    `downloads` row references the same `file_path` (reference-counted).
  - **Individual download** button per track (`/files/<job_id>.mp3`).
  - **ZIP download** — select any subset (or all) and download a single ZIP
    with deduplicated filenames inside the archive.
  - Multi-select with header checkbox and "select all" toggle.
  - Search/filter bar.
- **Anonymous → user association on login** — downloads made anonymously in
  the same browser session (matched by `identity_hash`) are automatically
  claimed by the user on their first login.
- **Header "mis descargas" link** — shown in the auth zone when logged in
  (next to the player name and logout button).
- **Player topbar "↓ Mis descargas" link** — quick navigation from the player.

---

## [3.0.1] - 2026-03-22

### Fixed
- **Player topbar** — "→ Admin" link replaced with "→ Download" so remote users
  are directed back to the public download page, not the local-only admin panel.
- **Header auth zone** — logged-in user's name now links to `/player` and has a
  musical note (♪) appended, making it a clear shortcut to the player page.

---

## [3.0.0] - 2026-03-22

### Added
- **Auth0 OAuth login (Google + Facebook).** Users can sign in from the public
  page via Auth0. Login is optional — anonymous downloads still work.
- **`/auth` blueprint** (`/auth/login`, `/auth/callback`, `/auth/logout`,
  `/auth/me`) built with Authlib (sync) for compatibility with gunicorn sync
  workers.
- **`User` model** — OAuth users are upserted into a `users` table on every
  login (`email`, `name`, `picture`, `provider`, `created_at`, `last_login`).
- **`user_email` column on `downloads`** — nullable FK to `users.email`.
  Anonymous downloads store `NULL`; admin panel shows `anonymous`.
- **`user_email` column on `playlists`** — nullable; playlists created by a
  logged-in remote user are scoped to that user.
- **Auth zone in `index.html` header** — login button (logged-out) or avatar +
  name + logout link (logged-in); populated by `app.js` calling `/auth/me` on
  page load.
- **`/player` is now public-but-authenticated** (`@user_required`). Local
  (Pi LAN) requests still bypass auth entirely — admin sees all tracks and
  playlists. Remote users see only their own tracks, playlists, and streams.
- **`?user=` filter in admin panel** — topbar input debounces and applies a
  `user_email` filter to the downloads table; clicking a user email in the
  table also sets the filter. Pagination preserves the filter parameter.
- **`user` column in admin table** — shows truncated email linked to the
  `?user=` filter, or `anonymous` for `NULL` rows. Detail panel also shows it.
  colspan bumped to 18.

### Changed
- `SESSION_COOKIE_SECURE` default unchanged (still env-driven).
- `APP_VERSION` default bumped to `3.0.0` in `__init__.py`, `Dockerfile`,
  and `docker-compose.yml`.

### Notes
- Auth0 credentials required in `.env` on the Pi:
  `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`,
  `AUTH0_CALLBACK_URL=https://diana.f1madrid.win/auth/callback`.
- Auth0 dashboard must have `https://diana.f1madrid.win/auth/callback` in
  Allowed Callback URLs and `https://diana.f1madrid.win` in Allowed Logout URLs
  and Allowed Web Origins.
- SQLite FK constraints are not enforced at DB level (SQLite limitation);
  the `user_email` relation is enforced only at the SQLAlchemy ORM level.
- **NOT in this release**: personal download history `/mis-descargas`
  and associating anonymous downloads to a user after login (→ v3.1.0).

---

## [2.0.2] - 2026-03-21

### Added
- **File size as a separate download column in admin table.** The `size` field
  is now its own column (after `title`) instead of being appended inline next
  to the title. When the record is `done`, the size is a direct download link
  (`/files/<job_id>`); otherwise it renders as plain muted text.

### Fixed
- **Playlist URLs now accepted.** Pasting a bare playlist URL
  (`?list=PL...` with no `v=`) previously returned a 400 error. It now passes
  through to yt-dlp (which has `noplaylist=True`), so the first track of the
  list — the one currently playing on YouTube — is downloaded instead.

---

## [2.0.1] - 2026-03-21

### Fixed
- **File size missing for existing downloads.** Records created before v1.7.0
  had `file_size = NULL` because the column was only populated on new downloads.
  A background migration thread now runs at startup and fills `file_size` for
  all `done` rows whose MP3 is still on disk (`os.path.getsize`). Files that
  have been deleted from disk are left as NULL silently.
- **`to_dict()` did not include `file_size`.** When the frontend polled
  `/status/<job_id>` after the job had expired from the in-memory store (e.g.
  after a container restart), the response came from `record.to_dict()` which
  was missing the `file_size` field, so the download label never showed the
  size even for freshly converted tracks. Field added.

---

## [2.0.0] - 2026-03-21

### Added
- **Private music player.** New `/player` SPA (single-page app) backed by a
  dedicated `player` blueprint. Streams MP3s from the existing downloads folder
  using HTTP range requests, supports playlists (create, rename, delete, reorder
  tracks), and includes a full-featured in-browser audio player with seek bar,
  volume, and shuffle/repeat modes.
- **File size in public download UI.** After a conversion completes the download
  button now shows the file size inline (e.g. `↓ Song Title.mp3 · 6.2 MB`).
  `file_size` is propagated through the in-memory job store and the
  `/status/<job_id>` JSON response so `app.js` can display it without an extra
  round-trip.
- **`is_favorite` column.** New boolean column on the `downloads` table
  (migrated inline via `ALTER TABLE … ADD COLUMN … DEFAULT 0`) used by the
  player to mark favourite tracks.
- **`→ Player` link in admin topbar.** Quick navigation from the admin DB view
  to the player, styled identically to the existing Analytics link.

### Changed
- **`auth_utils` module extracted.** `local_only` decorator, `_client_ip`, and
  `_is_local_request` moved from `admin_routes.py` into a new
  `app/auth_utils.py` so both the admin and player blueprints can share them
  without circular imports.

---

## [1.7.0] - 2026-03-21

### Added
- **Rename title from admin panel.** Clicking any title in the downloads table
  opens a `prompt()` pre-filled with the current title. On confirm, `POST /db/rename`
  updates both `title` and `file_name` in the DB (the actual file on disk keeps its
  `<job_id>.mp3` name). The table refreshes automatically.
- **File size column.** New `file_size` DB column (integer bytes) populated at
  download time via `os.path.getsize()`. The size is displayed inline next to each
  title using Jinja2's `filesizeformat` filter (e.g. "6.2 MiB"), and also shown in
  the row detail panel.
- **Date column moved.** The `date` column now appears after `fingerprint` instead
  of between `#` and `ip`, keeping network/identity fields together.

---

## [1.6.7] - 2026-03-21

### Fixed
- **"Eliminar seleccionados" button stays disabled after first successful delete.**
  `this.disabled` was never reset to `false` on the success path, so every
  subsequent click was silently swallowed — making it appear the confirm dialog
  never fired. Button is now re-enabled before `refreshTable()` is called.
- **MP3 files not removed on delete.** The `except` clause only caught
  `FileNotFoundError`; widened to `OSError` so any filesystem error (permissions,
  stale NFS handle, etc.) is handled gracefully without aborting the DB deletion.

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
