"""
mailer.py — Download notification emails via Gmail SMTP.

Sends a styled HTML email on every completed download.
Errors are logged only; a mail failure never affects the download response.

Accepts a plain dict (not a SQLAlchemy model) so it is safe to call from a
background thread that has no Flask application context.
"""

import logging
import os
import smtplib
import threading
import time
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("app.mailer")


def _build_html(data: dict) -> str:
    """Return an HTML email body with download details."""
    created_at = data.get("created_at")
    created = (
        created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if created_at
        else "—"
    )
    browser = f"{data.get('ua_browser') or '—'} {data.get('ua_browser_version') or ''}".strip()

    base_url = os.environ.get("SITE_URL", "").rstrip("/")
    job_id   = str(data.get("job_id") or "")
    file_name = str(data.get("file_name") or "—")
    if base_url and job_id:
        href = escape(f"{base_url}/files/{job_id}", quote=True)
        file_cell = f'<a href="{href}" style="color:#39FF14;">{escape(file_name)}</a>'
    else:
        file_cell = escape(file_name)

    rows = [
        ("Title",            escape(str(data.get("title") or "—"))),
        ("File",             file_cell),
        ("YouTube URL",      escape(str(data.get("youtube_url") or "—"))),
        ("Date",             escape(created)),
        ("IP",               escape(str(data.get("ip_address") or "—"))),
        ("Browser",          escape(browser)),
        ("OS",               escape(str(data.get("ua_os") or "—"))),
        ("Device",           escape(str(data.get("ua_device") or "PC"))),
        ("Language",         escape(str(data.get("accept_language") or "—"))),
        ("Fingerprint",      escape(str(data.get("fingerprint_hash") or "—"))),
    ]

    detail_rows_html = "".join(
        f"""
        <tr>
          <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                     border-bottom:1px solid #333;">{escape(label)}</td>
          <td style="padding:6px 12px;color:#e0e0e0;font-size:12px;word-break:break-all;
                     border-bottom:1px solid #333;font-family:'Courier New',monospace;">{value}</td>
        </tr>"""
        for label, value in rows
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#1c1c1c;font-family:system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#1c1c1c;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#252525;border:1px solid #333;border-radius:8px;
                    overflow:hidden;max-width:560px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="padding:20px 24px;border-bottom:1px solid #333;">
            <span style="font-size:22px;font-weight:600;letter-spacing:-1px;color:#e0e0e0;">
              yt<span style="color:#27a008;">2</span><span style="color:#39FF14;">mp3</span>
            </span>
            <span style="font-size:11px;color:#888;margin-left:8px;
                         text-transform:uppercase;letter-spacing:.1em;">new download</span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:16px 0 8px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {detail_rows_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 24px;border-top:1px solid #333;
                     font-size:11px;color:#555;text-align:center;">
            yt2mp3 · automated notification
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send(data: dict) -> None:
    """Send the notification email. Runs in a background thread.
    Accepts a plain dict — no SQLAlchemy session or Flask app context needed."""
    admin_email  = os.environ.get("ADMIN_EMAIL", "")
    smtp_host    = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port    = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user    = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from    = os.environ.get("SMTP_FROM", smtp_user)

    if not admin_email or not smtp_user or not smtp_password:
        logger.warning(
            "mailer: SMTP not configured (ADMIN_EMAIL=%r, SMTP_USER=%r) — skipping notification for job %s",
            bool(admin_email), bool(smtp_user), data.get("job_id"),
        )
        return

    title = str(data.get("title") or data.get("youtube_url") or "unknown").replace("\n", " ").replace("\r", " ")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[yt2mp3] {title}"
    msg["From"]    = smtp_from
    msg["To"]      = admin_email

    msg.attach(MIMEText(_build_html(data), "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [admin_email], msg.as_string())
        logger.info("mailer: notification sent for job %s", data.get("job_id"))
    except Exception as exc:  # noqa: BLE001
        logger.error("mailer: failed to send notification for job %s: %s", data.get("job_id"), exc)


def send_download_notification(data: dict) -> None:
    """Fire-and-forget: send the notification in a background thread.
    ``data`` must be a plain dict, not a SQLAlchemy model instance."""
    t = threading.Thread(target=_send, args=(data,), daemon=True)
    t.start()


# ── New-user notification ─────────────────────────────────────────────────────

def _build_new_user_html(data: dict) -> str:
    """Return an HTML email body for a new user registration."""
    created_at = data.get("created_at")
    created = (
        created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if created_at
        else "—"
    )
    rows = [
        ("Email",    escape(str(data.get("email") or "—"))),
        ("Name",     escape(str(data.get("name") or "—"))),
        ("Provider", escape(str(data.get("provider") or "—"))),
        ("Date",     escape(created)),
    ]
    detail_rows_html = "".join(
        f"""
        <tr>
          <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                     border-bottom:1px solid #333;">{escape(label)}</td>
          <td style="padding:6px 12px;color:#e0e0e0;font-size:12px;word-break:break-all;
                     border-bottom:1px solid #333;font-family:'Courier New',monospace;">{value}</td>
        </tr>"""
        for label, value in rows
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#1c1c1c;font-family:system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#1c1c1c;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#252525;border:1px solid #333;border-radius:8px;
                    overflow:hidden;max-width:560px;width:100%;">

        <!-- Header -->
        <tr>
          <td style="padding:20px 24px;border-bottom:1px solid #333;">
            <span style="font-size:22px;font-weight:600;letter-spacing:-1px;color:#e0e0e0;">
              yt<span style="color:#27a008;">2</span><span style="color:#39FF14;">mp3</span>
            </span>
            <span style="font-size:11px;color:#888;margin-left:8px;
                         text-transform:uppercase;letter-spacing:.1em;">nuevo usuario</span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:16px 0 8px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              {detail_rows_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 24px;border-top:1px solid #333;
                     font-size:11px;color:#555;text-align:center;">
            yt2mp3 · automated notification
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send_new_user(data: dict) -> None:
    """Send the new-user notification. Runs in a background thread."""
    admin_email   = os.environ.get("ADMIN_EMAIL", "")
    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port     = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user     = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from     = os.environ.get("SMTP_FROM", smtp_user)

    if not admin_email or not smtp_user or not smtp_password:
        logger.warning(
            "mailer: SMTP not configured — skipping new-user notification for %s",
            data.get("email"),
        )
        return

    email = str(data.get("email") or "unknown").replace("\n", " ").replace("\r", " ")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[yt2mp3] nuevo usuario: {email}"
    msg["From"]    = smtp_from
    msg["To"]      = admin_email

    msg.attach(MIMEText(_build_new_user_html(data), "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [admin_email], msg.as_string())
        logger.info("mailer: new-user notification sent for %s", email)
    except Exception as exc:  # noqa: BLE001
        logger.error("mailer: failed to send new-user notification for %s: %s", email, exc)


def send_new_user_notification(data: dict) -> None:
    """Fire-and-forget: notify admin of a new user registration.
    ``data`` keys: email, name, provider, created_at."""
    t = threading.Thread(target=_send_new_user, args=(data,), daemon=True)
    t.start()


# ── Failure notification ──────────────────────────────────────────────────────
#
# Throttled on purpose. When YouTube changes something the failure is rarely
# isolated: every download starts failing at once, and an email per attempt
# would bury the one message that matters. At most _FAILURE_MAX_PER_WINDOW
# are sent per window; the rest are counted and reported in the next one, so
# the volume itself becomes the signal.

_FAILURE_WINDOW_SECONDS = 3600
_FAILURE_MAX_PER_WINDOW = 3

_failure_lock = threading.Lock()
_failure_window_start = 0.0
_failure_sent = 0
_failure_suppressed = 0


def _failure_budget() -> tuple[bool, int]:
    """Return (may_send, suppressed_since_last_sent). Resets each window."""
    global _failure_window_start, _failure_sent, _failure_suppressed
    now = time.time()
    with _failure_lock:
        if now - _failure_window_start > _FAILURE_WINDOW_SECONDS:
            _failure_window_start = now
            _failure_sent = 0
            _failure_suppressed = 0
        if _failure_sent < _FAILURE_MAX_PER_WINDOW:
            _failure_sent += 1
            suppressed, _failure_suppressed = _failure_suppressed, 0
            return True, suppressed
        _failure_suppressed += 1
        return False, 0


def _build_failure_html(data: dict, suppressed: int) -> str:
    attempts = data.get("attempts") or []
    attempt_rows = "".join(
        f"""
        <tr>
          <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                     border-bottom:1px solid #333;font-family:'Courier New',monospace;">{escape(str(client))}</td>
          <td style="padding:6px 12px;color:#e0a93f;font-size:12px;word-break:break-word;
                     border-bottom:1px solid #333;font-family:'Courier New',monospace;">{escape(str(error)[:200])}</td>
        </tr>"""
        for client, error in attempts
    ) or """
        <tr><td colspan="2" style="padding:6px 12px;color:#888;font-size:12px;">
          No client-level detail — the download failed before the fallback ladder ran.
        </td></tr>"""

    video_id = data.get("video_id")
    watch = f"https://www.youtube.com/watch?v={video_id}" if video_id else data.get("youtube_url")

    banner = ""
    if suppressed:
        banner = f"""
      <tr><td style="padding:12px 24px;background:#3a2a12;color:#e0a93f;font-size:13px;">
        {suppressed} further failure(s) were suppressed since the last message —
        this looks like a general outage rather than one bad video.
      </td></tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#1c1c1c;font-family:system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#1c1c1c;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#252525;border:1px solid #333;border-radius:8px;
                    overflow:hidden;max-width:560px;width:100%;">
        <tr>
          <td style="padding:20px 24px;border-bottom:1px solid #333;">
            <span style="font-size:22px;font-weight:600;letter-spacing:-1px;color:#e0e0e0;">
              yt<span style="color:#27a008;">2</span><span style="color:#39FF14;">mp3</span>
            </span>
            <div style="color:#f0605f;font-size:13px;margin-top:6px;">Download failed</div>
          </td>
        </tr>{banner}
        <tr><td style="padding:16px 24px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
              <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                         border-bottom:1px solid #333;">URL</td>
              <td style="padding:6px 12px;color:#e0e0e0;font-size:12px;word-break:break-all;
                         border-bottom:1px solid #333;font-family:'Courier New',monospace;">
                <a href="{escape(str(watch or ''), quote=True)}" style="color:#39FF14;">{escape(str(watch or '—'))}</a>
              </td>
            </tr>
            <tr>
              <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                         border-bottom:1px solid #333;">Error</td>
              <td style="padding:6px 12px;color:#f0605f;font-size:12px;word-break:break-word;
                         border-bottom:1px solid #333;font-family:'Courier New',monospace;">
                {escape(str(data.get('error') or '—')[:300])}
              </td>
            </tr>
            <tr>
              <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                         border-bottom:1px solid #333;">Job</td>
              <td style="padding:6px 12px;color:#e0e0e0;font-size:12px;
                         border-bottom:1px solid #333;font-family:'Courier New',monospace;">
                {escape(str(data.get('job_id') or '—'))} · {data.get('elapsed', 0):.1f}s
              </td>
            </tr>
          </table>
        </td></tr>
        <tr><td style="padding:0 24px 20px;">
          <div style="color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.1em;
                      padding:8px 12px;">Player clients tried</div>
          <table width="100%" cellpadding="0" cellspacing="0">{attempt_rows}</table>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send_failure(data: dict) -> None:
    """Send a failure notification. Runs in a background thread."""
    may_send, suppressed = _failure_budget()
    if not may_send:
        logger.warning(
            "mailer: failure notification throttled for job %s (%d per %ds already sent)",
            data.get("job_id"), _FAILURE_MAX_PER_WINDOW, _FAILURE_WINDOW_SECONDS,
        )
        return

    admin_email   = os.environ.get("ADMIN_EMAIL", "")
    smtp_host     = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port     = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user     = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from     = os.environ.get("SMTP_FROM", smtp_user)

    if not admin_email or not smtp_user or not smtp_password:
        logger.warning("mailer: SMTP not configured — skipping failure notification")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[yt2mp3] Download failed"
    msg["From"]    = smtp_from
    msg["To"]      = admin_email
    msg.attach(MIMEText(_build_failure_html(data, suppressed), "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [admin_email], msg.as_string())
        logger.info("mailer: failure notification sent for job %s", data.get("job_id"))
    except Exception as exc:  # noqa: BLE001
        logger.error("mailer: failed to send failure notification: %s", exc)


def send_failure_notification(data: dict) -> None:
    """Fire-and-forget: never let a mail problem affect the download path."""
    t = threading.Thread(target=_send_failure, args=(data,), daemon=True)
    t.start()
