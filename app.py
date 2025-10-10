# --- Flask app initialization moved to the top ---
import os
import logging
import smtplib
import random
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, current_app, redirect, url_for
from flask_jwt_extended import jwt_required, create_access_token
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit, join_room
from flask_caching import Cache
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash
from twilio.rest import Client
from math import radians, sin, cos, sqrt, atan2
from typing import Dict, Union, Optional as TypingOptional
from pydantic import BaseModel, validator, ValidationError
from logging.handlers import RotatingFileHandler
from celery import Celery, shared_task
import redis
import bleach
try:
    from retry import retry
except ImportError:
    retry = None
try:
    from twilio.base.exceptions import TwilioRestException
except ImportError:
    TwilioRestException = Exception

# Initialize Flask app (must be before any @app.route usage)
app = Flask(__name__, template_folder='templates')

# Add index route for template and Socket.IO compatibility
@app.route('/')
def index():
    return render_template('index.html')

# Restore get_smtp_connection helper function
def get_smtp_connection():
    server = smtplib.SMTP(app.config['SMTP_HOST'], app.config['SMTP_PORT'])
    server.starttls()
    server.login(app.config['SMTP_USER'], app.config['SMTP_PASS'])
    return server

# Load required environment variables and fail early if missing
required_vars = [
    'SECRET_KEY', 'SQLALCHEMY_DATABASE_URI', 'JWT_SECRET_KEY', 'CELERY_BROKER_URL',
    'CELERY_RESULT_BACKEND', 'REDIS_URL', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS',
    'SMTP_FROM', 'APP_BASE_URL'
]
for var in required_vars:
    value = os.environ.get(var)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {var}")
    app.config[var] = value

# Now safe to initialize Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'redis', 'CACHE_REDIS_URL': app.config['REDIS_URL']})

# Import requests for HTTP calls
import requests
# Shipment status enum
from enum import Enum
class ShipmentStatus(Enum):
    CREATED = 'Created'
    IN_TRANSIT = 'In Transit'
    OUT_FOR_DELIVERY = 'Out for Delivery'
    DELIVERED = 'Delivered'

# Load required environment variables and fail early if missing
required_vars = [
    'SECRET_KEY', 'SQLALCHEMY_DATABASE_URI', 'JWT_SECRET_KEY', 'CELERY_BROKER_URL',
    'CELERY_RESULT_BACKEND', 'REDIS_URL', 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS',
    'SMTP_FROM', 'APP_BASE_URL'
]
for var in required_vars:
    value = os.environ.get(var)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {var}")
    app.config[var] = value

# Configure logging
handler = RotatingFileHandler('app.log', maxBytes=1000000, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'))
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
logger = app.logger

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*")
limiter = Limiter(app, key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])

# Initialize Celery
def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=app.config['CELERY_BROKER_URL'],
        backend=app.config['CELERY_RESULT_BACKEND']
    )
    celery.conf.update(app.config)
    return celery

celery = make_celery(app)

# Initialize Redis
redis_client = redis.Redis.from_url(app.config['REDIS_URL'])

# Initialize Twilio
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# Admin sessions (stored in Redis)
admin_sessions = {}


# Database Models

# Routes

# Celery Tasks
@shared_task(bind=True, max_retries=3, retry_backoff=2, retry_jitter=True)
def send_checkpoint_email_async(self, tracking: str, email: str, checkpoint: dict):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Shipment {tracking} Checkpoint Update"
        msg['From'] = app.config['SMTP_FROM']
        msg['To'] = email

        text = f"""Shipment Checkpoint Update

Tracking: {tracking}
Position: {checkpoint['position']}
Label: {checkpoint['label']}
Location: ({checkpoint['lat']}, {checkpoint['lng']})
Note: {checkpoint['note'] or 'None'}
Status: {checkpoint['status'] or 'None'}
Timestamp: {checkpoint['timestamp']}
Track: {app.config['APP_BASE_URL']}/track/{tracking}
"""
        if checkpoint.get('proof_photo'):
            text += f"Proof Photo: {checkpoint['proof_photo']}\n"

        text_part = MIMEText(text, 'plain')

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shipment Checkpoint Update</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-100 font-sans">
    <div class="max-w-2xl mx-auto bg-white shadow-md rounded-lg overflow-hidden">
        <div class="bg-blue-600 text-white text-center py-4">
            <img src="{app.config['APP_BASE_URL']}/static/logo.png" alt="Courier Logo" class="h-12 mx-auto" style="max-width: 150px;">
            <h1 class="text-2xl font-bold mt-2">Shipment Checkpoint Update</h1>
        </div>
        <div class="p-6">
            <h2 class="text-xl font-semibold text-gray-800">Tracking: {tracking}</h2>
            <div class="mt-4 space-y-2">
                <p><span class="font-medium text-gray-700">Position:</span> {checkpoint['position']}</p>
                <p><span class="font-medium text-gray-700">Label:</span> {checkpoint['label']}</p>
                <p><span class="font-medium text-gray-700">Location:</span> ({checkpoint['lat']}, {checkpoint['lng']})</p>
                <p><span class="font-medium text-gray-700">Note:</span> {checkpoint['note'] or 'None'}</p>
                <p><span class="font-medium text-gray-700">Status:</span> {checkpoint['status'] or 'None'}</p>
                <p><span class="font-medium text-gray-700">Timestamp:</span> <script>document.write(formatTimestamp('{checkpoint['timestamp']}'))</script></p>
                <!-- Proof Photo: Only shown if available -->
                {f'<p><span class="font-medium text-gray-700">Proof Photo:</span> <a href="{checkpoint["proof_photo"]}" class="text-blue-600 hover:underline" target="_blank" rel="noopener">View</a></p>' if checkpoint.get('proof_photo') else ''}
            </div>
            <div class="mt-6 text-center">
                <a href="{app.config['APP_BASE_URL']}/track/{tracking}" class="inline-block bg-blue-600 text-white font-semibold py-2 px-4 rounded hover:bg-blue-700">
                    Track Shipment
                </a>
            </div>
        </div>
        <div class="bg-gray-200 text-gray-600 text-center py-4 text-sm">
            <p>&copy; {datetime.now().year} Courier Tracking Service. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        html_part = MIMEText(html, 'html')

        msg.attach(text_part)
        msg.attach(html_part)

        @retry(tries=3, delay=2, backoff=2, exceptions=(smtplib.SMTPException, smtplib.SMTPServerDisconnected))
        def send_email():
            try:
                server = get_smtp_connection()
                server.send_message(msg)
                logger.info(f"Sent checkpoint email to {email} for shipment {tracking}")
            except smtplib.SMTPAuthenticationError:
                logger.error(f"SMTP authentication failed for {email}")
                raise
            except smtplib.SMTPConnectError:
                logger.error(f"SMTP connection failed for {email}")
                raise
            except smtplib.SMTPException as e:
                logger.error(f"SMTP error sending email to {email}: {e}")
                raise

        send_email()
    except smtplib.SMTPAuthenticationError:
        logger.error(f"Authentication error sending checkpoint email to {email}")
        raise self.retry(exc=Exception("SMTP authentication failed"))
    except smtplib.SMTPConnectError:
        logger.error(f"Connection error sending checkpoint email to {email}")
        raise self.retry(exc=Exception("SMTP server unreachable"))
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending checkpoint email to {email}: {e}")
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"Unexpected error sending checkpoint email to {email}: {e}")
        raise

@shared_task(bind=True, max_retries=3, retry_backoff=2, retry_jitter=True)
def send_checkpoint_sms_async(self, tracking: str, phone: str, checkpoint: dict):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"Shipment {tracking} Update: Checkpoint {checkpoint['position']} ({checkpoint['label']}) at ({checkpoint['lat']}, {checkpoint['lng']}) - {checkpoint['timestamp']}",
            from_=TWILIO_PHONE_NUMBER,
            to=phone
        )
        logger.info(f"Sent checkpoint SMS to {phone} for shipment {tracking}")
    except TwilioRestException as e:
        logger.error(f"Twilio error sending SMS to {phone}: {e}")
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"Unexpected error sending SMS to {phone}: {e}")
        raise

