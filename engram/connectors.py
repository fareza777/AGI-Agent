"""External integrations (outbound), with honest capability gating.

Each connector reports whether it is configured. When it is NOT configured the
tool returns a clear ERROR telling the user what to set — it never silently
no-ops and never lets the agent claim it "sent the email" when nothing was sent.
That honesty is the whole point: a do-everything agent that lies about what it
did is worse than one that admits a channel isn't wired up.

Implemented now:
  - send_email via SMTP (configure ENGRAM_SMTP_* env vars).

Designed-for (return a 'not configured' message until wired with OAuth creds):
  - Gmail / Google Calendar / Google Drive. These need an OAuth flow that can't
    live in env vars alone; status() surfaces them so /doctor can show the gap.
"""

import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger("engram.connectors")


def email_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def send_email(to: str, subject: str, body: str) -> str:
    """Send a plain-text email via SMTP. Returns a status string (ERROR-prefixed
    on failure so the agent reports it honestly)."""
    if not email_configured():
        return ("ERROR: email belum dikonfigurasi. Set ENGRAM_SMTP_HOST, "
                "ENGRAM_SMTP_PORT, ENGRAM_SMTP_USER, ENGRAM_SMTP_PASSWORD, "
                "ENGRAM_SMTP_FROM di .env untuk mengaktifkan pengiriman email.")
    recipients = [r.strip() for r in str(to).replace(";", ",").split(",") if r.strip()]
    if not recipients:
        return "ERROR: tidak ada alamat tujuan yang valid."
    msg = EmailMessage()
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "(tanpa subjek)"
    msg.set_content(body or "")
    try:
        if config.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass  # server without STARTTLS (e.g. local relay)
        with server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as exc:
        log.warning("send_email failed", exc_info=True)
        return f"ERROR: gagal kirim email ({type(exc).__name__}: {exc})."
    return f"Email terkirim ke {', '.join(recipients)} (subjek: {msg['Subject']})."


def status() -> dict:
    """{connector: 'ready' | 'not configured'} for /doctor."""
    return {
        "email (SMTP)": "ready" if email_configured() else "not configured",
        "gmail": "not configured (needs OAuth)",
        "google_calendar": "not configured (needs OAuth)",
        "google_drive": "not configured (needs OAuth)",
    }
