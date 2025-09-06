"""
Email utilities for Consignment Tracker.
If SMTP_HOST is not set, emails are printed to stdout (dev mode).
Provides send_email() and send_checkpoint_email() with async support.
"""

import os
import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from functools import partial

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SMTP Configuration (env vars)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@example.com")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "10"))  # seconds

# App Configuration
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
DEV_MODE = not SMTP_HOST  # Fallback to stdout if no SMTP configured

# Thread pool for async email sending
_executor = ThreadPoolExecutor(max_workers=4)

def send_email(
    to_email: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    retries: int = 2,
) -> bool:
    """
    Send an email via SMTP or print to stdout in dev mode.
    Args:
        to_email: Recipient email address.
        subject: Email subject.
        html: HTML content.
        text: Plaintext fallback (optional).
        retries: Number of retry attempts on failure.
    Returns:
        bool: True if sent successfully, False otherwise.
    """
    if DEV_MODE:
        logger.info(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{html}\n---")
        return True

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.add_alternative(html, subtype="html")
    if text:
        msg.set_content(text)

    for attempt in range(retries + 1):
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
                if SMTP_USE_TLS:
                    server.starttls()
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
                logger.info(f"Email sent to {to_email}")
                return True
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed for {to_email}: {str(e)}")
            if attempt == retries:
                logger.error(f"Failed to send email to {to_email}: {str(e)}")
                return False

def send_checkpoint_email_async(
    shipment: Dict[str, Any],
    checkpoint: Dict[str, Any],
    subscribers: Optional[list] = None,
) -> None:
    """
    Send checkpoint update emails asynchronously.
    Args:
        shipment: Shipment data (dict with keys like 'id', 'tracking', 'title').
        checkpoint: Checkpoint data (dict with keys like 'label', 'note', 'timestamp').
        subscribers: Optional list of subscriber emails. If None, fetches from DB.
    """
    if not subscribers:
        from database import get_db
        db = get_db()
        subscribers = db.execute(
            "SELECT email FROM subscribers WHERE shipment_id=? AND is_active=1",
            (shipment["id"],),
        ).fetchall()

    if not subscribers:
        logger.info("No active subscribers for shipment %s", shipment["tracking"])
        return

    track_url = f"{APP_BASE_URL.rstrip('/')}/track/{shipment['tracking']}"
    subject = f"Update: {shipment['title']} ({shipment['tracking']}) — {checkpoint['label']}"
    html = f"""
    <p>Update for <strong>{shipment['title']}</strong> ({shipment['tracking']}):</p>
    <p><strong>{checkpoint['label']}</strong>{': ' + checkpoint['note'] if checkpoint.get('note') else ''}</p>
    <p>Time: {checkpoint['timestamp']}</p>
    <p>Coords: {checkpoint['lat']}, {checkpoint['lng']}</p>
    <p><a href="{track_url}">View on map</a></p>
    <hr>
    <p style="font-size:12px;color:#666">Unsubscribe via the tracking page.</p>
    """

    # Submit email tasks to thread pool
    for sub in subscribers:
        email = sub["email"]
        _executor.submit(
            partial(send_email, email, subject, html),
        )

def send_checkpoint_email(shipment: Dict[str, Any], checkpoint: Dict[str, Any]) -> None:
    """
    Wrapper for backward compatibility (calls async version).
    """
    send_checkpoint_email_async(shipment, checkpoint)

# Cleanup on shutdown
def shutdown_email_executor():
    _executor.shutdown(wait=True)

# Register shutdown handler (e.g., in app.py)
# import atexit; atexit.register(shutdown_email_executor)
