import os
import threading
import logging
from urllib.parse import quote_plus
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext

from app import db, Shipment, Checkpoint, Subscriber, app, send_checkpoint_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('TELEGRAM_TOKEN', '').strip()
ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_CHAT_ID', '').strip()  # optional: restrict notifications

def start_bot():
    if not TOKEN:
        logger.info('No TELEGRAM_TOKEN set — Telegram bot disabled.')
        return

    def cmd_status(update: Update, context: CallbackContext):
        if not context.args:
            update.message.reply_text('Usage: /status <TRACKING>')
            return
        t = context.args[0].strip()
        shp = Shipment.query.filter_by(tracking_number=t).first()
        if not shp:
            update.message.reply_text(f'Tracking {t} not found.')
            return
        latest = shp.checkpoints[-1] if shp.checkpoints else None
        text = f"*{shp.title}* ({shp.tracking_number})\nStatus: {shp.status}\nUpdated: {shp.updated_at}\n"
        if latest:
            text += f"Latest: {latest.label} at {latest.timestamp} ({latest.lat:.4f},{latest.lng:.4f})\n"
        text += f"Map: {app.config.get('APP_BASE_URL','http://localhost:5000')}/track/{quote_plus(shp.tracking_number)}"
        update.message.reply_text(text)

    def cmd_create(update: Update, context: CallbackContext):
        # /create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text('Usage: /create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng')
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else 'Consignment'
            o = parts[2].split(',')
            d = parts[3].split(',')
            shp = Shipment(
                tracking_number=tracking,
                title=title,
                origin_lat=float(o[0]), origin_lng=float(o[1]),
                dest_lat=float(d[0]), dest_lng=float(d[1]),
                status='Created'
            )
            db.session.add(shp); db.session.commit()
            update.message.reply_text(f'Created shipment {tracking}')
        except Exception as e:
            update.message.reply_text('Error creating shipment: ' + str(e))

    def cmd_addcp(update: Update, context: CallbackContext):
        # /addcp TRACKING|lat,lng|Label|note
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text('Usage: /addcp TRACKING|lat,lng|Label|note')
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            coords = parts[1].split(',')
            label = parts[2].strip() if len(parts) > 2 else 'Scanned'
            note = parts[3].strip() if len(parts) > 3 else None
            shp = Shipment.query.filter_by(tracking_number=tracking).first()
            if not shp:
                update.message.reply_text(f'Tracking {tracking} not found.')
                return
            cp = Checkpoint(
                shipment_id=shp.id,
                position=len(shp.checkpoints),
                lat=float(coords[0]), lng=float(coords[1]),
                label=label, note=note
            )
            shp.updated_at = datetime.utcnow()
            db.session.add(cp); db.session.commit()
            update.message.reply_text(f'Added checkpoint to {tracking}: {label} ({coords[0]},{coords[1]})')
            # notify admin chat if configured
            if ADMIN_CHAT_ID:
                bot = Bot(TOKEN)
                bot.send_message(chat_id=ADMIN_CHAT_ID, text=f'Checkpoint added: {tracking} - {label}')
        except Exception as e:
            update.message.reply_text('Error adding checkpoint: ' + str(e))

    def cmd_list(update: Update, context: CallbackContext):
        ships = Shipment.query.order_by(Shipment.updated_at.desc()).limit(20).all()
        if not ships:
            update.message.reply_text('No shipments found.')
            return
        text = 'Recent shipments:\n' + '\n'.join([f"{s.tracking_number}: {s.title} ({s.status})" for s in ships])
        update.message.reply_text(text)

    def cmd_remove_sub(update: Update, context: CallbackContext):
        # /remove_sub TRACKING|email
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text('Usage: /remove_sub TRACKING|email')
            return
        try:
            tracking, email = payload.split('|',1)
            s = Shipment.query.filter_by(tracking_number=tracking.strip()).first()
            if not s:
                update.message.reply_text('Shipment not found.')
                return
            sub = Subscriber.query.filter_by(shipment_id=s.id, email=email.strip().lower()).first()
            if not sub:
                update.message.reply_text('Subscriber not found.')
                return
            sub.is_active = False
            db.session.commit()
            update.message.reply_text(f'Removed subscriber {email} for {tracking}')
        except Exception as e:
            update.message.reply_text('Error: ' + str(e))

    def cmd_simulate(update: Update, context: CallbackContext):
        # /simulate TRACKING|steps|interval_seconds
        payload = ' '.join(context.args)
        if not payload or '|' not in payload:
            update.message.reply_text('Usage: /simulate TRACKING|steps|interval_seconds')
            return
        try:
            parts = payload.split('|')
            tracking = parts[0].strip()
            steps = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else 6
            interval = float(parts[2].strip()) if len(parts) > 2 and parts[2].strip() else 3.0
            s = Shipment.query.filter_by(tracking_number=tracking).first()
            if not s:
                update.message.reply_text('Shipment not found.')
                return
            # spawn thread similar to API simulate
            def worker(shipment_id, steps, interval):
                import time
                from datetime import datetime
                s2 = Shipment.query.get(shipment_id)
                lat1, lng1 = s2.origin_lat, s2.origin_lng
                lat2, lng2 = s2.dest_lat, s2.dest_lng
                for i in range(steps):
                    frac = (i+1)/float(steps)
                    lat = lat1 + (lat2 - lat1) * frac
                    lng = lng1 + (lng2 - lng1) * frac
                    cp = Checkpoint(shipment_id=s2.id, position=len(s2.checkpoints), lat=lat, lng=lng, label=f'Simulated {i+1}/{steps}', note=None)
                    s2.updated_at = datetime.utcnow()
                    db.session.add(cp); db.session.commit()
                    for sub in [x for x in s2.subscribers if x.is_active]:
                        try:
                            send_checkpoint_email(s2, sub, cp)
                        except Exception as e:
                            print('Email send error', e)
                    time.sleep(interval)
            t = threading.Thread(target=worker, args=(s.id, steps, interval), daemon=True)
            t.start()
            update.message.reply_text(f'Started simulation for {tracking}: {steps} steps, {interval}s interval.')
        except Exception as e:
            update.message.reply_text('Error: ' + str(e))

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler('status', cmd_status))
    dp.add_handler(CommandHandler('create', cmd_create))
    dp.add_handler(CommandHandler('addcp', cmd_addcp))
    dp.add_handler(CommandHandler('list', cmd_list))
    dp.add_handler(CommandHandler('remove_sub', cmd_remove_sub))
    dp.add_handler(CommandHandler('simulate', cmd_simulate))

    logger.info('Starting Telegram bot polling...')
    updater.start_polling()
    logger.info('Telegram bot started.')
    return updater
