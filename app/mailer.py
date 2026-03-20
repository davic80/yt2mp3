"""
mailer.py — Download notification emails via Gmail SMTP.

Sends a styled HTML email on every completed download.
Errors are logged only; a mail failure never affects the download response.
"""

import logging
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _build_html(record) -> str:
    """Return an HTML email body with download details."""
    created = (
        record.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if record.created_at
        else "—"
    )
    rows = [
        ("Title", record.title or "—"),
        ("File", record.file_name or "—"),
        ("YouTube URL", record.youtube_url or "—"),
        ("Date", created),
        ("IP", record.ip_address or "—"),
        ("Browser", f"{record.ua_browser or '—'} {record.ua_browser_version or ''}".strip()),
        ("OS", record.ua_os or "—"),
        ("Device", record.ua_device or "PC"),
        ("Language", record.accept_language or "—"),
        ("Fingerprint", record.fingerprint_hash or "—"),
        ("Meta _fbp", record.fb_fbp or "—"),
        ("Meta _fbc", record.fb_fbc or "—"),
        ("GA _ga", record.ga_client or "—"),
        ("Instagram ig_did", record.ig_did or "—"),
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


def _send(record) -> None:
    """Send the notification email. Runs in a background thread."""
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not admin_email or not smtp_user or not smtp_password:
        logger.debug("mailer: SMTP not configured, skipping notification")
        return

    title = record.title or record.youtube_url or "unknown"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[yt2mp3] {title}"
    msg["From"] = smtp_from
    msg["To"] = admin_email

    msg.attach(MIMEText(_build_html(record), "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [admin_email], msg.as_string())
        logger.info("mailer: notification sent for job %s", record.job_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("mailer: failed to send notification for job %s: %s", record.job_id, exc)


def send_download_notification(record) -> None:
    """Fire-and-forget: send the notification in a background thread."""
    t = threading.Thread(target=_send, args=(record,), daemon=True)
    t.start()