@shared_task(bind=True, max_retries=3, retry_backoff=2, retry_jitter=True)
def send_tawkto_notification_email_async(self, chat_id: str, visitor_name: str, visitor_email: str, visitor_phone: str, tracking: str):
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"New Tawk.to Chat Started: {chat_id}"
        msg['From'] = app.config['SMTP_FROM']
        msg['To'] = app.config['ADMIN_EMAIL']

        text = f"""New Tawk.to Chat Notification

Chat ID: {chat_id}
Visitor: {visitor_name}
Email: {visitor_email}
Phone: {visitor_phone}
Tracking Number: {tracking}
Track: {app.config['APP_BASE_URL']}/track/{tracking}
"""
        text_part = MIMEText(text, 'plain')

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>New Chat Notification</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-100 font-sans">
    <div class="max-w-2xl mx-auto bg-white shadow-md rounded-lg overflow-hidden">
        <div class="bg-blue-600 text-white text-center py-4">
            <img src="{app.config['APP_BASE_URL']}/static/logo.png" alt="Courier Logo" class="h-12 mx-auto" style="max-width: 150px;">
            <h1 class="text-2xl font-bold mt-2">New Chat Notification</h1>
        </div>
        <div class="p-6">
            <h2 class="text-xl font-semibold text-gray-800">Chat ID: {chat_id}</h2>
            <div class="mt-4 space-y-2">
                <p><span class="font-medium text-gray-700">Visitor:</span> {visitor_name}</p>
                <p><span class="font-medium text-gray-700">Email:</span> {visitor_email}</p>
                <p><span class="font-medium text-gray-700">Phone:</span> {visitor_phone}</p>
                <p><span class="font-medium text-gray-700">Tracking Number:</span> {tracking}</p>
            </div>
            <div class="mt-6 text-center">
                <a href="{app.config['APP_BASE_URL']}/track/{tracking}" class="inline-block bg-blue-600 text-white font-semibold py-2 px-4 rounded hover:bg-blue-700">
                    Track Shipment
                </a>
            </div>
        </div>
        <div class="bg-gray-200 text-gray-600 text-center py-4 text-sm">
            <p>&copy; {datetime.now().year} Courier Tracking Service. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        html_part = MIMEText(html, 'html')

        msg.attach(text_part)
        msg.attach(html_part)

        @retry(tries=3, delay=2, backoff=2, exceptions=(smtplib.SMTPException, smtplib.SMTPServerDisconnected))
        def send_email():
            try:
                server = get_smtp_connection()
                server.send_message(msg)
                logger.info(f"Sent Tawk.to chat notification email to {app.config['ADMIN_EMAIL']} for chat {chat_id}")
            except smtplib.SMTPAuthenticationError:
                logger.error(f"SMTP authentication failed for {app.config['ADMIN_EMAIL']}")
                raise
            except smtplib.SMTPConnectError:
                logger.error(f"SMTP connection failed for {app.config['ADMIN_EMAIL']}")
                raise
            except smtplib.SMTPException as e:
                logger.error(f"SMTP error sending email to {app.config['ADMIN_EMAIL']}: {e}")
                raise

        send_email()
    except smtplib.SMTPAuthenticationError:
        logger.error(f"Authentication error sending Tawk.to notification email to {app.config['ADMIN_EMAIL']}")
        raise self.retry(exc=Exception("SMTP authentication failed"))
    except smtplib.SMTPConnectError:
        logger.error(f"Connection error sending Tawk.to notification email to {app.config['ADMIN_EMAIL']}")
        raise self.retry(exc=Exception("SMTP server unreachable"))
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending Tawk.to notification email to {app.config['ADMIN_EMAIL']}: {e}")
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"Unexpected error sending Tawk.to notification email to {app.config['ADMIN_EMAIL']}: {e}")
        raise

@shared_task(bind=True, max_retries=3, retry_backoff=2, retry_jitter=True)
def send_tawkto_notification_sms_async(self, chat_id: str, visitor_name: str, tracking: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"New Tawk.to chat started: {chat_id} by {visitor_name} for tracking {tracking}",
            from_=TWILIO_PHONE_NUMBER,
            to=app.config['ADMIN_PHONE']
        )
        logger.info(f"Sent Tawk.to chat notification SMS to {app.config['ADMIN_PHONE']} for chat {chat_id}")
    except TwilioRestException as e:
        logger.error(f"Twilio error sending Tawk.to notification SMS to {app.config['ADMIN_PHONE']}: {e}")
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"Failed to send Tawk.to notification SMS to {app.config['ADMIN_PHONE']}: {e}")
        raise

# Telegram Helper Functions
def send_message(text, chat_id, reply_markup=None):
    import requests
    url = f"https://api.telegram.org/bot{app.config['TELEGRAM_TOKEN']}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Sent Telegram message to {chat_id}: {text}")
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message to {chat_id}: {e}")

def get_admin_keyboard():
    return {
        'keyboard': [
            [{'text': '/create'}, {'text': '/update'}, {'text': '/list'}]
        ],
        'resize_keyboard': True,
        'one_time_keyboard': True
    }

def send_checkpoint_notifications(shipment, checkpoint):
    subscribers = Subscriber.query.filter_by(shipment_id=shipment.id).all()
    checkpoint_dict = checkpoint.to_dict()
    for subscriber in subscribers:
        if subscriber.email:
            send_checkpoint_email_async.delay(shipment.tracking, subscriber.email, checkpoint_dict)
        if subscriber.phone and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER:
            send_checkpoint_sms_async.delay(shipment.tracking, subscriber.phone, checkpoint_dict)

# Error Handlers
@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': e.description}), 400

@app.errorhandler(401)
def unauthorized(e):
    return jsonify({'error': e.description}), 401

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': e.description}), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'Rate limit exceeded. Please try again later.'}), 429

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    socketio.run(app, debug=False)

# Haversine formula for distance calculation
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance

def calculate_eta(distance_km, speed_kmh=50):
    return timedelta(hours=distance_km / speed_kmh)

# Geocoding function using Nominatim
@cache.memoize(timeout=86400)
def geocode_address(address: str) -> Dict[str, float]:
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "CourierTrackingApp/1.0 (contact@yourdomain.com)"},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError(f"Geocoding failed for address: {address}")
        location = data[0]
        return {"lat": float(location['lat']), "lng": float(location['lon'])}
    except requests.RequestException as e:
        logger.error(f"Nominatim geocoding error: {e}")
        raise ValueError(f"Failed to geocode address: {address}")

# Async email task
@shared_task(bind=True, max_retries=3, retry_backoff=2, retry_jitter=True)
def send_checkpoint_email_async(self, shipment: dict, checkpoint: dict, email: str):
    try:
        utc_time = datetime.fromisoformat(checkpoint['timestamp'].replace('Z', '+00:00'))
        wat_time = utc_time + timedelta(hours=1)
        wat_time_str = wat_time.strftime("%Y-%m-%d %I:%M:%S %p WAT")

        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Update: Shipment {shipment['tracking']} - {checkpoint['label']}"
        msg['From'] = current_app.config['SMTP_FROM']
        msg['To'] = email

        text = f"""Courier Tracking Update

Shipment: {shipment['title']} ({shipment['tracking']})
Status: {shipment['status']}
Updated: {wat_time_str}

Checkpoint:
- Label: {checkpoint['label']}
- Location: ({checkpoint['lat']:.4f}, {checkpoint['lng']:.4f})
- Note: {checkpoint['note'] or 'None'}

Track: {current_app.config['APP_BASE_URL']}/track/{shipment['tracking']}
Unsubscribe: {current_app.config['APP_BASE_URL']}/unsubscribe/{shipment['tracking']}?email={email}
"""
        text_part = MIMEText(text, 'plain')

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shipment Update</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-100 font-sans">
    <div class="max-w-2xl mx-auto bg-white shadow-md rounded-lg overflow-hidden">
        <div class="bg-blue-600 text-white text-center py-4">
            <img src="{current_app.config['APP_BASE_URL']}/static/logo.png" alt="Courier Logo" class="h-12 mx-auto" style="max-width: 150px;">
            <h1 class="text-2xl font-bold mt-2">Shipment Update</h1>
        </div>
        <div class="p-6">
            <h2 class="text-xl font-semibold text-gray-800">Shipment: {shipment['title']} ({shipment['tracking']})</h2>
            <div class="mt-4 space-y-2">
                <p><span class="font-medium text-gray-700">Status:</span> {shipment['status']}</p>
                <p><span class="font-medium text-gray-700">Updated:</span> {wat_time_str}</p>
            </div>
            <h3 class="text-lg font-semibold text-gray-800 mt-6">Latest Checkpoint</h3>
            <div class="mt-2 space-y-2">
                <p><span class="font-medium text-gray-700">Label:</span> {checkpoint['label']}</p>
                <p><span class="font-medium text-gray-700">Location:</span> ({checkpoint['lat']:.4f}, {checkpoint['lng']:.4f})</p>
                <p><span class="font-medium text-gray-700">Note:</span> {checkpoint['note'] or 'None'}</p>
            </div>
            <div class="mt-6 text-center">
                <a href="{current_app.config['APP_BASE_URL']}/track/{shipment['tracking']}" class="inline-block bg-blue-600 text-white font-semibold py-2 px-4 rounded hover:bg-blue-700">
                    Track Shipment
                </a>
            </div>
        </div>
        <div class="bg-gray-200 text-gray-600 text-center py-4 text-sm">
            <p>You're receiving this email because you're subscribed to updates for this shipment.</p>
            <p><a href="{current_app.config['APP_BASE_URL']}/unsubscribe/{shipment['tracking']}?email={email}" class="text-blue-600 hover:underline">Unsubscribe</a></p>
            <p>&copy; {datetime.now().year} Courier Tracking Service. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
