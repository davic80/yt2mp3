from app import create_app
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_app()
# Trust one level of reverse proxy (Cloudflare Tunnel → gunicorn).
# Ensures url_for() generates https:// URLs and Secure cookies are set
# correctly when Flask sees plain HTTP internally but clients are on HTTPS.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
