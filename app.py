import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory, g
from database import get_db, close_connection, init_db
from email_utils import send_checkpoint_email
from telegram_webhook import telegram_bp
import threading
import time

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")
app.register_blueprint(telegram_bp, url_prefix="/telegram")
app.teardown_appcontext(close_connection)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "password")

# Simple admin session decorator
from functools import wraps
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper

# --- UI routes ---
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

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = request.form.get("user") or ""
        pwd = request.form.get("password") or ""
        if user == ADMIN_USER and pwd == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            nxt = request.args.get("next") or url_for("admin_app")
            return redirect(nxt)
        return render_template("admin_login.html", error="Invalid credentials")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

# serve built SPA if present
@app.route("/admin/app")
@login_required
def admin_app():
    # prefer built SPA under static/admin-app/index.html
    path = os.path.join(app.static_folder or "static", "admin-app", "index.html")
    if os.path.exists(path):
        return send_from_directory(os.path.join(app.static_folder, "admin-app"), "index.html")
    # fallback: server-rendered dashboard
    return render_template("admin_dashboard.html")

@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    return render_template("admin_dashboard.html")

# --- JSON API: public ---
@app.route("/api/shipments/<tracking>")
def api_get_shipment(tracking):
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error": "not found"}), 404
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
    for k in ("tracking", "origin", "destination"):
        if k not in data:
            return jsonify({"error": f"missing {k}"}), 400
    db = get_db()
    try:
        db.execute(
            "INSERT INTO shipments (tracking, title, origin_lat, origin_lng, dest_lat, dest_lng, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data["tracking"], data.get("title", "Consignment"),
             float(data["origin"]["lat"]), float(data["origin"]["lng"]),
             float(data["destination"]["lat"]), float(data["destination"]["lng"]),
             data.get("status", "Created"))
        )
        db.commit()
        return jsonify({"ok": True}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/shipments/<tracking>/checkpoints", methods=["POST"])
def api_add_checkpoint(tracking):
    data = request.get_json(force=True) or {}
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error": "shipment not found"}), 404
    pos = db.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shp["id"],)).fetchone()["c"]
    db.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, note) VALUES (?, ?, ?, ?, ?, ?)",
               (shp["id"], pos, float(data["lat"]), float(data["lng"]), data.get("label", "Scanned"), data.get("note")))
    db.execute("UPDATE shipments SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (data.get("status", shp["status"]), shp["id"]))
    db.commit()
    # notify subscribers
    cp = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
    try:
        send_checkpoint_email(shp, cp)
    except Exception as e:
        print("Email notify error:", e)
    return jsonify({"ok": True}), 201

@app.route("/api/shipments/<tracking>/subscribe", methods=["POST"])
def api_subscribe(tracking):
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "missing email"}), 400
    db = get_db()
    shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not shp:
        return jsonify({"error": "shipment not found"}), 404
    # dedupe
    sub = db.execute("SELECT * FROM subscribers WHERE shipment_id=? AND email=?", (shp["id"], email)).fetchone()
    if sub:
        db.execute("UPDATE subscribers SET is_active=1 WHERE id=?", (sub["id"],))
    else:
        db.execute("INSERT INTO subscribers (shipment_id, email, is_active) VALUES (?, ?, 1)", (shp["id"], email))
    db.commit()
    return jsonify({"ok": True})

# --- Admin APIs (protected) ---
@app.route("/api/admin/shipments")
@login_required
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
@login_required
def api_remove_subscriber(tracking):
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    db = get_db()
    s = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not s:
        return jsonify({"error": "shipment not found"}), 404
    sub = db.execute("SELECT * FROM subscribers WHERE shipment_id=? AND email=?", (s["id"], email)).fetchone()
    if not sub:
        return jsonify({"error": "subscriber not found"}), 404
    db.execute("UPDATE subscribers SET is_active=0 WHERE id=?", (sub["id"],))
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/admin/shipments/<tracking>/simulate", methods=["POST"])
@login_required
def api_simulate(tracking):
    data = request.get_json(force=True) or {}
    steps = int(data.get("steps", 6))
    interval = float(data.get("interval", 3.0))
    db = get_db()
    s = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    if not s:
        return jsonify({"error": "shipment not found"}), 404

    def simulate_worker(shipment_id, steps, interval):
        import time
        db2 = get_db()
        cur = db2.cursor()
        shp = cur.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
        lat1, lng1 = shp["origin_lat"], shp["origin_lng"]
        lat2, lng2 = shp["dest_lat"], shp["dest_lng"]
        for i in range(steps):
            frac = (i+1)/float(steps)
            lat = lat1 + (lat2 - lat1)*frac
            lng = lng1 + (lng2 - lng1)*frac
            pos = cur.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shipment_id,)).fetchone()["c"]
            cur.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label) VALUES (?, ?, ?, ?, ?)",
                        (shipment_id, pos, lat, lng, f"Simulated {i+1}/{steps}"))
            cur.execute("UPDATE shipments SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (shipment_id,))
            db2.commit()
            cp = cur.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shipment_id,)).fetchone()
            # notify subscribers
            try:
                send_checkpoint_email(shp, cp)
            except Exception as e:
                print("Email send error:", e)
            time.sleep(interval)

    t = threading.Thread(target=simulate_worker, args=(s["id"], steps, interval), daemon=True)
    t.start()
    return jsonify({"ok": True, "started": True})

# CLI helpers
@app.cli.command("init-db")
def init_db_cmd():
    init_db()
    print("Database initialized.")

@app.cli.command("seed-demo")
def seed_demo():
    init_db()
    db = get_db()
    cur = db.cursor()
    import time
    tracking = "SIM" + time.strftime("%y%m%d%H%M%S")
    cur.execute("INSERT INTO shipments (tracking, title, origin_lat, origin_lng, dest_lat, dest_lng, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tracking, "Demo Consignment", 6.5244, 3.3792, 51.5074, -0.1278, "In Transit"))
    db.commit()
    shp = cur.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
    lats = [6.5244, 14.0, 25.0, 35.0, 45.0, 51.5074]
    lngs = [3.3792, -5.0, -20.0, -35.0, -20.0, -0.1278]
    labels = ["Picked up", "Departed facility", "In flight", "Arrived hub", "Out for delivery", "Delivered"]
    for i, (la, ln) in enumerate(zip(lats, lngs)):
        cur.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label) VALUES (?, ?, ?, ?, ?)",
                    (shp["id"], i, la, ln, labels[min(i, len(labels)-1)]))
    db.commit()
    print("Seeded demo tracking number:", tracking)

if __name__ == "__main__":
    # helpful for local dev
    from dotenv import load_dotenv
    load_dotenv()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
