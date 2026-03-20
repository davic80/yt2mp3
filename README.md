# yt2mp3

Web app para convertir vídeos de YouTube a MP3.
Desplegada en `diana.f1madrid.win` via Cloudflare Tunnel desde una Raspberry Pi.

---

## Despliegue en Raspberry Pi

### 1. Clonar el repositorio

```bash
git clone https://github.com/davic80/yt2mp3.git
cd yt2mp3
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Edita `.env` y rellena:
- `SECRET_KEY` — genera una con `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `CLOUDFLARE_TUNNEL_TOKEN` — ver sección Cloudflare Tunnel más abajo

### 3. Configurar Cloudflare Tunnel

1. Ve a [dash.cloudflare.com](https://dash.cloudflare.com) → **Zero Trust** → **Networks** → **Tunnels**
2. Crea un nuevo tunnel (tipo Cloudflared)
3. Copia el token del connector y ponlo en `.env` como `CLOUDFLARE_TUNNEL_TOKEN`
4. En **Public Hostnames** del tunnel, añade:
   - **Subdomain:** `diana`
   - **Domain:** `f1madrid.win`
   - **Service:** `http://app:5000`

### 4. Arrancar

```bash
# Primera vez o tras actualizar imagen
docker compose pull

# Arrancar en background
docker compose up -d

# Ver logs
docker compose logs -f
```

### 5. Actualizar a nueva versión

```bash
docker compose pull && docker compose up -d
```

---

## Desarrollo local

```bash
# Instalar dependencias (requiere ffmpeg en el sistema)
pip install -r requirements.txt

# Arrancar en modo desarrollo
FLASK_APP=wsgi.py FLASK_ENV=development flask run

# O con Docker
docker build -t yt2mp3:dev .
docker run -p 5000:5000 \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/database:/app/database \
  -e SECRET_KEY=dev \
  yt2mp3:dev
```

---

## Estructura

```
app/
  __init__.py      # Flask app factory
  models.py        # SQLAlchemy models (tabla downloads)
  routes.py        # Endpoints: GET /, POST /download, GET /status/<id>, GET /files/<f>
  downloader.py    # yt-dlp wrapper con jobs en background (threading)
  fingerprint.py   # Recolección de metadatos de usuario
  templates/
    index.html     # UI
static/
  style.css
  app.js
Dockerfile
docker-compose.yml
.github/workflows/build-push.yml
```

---

## CI/CD

Cada push a `main` desencadena un GitHub Action que:

1. Construye la imagen Docker para `linux/amd64` y `linux/arm64`
2. La publica en `ghcr.io/davic80/yt2mp3:latest`

La imagen se construye con caché de GitHub Actions para acelerar builds sucesivos.

---

## Base de datos

SQLite en `/app/database/yt2mp3.db` (montado como volumen).

Tabla `downloads` — campos relevantes:
- Metadatos de la petición (IP, User-Agent parseado, Accept-Language, Referrer)
- Browser fingerprint (canvas, WebGL, fonts, screen, timezone)
- Cookies de tracking (Meta `_fbp`/`_fbc`, Google Analytics `_ga`, Instagram `ig_did`)
- Estado del job y ruta del archivo descargado
