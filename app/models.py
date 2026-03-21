from datetime import datetime, timezone
from app import db


class Download(db.Model):
    __tablename__ = "downloads"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(16), default="pending")  # pending, done, error
    error_message = db.Column(db.Text, nullable=True)

    # Content
    youtube_url = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.Text, nullable=True)
    file_name = db.Column(db.Text, nullable=True)
    title = db.Column(db.Text, nullable=True)

    # Network
    ip_address = db.Column(db.String(64), nullable=True)
    referrer = db.Column(db.Text, nullable=True)
    country_code = db.Column(db.String(2), nullable=True)   # ISO 3166-1 alpha-2
    city = db.Column(db.String(128), nullable=True)

    # User-Agent (raw + parsed)
    user_agent_raw = db.Column(db.Text, nullable=True)
    ua_browser = db.Column(db.String(128), nullable=True)
    ua_browser_version = db.Column(db.String(64), nullable=True)
    ua_os = db.Column(db.String(128), nullable=True)
    ua_os_version = db.Column(db.String(64), nullable=True)
    ua_device = db.Column(db.String(64), nullable=True)  # Mobile / PC / Tablet
    ua_is_mobile = db.Column(db.Boolean, default=False)
    ua_is_bot = db.Column(db.Boolean, default=False)
    accept_language = db.Column(db.String(256), nullable=True)

    # Browser fingerprint (client-side JS)
    fingerprint_hash = db.Column(db.String(256), nullable=True)
    fingerprint_components = db.Column(db.Text, nullable=True)  # JSON string

    # Hardware inference (v1.4.0)
    hardware_model = db.Column(db.String(256), nullable=True)  # e.g. "Apple M1 Pro · MacBook Pro"
    identity_hash  = db.Column(db.String(16),  nullable=True)  # 8-char stable device hash

    # Bot score (v1.5.0) — 0-100 heuristic
    bot_score = db.Column(db.Integer, nullable=True)

    # File size in bytes (v1.7.0)
    file_size = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "title": self.title,
            "file_name": self.file_name,
            "error_message": self.error_message,
        }
