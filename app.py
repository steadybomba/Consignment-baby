"""
Main Flask app for Consignment Tracker (updated for thread-safety, validation, security, and async email).
"""

import os
import threading
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional, Dict, Any

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, send_from_directory, current_app, session
)
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from celery import Celery
from pydantic import BaseModel, Field, ValidationError
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.from_mapping(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///consignment.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY=os.environ.get("JWT_SECRET_KEY", "super-secret-key"),
    CELERY_BROKER_URL=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    ADMIN_PASSWORD_HASH=generate_password_hash(os.environ.get("ADMIN_PASSWORD", "change-me")),
    SMTP_HOST=os.environ.get("SMTP_HOST", ""),
    SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
    SMTP_USER=os.environ.get("SMTP_USER", ""),
    SMTP_PASS=os.environ.get("SMTP_PASS", ""),
    SMTP_FROM=os.environ.get("SMTP_FROM", "no-reply@example.com"),
    APP_BASE_URL=os.environ.get("APP_BASE_URL", "http://localhost:5000")
)

# Initialize extensions
db = SQLAlchemy(app)
jwt = JWTManager(app)
socketio = SocketIO(app, cors_allowed_origins="*")
celery = Celery(app.name, broker=app.config["CELERY_BROKER_URL"])
celery.conf.update(app.config)

# Rate limiting
limiter = Limiter(app, key_func=get_remote_address)

# Models
class Shipment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking = db.Column(db.String(50), unique=True, nullable=False)
    title = db.Column(db.String(100))
    origin_lat = db.Column(db.Float, nullable=False)
    origin_lng = db.Column(db.Float, nullable=False)
    dest_lat = db.Column(db.Float, nullable=False)
    dest_lng = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="Created")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tracking": self.tracking,
            "title": self.title,
            "origin_lat": self.origin_lat,
            "origin_lng": self.origin_lng,
            "dest_lat": self.dest_lat,
            "dest_lng": self.dest_lng,
            "status": self.status,
            "updated_at": self.updated_at
        }

class Checkpoint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipment.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    label = db.Column(db.String(50), nullable=False)
    note = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "shipment_id": self.shipment_id,
            "position": self.position,
            "lat": self.lat,
            "lng": self.lng,
            "label": self.label,
            "note": self.note,
            "timestamp": self.timestamp
        }

class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipment.id"), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

# Pydantic models for validation
class CheckpointCreate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    label: str = Field(..., min_length=1)
    note: Optional[str] = None

class ShipmentCreate(BaseModel):
    tracking_number: str = Field(..., min_length=1)
    title: str = "Consignment"
    origin: Dict[str, float]
    destination: Dict[str, float]
    status: str = "Created"

# Security headers
@app.after_request
def set_security_headers(response):
    headers = {
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer-when-downgrade",
        "Content-Security-Policy": "default-src 'self' 'unsafe-inline' data: https:;"
    }
    for k, v in headers.items():
        response.headers.setdefault(k, v)
    return response

# Routes
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/track/<tracking>")
def track_page(tracking):
    shipment = Shipment.query.filter_by(tracking=tracking).first_or_404()
    checkpoints = Checkpoint.query.filter_by(shipment_id=shipment.id).order_by(Checkpoint.position).all()
    return render_template("track.html", shipment=shipment, checkpoints=checkpoints)

# Authentication
@app.route("/admin/login", methods=["POST"])
@limiter.limit("5/minute")
def admin_login():
    data = request.get_json()
    if not data or "user" not in data or "password" not in data:
        return jsonify({"error": "Missing credentials"}), 400
    
    if (data["user"] == os.environ.get("ADMIN_USER", "admin") and 
        check_password_hash(app.config["ADMIN_PASSWORD_HASH"], data["password"])):
        access_token = create_access_token(identity=data["user"])
        return jsonify(access_token=access_token)
    
    return jsonify({"error": "Invalid credentials"}), 401

