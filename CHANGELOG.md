# Changelog

Todos los cambios notables de este proyecto se documentan aquí.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).
Versionado según [Semantic Versioning](https://semver.org/lang/es/).

---

## [1.1.0] - 2026-03-20

### Added
- Panel de administración en `/db` protegido con **Passkey / WebAuthn**
  - Registro de passkeys (Face ID, Touch ID, YubiKey, Windows Hello) **restringido exclusivamente a red local** — nunca accesible desde internet
  - Autenticación sin contraseña con sesión firmada de 8 horas
  - Tres estados en la página de login: registro (local sin creds), autenticación (con creds), bloqueado (remoto sin creds — sin revelar que existe registro)
  - Opción de añadir passkeys adicionales solo desde red local
  - Recuperación de emergencia via comando CLI en la Raspberry Pi
- Tabla de descargas paginada (25 filas/página) con fila expandible de detalle completo
  - Columnas resumen: ID, fecha, IP, URL, navegador, OS, dispositivo, idioma, fingerprint, indicadores de cookies tracking
  - Fila de detalle: User-Agent raw, todas las cookies JSON, componentes del fingerprint, estado del job, archivo MP3
  - Indicadores visuales (puntos de color) para presencia de cookies Meta `_fbp`/`_fbc`, Google Analytics `_ga`, Instagram `ig_did`
- Nuevos modelos de base de datos: `AdminUser`, `WebAuthnCredential`, `WebAuthnChallenge`
- Nuevas variables de entorno: `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGIN`, `SESSION_COOKIE_SECURE`
- Sistema de versionado semántico con CHANGELOG, GitHub Releases automáticas e imágenes Docker tagueadas
- `docker-compose.yml` acepta variable `IMAGE_TAG` para seleccionar versión específica del contenedor (`IMAGE_TAG=1.0.0 docker compose up -d`)

### Changed
- `docker-compose.yml`: imagen usa `${IMAGE_TAG:-latest}` en lugar de `:latest` fijo
- `docker-compose.yml`: añadidas variables de entorno WebAuthn al servicio `app`
- GitHub Actions: workflow ahora crea GitHub Release automática en cada tag `v*` con extracto del CHANGELOG y metadata de la imagen Docker

---

## [1.0.0] - 2026-03-20

> Tag de documentación — no genera imagen Docker en GHCR.

### Added
- Aplicación web Flask para convertir URLs de YouTube a MP3
- Descarga del audio en la mejor calidad disponible via **yt-dlp** + **ffmpeg** (VBR quality 0)
- Jobs de descarga en background con `threading`, polling de estado cada 1.5 s desde el frontend
- Barra de progreso animada con estados: analizando → descargando → convirtiendo → listo
- Enlace de descarga directa del MP3 con nombre del vídeo como `Content-Disposition`
- **UI dark mode**: fondo `#1c1c1c`, input y botón verde fluor `#39FF14`, tipografía Inter, botón "magia"
- Base de datos **SQLite** (SQLAlchemy) con tabla `downloads` y más de 20 campos de metadata
- Recolección de metadatos del visitante:
  - IP real via header `CF-Connecting-IP` (Cloudflare) con fallback a `X-Forwarded-For`
  - User-Agent parseado: browser, versión, OS, versión OS, tipo de dispositivo, is_mobile, is_bot
  - `Accept-Language` y `Referer` HTTP headers
- **Browser fingerprinting** client-side (sin dependencias externas):
  - Canvas fingerprint, WebGL renderer, resolución de pantalla, profundidad de color
  - Timezone (nombre + offset), hardware concurrency, device memory, max touch points
  - Lista de plugins del navegador
  - Hash numérico agregado como `visitorId`
- Captura de **cookies de tracking** accesibles (no HttpOnly):
  - Meta Pixel: `_fbp`, `_fbc`
  - Google Analytics: `_ga`, `_ga_*` (session ID)
  - Instagram: `ig_did`
  - Todas las cookies serializadas en JSON en campo `cookies_json`
- **Rate limiting** por IP via Flask-Limiter: 10 descargas/hora, 3 descargas/minuto
- Respuesta HTTP 429 con mensaje amigable renderizado en la UI
- API REST:
  - `POST /download` — acepta URL + fingerprint + cookies, devuelve `job_id`
  - `GET /status/<job_id>` — progreso y estado del job
  - `GET /files/<job_id>` — sirve el MP3 para descarga directa
- **Dockerfile**: `python:3.12-slim` + ffmpeg + gunicorn (2 workers, 4 threads, timeout 300 s)
- **Docker Compose**: servicio `app` + `cloudflared` tunnel, healthcheck, volúmenes persistentes para descargas y base de datos
- **GitHub Actions**: build multi-arch (`linux/amd64` + `linux/arm64`) → `ghcr.io/davic80/yt2mp3`
- Soporte de despliegue via Cloudflare Tunnel hacia `diana.f1madrid.win`
