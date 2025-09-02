import os
import smtplib
from email.message import EmailMessage
from datetime import datetime
from itsdangerous import URLSafeSerializer, BadSignature

from flask import Flask, jsonify, render_template, request, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
from email_validator import validate_email, EmailNotValidError

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
    db_url = os.getenv("DATABASE_URL", "sqlite:///tracker.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["SMTP_HOST"] = os.getenv("SMTP_HOST", "")
    app.config["SMTP_PORT"] = int(os.getenv("SMTP_PORT", "587"))
    app.config["SMTP_USER"] = os.getenv("SMTP_USER", "")
    app.config["SMTP_PASS"] = os.getenv("SMTP_PASS", "")
    app.config["SMTP_FROM"] = os.getenv("SMTP_FROM", "no-reply@example.com")
    app.config["SMTP_USE_TLS"] = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    app.config["APP_BASE_URL"] = os.getenv("APP_BASE_URL", "http://localhost:5000")
    return app

app = create_app()
db = SQLAlchemy(app)

class Shipment(db.Model):
    __tablename__ = "shipments"
    id = db.Column(db.Integer, primary_key=True)
    tracking_number = db.Column(db.String(24), unique=True, index=True, nullable=False)
    title = db.Column(db.String(120), nullable=False, default="Consignment")
    origin_lat = db.Column(db.Float, nullable=False)
    origin_lng = db.Column(db.Float, nullable=False)
    dest_lat = db.Column(db.Float, nullable=False)
    dest_lng = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), nullable=False, default="Created")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    checkpoints = relationship("Checkpoint", back_populates="shipment", order_by="Checkpoint.position.asc()", cascade="all, delete-orphan")
    subscribers = relationship("Subscriber", back_populates="shipment", cascade="all, delete-orphan")

class Checkpoint(db.Model):
    __tablename__ = "checkpoints"
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    label = db.Column(db.String(120), nullable=False, default="Scanned")
    note = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="checkpoints")

class Subscriber(db.Model):
    __tablename__ = "subscribers"
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipment = relationship("Shipment", back_populates="subscribers")

def serializer():
    return URLSafeSerializer(app.config["SECRET_KEY"], salt="subs")

def unsubscribe_token(subscriber_id):
    return serializer().dumps({"sid": int(subscriber_id)})

def verify_unsubscribe_token(token):
    try:
        data = serializer().loads(token)
        return int(data.get("sid"))
    except Exception:
        return None

def send_email(to_email, subject, html, text=None):
    host = app.config["SMTP_HOST"]
    if not host:
        print(f"[DEV] Email to {to_email} (subject: {subject})\n{html}")
        return
    msg = EmailMessage()
    msg["From"] = app.config["SMTP_FROM"]
    msg["To"] = to_email
    msg["Subject"] = subject
    if text:
        msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, app.config["SMTP_PORT"]) as s:
        if app.config["SMTP_USE_TLS"]:
            s.starttls()
        if app.config["SMTP_USER"]:
            s.login(app.config["SMTP_USER"], app.config["SMTP_PASS"])
        s.send_message(msg)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        form = request.form
        tracking = (form.get("tracking_number") or "").strip()
        title = (form.get("title") or "Consignment").strip() or "Consignment"
        origin_lat = float(form.get("origin_lat"))
        origin_lng = float(form.get("origin_lng"))
        dest_lat = float(form.get("dest_lat"))
        dest_lng = float(form.get("dest_lng"))
        status = form.get("status") or "Created"
        if not tracking:
            abort(400, "Tracking number required")
        existing = Shipment.query.filter_by(tracking_number=tracking).first()
        if existing:
            abort(400, "Tracking number already exists")
        shp = Shipment(
            tracking_number=tracking,
            title=title,
            origin_lat=origin_lat, origin_lng=origin_lng,
            dest_lat=dest_lat, dest_lng=dest_lng,
            status=status,
        )
        db.session.add(shp)
        db.session.commit()
        return redirect(url_for("track_page", tracking=tracking))
    return render_template("admin.html")

@app.route("/track/<tracking>")
def track_page(tracking):
    shipment = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    return render_template("track.html", tracking=tracking, title=shipment.title)

