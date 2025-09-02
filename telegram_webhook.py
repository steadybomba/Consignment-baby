"""
Webhook mode Telegram handler: register webhook path /telegram/webhook/<TOKEN>
This blueprint handles updates and dispatches same commands as polling bot, but via HTTP.
"""

import os
import threading
import requests
from flask import Blueprint, request, jsonify, current_app
from database import get_db
from email_utils import send_checkpoint_email

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
bp = Blueprint("telegram_webhook", __name__, url_prefix="/telegram")

def send_message(chat_id, text):
    token = TOKEN
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print("Telegram send error", e)

@bp.route("/webhook/<token>", methods=["POST"])
def webhook(token):
    if not TOKEN or token != TOKEN:
        return jsonify({"ok": False, "error": "invalid token"}), 403
    data = request.get_json(force=True)
    threading.Thread(target=handle_update, args=(data,), daemon=True).start()
    return jsonify({"ok": True})

def handle_update(update_json):
    try:
        message = update_json.get("message") or update_json.get("edited_message") or {}
        text = message.get("text","")
        chat = message.get("chat",{})
        chat_id = chat.get("id")
        if not text or not chat_id:
            return
        parts = text.strip().split(" ",1)
        cmd = parts[0].lstrip("/").split("@")[0].lower()
        payload = parts[1].strip() if len(parts)>1 else ""
        db = get_db()
        cur = db.cursor()

        if cmd == "status":
            t = payload.split()[0] if payload else ""
            if not t:
                send_message(chat_id, "Usage: /status <TRACKING>")
                return
            shp = cur.execute("SELECT * FROM shipments WHERE tracking=?", (t,)).fetchone()
            if not shp:
                send_message(chat_id, f"Tracking {t} not found.")
                return
            latest = cur.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
            text = f"{shp['title']} ({shp['tracking']})\nStatus: {shp['status']}\nUpdated: {shp['updated_at']}\n"
            if latest:
                text += f"Latest: {latest['label']} at {latest['timestamp']} ({latest['lat']:.4f},{latest['lng']:.4f})\n"
            text += f"Map: {os.getenv('APP_BASE_URL','http://localhost:5000')}/track/{shp['tracking']}"
            send_message(chat_id, text)

        elif cmd == "create":
            if "|" not in payload:
                send_message(chat_id, "Usage: /create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng")
                return
            p = payload.split("|")
            tracking = p[0].strip()
            title = p[1].strip() if len(p)>1 else "Consignment"
            o = p[2].split(","); d = p[3].split(",")
            cur.execute("INSERT INTO shipments (tracking, title, origin_lat, origin_lng, dest_lat, dest_lng, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (tracking, title, float(o[0]), float(o[1]), float(d[0]), float(d[1]), "Created"))
            db.commit()
            send_message(chat_id, f"Created {tracking}")

        elif cmd == "addcp":
            if "|" not in payload:
                send_message(chat_id, "Usage: /addcp TRACKING|lat,lng|Label|note")
                return
            p = payload.split("|"); tracking = p[0].strip(); coords = p[1].split(",")
            label = p[2].strip() if len(p)>2 else "Scanned"; note = p[3].strip() if len(p)>3 else None
            shp = cur.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
            if not shp:
                send_message(chat_id, "Shipment not found."); return
            pos = cur.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shp["id"],)).fetchone()["c"]
            cur.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, note, timestamp) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                        (shp["id"], pos, float(coords[0]), float(coords[1]), label, note))
            cur.execute("UPDATE shipments SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (shp["id"],))
            db.commit()
            cp = cur.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
            try:
                send_checkpoint_email(shp, cp)
            except Exception as e:
                print("Email error", e)
            send_message(chat_id, f"Added checkpoint to {tracking}")

        elif cmd == "list":
            ships = cur.execute("SELECT tracking, title, status FROM shipments ORDER BY updated_at DESC LIMIT 20").fetchall()
            if not ships:
                send_message(chat_id, "No shipments found.")
                return
            msg = "Recent shipments:\\n" + "\\n".join([f\"{s['tracking']}: {s['title']} ({s['status']})\" for s in ships])
            send_message(chat_id, msg)

        elif cmd == "remove_sub":
            if "|" not in payload:
                send_message(chat_id, "Usage: /remove_sub TRACKING|email"); return
            tracking, email = payload.split("|",1)
            s = cur.execute("SELECT * FROM shipments WHERE tracking=?", (tracking.strip(),)).fetchone()
            if not s: send_message(chat_id, "Shipment not found"); return
            sub = cur.execute("SELECT * FROM subscribers WHERE shipment_id=? AND email=?", (s["id"], email.strip().lower())).fetchone()
            if not sub: send_message(chat_id, "Subscriber not found"); return
            cur.execute("UPDATE subscribers SET is_active=0 WHERE id=?", (sub["id"],))
            db.commit()
            send_message(chat_id, f"Removed {email}")

        elif cmd == "simulate":
            if "|" not in payload:
                send_message(chat_id, "Usage: /simulate TRACKING|steps|interval_seconds"); return
            parts = payload.split("|")
            tracking = parts[0].strip()
            steps = int(parts[1].strip()) if len(parts)>1 and parts[1].strip() else 6
            interval = float(parts[2].strip()) if len(parts)>2 and parts[2].strip() else 3.0
            s = cur.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
            if not s: send_message(chat_id, "Shipment not found"); return
            # spawn background worker
            def worker(shipment_id, steps, interval):
                import time
                from database import get_db as get_db2
                db2 = get_db2()
                cur2 = db2.cursor()
                shp2 = cur2.execute("SELECT * FROM shipments WHERE id=?", (shipment_id,)).fetchone()
                lat1, lng1 = shp2["origin_lat"], shp2["origin_lng"]
                lat2, lng2 = shp2["dest_lat"], shp2["dest_lng"]
                for i in range(steps):
                    frac = (i+1)/float(steps)
                    lat = lat1 + (lat2 - lat1) * frac
                    lng = lng1 + (lng2 - lng1) * frac
                    pos = cur2.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shipment_id,)).fetchone()["c"]
                    cur2.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, timestamp) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                                 (shipment_id, pos, lat, lng, f"Simulated {i+1}/{steps}"))
                    cur2.execute("UPDATE shipments SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (shipment_id,))
                    db2.commit()
                    cp = cur2.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shipment_id,)).fetchone()
                    try:
                        send_checkpoint_email(shp2, cp)
                    except Exception as e:
                        print("Email error", e)
                    time.sleep(interval)
            threading.Thread(target=worker, args=(s["id"], steps, interval), daemon=True).start()
            send_message(chat_id, f"Started simulation for {tracking}: {steps} steps, {interval}s interval.")
    except Exception as e:
        print("Webhook handler error", e)
