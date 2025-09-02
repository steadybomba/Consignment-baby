"""
Main Flask app for Consignment Tracker (updated for thread-safety, validation, and security).
"""

import os
import threading
import sqlite3
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory, current_app, after_this_request
)
from database import get_db, init_db, close_connection, new_db_connection
from email_utils import send_checkpoint_email
import logging

# try to import bot modules (optional)
try:
    import telegram_bot
except Exception:
    telegram_bot = None

try:
    import telegram_webhook
except Exception:
    telegram_webhook = None

# App config
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")
app.teardown_appcontext(close_connection)

# Security: session lifetime
app.permanent_session_lifetime = timedelta(hours=2)

# Register webhook blueprint if available
if telegram_webhook is not None:
    try:
        app.register_blueprint(telegram_webhook.bp)
    except Exception as e:
        app.logger.warning("Failed to register telegram_webhook blueprint: %s", e)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

# Logging
logging.basicConfig(level=logging.INFO)

# --- Authentication helpers ---
from functools import wraps
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper

# Minimal security headers for responses
@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer-when-downgrade")
    # a permissive CSP — adjust for your production needs
    response.headers.setdefault("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data: https:;")
    return response

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/track/<tracking>")
def track_page(tracking):
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return render_template("not_found.html", tracking=tracking), 404
    cps = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY position ASC", (shp["id"],)).fetchall()
    return render_template("track.html", shipment=shp, checkpoints=cps)

# Admin login with small brute-force delay (simple)
_failed_login = {}

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    error = None
    if request.method == "POST":
        user = request.form.get("user","").strip()
        pwd = request.form.get("password","").strip()
        key = f"{request.remote_addr}:{user}"
        fails = _failed_login.get(key, 0)
        if fails >= 5:
            # simple throttle
            import time
            time.sleep(min(5, fails - 4))
        if user == os.environ.get("ADMIN_USER", ADMIN_USER) and pwd == os.environ.get("ADMIN_PASSWORD", ADMIN_PASSWORD):
            session.permanent = True
            session["admin_logged_in"] = True
            _failed_login.pop(key, None)
            next_url = request.args.get("next") or url_for("admin_app")
            return redirect(next_url)
        else:
            _failed_login[key] = fails + 1
            error = "Invalid credentials"
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

# Serve built SPA if available, otherwise fallback to server dashboard
@app.route("/admin/app")
@admin_required
def admin_app():
    spa_index = os.path.join(app.static_folder or "static", "admin-app", "index.html")
    if os.path.isfile(spa_index):
        return send_from_directory(os.path.join(app.static_folder, "admin-app"), "index.html")
    return render_template("admin_dashboard.html")

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin_dashboard.html")

# --- Input validators ---
def _validate_coords(obj, key):
    """
    Expect obj[key] to be a mapping with 'lat' and 'lng' numeric values.
    Returns (lat, lng) on success or raises ValueError.
    """
    if key not in obj or not isinstance(obj[key], dict):
        raise ValueError(f"missing or invalid {key}")
    lat = obj[key].get("lat")
    lng = obj[key].get("lng")
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except Exception:
        raise ValueError(f"invalid coordinates for {key}")
    return lat_f, lng_f

# --- Public JSON API ---
@app.route("/api/shipments/<tracking>")
def api_get_shipment(tracking):
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error":"not found"}), 404
    cps = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY position ASC", (shp["id"],)).fetchall()
    return jsonify({
        "tracking": shp["tracking"],
        "title": shp["title"],
        "status": shp["status"],
        "origin": {"lat": shp["origin_lat"], "lng": shp["origin_lng"]},
        "destination": {"lat": shp["dest_lat"], "lng": shp["dest_lng"]},
        "updated_at": shp["updated_at"],
        "checkpoints": [dict(c) for c in cps]
    })

