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

    base_url = os.environ.get("WEBAUTHN_ORIGIN", "").rstrip("/")
    job_id   = data.get("job_id") or ""
    file_name = data.get("file_name") or "—"
    if base_url and job_id:
        file_cell = f'<a href="{base_url}/files/{job_id}" style="color:#39FF14;">{file_name}</a>'
    else:
        file_cell = file_name

    rows = [
        ("Title",            data.get("title") or "—"),
        ("File",             file_cell),
        ("YouTube URL",      data.get("youtube_url") or "—"),
        ("Date",             created),
        ("IP",               data.get("ip_address") or "—"),
        ("Browser",          browser),
        ("OS",               data.get("ua_os") or "—"),
        ("Device",           data.get("ua_device") or "PC"),
        ("Language",         data.get("accept_language") or "—"),
        ("Fingerprint",      data.get("fingerprint_hash") or "—"),
    ]

    detail_rows_html = "".join(
        f"""
        <tr>
          <td style="padding:6px 12px;color:#888;font-size:12px;white-space:nowrap;
                     border-bottom:1px solid #333;">{label}</td>
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

    title = data.get("title") or data.get("youtube_url") or "unknown"
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
