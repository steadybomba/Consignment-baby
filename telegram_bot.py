"""
Polling Telegram bot using python-telegram-bot v13.x style (Updater).
If TELEGRAM_TOKEN not set, this file will do nothing.
"""

import os
import threading
import logging
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext

from database import get_db
from email_utils import send_checkpoint_email

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()

logger = logging.getLogger("telegram_bot")
logger.setLevel(logging.INFO)

def start_bot():
    if not TOKEN:
        logger.info("No TELEGRAM_TOKEN set — polling disabled.")
        return

    def cmd_status(update: Update, context: CallbackContext):
        if not context.args:
            update.message.reply_text("Usage: /status <TRACKING>")
            return
        t = context.args[0].strip()
        db = get_db()
        shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (t,)).fetchone()
        if not shp:
            update.message.reply_text(f"Tracking {t} not found.")
            return
        latest = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
        text = f"{shp['title']} ({shp['tracking']})\nStatus: {shp['status']}\nUpdated: {shp['updated_at']}\n"
        if latest:
            text += f"Latest: {latest['label']} at {latest['timestamp']} ({latest['lat']:.4f},{latest['lng']:.4f})\n"
        text += f"Map: {os.getenv('APP_BASE_URL','http://localhost:5000')}/track/{shp['tracking']}"
        update.message.reply_text(text)

    def cmd_create(update: Update, context: CallbackContext):
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text("Usage: /create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng")
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            title = parts[1].strip() if len(parts)>1 else "Consignment"
            o = parts[2].split(',')
            d = parts[3].split(',')
            db = get_db()
            db.execute("INSERT INTO shipments (tracking, title, origin_lat, origin_lng, dest_lat, dest_lng, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (tracking, title, float(o[0]), float(o[1]), float(d[0]), float(d[1]), "Created"))
            db.commit()
            update.message.reply_text(f"Created shipment {tracking}")
        except Exception as e:
            update.message.reply_text("Error: "+str(e))

    def cmd_addcp(update: Update, context: CallbackContext):
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text("Usage: /addcp TRACKING|lat,lng|Label|note")
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            coords = parts[1].split(',')
            label = parts[2].strip() if len(parts)>2 else "Scanned"
            note = parts[3].strip() if len(parts)>3 else None
            db = get_db()
            shp = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
            if not shp:
                update.message.reply_text("Shipment not found.")
                return
            pos = db.execute("SELECT COUNT(*) AS c FROM checkpoints WHERE shipment_id=?", (shp["id"],)).fetchone()["c"]
            db.execute("INSERT INTO checkpoints (shipment_id, position, lat, lng, label, note, timestamp) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                       (shp["id"], pos, float(coords[0]), float(coords[1]), label, note))
            db.execute("UPDATE shipments SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (shp["id"],))
            db.commit()
            cp = db.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shp["id"],)).fetchone()
            try:
                send_checkpoint_email(shp, cp)
            except Exception as e:
                logger.warning("Email error: %s", e)
            update.message.reply_text(f"Added checkpoint to {tracking}: {label}")
            if ADMIN_CHAT_ID:
                Bot(TOKEN).send_message(chat_id=ADMIN_CHAT_ID, text=f"Checkpoint added: {tracking} - {label}")
        except Exception as e:
            update.message.reply_text("Error: "+str(e))

    def cmd_list(update: Update, context: CallbackContext):
        db = get_db()
        ships = db.execute("SELECT tracking, title, status FROM shipments ORDER BY updated_at DESC LIMIT 20").fetchall()
        if not ships:
            update.message.reply_text("No shipments.")
            return
        msg = "\n".join([f"{s['tracking']}: {s['title']} ({s['status']})" for s in ships])
        update.message.reply_text(msg)

    def cmd_remove_sub(update: Update, context: CallbackContext):
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text("Usage: /remove_sub TRACKING|email")
            return
        try:
            tracking, email = payload.split('|',1)
            db = get_db()
            s = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking.strip(),)).fetchone()
            if not s:
                update.message.reply_text("Shipment not found.")
                return
            sub = db.execute("SELECT * FROM subscribers WHERE shipment_id=? AND email=?", (s["id"], email.strip().lower())).fetchone()
            if not sub:
                update.message.reply_text("Subscriber not found.")
                return
            db.execute("UPDATE subscribers SET is_active=0 WHERE id=?", (sub["id"],))
            db.commit()
            update.message.reply_text(f"Removed subscriber {email} for {tracking}")
        except Exception as e:
            update.message.reply_text("Error: "+str(e))

    def cmd_simulate(update: Update, context: CallbackContext):
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text("Usage: /simulate TRACKING|steps|interval_seconds")
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            steps = int(parts[1].strip()) if len(parts)>1 and parts[1].strip() else 6
            interval = float(parts[2].strip()) if len(parts)>2 and parts[2].strip() else 3.0
            db = get_db()
            s = db.execute("SELECT * FROM shipments WHERE tracking=?", (tracking,)).fetchone()
            if not s:
                update.message.reply_text("Shipment not found.")
                return
            def worker(shipment_id, steps, interval):
                import time
                from database import get_db as get_db2
                db2 = get_db2()
                cur = db2.cursor()
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
                    db2.commit()
                    cp = cur.execute("SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", (shipment_id,)).fetchone()
                    try:
                        send_checkpoint_email(shp2, cp)
                    except Exception as e:
                        print("Email error", e)
                    time.sleep(interval)
            threading.Thread(target=worker, args=(s["id"], steps, interval), daemon=True).start()
            update.message.reply_text(f"Started simulation for {tracking}: {steps} steps, {interval}s interval.")
        except Exception as e:
            update.message.reply_text("Error: "+str(e))

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("status", cmd_status))
    dp.add_handler(CommandHandler("create", cmd_create))
    dp.add_handler(CommandHandler("addcp", cmd_addcp))
    dp.add_handler(CommandHandler("list", cmd_list))
    dp.add_handler(CommandHandler("remove_sub", cmd_remove_sub))
    dp.add_handler(CommandHandler("simulate", cmd_simulate))

    updater.start_polling()
    logger.info("Polling Telegram bot started.")
    return updater