@app.route("/api/shipments", methods=["POST"])
def api_create_shipment():
    data = request.get_json(force=True) or {}
    # required top-level keys
    for k in ("tracking_number","origin","destination"):
        if k not in data:
            return jsonify({"error": f"missing {k}"}), 400
    try:
        lat_o, lng_o = _validate_coords(data, "origin")
        lat_d, lng_d = _validate_coords(data, "destination")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM shipments WHERE tracking=?", (data["tracking_number"],)).fetchone()
    if existing:
        return jsonify({"error":"tracking exists"}), 400
    try:
        db.execute(
            "INSERT INTO shipments (tracking, title, origin_lat, origin_lng, dest_lat, dest_lng, status) VALUES (?,?,?,?,?,?,?)",
            (data["tracking_number"], data.get("title","Consignment"),
             lat_o, lng_o, lat_d, lng_d, data.get("status","Created"))
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        return jsonify({"error":"db error", "detail": str(e)}), 500
    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/checkpoints", methods=["POST"])
def api_add_checkpoint(tracking):
    data = request.get_json(force=True) or {}
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error":"not found"}), 404
    # validate lat/lng
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except Exception:
        return jsonify({"error":"invalid lat/lng"}, 400)
    label = data.get("label", "Scanned")
    note = data.get("note")
    pos = db.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shp["id"],)).fetchone()["c"]
    db.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, note, timestamp) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
               (shp["id"], pos, lat, lng, label, note))
    db.execute("UPDATE shipments SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (data.get("status", shp["status"]), shp["id"]))
    db.commit()
    # send emails (non-blocking)
    try:
        cp = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
        threading.Thread(target=send_checkpoint_email, args=(shp, cp), daemon=True).start()
    except Exception as e:
        current_app.logger.error("Email notify failed: %s", e)
    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/subscribe", methods=["POST"])
def api_subscribe(tracking):
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error":"missing email"}), 400
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error":"not found"}), 404
    sub = db.execute("SELECT id FROM subscribers WHERE shipment_id=? AND email=?", (shp["id"], email)).fetchone()
    if sub:
        db.execute("UPDATE subscribers SET is_active=1 WHERE id=?", (sub["id"],))
    else:
        db.execute("INSERT INTO subscribers (shipment_id, email, is_active) VALUES (?, ?, 1)", (shp["id"], email))
    db.commit()
    return jsonify({"ok": True})

# --- Admin JSON endpoints (protected) ---
@app.route("/api/admin/shipments")
@admin_required
def api_admin_shipments():
    db = get_db()
    ships = db.execute("SELECT * FROM shipments ORDER BY updated_at DESC").fetchall()
    out = []
    for s in ships:
        cps = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY position ASC", (s["id"],)).fetchall()
        subs = db.execute("SELECT * FROM subscribers WHERE shipment_id=?", (s["id"],)).fetchall()
        out.append({
            "id": s["id"],
            "tracking_number": s["tracking"],
            "title": s["title"],
            "status": s["status"],
            "origin": {"lat": s["origin_lat"], "lng": s["origin_lng"]},
            "destination": {"lat": s["dest_lat"], "lng": s["dest_lng"]},
            "updated_at": s["updated_at"],
            "checkpoints": [dict(c) for c in cps],
            "subscribers": [dict(sub) for sub in subs]
        })
    return jsonify(out)

@app.route("/api/admin/shipments/<tracking>/remove_subscriber", methods=["POST"])
@admin_required
def api_remove_subscriber(tracking):
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    db = get_db()
    s = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not s:
        return jsonify({"error":"not found"}), 404
    sub = db.execute("SELECT * FROM subscribers WHERE shipment_id=? AND email=?", (s["id"], email)).fetchone()
    if not sub:
        return jsonify({"error":"not found"}), 404
    db.execute("UPDATE subscribers SET is_active=0 WHERE id=?", (sub["id"],))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/admin/shipments/<tracking>/simulate", methods=["POST"])
@admin_required
def api_simulate(tracking):
    data = request.get_json(force=True) or {}
    steps = int(data.get("steps", 6))
    interval = float(data.get("interval", 3.0))
    # use a new DB connection for the worker thread
    db_main = get_db()
    s = db_main.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not s:
        return jsonify({"error":"not found"}), 404

    def worker(shipment_id, steps, interval):
        import time
        conn = new_db_connection()
        cur = conn.cursor()
        try:
            shp2 = cur.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
            lat1, lng1 = shp2["origin_lat"], shp2["origin_lng"]
            lat2, lng2 = shp2["dest_lat"], shp2["dest_lng"]
            for i in range(steps):
                frac = (i+1)/float(steps)
                lat = lat1 + (lat2 - lat1) * frac
                lng = lng1 + (lng2 - lng1) * frac
                pos = cur.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shipment_id,)).fetchone()["c"]
                cur.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, timestamp) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                            (shipment_id, pos, lat, lng, f"Simulated {i+1}/{steps}"))
                cur.execute("UPDATE shipments SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (shipment_id,))
                conn.commit()
                cp = cur.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shipment_id,)).fetchone()
                try:
                    send_checkpoint_email(shp2, cp)
                except Exception as e:
                    current_app.logger.error("Email send error in worker: %s", e)
                time.sleep(interval)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    t = threading.Thread(target=worker, args=(s["id"], steps, interval), daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True})

# Start polling bot in background only if TELEGRAM_TOKEN present and polling mode desired
def _start_polling_bot_async():
    if telegram_bot is None:
        app.logger.info("telegram_bot module not available; polling disabled.")
        return
    try:
        t = threading.Thread(target=telegram_bot.start_bot, daemon=True)
        t.start()
    except Exception as e:
        app.logger.warning("Polling bot failed to start: %s", e)

if __name__ == "__main__":
    # start polling bot if token set (optional)
    if os.getenv("TELEGRAM_TOKEN"):
        _start_polling_bot_async()
    # init DB if needed
    init_db()
    port = int(os.environ.get("PORT", 5000))
    # Use explicit host/port
    app.run(host="0.0.0.0", port=port, debug=bool(os.getenv("FLASK_DEBUG", False)))