@app.route("/api/shipments/<tracking>")
def api_get_shipment(tracking):
    shp = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    return jsonify({
        "tracking_number": shp.tracking_number,
        "title": shp.title,
        "status": shp.status,
        "origin": {"lat": shp.origin_lat, "lng": shp.origin_lng},
        "destination": {"lat": shp.dest_lat, "lng": shp.dest_lng},
        "updated_at": shp.updated_at.isoformat(),
        "checkpoints": [
            {
                "position": cp.position,
                "lat": cp.lat,
                "lng": cp.lng,
                "label": cp.label,
                "note": cp.note,
                "timestamp": cp.timestamp.isoformat()
            } for cp in shp.checkpoints
        ]
    })

@app.route("/api/shipments", methods=["POST"])
def api_create_shipment():
    data = request.json or {}
    required = ["tracking_number","origin","destination"]
    for k in required:
        if k not in data:
            abort(400, "Missing field: %s" % k)
    existing = Shipment.query.filter_by(tracking_number=data["tracking_number"]).first()
    if existing:
        abort(400, "Tracking number already exists")
    shp = Shipment(
        tracking_number=data["tracking_number"],
        title=data.get("title","Consignment"),
        origin_lat=float(data["origin"]["lat"]),
        origin_lng=float(data["origin"]["lng"]),
        dest_lat=float(data["destination"]["lat"]),
        dest_lng=float(data["destination"]["lng"]),
        status=data.get("status","Created"),
    )
    db.session.add(shp)
    db.session.commit()
    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/checkpoints", methods=["POST"])
def api_add_checkpoint(tracking):
    shp = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    data = request.json or {}
    cp = Checkpoint(
        shipment_id=shp.id,
        position=int(data.get("position", len(shp.checkpoints))),
        lat=float(data["lat"]),
        lng=float(data["lng"]),
        label=data.get("label","Scanned"),
        note=data.get("note"),
        timestamp=datetime.utcnow(),
    )
    shp.status = data.get("status", shp.status)
    shp.updated_at = datetime.utcnow()
    db.session.add(cp)
    db.session.commit()

    active_subs = [s for s in shp.subscribers if s.is_active]
    for s in active_subs:
        send_checkpoint_email(shp, s, cp)

    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/subscribe", methods=["POST"])
def api_subscribe(tracking):
    shp = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    try:
        email = validate_email(email).normalized
    except EmailNotValidError as e:
        abort(400, "Invalid email: %s" % str(e))
    sub = Subscriber.query.filter_by(shipment_id=shp.id, email=email).first()
    if not sub:
        sub = Subscriber(shipment_id=shp.id, email=email, is_active=True)
        db.session.add(sub)
    else:
        sub.is_active = True
    db.session.commit()
    send_email(
        to_email=email,
        subject="Subscribed to %s (%s) updates" % (shp.title, shp.tracking_number),
        html=render_template("emails/subscribed.html", shipment=shp, unsubscribe_url=unsubscribe_url(sub))
    )
    return jsonify({"ok": True})

def unsubscribe_url(subscriber):
    token = unsubscribe_token(subscriber.id)
    return "%s%s" % (app.config['APP_BASE_URL'], url_for('unsubscribe', token=token))

@app.route("/unsubscribe/<token>")
def unsubscribe(token):
    sid = verify_unsubscribe_token(token)
    if not sid:
        abort(400, "Invalid or expired token")
    sub = Subscriber.query.get(sid)
    if not sub:
        abort(404)
    sub.is_active = False
    db.session.commit()
    return render_template("unsubscribed.html", email=sub.email)

def send_checkpoint_email(shipment, subscriber, checkpoint):
    url = "%s%s" % (app.config['APP_BASE_URL'], url_for('track_page', tracking=shipment.tracking_number))
    html = render_template("emails/checkpoint.html",
                           shipment=shipment, checkpoint=checkpoint, track_url=url,
                           unsubscribe_url=unsubscribe_url(subscriber))
    send_email(subscriber.email, "Update: %s at %s" % (shipment.title, checkpoint.label), html)


