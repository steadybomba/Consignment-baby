# telegram_webhook.py
# Replace polling bot with webhook-based lightweight handler integrated with Flask.
import os, json, threading, logging
from flask import request, current_app, Blueprint, jsonify

from app import db, Shipment, Checkpoint, Subscriber, app, send_checkpoint_email
from datetime import datetime

bp = Blueprint('telegram_webhook', __name__)

TOKEN = os.getenv('TELEGRAM_TOKEN', '').strip()
# expected webhook path: /telegram/webhook/<TOKEN>

def handle_update(update_json):
    try:
        message = update_json.get('message') or update_json.get('edited_message') or {}
        text = message.get('text','')
        chat = message.get('chat',{})
        chat_id = chat.get('id')
        if not text or not chat_id:
            return
        args = text.strip().split(' ')
        cmd = args[0].lstrip('/').split('@')[0]
        payload = ' '.join(args[1:]).strip()

        # simple command handlers
        if cmd == 'status':
            t = payload.split(' ')[0] if payload else ''
            if not t:
                send_message(chat_id, 'Usage: /status <TRACKING>')
                return
            shp = Shipment.query.filter_by(tracking_number=t).first()
            if not shp:
                send_message(chat_id, f'Tracking {t} not found.')
                return
            latest = shp.checkpoints[-1] if shp.checkpoints else None
            text = f"{shp.title} ({shp.tracking_number})\nStatus: {shp.status}\nUpdated: {shp.updated_at}\n"
            if latest:
                text += f"Latest: {latest.label} at {latest.timestamp} ({latest.lat:.4f},{latest.lng:.4f})\n"
            text += f"Map: {app.config.get('APP_BASE_URL')}/track/{shp.tracking_number}"
            send_message(chat_id, text)
        elif cmd == 'create':
            # payload: TRACKING|Title|lat,lng|lat,lng
            if '|' not in payload:
                send_message(chat_id, 'Usage: /create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng')
                return
            parts = payload.split('|')
            tracking = parts[0].strip()
            title = parts[1].strip() if len(parts)>1 else 'Consignment'
            o = parts[2].split(','); d = parts[3].split(',')
            shp = Shipment(tracking_number=tracking, title=title, origin_lat=float(o[0]), origin_lng=float(o[1]), dest_lat=float(d[0]), dest_lng=float(d[1]), status='Created')
            db.session.add(shp); db.session.commit()
            send_message(chat_id, f'Created {tracking}')
        elif cmd == 'addcp':
            if '|' not in payload:
                send_message(chat_id, 'Usage: /addcp TRACKING|lat,lng|Label|note')
                return
            parts = payload.split('|')
            tracking = parts[0].strip(); coords = parts[1].split(','); label = parts[2] if len(parts)>2 else 'Scanned'; note = parts[3] if len(parts)>3 else None
            shp = Shipment.query.filter_by(tracking_number=tracking).first()
            if not shp:
                send_message(chat_id, 'Shipment not found.'); return
            cp = Checkpoint(shipment_id=shp.id, position=len(shp.checkpoints), lat=float(coords[0]), lng=float(coords[1]), label=label, note=note)
            shp.updated_at = datetime.utcnow()
            db.session.add(cp); db.session.commit()
            # notify subs
            for sub in [x for x in shp.subscribers if x.is_active]:
                try: send_checkpoint_email(shp, sub, cp)
                except Exception as e: print('email error', e)
            send_message(chat_id, f'Added checkpoint to {tracking}')
        elif cmd == 'simulate':
            # payload TRACKING|steps|interval
            if '|' not in payload:
                send_message(chat_id, 'Usage: /simulate TRACKING|steps|interval_seconds'); return
            parts = payload.split('|')
            tracking = parts[0].strip(); steps = int(parts[1]) if len(parts)>1 and parts[1] else 6; interval = float(parts[2]) if len(parts)>2 and parts[2] else 3.0
            shp = Shipment.query.filter_by(tracking_number=tracking).first()
            if not shp: send_message(chat_id, 'Shipment not found'); return
            def worker(shipment_id, steps, interval):
                import time
                from datetime import datetime
                s = Shipment.query.get(shipment_id)
                lat1, lng1 = s.origin_lat, s.origin_lng; lat2, lng2 = s.dest_lat, s.dest_lng
                for i in range(steps):
                    frac = (i+1)/float(steps)
                    lat = lat1 + (lat2 - lat1) * frac; lng = lng1 + (lng2 - lng1) * frac
                    cp = Checkpoint(shipment_id=s.id, position=len(s.checkpoints), lat=lat, lng=lng, label=f'Simulated {i+1}/{steps}', note=None)
                    s.updated_at = datetime.utcnow()
                    db.session.add(cp); db.session.commit()
                    for sub in [x for x in s.subscribers if x.is_active]:
                        try: send_checkpoint_email(s, sub, cp)
                        except Exception as e: print('email error', e)
                    time.sleep(interval)
            threading.Thread(target=worker, args=(shp.id, steps, interval), daemon=True).start()
            send_message(chat_id, f'Started simulation for {tracking}')
        elif cmd == 'remove_sub':
            if '|' not in payload:
                send_message(chat_id, 'Usage: /remove_sub TRACKING|email'); return
            tracking, email = payload.split('|',1)
            s = Shipment.query.filter_by(tracking_number=tracking.strip()).first()
            if not s: send_message(chat_id, 'Shipment not found'); return
            sub = Subscriber.query.filter_by(shipment_id=s.id, email=email.strip().lower()).first()
            if not sub: send_message(chat_id, 'Subscriber not found'); return
            sub.is_active = False; db.session.commit(); send_message(chat_id, f'Removed {email}')
    except Exception as e:
        print('Webhook handler error', e)

def send_message(chat_id, text):
    import requests, os, json
    token = os.getenv('TELEGRAM_TOKEN','').strip()
    if not token or not chat_id: return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    r = requests.post(url, json={'chat_id': chat_id, 'text': text})
    return r

@bp.route('/telegram/webhook/'+os.getenv('TELEGRAM_TOKEN',''), methods=['POST'])
def telegram_webhook():
    data = request.get_json(force=True)
    threading.Thread(target=handle_update, args=(data,), daemon=True).start()
    return jsonify({'ok': True})
