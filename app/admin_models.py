import base64
from app import db


class AdminUser(db.Model):
    """Single admin user that owns one or more passkeys."""
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, default="admin")
    # WebAuthn user handle (random bytes, base64url encoded)
    user_handle = db.Column(db.String(128), unique=True, nullable=False)

    credentials = db.relationship("WebAuthnCredential", back_populates="user", cascade="all, delete-orphan")


class WebAuthnCredential(db.Model):
    """A registered passkey (authenticator) for an AdminUser."""
    __tablename__ = "webauthn_credentials"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("admin_users.id"), nullable=False)

    # Credential ID from the authenticator (base64url encoded)
    credential_id = db.Column(db.Text, unique=True, nullable=False, index=True)
    # CBOR-encoded public key (base64url)
    public_key = db.Column(db.Text, nullable=False)
    # Signature counter to detect cloned authenticators
    sign_count = db.Column(db.Integer, default=0)
    # Human-readable name set during registration
    name = db.Column(db.String(128), default="My Passkey")
    # Transports reported by the authenticator (JSON list)
    transports = db.Column(db.Text, nullable=True)

    user = db.relationship("AdminUser", back_populates="credentials")

    def credential_id_bytes(self) -> bytes:
        return base64.urlsafe_b64decode(self.credential_id + "==")

    def public_key_bytes(self) -> bytes:
        return base64.urlsafe_b64decode(self.public_key + "==")


class WebAuthnChallenge(db.Model):
    """Short-lived challenge stored server-side during a WebAuthn ceremony."""
    __tablename__ = "webauthn_challenges"

    id = db.Column(db.Integer, primary_key=True)
    # 'registration' or 'authentication'
    ceremony = db.Column(db.String(16), nullable=False)
    # base64url encoded challenge bytes
    challenge = db.Column(db.Text, nullable=False)
    # epoch seconds — expired after 5 minutes
    expires_at = db.Column(db.Float, nullable=False)