# API Endpoints
@app.route("/api/shipments/<tracking>")
def api_get_shipment(tracking):
    shipment = Shipment.query.filter_by(tracking=tracking).first_or_404()
    checkpoints = Checkpoint.query.filter_by(shipment_id=shipment.id).order_by(Checkpoint.position).all()
    return jsonify({
        "tracking": shipment.tracking,
        "title": shipment.title,
        "status": shipment.status,
        "origin": {"lat": shipment.origin_lat, "lng": shipment.origin_lng},
        "destination": {"lat": shipment.dest_lat, "lng": shipment.dest_lng},
        "updated_at": shipment.updated_at,
        "checkpoints": [{
            "lat": cp.lat,
            "lng": cp.lng,
            "label": cp.label,
            "note": cp.note,
            "timestamp": cp.timestamp
        } for cp in checkpoints]
    })

@app.route("/api/shipments", methods=["POST"])
def api_create_shipment():
    try:
        data = ShipmentCreate(**request.get_json())
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    if Shipment.query.filter_by(tracking=data.tracking_number).first():
        return jsonify({"error": "Tracking number exists"}), 400

    shipment = Shipment(
        tracking=data.tracking_number,
        title=data.title,
        origin_lat=data.origin["lat"],
        origin_lng=data.origin["lng"],
        dest_lat=data.destination["lat"],
        dest_lng=data.destination["lng"],
        status=data.status
    )
    db.session.add(shipment)
    db.session.commit()
    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/checkpoints", methods=["POST"])
def api_add_checkpoint(tracking):
    shipment = Shipment.query.filter_by(tracking=tracking).first_or_404()
    
    try:
        data = CheckpointCreate(**request.get_json())
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    position = Checkpoint.query.filter_by(shipment_id=shipment.id).count()
    checkpoint = Checkpoint(
        shipment_id=shipment.id,
        position=position,
        lat=data.lat,
        lng=data.lng,
        label=data.label,
        note=data.note
    )
    db.session.add(checkpoint)
    shipment.updated_at = datetime.utcnow()
    db.session.commit()

    # Celery task for async email
    send_checkpoint_email_task.delay(shipment.id, checkpoint.id)
    
    return jsonify({"ok": True}), 201

# Celery task for email
@celery.task
def send_checkpoint_email_task(shipment_id: int, checkpoint_id: int):
    from email_utils import send_checkpoint_email_async
    shipment = Shipment.query.get(shipment_id)
    checkpoint = Checkpoint.query.get(checkpoint_id)
    if shipment and checkpoint:
        send_checkpoint_email_async(shipment.to_dict(), checkpoint.to_dict())

# WebSocket
@socketio.on("subscribe")
def handle_subscribe(tracking):
    shipment = Shipment.query.filter_by(tracking=tracking).first()
    if shipment:
        checkpoints = Checkpoint.query.filter_by(shipment_id=shipment.id).order_by(Checkpoint.position).all()
        emit("update", {
            "tracking": shipment.tracking,
            "checkpoints": [{
                "lat": cp.lat,
                "lng": cp.lng,
                "label": cp.label
            } for cp in checkpoints]
        })

# Admin routes
@app.route("/api/admin/shipments")
@jwt_required()
def api_admin_shipments():
    shipments = Shipment.query.order_by(Shipment.updated_at.desc()).all()
    return jsonify([{
        "id": s.id,
        "tracking_number": s.tracking,
        "title": s.title,
        "status": s.status,
        "origin": {"lat": s.origin_lat, "lng": s.origin_lng},
        "destination": {"lat": s.dest_lat, "lng": s.dest_lng},
        "updated_at": s.updated_at
    } for s in shipments])

# Initialize DB
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database initialized.")

# Telegram bot integration
def start_telegram_bot():
    if not os.getenv("TELEGRAM_TOKEN"):
        return
    
    try:
        from telegram_bot import start_bot_async
        start_bot_async()
        app.logger.info("Telegram bot started in background")
    except ImportError:
        app.logger.warning("Telegram bot module not available")
    except Exception as e:
        app.logger.error("Failed to start Telegram bot: %s", str(e))

if __name__ == "__main__":
    # Initialize database
    with app.app_context():
        db.create_all()
    
    # Start Telegram bot if configured
    start_telegram_bot()
    
    # Run app
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=bool(os.getenv("FLASK_DEBUG")))
