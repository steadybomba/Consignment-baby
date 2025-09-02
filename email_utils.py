"""
Email utilities. If SMTP_HOST is not set, emails are printed (dev).
Provides send_email() and send_checkpoint_email().
"""

import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
from database import get_db

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "no-reply@example.com")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1","true","yes")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")

def send_email(to_email: str, subject: str, html: str, text: str = None):
    if not SMTP_HOST:
        print(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{html}\n---")
        return
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    if text:
        msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        if SMTP_USE_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def send_checkpoint_email(shipment_row, checkpoint_row):
    db = get_db()
    subs = db.execute("SELECT email FROM subscribers WHERE shipment_id=? AND is_active=1", (shipment_row["id"],)).fetchall()
    if not subs:
        return
    track_url = f"{APP_BASE_URL.rstrip('/')}/track/{shipment_row['tracking']}"
    subject = f"Update: {shipment_row['title']} ({shipment_row['tracking']}) — {checkpoint_row['label']}"
    html = f"""
    <p>Update for <strong>{shipment_row['title']}</strong> ({shipment_row['tracking']}):</p>
    <p><strong>{checkpoint_row['label']}</strong>{': ' + (checkpoint_row['note'] or '') if checkpoint_row['note'] else ''}</p>
    <p>Time: {checkpoint_row['timestamp']}</p>
    <p>Coords: {checkpoint_row['lat']}, {checkpoint_row['lng']}</p>
    <p><a href="{track_url}">View on map</a></p>
    <hr>
    <p style="font-size:12px;color:#666">Unsubscribe via the tracking page.</p>
    """
    for r in subs:
        try:
            send_email(r["email"], subject, html)
        except Exception as e:
            print("Failed to send email to", r["email"], e)