# --- Admin JSON endpoints for SPA ---
@app.route('/api/admin/shipments')
def api_admin_shipments():
    shipments = Shipment.query.order_by(Shipment.updated_at.desc()).all()
    out = []
    for s in shipments:
        out.append({
            'tracking_number': s.tracking_number,
            'title': s.title,
            'status': s.status,
            'origin': {'lat': s.origin_lat, 'lng': s.origin_lng},
            'destination': {'lat': s.dest_lat, 'lng': s.dest_lng},
            'updated_at': s.updated_at.isoformat(),
            'checkpoints': [{'position': cp.position, 'lat': cp.lat, 'lng': cp.lng, 'label': cp.label, 'note': cp.note, 'timestamp': cp.timestamp.isoformat()} for cp in s.checkpoints],
            'subscribers': [{'email': sub.email, 'is_active': sub.is_active} for sub in s.subscribers]
        })
    return jsonify(out)

@app.route('/api/admin/shipments/<tracking>/remove_subscriber', methods=['POST'])
def api_remove_subscriber(tracking):
    shp = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    sub = Subscriber.query.filter_by(shipment_id=shp.id, email=email).first()
    if not sub:
        abort(404, 'Subscriber not found')
    sub.is_active = False
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/shipments/<tracking>/simulate', methods=['POST'])
def api_simulate(tracking):
    shp = Shipment.query.filter_by(tracking_number=tracking).first_or_404()
    data = request.json or {}
    steps = int(data.get('steps', 6))
    interval = float(data.get('interval', 3.0))

    # spawn background thread to add checkpoints evenly between origin and destination
    def simulate_worker(shipment_id, steps, interval):
        import time, math
        from datetime import datetime
        s = Shipment.query.get(shipment_id)
        if not s:
            return
        lat1, lng1 = s.origin_lat, s.origin_lng
        lat2, lng2 = s.dest_lat, s.dest_lng
        for i in range(steps):
            frac = (i+1)/float(steps)
            lat = lat1 + (lat2 - lat1) * frac
            lng = lng1 + (lng2 - lng1) * frac
            cp = Checkpoint(shipment_id=s.id, position=len(s.checkpoints), lat=lat, lng=lng, label=f'Simulated {i+1}/{steps}', note=None)
            s.updated_at = datetime.utcnow()
            db.session.add(cp); db.session.commit()
            # notify subscribers
            for sub in [x for x in s.subscribers if x.is_active]:
                try:
                    send_checkpoint_email(s, sub, cp)
                except Exception as e:
                    print('Email send error', e)
            time.sleep(interval)

    t = threading.Thread(target=simulate_worker, args=(shp.id, steps, interval), daemon=True)
    t.start()
    return jsonify({'ok': True, 'started': True})


# Register optional webhook blueprint (telegram_webhook.py)
try:
    from telegram_webhook import bp as telegram_bp
    app.register_blueprint(telegram_bp)
except Exception:
    pass

# NOTE: To use webhook mode, set TELEGRAM_TOKEN and configure Telegram webhook to point to:
#   os.getenv("APP_BASE_URL", "http://localhost:5000")/telegram/webhook/<TELEGRAM_TOKEN>
# Telegram requires an HTTPS endpoint. You can use a reverse proxy (ngrok, Cloud Run, etc.)

@app.cli.command("init-db")
def init_db_cmd():
    db.create_all()
    print("Database initialized.")

@app.cli.command("seed-demo")
def seed_demo():
    db.create_all()
    tracking = "SIM" + datetime.utcnow().strftime("%y%m%d%H%M%S")
    shp = Shipment(
        tracking_number=tracking,
        title="Demo Consignment",
        origin_lat=6.5244, origin_lng=3.3792,
        dest_lat=51.5074, dest_lng=-0.1278,
        status="In Transit",
    )
    db.session.add(shp); db.session.commit()
    lats = [6.5244, 14.0, 25.0, 35.0, 45.0, 51.5074]
    lngs = [3.3792, -5.0, -20.0, -35.0, -20.0, -0.1278]
    labels = ["Picked up", "Departed facility", "In flight", "Arrived hub", "Out for delivery", "Delivered"]
    for i,(la,ln) in enumerate(zip(lats, lngs)):
        cp = Checkpoint(
            shipment_id=shp.id, position=i, lat=la, lng=ln, label=labels[min(i, len(labels)-1)],
            note=None, timestamp=datetime.utcnow())
        db.session.add(cp)
    db.session.commit()
    print("Seeded demo tracking number:", tracking)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
