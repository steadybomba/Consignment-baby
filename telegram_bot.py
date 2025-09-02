"""
Dual-mode Telegram bot: polling + webhook (python-telegram-bot v13.x)
Controlled by environment:
- TELEGRAM_MODE = "polling" (default) or "webhook"
- TELEGRAM_WEBHOOK_URL = "https://yourdomain.com/telegram/webhook" (for webhook mode)
- TELEGRAM_WEBHOOK_PORT = 8443 (default)
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
BOT_MODE = os.getenv("TELEGRAM_MODE", "polling").lower()
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
WEBHOOK_PORT = int(os.getenv("TELEGRAM_WEBHOOK_PORT", "8443"))

logger = logging.getLogger("telegram_bot")
logger.setLevel(logging.INFO)


def start_bot():
    """Start Telegram bot in polling or webhook mode."""
    if not TOKEN:
        logger.info("No TELEGRAM_TOKEN set — Telegram bot disabled.")
        return

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # --- COMMAND HANDLERS ---
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
        latest = db.execute(
            "SELECT * FROM checkpoints WHERE shipment_id=? ORDER BY id DESC LIMIT 1", 
            (shp["id"],)
        ).fetchone()
        text = f"{shp['title']} ({shp['tracking']})\nStatus: {shp['status']}\nUpdated: {shp['updated_at']}\n"
        if latest:
            text += f"Latest: {latest['label']} at {latest['timestamp']} ({latest['lat']:.4f},{latest['lng']:.4f})\n"
        text += f"Map: {os.getenv('APP_BASE_URL','http://localhost:5000')}/track/{shp['tracking']}"
        update.message.reply_text(text)

    # Add your other commands: /create, /addcp, /list, /remove_sub, /simulate...

    dp.add_handler(CommandHandler("status", cmd_status))
    # dp.add_handler(CommandHandler("create", cmd_create))
    # dp.add_handler(CommandHandler("addcp", cmd_addcp))
    # dp.add_handler(CommandHandler("list", cmd_list))
    # dp.add_handler(CommandHandler("remove_sub", cmd_remove_sub))
    # dp.add_handler(CommandHandler("simulate", cmd_simulate))

    # --- START MODE ---
    if BOT_MODE == "webhook":
        if not WEBHOOK_URL:
            logger.error("TELEGRAM_WEBHOOK_URL missing — cannot start webhook mode.")
            return
        logger.info(f"Starting Telegram bot in WEBHOOK mode at {WEBHOOK_URL}")
        updater.start_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
        )
    else:
        logger.info("Starting Telegram bot in POLLING mode")
        updater.start_polling()

    return updater