"""
        html_part = MIMEText(html, 'html')

        msg.attach(text_part)
        msg.attach(html_part)

        @retry(tries=3, delay=2, backoff=2, exceptions=(smtplib.SMTPException, smtplib.SMTPServerDisconnected))
        def send_email():
            try:
                server = get_smtp_connection()
                server.send_message(msg)
                logger.info(f"Sent email to {email} for checkpoint {checkpoint['id']} of shipment {shipment['tracking']}")
            except smtplib.SMTPAuthenticationError:
                logger.error(f"SMTP authentication failed for {email}: Invalid credentials")
                raise
            except smtplib.SMTPConnectError:
                logger.error(f"SMTP connection failed for {email}: Server unreachable")
                raise
            except smtplib.SMTPException as e:
                logger.error(f"SMTP error sending email to {email}: {e}")
                raise

        send_email()
    except smtplib.SMTPAuthenticationError:
        logger.error(f"Authentication error sending email to {email} for checkpoint {checkpoint['id']}: SMTP credentials invalid")
        raise self.retry(exc=Exception("SMTP authentication failed"))
    except smtplib.SMTPConnectError:
        logger.error(f"Connection error sending email to {email} for checkpoint {checkpoint['id']}: SMTP server unreachable")
        raise self.retry(exc=Exception("SMTP server unreachable"))
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email to {email} for checkpoint {checkpoint['id']}: {e}")
        raise self.retry(exc=e)
    except Exception as e:
        logger.error(f"Unexpected error sending email to {email} for checkpoint {checkpoint['id']}: {e}")
        raise

# Celery task for SMS
@celery.task
def send_checkpoint_sms_async(shipment_dict, checkpoint_dict, phone):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=f"Update for {shipment_dict['tracking']}: {checkpoint_dict['label']} at {checkpoint_dict['timestamp']}",
            from_=TWILIO_PHONE_NUMBER,
            to=phone
        )
        logger.info(f"Sent SMS to {phone} for checkpoint {checkpoint_dict['id']} of shipment {shipment_dict['tracking']}")
    except Exception as e:
        logger.error(f"Failed to send SMS to {phone}: {e}")

# Pydantic models
class Coordinate(BaseModel):
    lat: float
    lng: float

    @validator('lat')
    def check_lat(cls, v):
        if not -90 <= v <= 90:
            raise ValueError('Latitude must be between -90 and 90')
        return v

    @validator('lng')
    def check_lng(cls, v):
        if not -180 <= v <= 180:
            raise ValueError('Longitude must be between -180 and 180')
        return v

class CheckpointCreate(BaseModel):
    lat: TypingOptional[float] = None
    lng: TypingOptional[float] = None
    address: TypingOptional[str] = None
    label: str
    note: TypingOptional[str] = None
    status: TypingOptional[str] = None
    proof_photo: TypingOptional[str] = None

    @validator('label')
    def check_label(cls, v):
        v = bleach.clean(v.strip())
        if not v:
            raise ValueError('Label cannot be empty')
        return v

    @validator('address', always=True)
    def check_coordinates_or_address(cls, v, values):
        if not v and (values.get('lat') is None or values.get('lng') is None):
            raise ValueError('Either address or lat/lng must be provided')
        if v and (values.get('lat') is not None or values.get('lng') is not None):
            raise ValueError('Provide either address or lat/lng, not both')
        return bleach.clean(v.strip()) if v else v

class ShipmentCreate(BaseModel):
    tracking_number: str
    title: str = "Consignment"
    origin: Union[Dict[str, float], str]
    destination: Union[Dict[str, float], str]
    status: str = ShipmentStatus.CREATED.value

    @validator('tracking_number')
    def check_tracking_number(cls, v):
        v = bleach.clean(v.strip())
        if not v:
            raise ValueError('Tracking number cannot be empty')
        return v

    @validator('title')
    def check_title(cls, v):
        v = bleach.clean(v.strip())
        if not v:
            raise ValueError('Title cannot be empty')
        return v

    @validator('origin', 'destination')
    def check_coordinates_or_address(cls, v):
        if isinstance(v, dict):
            if 'lat' not in v or 'lng' not in v:
                raise ValueError('Coordinates must be a dict with lat and lng')
            Coordinate(**v)
        elif not isinstance(v, str) or not v.strip():
            raise ValueError('Address must be a non-empty string')
        return bleach.clean(v.strip()) if isinstance(v, str) else v

    @validator('status')
    def check_status(cls, v):
        if v not in [status.value for status in ShipmentStatus]:
            raise ValueError(f"Status must be one of: {', '.join(status.value for status in ShipmentStatus)}")
        return v

# SQLAlchemy models
class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'status': self.status,
            'timestamp': self.timestamp.isoformat() + 'Z'
        }

class Shipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking = db.Column(db.String(50), unique=True, nullable=False)
    title = db.Column(db.String(100), nullable=False)
    origin_lat = db.Column(db.Float, nullable=False)
    origin_lng = db.Column(db.Float, nullable=False)
    dest_lat = db.Column(db.Float, nullable=False)
    dest_lng = db.Column(db.Float, nullable=False)
    origin_address = db.Column(db.String(200))
    dest_address = db.Column(db.String(200))
    distance_km = db.Column(db.Float)
    eta = db.Column(db.DateTime)
    status = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    checkpoints = db.relationship('Checkpoint', backref='shipment', lazy=True)
    subscribers = db.relationship('Subscriber', backref='shipment', lazy=True)
    history = db.relationship('StatusHistory', backref='shipment', lazy=True)
    simulation_state = db.relationship('SimulationState', backref='shipment', uselist=False, lazy=True)

    def calculate_distance_and_eta(self):
        self.distance_km = haversine(self.origin_lat, self.origin_lng, self.dest_lat, self.dest_lng)
        self.eta = datetime.utcnow() + calculate_eta(self.distance_km)

    def to_dict(self):
        return {
            'id': self.id,
            'tracking': self.tracking,
            'title': self.title,
            'origin': {'lat': self.origin_lat, 'lng': self.origin_lng, 'address': self.origin_address},
            'destination': {'lat': self.dest_lat, 'lng': self.dest_lng, 'address': self.dest_address},
            'distance_km': self.distance_km,
            'eta': self.eta.isoformat() + 'Z' if self.eta else None,
            'status': self.status,
            'created_at': self.created_at.isoformat() + 'Z',
            'checkpoints': [cp.to_dict() for cp in self.checkpoints]
        }

class Checkpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    label = db.Column(db.String(100), nullable=False)
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=True)
    proof_photo = db.Column(db.String(500), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'shipment_id': self.shipment_id,
            'position': self.position,
            'lat': self.lat,
            'lng': self.lng,
            'label': self.label,
            'note': self.note,
            'status': self.status,
            'proof_photo': self.proof_photo,
            'timestamp': self.timestamp.isoformat() + 'Z'
        }

class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, default=True)

class SimulationState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey('shipment.id'), nullable=False)
    tracking = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='running')
    current_position = db.Column(db.Integer, nullable=False, default=0)
    waypoints = db.Column(db.Text, nullable=False)
    current_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    num_points = db.Column(db.Integer, nullable=False)
    step_hours = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            'tracking': self.tracking,
            'status': self.status,
            'current_position': self.current_position,
            'waypoints': json.loads(self.waypoints),
            'current_time': self.current_time.isoformat() + 'Z',
            'num_points': self.num_points,
            'step_hours': self.step_hours
        }

# In-memory session store for Telegram admin authentication
admin_sessions = {}  # {chat_id: {'authenticated': True, 'expires': timestamp, 'state': {}}}

# Helper functions for admin route
def handle_shipment_form(request, shipments, simulation_states):
    try:
        tracking = bleach.clean(request.form.get('tracking', '').strip())
        title = bleach.clean(request.form.get('title', 'Consignment').strip())
        origin = bleach.clean(request.form.get('origin', '').strip())
        destination = bleach.clean(request.form.get('destination', '').strip())
        status = request.form.get('status')

        if not tracking or not origin or not destination or not status:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Missing required fields'), 400

        if len(tracking) > 50 or len(title) > 100:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Tracking or title too long'), 400

        if status not in [status.value for status in ShipmentStatus]:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Invalid status'), 400

        data = {
            'tracking_number': tracking,
            'title': title,
            'origin': origin,
            'destination': destination,
            'status': status
        }
        ShipmentCreate(**data)
        if ',' in origin and all(x.replace('.', '').isdigit() for x in origin.split(',')):
            origin_lat, origin_lng = map(float, origin.split(','))
            origin_address = None
        else:
            coords = geocode_address(origin)
            origin_lat, origin_lng = coords['lat'], coords['lng']
            origin_address = origin
        if ',' in destination and all(x.replace('.', '').isdigit() for x in destination.split(',')):
            dest_lat, dest_lng = map(float, destination.split(','))
            dest_address = None
        else:
            coords = geocode_address(destination)
            dest_lat, dest_lng = coords['lat'], coords['lng']
            dest_address = destination
        shipment = Shipment(
            tracking=tracking,
            title=title,
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=dest_lat,
            dest_lng=dest_lng,
            origin_address=origin_address,
            dest_address=dest_address,
            status=status
        )
        shipment.calculate_distance_and_eta()
        with db.session.begin():
            db.session.add(shipment)
            db.session.add(StatusHistory(shipment=shipment, status=status))
        socketio.emit('update', shipment.to_dict(), namespace='/', room=shipment.tracking)
        return redirect(url_for('admin'))
    except ValidationError as e:
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 400
    except ValueError as e:
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 400
    except IntegrityError:
        db.session.rollback()
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Tracking number already exists'), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in shipment creation: {e}")
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Database error'), 500

def handle_checkpoint_form(request, shipments, simulation_states):
    try:
        tracking = bleach.clean(request.form.get('tracking', '').strip())
        location = bleach.clean(request.form.get('location', '').strip())
        label = bleach.clean(request.form.get('label', '').strip())
        note = bleach.clean(request.form.get('note', '').strip()) or None
        status = request.form.get('status') or None
        proof_photo = bleach.clean(request.form.get('proof_photo', '').strip()) or None

        if not tracking or not location or not label:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Missing required fields'), 400

        if len(tracking) > 50 or len(label) > 100 or (note and len(note) > 500) or (proof_photo and len(proof_photo) > 500):
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Input too long'), 400

        valid_statuses = ['', *list(status.value for status in ShipmentStatus)]
        if status and status not in valid_statuses:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Invalid status'), 400

        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Shipment not found'), 404

        data = {
            'address': location if ',' not in location else None,
            'lat': float(location.split(',')[0]) if ',' in location and all(x.replace('.', '').isdigit() for x in location.split(',')) else None,
            'lng': float(location.split(',')[1]) if ',' in location and all(x.replace('.', '').isdigit() for x in location.split(',')) else None,
            'label': label,
            'note': note,
            'status': status,
            'proof_photo': proof_photo
        }
        CheckpointCreate(**data)
        if data['address']:
            coords = geocode_address(data['address'])
            lat, lng = coords['lat'], coords['lng']
        else:
            lat, lng = data['lat'], data['lng']
        position = db.session.query(db.func.max(Checkpoint.position)).filter_by(shipment_id=shipment.id).scalar() or 0
        checkpoint = Checkpoint(
            shipment_id=shipment.id,
            position=position + 1,
            lat=lat,
            lng=lng,
            label=data['label'],
            note=data['note'],
            status=data['status'],
            proof_photo=data['proof_photo']
        )
        with db.session.begin():
            db.session.add(checkpoint)
            if data['status']:
                shipment.status = data['status']
                db.session.add(StatusHistory(shipment=shipment, status=data['status']))
                if shipment.status == ShipmentStatus.DELIVERED.value:
                    shipment.eta = checkpoint.timestamp
        socketio.emit('update', shipment.to_dict(), namespace='/', room=tracking)
        for subscriber in shipment.subscribers:
            if subscriber.is_active:
                send_checkpoint_email_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.email)
                if subscriber.phone:
                    send_checkpoint_sms_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.phone)
        return redirect(url_for('admin'))
    except ValidationError as e:
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 400
    except ValueError as e:
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 400
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in checkpoint creation: {e}")
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Database error'), 500

def handle_simulation_form(request, shipments, simulation_states):
    try:
        tracking = bleach.clean(request.form.get('tracking', '').strip())
        num_points = request.form.get('num_points')
        step_hours = request.form.get('step_hours')

        if not tracking or not num_points or not step_hours:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Missing required fields'), 400

        num_points = int(num_points)
        step_hours = int(step_hours)
        if num_points < 2 or num_points > 10 or step_hours < 1 or step_hours > 24:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Number of points must be 2-10, step hours must be 1-24'), 400

        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Shipment not found'), 404
        if shipment.status == ShipmentStatus.DELIVERED.value:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Shipment already delivered'), 400
        simulation_state = SimulationState.query.filter_by(shipment_id=shipment.id).first()
        if simulation_state and simulation_state.status == 'running':
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Simulation already running'), 400
        run_simulation_async.delay(shipment.id, num_points, step_hours)
        return redirect(url_for('admin'))
    except ValueError as e:
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 400
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in simulation creation: {e}")
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Database error'), 500

def handle_subscribe_form(request, shipments, simulation_states):
    try:
        tracking = bleach.clean(request.form.get('tracking', '').strip())
        email = bleach.clean(request.form.get('email', '').strip())
        phone = bleach.clean(request.form.get('phone', '').strip()) or None

        if not tracking or (not email and not phone):
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Tracking number and either email or phone required'), 400

        if len(tracking) > 50 or (email and len(email) > 120) or (phone and len(phone) > 20):
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Input too long'), 400

        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Shipment not found'), 404

        subscriber = Subscriber(shipment_id=shipment.id, email=email, phone=phone)
        with db.session.begin():
            db.session.add(subscriber)
        return redirect(url_for('admin'))
    except IntegrityError:
        db.session.rollback()
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Already subscribed'), 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in subscription: {e}")
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Database error'), 500

def handle_track_multiple_form(request, shipments, simulation_states):
    try:
        tracking_numbers = bleach.clean(request.form.get('tracking_numbers', '').strip())
        if not tracking_numbers:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='At least one tracking number required'), 400

        tracking_list = [tn.strip() for tn in tracking_numbers.split(',') if tn.strip()]
        if not tracking_list:
            return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='No valid tracking numbers provided'), 400

        tracked_shipments = []
        for tn in tracking_list:
            shipment = Shipment.query.filter_by(tracking=tn).first()
            if shipment:
                tracked_shipments.append(shipment)
        return render_template('track_multiple.html', tracked_shipments=tracked_shipments)
    except SQLAlchemyError as e:
        logger.error(f"Database error in track multiple: {e}")
        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Database error'), 500

# Admin dashboard route
@app.route('/admin', methods=['GET', 'POST'])
@jwt_required()
def admin():
    try:
        shipments = Shipment.query.all()
        simulation_states = SimulationState.query.all()

        if request.method == 'POST':
            form_type = request.form.get('form_type')
            try:
                if form_type == 'shipment':
                    return handle_shipment_form(request, shipments, simulation_states)
                elif form_type == 'checkpoint':
                    return handle_checkpoint_form(request, shipments, simulation_states)
                elif form_type == 'simulation':
                    return handle_simulation_form(request, shipments, simulation_states)
                elif form_type == 'subscribe':
                    return handle_subscribe_form(request, shipments, simulation_states)
                elif form_type == 'track_multiple':
                    return handle_track_multiple_form(request, shipments, simulation_states)
                else:
                    return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error='Invalid form type'), 400
            except Exception as e:
                logger.error(f"Admin form error: {e}")
                return render_template('admin.html', shipments=shipments, simulation_states=simulation_states, error=str(e)), 500

        return render_template('admin.html', shipments=shipments, simulation_states=simulation_states)
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        return render_template('error.html', error='Failed to load admin dashboard'), 500

# Pause simulation route
@app.route('/pause_simulation/<tracking>', methods=['POST'])
@jwt_required()
def pause_simulation(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Shipment not found'), 404
        simulation_state = SimulationState.query.filter_by(shipment_id=shipment.id).first()
        if not simulation_state or simulation_state.status != 'running':
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='No active simulation to pause'), 400
        with db.session.begin():
            simulation_state.status = 'paused'
        return redirect(url_for('admin'))
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in pause simulation: {e}")
        return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Database error'), 500
    except Exception as e:
        logger.error(f"Pause simulation error for {tracking}: {e}")
        return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Failed to pause simulation'), 500

# Continue simulation route
@app.route('/continue_simulation/<tracking>', methods=['POST'])
@jwt_required()
def continue_simulation(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Shipment not found'), 404
        simulation_state = SimulationState.query.filter_by(shipment_id=shipment.id).first()
        if not simulation_state or simulation_state.status != 'paused':
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='No paused simulation to continue'), 400
        with db.session.begin():
            simulation_state.status = 'running'
        run_simulation_async.delay(shipment.id, simulation_state.num_points, simulation_state.step_hours)
        return redirect(url_for('admin'))
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in continue simulation: {e}")
        return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Database error'), 500
    except Exception as e:
        logger.error(f"Continue simulation error for {tracking}: {e}")
        return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Failed to continue simulation'), 500

# Keep-Alive / Ping endpoint
@app.route('/ping', methods=['GET'])
@limiter.limit("1000 per day")
def ping():
    try:
        db.session.execute('SELECT 1')
        return jsonify({'status': 'alive', 'timestamp': datetime.utcnow().isoformat() + 'Z'}), 200
    except Exception as e:
        logger.error(f"Ping failed: {e}")
        return jsonify({'status': 'unhealthy'}), 500

# Health endpoint
@app.route('/health')
@limiter.limit("1000 per day")
def health():
    try:
        db.session.execute('SELECT 1')
        celery_status = celery.control.ping(timeout=1)
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'celery': 'responsive' if celery_status else 'unresponsive',
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        })
    except SQLAlchemyError as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'unhealthy', 'database': 'disconnected'}), 500
    except Exception as e:
        logger.error(f"Health check exception: {e}")
        return jsonify({'status': 'unhealthy'}), 500

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'POST':
            password = request.form.get('password', '')
            if check_password_hash(app.config['ADMIN_PASSWORD_HASH'], password):
                access_token = create_access_token(identity='admin')
                response = redirect(url_for('admin'))
                response.set_cookie('access_token', access_token, httponly=True, secure=True)
                return response
            return render_template('login.html', error='Invalid password'), 401
        return render_template('login.html')
    except Exception as e:
        logger.error(f"Login error: {e}")
        return render_template('error.html', error='Login failed'), 400

@app.route('/track', methods=['GET'])
def track_redirect():
    try:
        tracking = bleach.clean(request.args.get('tracking', '').strip())
        if tracking:
            return redirect(url_for('track', tracking=tracking))
        return render_template('error.html', error='Tracking number required'), 400
    except Exception as e:
        logger.error(f"Track redirect error: {e}")
        return render_template('error.html', error='Failed to redirect to tracking'), 500

@app.route('/track/<tracking>')
@cache.cached(timeout=300, query_string=True)
def track(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return render_template('error.html', error='Shipment not found'), 404
        pagination = Checkpoint.query.filter_by(shipment_id=shipment.id).order_by(Checkpoint.position).paginate(page=page, per_page=per_page, error_out=False)
        checkpoints = [cp.to_dict() for cp in pagination.items]
        history = [h.to_dict() for h in shipment.history]
        return render_template(
            'track.html',
            shipment=shipment,
            checkpoints=checkpoints,
            history=history,
            pagination=pagination,
            origin_lat=shipment.origin_lat,
            origin_lng=shipment.origin_lng,
            dest_lat=shipment.dest_lat,
            dest_lng=shipment.dest_lng
        )
    except Exception as e:
        logger.error(f"Track error for {tracking}: {e}")
        return render_template('error.html', error='Failed to retrieve tracking data'), 500

@socketio.on('connect', namespace='/')
def handle_connect():
    try:
        tracking = bleach.clean(request.args.get('tracking', '').strip())
        if tracking:
            join_room(tracking)
            logger.info(f'Client connected to WebSocket for {tracking}')
            emit('status', {'message': 'Connected'})
    except Exception as e:
        logger.error(f"WebSocket connect error: {e}")

@socketio.on('subscribe')
def handle_subscribe(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        join_room(tracking)
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if shipment:
            emit('update', shipment.to_dict(), room=tracking)
    except Exception as e:
        logger.error(f"WebSocket subscribe error for {tracking}: {e}")
        emit('error', {'message': 'Failed to subscribe'})

@app.route('/shipments', methods=['GET'])
@jwt_required()
def list_shipments():
    try:
        shipments = Shipment.query.all()
        return [s.to_dict() for s in shipments]
    except Exception as e:
        logger.error(f"Shipment list error: {e}")
        return {'error': 'Failed to retrieve shipments'}, 500

@app.route('/shipments', methods=['POST'])
@jwt_required()
def create_shipment():
    try:
        data = request.get_json() or request.form
        data = {k: bleach.clean(v.strip()) if isinstance(v, str) else v for k, v in data.items()}
        shipment_data = ShipmentCreate(**data).dict()
        origin = shipment_data['origin']
        destination = shipment_data['destination']
        if isinstance(origin, str):
            coords = geocode_address(origin)
            origin_lat, origin_lng = coords['lat'], coords['lng']
            origin_address = origin
        else:
            origin_lat, origin_lng = origin['lat'], origin['lng']
            origin_address = None
        if isinstance(destination, str):
            coords = geocode_address(destination)
            dest_lat, dest_lng = coords['lat'], coords['lng']
            dest_address = destination
        else:
            dest_lat, dest_lng = destination['lat'], destination['lng']
            dest_address = None
        shipment = Shipment(
            tracking=shipment_data['tracking_number'],
            title=shipment_data['title'],
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=dest_lat,
            dest_lng=dest_lng,
            origin_address=origin_address,
            dest_address=dest_address,
            status=shipment_data['status']
        )
        shipment.calculate_distance_and_eta()
        with db.session.begin():
            db.session.add(shipment)
            db.session.add(StatusHistory(shipment=shipment, status=shipment_data['status']))
        socketio.emit('update', shipment.to_dict(), namespace='/', room=shipment.tracking)
        if request.form:
            return redirect(url_for('admin'))
        return shipment.to_dict(), 201
    except ValidationError as e:
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error=str(e)), 400
        return {'error': str(e)}, 400
    except ValueError as e:
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error=str(e)), 400
        return {'error': str(e)}, 400
    except IntegrityError:
        db.session.rollback()
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Tracking number already exists'), 409
        return {'error': 'Tracking number already exists'}, 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in shipment creation: {e}")
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Database error'), 500
        return {'error': 'Database error'}, 500
    except Exception as e:
        logger.error(f"Shipment creation error: {e}")
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Failed to create shipment'), 500
        return {'error': 'Failed to create shipment'}, 500

@app.route('/shipments/<tracking>/checkpoints', methods=['POST'])
@jwt_required()
def add_checkpoint(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            if request.form:
                return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Shipment not found'), 404
            return {'error': 'Shipment not found'}, 404
        data = request.get_json() or request.form
        data = {k: bleach.clean(v.strip()) if isinstance(v, str) else v for k, v in data.items()}
        checkpoint_data = CheckpointCreate(**data).dict()
        if checkpoint_data['address']:
            coords = geocode_address(checkpoint_data['address'])
            lat, lng = coords['lat'], coords['lng']
        else:
            lat, lng = checkpoint_data['lat'], checkpoint_data['lng']
        position = db.session.query(db.func.max(Checkpoint.position)).filter_by(shipment_id=shipment.id).scalar() or 0
        checkpoint = Checkpoint(
            shipment_id=shipment.id,
            position=position + 1,
            lat=lat,
            lng=lng,
            label=checkpoint_data['label'],
            note=checkpoint_data['note'],
            status=checkpoint_data['status'],
            proof_photo=checkpoint_data.get('proof_photo')
        )
        with db.session.begin():
            db.session.add(checkpoint)
            if checkpoint_data['status']:
                shipment.status = checkpoint_data['status']
                db.session.add(StatusHistory(shipment=shipment, status=checkpoint_data['status']))
                if shipment.status == ShipmentStatus.DELIVERED.value:
                    shipment.eta = checkpoint.timestamp
        socketio.emit('update', shipment.to_dict(), namespace='/', room=tracking)
        for subscriber in shipment.subscribers:
            if subscriber.is_active:
                send_checkpoint_email_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.email)
                if subscriber.phone:
                    send_checkpoint_sms_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.phone)
        if request.form:
            return redirect(url_for('admin'))
        return checkpoint.to_dict(), 201
    except ValidationError as e:
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error=str(e)), 400
        return {'error': str(e)}, 400
    except ValueError as e:
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error=str(e)), 400
        return {'error': str(e)}, 400
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in checkpoint creation: {e}")
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Database error'), 500
        return {'error': 'Database error'}, 500
    except Exception as e:
        logger.error(f"Checkpoint creation error for {tracking}: {e}")
        if request.form:
            return render_template('admin.html', shipments=Shipment.query.all(), simulation_states=SimulationState.query.all(), error='Failed to add checkpoint'), 500
        return {'error': 'Failed to add checkpoint'}, 500

@app.route('/shipments/<tracking>/subscribe', methods=['POST'])
def subscribe(tracking):
    try:
        tracking = bleach.clean(tracking.strip())
        data = request.get_json()
        email = bleach.clean(data.get('email', '').strip()) if data.get('email') else None
        phone = bleach.clean(data.get('phone', '').strip()) if data.get('phone') else None
        if not email and not phone:
            return {'error': 'Email or phone is required'}, 400
        shipment = Shipment.query.filter_by(tracking=tracking).first()
        if not shipment:
            return {'error': 'Shipment not found'}, 404
        subscriber = Subscriber(shipment_id=shipment.id, email=email or '', phone=phone or None)
        with db.session.begin():
            db.session.add(subscriber)
        return {'message': 'Subscribed successfully'}, 201
    except IntegrityError:
        db.session.rollback()
        return {'error': 'Already subscribed'}, 409
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in subscription: {e}")
        return {'error': 'Database error'}, 500
    except Exception as e:
        logger.error(f"Subscription error for {tracking}: {e}")
        return {'error': 'Failed to subscribe'}, 500

@app.route('/track_multiple', methods=['POST'])
def track_multiple():
    try:
        tracking_numbers = [bleach.clean(tn.strip()) for tn in request.get_json() if tn.strip()]
        if not tracking_numbers:
            return {'error': 'Tracking numbers required'}, 400
        shipments = []
        for tn in tracking_numbers:
            shipment = Shipment.query.filter_by(tracking=tn).first()
            if shipment:
                shipments.append(shipment.to_dict())
        return {'shipments': shipments}
    except Exception as e:
        logger.error(f"Track multiple error: {e}")
        return {'error': 'Failed to track shipments'}, 500

# Simulation logic
def generate_waypoints(start_lat, start_lng, end_lat, end_lng, num_points):
    waypoints = []
    for i in range(num_points + 1):
        t = i / num_points
        lat = start_lat + t * (end_lat - start_lat)
        lng = start_lng + t * (end_lng - start_lng)
        deviation = random.uniform(-0.01, 0.01)
        lat += deviation
        lng += deviation
        waypoints.append((lat, lng))
    return waypoints

@shared_task
def run_simulation_async(shipment_id, num_points, step_hours):
    try:
        shipment = Shipment.query.get(shipment_id)
        if not shipment:
            logger.error(f"Shipment {shipment_id} not found for simulation")
            return
        simulation_state = SimulationState.query.filter_by(shipment_id=shipment_id).first()
        start_position = simulation_state.current_position if simulation_state else 0
        waypoints = json.loads(simulation_state.waypoints) if simulation_state else generate_waypoints(
            shipment.origin_lat, shipment.origin_lng, shipment.dest_lat, shipment.dest_lng, num_points
        )
        current_time = simulation_state.current_time if simulation_state else datetime.utcnow()

        if not simulation_state:
            simulation_state = SimulationState(
                shipment_id=shipment.id,
                tracking=shipment.tracking,
                status='running',
                current_position=0,
                waypoints=json.dumps(waypoints),
                current_time=current_time,
                num_points=num_points,
                step_hours=step_hours
            )
            with db.session.begin():
                db.session.add(simulation_state)

        updates = []
        statuses = [ShipmentStatus.IN_TRANSIT.value, ShipmentStatus.OUT_FOR_DELIVERY.value, ShipmentStatus.DELIVERED.value]
        for i, (lat, lng) in enumerate(waypoints[start_position:], start_position + 1):
            if simulation_state.status != 'running':
                logger.info(f"Simulation paused or stopped for {shipment.tracking}")
                break
            status = statuses[min(i // (num_points // len(statuses)), len(statuses) - 1)] if i == num_points else None
            checkpoint = Checkpoint(
                shipment_id=shipment.id,
                position=i,
                lat=lat,
                lng=lng,
                label=f"Checkpoint {i}",
                note=f"Simulated checkpoint at {current_time.isoformat()}",
                status=status,
                timestamp=current_time
            )
            with db.session.begin():
                db.session.add(checkpoint)
                if status and status != shipment.status:
                    shipment.status = status
                    db.session.add(StatusHistory(shipment=shipment, status=status))
                    if shipment.status == ShipmentStatus.DELIVERED.value:
                        shipment.eta = checkpoint.timestamp
                simulation_state.current_position = i
                simulation_state.current_time = current_time
            updates.append(shipment.to_dict())
            if len(updates) >= 5:
                socketio.emit('update', updates[-1], namespace='/', room=shipment.tracking)
                updates = []
            for subscriber in shipment.subscribers:
                if subscriber.is_active:
                    send_checkpoint_email_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.email)
                    if subscriber.phone:
                        send_checkpoint_sms_async.delay(shipment.to_dict(), checkpoint.to_dict(), subscriber.phone)
            current_time += timedelta(hours=step_hours)
            socketio.sleep(1)  # Simulate delay
        if updates:
            socketio.emit('update', updates[-1], namespace='/', room=shipment.tracking)
        if simulation_state.status == 'running':
            with db.session.begin():
                simulation_state.status = 'completed'
        logger.info(f"Simulation completed for {shipment.tracking}")
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error in simulation for {shipment_id}: {e}")
    except Exception as e:
        logger.error(f"Simulation error for {shipment_id}: {e}")

# Telegram webhook
@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = request.get_json(force=True) or {}
        logger.debug(f"Got Telegram update: {json.dumps(update, indent=2)}")

        message = update.get('message', {})
        callback_query = update.get('callback_query', {})

        def send_message(text, chat_id, reply_markup=None, edit_message_id=None):
            try:
                payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
                if reply_markup:
                    payload['reply_markup'] = reply_markup
                if edit_message_id:
                    payload['message_id'] = edit_message_id
                    response = requests.post(
                        f"https://api.telegram.org/bot{current_app.config['TELEGRAM_TOKEN']}/editMessageText",
                        json=payload,
                        timeout=5
                    )
                else:
                    response = requests.post(
                        f"https://api.telegram.org/bot{current_app.config['TELEGRAM_TOKEN']}/sendMessage",
                        json=payload,
                        timeout=5
                    )
                response.raise_for_status()
                logger.info(f"Sent {'edited' if edit_message_id else 'new'} message to chat {chat_id}: '{text}'")
            except requests.RequestException as e:
                logger.error(f"Telegram message to {chat_id} failed: {e}")

        def get_navigation_keyboard(tracking=None):
            buttons = [
                [{'text': 'Create Shipment', 'callback_data': '/create'}],
                [{'text': 'Subscribe', 'callback_data': '/subscribe'}],
                [{'text': 'Add Checkpoint', 'callback_data': '/addcp'}],
                [{'text': 'Simulate', 'callback_data': '/simulate'}],
                [{'text': 'Track Multiple', 'callback_data': '/track_multiple'}]
            ]
            session = admin_sessions.get(chat_id)
            if session and session.get('authenticated') and datetime.utcnow() < session['expires']:
                buttons.insert(0, [{'text': 'Admin Panel', 'callback_data': '/admin_menu'}])
            if tracking:
                buttons.append([{'text': f'Track {tracking}', 'url': f'{current_app.config["APP_BASE_URL"]}/track/{tracking}'}])
            return {'inline_keyboard': buttons}

        def get_admin_keyboard():
            return {'inline_keyboard': [
                [{'text': 'List Shipments', 'callback_data': '/admin_list_shipments'}],
                [{'text': 'Create Shipment', 'callback_data': '/admin_create_shipment'}],
                [{'text': 'Update Status', 'callback_data': '/admin_update_status'}],
                [{'text': 'Delete Shipment', 'callback_data': '/admin_delete_shipment'}],
                [{'text': 'List Subscribers', 'callback_data': '/admin_list_subscribers'}],
                [{'text': 'Unsubscribe User', 'callback_data': '/admin_unsubscribe'}],
                [{'text': 'Pause Simulation', 'callback_data': '/admin_pause_sim'}],
                [{'text': 'Continue Simulation', 'callback_data': '/admin_continue_sim'}],
                [{'text': 'Health Check', 'callback_data': '/admin_health'}],
                [{'text': 'Logout', 'callback_data': '/admin_logout'}]
            ]}

        def get_shipment_selection_keyboard(command, prefix="select_shipment"):
            shipments = Shipment.query.all()
            buttons = [
                [{'text': f"{s.tracking}: {s.title}", 'callback_data': f"{prefix}|{command}|{s.tracking}"}]
                for s in shipments[:10]
            ]
            buttons.append([{'text': 'Back to Admin Menu', 'callback_data': '/admin_menu'}])
            return {'inline_keyboard': buttons}

        def get_status_selection_keyboard(tracking):
            buttons = [
                [{'text': status.value, 'callback_data': f"select_status|/admin_update_status|{tracking}|{status.value}"}]
                for status in ShipmentStatus
            ]
            buttons.append([{'text': 'Back to Admin Menu', 'callback_data': '/admin_menu'}])
            return {'inline_keyboard': buttons}

        def handle_callback_query(chat_id, callback_data, message_id):
            session = admin_sessions.get(chat_id)
            is_admin = session and session.get('authenticated') and datetime.utcnow() < session['expires']

            if not is_admin and callback_data.startswith('/admin_'):
                send_message("🚫 Not authorized! Use /admin_login &lt;password&gt; first. 🔐", chat_id, get_navigation_keyboard(), message_id)
                return jsonify({'error': 'Not authorized'}), 403

            parts = callback_data.split('|')
            command = parts[0]

            if command == '/create':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/create', 'step': 'tracking'}
                admin_sessions[chat_id] = session
                send_message("📦 Enter tracking number:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking'})

            elif command == '/subscribe':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/subscribe', 'step': 'tracking'}
                admin_sessions[chat_id] = session
                send_message("📩 Enter tracking number:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking'})

            elif command == '/addcp':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/addcp', 'step': 'tracking'}
                admin_sessions[chat_id] = session
                send_message("📍 Enter tracking number:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking'})

            elif command == '/simulate':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/simulate', 'step': 'tracking'}
                admin_sessions[chat_id] = session
                send_message("🚚 Enter tracking number:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking'})

            elif command == '/track_multiple':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/track_multiple', 'step': 'tracking_list'}
                admin_sessions[chat_id] = session
                send_message("📋 Enter tracking numbers (comma-separated):", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking list'})

            elif command == '/admin_menu':
                send_message("🔧 Admin Panel - Choose an action:", chat_id, get_admin_keyboard(), message_id)
                return jsonify({'message': 'Admin menu displayed'})

            elif command == '/admin_list_shipments':
                try:
                    shipments = Shipment.query.all()
                    if not shipments:
                        send_message("📦 No shipments found! Create one with /admin_create_shipment. 🚚", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'message': 'No shipments'})
                    info = [f"{s.tracking}: {s.title} - Status: {s.status} - Distance: {s.distance_km:.2f}km - ETA: {s.eta.isoformat() if s.eta else 'Not set'}" for s in shipments]
                    send_message(f"📦 Shipments ({len(shipments)}):\n" + "\n".join(info), chat_id, get_admin_keyboard(), message_id)
                    logger.info(f"Admin {chat_id} listed {len(shipments)} shipments")
                    return jsonify({'message': 'Listed shipments'})
                except SQLAlchemyError as e:
                    logger.error(f"Database error in /admin_list_shipments: {e}")
                    send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                    return jsonify({'error': 'Database error'}), 500

            elif command == '/admin_create_shipment':
                session = admin_sessions.get(chat_id, {})
                session['state'] = {'command': '/admin_create_shipment', 'step': 'tracking'}
                admin_sessions[chat_id] = session
                send_message("📦 Enter tracking number:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                return jsonify({'message': 'Prompted for tracking'})

            elif command == '/admin_update_status':
                send_message("📝 Select a shipment to update status:", chat_id, get_shipment_selection_keyboard('/admin_update_status'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_delete_shipment':
                send_message("🗑️ Select a shipment to delete:", chat_id, get_shipment_selection_keyboard('/admin_delete_shipment'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_list_subscribers':
                send_message("📩 Select a shipment to list subscribers:", chat_id, get_shipment_selection_keyboard('/admin_list_subscribers'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_unsubscribe':
                send_message("📩 Select a shipment to unsubscribe a user:", chat_id, get_shipment_selection_keyboard('/admin_unsubscribe'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_pause_sim':
                send_message("⏸️ Select a shipment to pause simulation:", chat_id, get_shipment_selection_keyboard('/admin_pause_sim'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_continue_sim':
                send_message("▶️ Select a shipment to continue simulation:", chat_id, get_shipment_selection_keyboard('/admin_continue_sim'), message_id)
                return jsonify({'message': 'Prompted for shipment selection'})

            elif command == '/admin_health':
                try:
                    db.session.execute('SELECT 1')
                    celery_status = celery.control.ping(timeout=1)
                    health_info = (
                        f"🩺 Server Health:\n"
                        f"- Database: {'connected' if db.session.execute('SELECT 1') else 'disconnected'}\n"
                        f"- Celery: {'responsive' if celery_status else 'unresponsive'}\n"
                        f"- Timestamp: {datetime.utcnow().isoformat()}Z"
                    )
                    send_message(health_info, chat_id, get_admin_keyboard(), message_id)
                    logger.info(f"Admin {chat_id} checked server health")
                    return jsonify({'message': 'Health checked'})
                except SQLAlchemyError as e:
                    logger.error(f"Database error in /admin_health: {e}")
                    send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                    return jsonify({'error': 'Database error'}), 500

            elif command == '/admin_logout':
                admin_sessions.pop(chat_id, None)
                logger.info(f"Admin session ended for chat {chat_id}")
                send_message("🔐 Admin session ended! 👋", chat_id, get_navigation_keyboard(), message_id)
                return jsonify({'message': 'Logged out'})

            elif command == '/cancel':
                session = admin_sessions.get(chat_id, {})
                session.pop('state', None)
                admin_sessions[chat_id] = session
                send_message("❌ Action cancelled.", chat_id, get_admin_keyboard() if is_admin else get_navigation_keyboard(), message_id)
                return jsonify({'message': 'Cancelled'})

            elif command == 'select_shipment':
                _, sub_command, tracking = parts
                if sub_command == '/admin_update_status':
                    send_message(f"📝 Select new status for {tracking}:", chat_id, get_status_selection_keyboard(tracking), message_id)
                elif sub_command == '/admin_delete_shipment':
                    shipment = Shipment.query.filter_by(tracking=tracking).first()
                    if not shipment:
                        send_message("🔍 Shipment not found! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Shipment not found'}), 404
                    try:
                        with db.session.begin():
                            db.session.delete(shipment)
                        socketio.emit('update', {'tracking': tracking, 'deleted': True}, namespace='/', room=tracking)
                        logger.info(f"Admin {chat_id} deleted shipment {tracking}")
                        send_message(f"🗑️ Shipment {tracking} deleted!", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'message': 'Shipment deleted'})
                    except SQLAlchemyError as e:
                        db.session.rollback()
                        logger.error(f"Database error in /admin_delete_shipment: {e}")
                        send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Database error'}), 500
                elif sub_command == '/admin_list_subscribers':
                    shipment = Shipment.query.filter_by(tracking=tracking).first()
                    if not shipment:
                        send_message("🔍 Shipment not found! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Shipment not found'}), 404
                    subscribers = Subscriber.query.filter_by(shipment_id=shipment.id, is_active=True).all()
                    if not subscribers:
                        send_message(f"📩 No subscribers for {tracking}!", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'message': 'No subscribers'})
                    info = [f"- {s.email or s.phone}" for s in subscribers]
                    send_message(f"📩 Subscribers for {tracking} ({len(subscribers)}):\n" + "\n".join(info), chat_id, get_admin_keyboard(), message_id)
                    logger.info(f"Admin {chat_id} listed subscribers for {tracking}")
                    return jsonify({'message': 'Listed subscribers'})
                elif sub_command == '/admin_unsubscribe':
                    session = admin_sessions.get(chat_id, {})
                    session['state'] = {'command': '/admin_unsubscribe', 'step': 'contact', 'tracking': tracking}
                    admin_sessions[chat_id] = session
                    send_message(f"📩 Enter email or phone to unsubscribe from {tracking}:", chat_id, {'inline_keyboard': [[{'text': 'Cancel', 'callback_data': '/cancel'}]]}, message_id)
                    return jsonify({'message': 'Prompted for contact'})
                elif sub_command == '/admin_pause_sim':
                    shipment = Shipment.query.filter_by(tracking=tracking).first()
                    if not shipment:
                        send_message("🔍 Shipment not found! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Shipment not found'}), 404
                    simulation_state = SimulationState.query.filter_by(shipment_id=shipment.id).first()
                    if not simulation_state or simulation_state.status != 'running':
                        send_message("⏸️ No active simulation to pause! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'No active simulation'}), 400
                    try:
                        with db.session.begin():
                            simulation_state.status = 'paused'
                        logger.info(f"Admin {chat_id} paused simulation for {tracking}")
                        send_message(f"⏸️ Simulation paused for {tracking}!", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'message': 'Simulation paused'})
                    except SQLAlchemyError as e:
                        db.session.rollback()
                        logger.error(f"Database error in /admin_pause_sim: {e}")
                        send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Database error'}), 500
                elif sub_command == '/admin_continue_sim':
                    shipment = Shipment.query.filter_by(tracking=tracking).first()
                    if not shipment:
                        send_message("🔍 Shipment not found! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Shipment not found'}), 404
                    simulation_state = SimulationState.query.filter_by(shipment_id=shipment.id).first()
                    if not simulation_state or simulation_state.status != 'paused':
                        send_message("▶️ No paused simulation to continue! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'No paused simulation'}), 400
                    try:
                        with db.session.begin():
                            simulation_state.status = 'running'
                        run_simulation_async.delay(shipment.id, simulation_state.num_points, simulation_state.step_hours)
                        logger.info(f"Admin {chat_id} continued simulation for {tracking}")
                        send_message(f"▶️ Simulation continued for {tracking}!", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'message': 'Simulation continued'})
                    except SQLAlchemyError as e:
                        db.session.rollback()
                        logger.error(f"Database error in /admin_continue_sim: {e}")
                        send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                        return jsonify({'error': 'Database error'}), 500

            elif command == 'select_status':
                _, sub_command, tracking, status = parts
                shipment = Shipment.query.filter_by(tracking=tracking).first()
                if not shipment:
                    send_message("🔍 Shipment not found! 😿", chat_id, get_admin_keyboard(), message_id)
                    return jsonify({'error': 'Shipment not found'}), 404
                try:
                    with db.session.begin():
                        shipment.status = status
                        db.session.add(StatusHistory(shipment=shipment, status=status))
                    socketio.emit('update', shipment.to_dict(), namespace='/', room=tracking)
                    logger.info(f"Admin {chat_id} updated status of {tracking} to {status}")
                    send_message(f"📝 Status updated for {tracking} to {status}!", chat_id, get_admin_keyboard(), message_id)
                    return jsonify({'message': 'Status updated'})
                except SQLAlchemyError as e:
                    db.session.rollback()
                    logger.error(f"Database error in /admin_update_status: {e}")
                    send_message("💾 Database error! 😿", chat_id, get_admin_keyboard(), message_id)
                    return jsonify({'error': 'Database error'}), 500

            else:
                send_message("❓ Unknown action. Back to menu.", chat_id, get_admin_keyboard() if is_admin else get_navigation_keyboard(), message_id)
                return jsonify({'error': 'Unknown callback'}), 400

        if callback_query:
            chat_id = callback_query['message']['chat']['id']
            callback_data = callback_query['data']
            message_id = callback_query['message']['message_id']
            return handle_callback_query(chat_id, callback_data, message_id)

        chat_id = message.get('chat', {}).get('id')
        text = bleach.clean(message.get('text', '').strip())
        if not chat_id or not text:
            send_message("🚫 Invalid message! Missing chat_id or text 😿", chat_id, get_navigation_keyboard())
            return jsonify({'error': 'Invalid message'}), 400

        command, *args = text.split(' ', 1)
        args = args[0].split('|') if args else []

        if command == '/start':
            send_message(
                "👋 Welcome to the Courier Tracking Bot! 🚚\n"
                "Track shipments, subscribe for updates, or manage shipments as an admin.\n"
                "Commands:\n"
                "/track &lt;tracking_number&gt; - Track a shipment\n"
                "/admin_login &lt;password&gt; - Login as admin\n"
                "Use the buttons below to navigate:",
                chat_id,
                get_navigation_keyboard()
            )
            return jsonify({'message': 'Start command processed'})

        elif command == '/track':
            if not args or not args[0].strip():
                send_message("❌ Please provide a tracking number! Usage: /track &lt;tracking_number&gt;", chat_id, get_navigation_keyboard())
                return jsonify({'error': 'Missing tracking number'}), 400
            tracking = bleach.clean(args[0].strip())
            shipment = Shipment.query.filter_by(tracking=tracking).first()
            if not shipment:
                send_message(f"🔍 Shipment {tracking} not found! 😿", chat_id, get_navigation_keyboard())
                return jsonify({'error': 'Shipment not found'}), 404
            checkpoints = Checkpoint.query.filter_by(shipment_id=shipment.id).order_by(Checkpoint.position)
            if not checkpoints:
                send_message(f"📍 No checkpoints found for {tracking}.", chat_id, get_navigation_keyboard())
                return jsonify({'error': 'No checkpoints found'}), 404
            first_checkpoint = checkpoints.first()
            eta = shipment.eta.isoformat() + 'Z' if shipment.eta else 'Not available'
            distance = shipment.distance_km if shipment.distance_km is not None else 'Calculating...'
            send_message(
                f"📦 Shipment {tracking} - {shipment.title}\n"
                f"Status: {shipment.status}\n"
                f"Distance: {distance:.2f} km\n"
                f"ETA: {eta}\n\n"
                f"📍 Latest Checkpoint:\n"
                f"- Label: {first_checkpoint.label}\n"
                f"- Location: ({first_checkpoint.lat}, {first_checkpoint.lng})\n"
                f"- Note: {first_checkpoint.note or 'None'}\n"
                f"- Status: {first_checkpoint.status or 'None'}\n"
                f"- Timestamp: {first_checkpoint.timestamp.isoformat()}Z\n"
                f"Track: {app.config['APP_BASE_URL']}/track/{tracking}",
                chat_id,
                get_navigation_keyboard()
            )
            return jsonify({'message': 'Tracked shipment'})
        elif command == '/admin_login':
            if not args or len(args) != 1:
                send_message("🔑 Usage: /admin_login &lt;password&gt;", chat_id)
                return jsonify({'error': 'Invalid admin login'}), 400
            password = args[0]
            if check_password_hash(app.config['ADMIN_PASSWORD_HASH'], password):
                session = admin_sessions.setdefault(chat_id, {})
                session['authenticated'] = True
                session['expires'] = datetime.utcnow() + timedelta(hours=1)
                send_message("✅ Admin login successful! Welcome back.", chat_id, get_admin_keyboard())
                logger.info(f"Admin {chat_id} logged in")
            else:
                send_message("❌ Invalid password! Access denied.", chat_id)
                logger.warning(f"Failed admin login attempt for {chat_id}")
            return jsonify({'message': 'Admin login processed'})
        else:
            send_message("❓ Unknown command. Use /track or /admin_login.", chat_id, get_navigation_keyboard())
            return jsonify({'error': 'Unknown command'}), 400
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return jsonify({'error': 'Failed to process Telegram update'}, 500)
