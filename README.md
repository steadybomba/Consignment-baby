# Consignment-baby
$$$
# Consignment Tracker (Render-ready)

Quick start (local):
1. python -m venv .venv && source .venv/bin/activate
2. pip install -r requirements.txt
3. cp .env.example .env and set TELEGRAM_TOKEN, SMTP_* and APP_BASE_URL as needed
4. flask --app app.py init-db
5. python app.py

For frontend dev:
cd frontend
npm install
npm run dev

Render deployment:
- Push repo to GitHub
- Create new Web Service on Render, connect repo
- Set environment variables in Render dashboard (ADMIN_USER, ADMIN_PASSWORD, TELEGRAM_TOKEN, APP_BASE_URL, SMTP_*)
- Deploy; run the webhook set command:
  curl -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" -d "url=https://<your-render-url>/telegram/webhook/$TELEGRAM_TOKEN"

# Consignment Tracker (Demo)



A minimal Flask app to simulate package tracking with a live map (Leaflet) and an email engine for checkpoint notifications.

## Features
- Create shipments with origin/destination and a tracking number
- Add checkpoints (with coords, label, note) via API — map updates automatically (polling)
- Subscribe to email updates per shipment (unsubscribe link included)
- Simple, clean UI; mobile-friendly

## Quickstart
```bash
cd consignment-tracker
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# optional: copy env and edit
cp .env.example .env

# init DB
export FLASK_APP=app.py  # Windows PowerShell: $env:FLASK_APP='app.py'
flask --app app.py init-db

# run
python app.py
```

Open http://localhost:5000

### Seed a demo shipment
```bash
flask --app app.py seed-demo
```
Then paste the printed tracking number on the home page.

## API
Create shipment:
```http
POST /api/shipments
Content-Type: application/json

{
  "tracking_number":"SIM250901123456",
  "title":"Demo Consignment",
  "origin":{"lat":6.5244,"lng":3.3792},
  "destination":{"lat":51.5074,"lng":-0.1278},
  "status":"In Transit"
}
```

Add checkpoint (triggers emails):
```http
POST /api/shipments/{tracking}/checkpoints
Content-Type: application/json

{
  "position": 1,
  "lat": 14.0,
  "lng": -5.0,
  "label": "Departed facility",
  "note": "Left Lagos hub",
  "status": "In Transit"
}
```

Subscribe to updates:
```http
POST /api/shipments/{tracking}/subscribe
Content-Type: application/json

{ "email":"you@example.com" }
```

## Email setup
Configure SMTP env vars in `.env` (or set in your environment). For local dev you can leave SMTP_HOST empty to log emails to console, or use a mail catcher.

## Notes
- This is a demo. Add authentication/rate limiting before exposing admin or APIs publicly.
- For real-time without polling, switch to WebSockets or SSE.

## Admin dashboard

Visit `/admin/dashboard` for a richer admin UI to manage shipments, add checkpoints, and manage subscribers.

## Telegram bot

Set environment variables:

```
TELEGRAM_TOKEN=your_bot_token
# optional: admin chat id to receive notifications
TELEGRAM_ADMIN_CHAT_ID=123456789
```

Commands supported:
- `/status <TRACKING>` — get current status and latest checkpoint
- `/create TRACKING|Title|orig_lat,orig_lng|dest_lat,dest_lng` — create a shipment
- `/addcp TRACKING|lat,lng|Label|note` — add a checkpoint

The bot will start automatically when `TELEGRAM_TOKEN` is set and the Flask app is started.


## Vite + React Admin (production build)

A full Vite React admin app lives in `frontend/`. It's pre-configured for a simple build pipeline.

Quickstart (requires Node.js & npm/yarn):
```bash
cd frontend
npm install    # or yarn
npm run build  # produces production build into ../frontend-dist
```

### Serve the built frontend from Flask (production)
After `npm run build` you'll have `frontend-dist/index.html` and assets.
You can serve those directly from a static host or copy them into `static/` of the Flask app.
Example (simple): `cp -r frontend-dist/* static/admin-build/` then open `/admin/app` (you may need to update route to point to built index.html).

## Webhook-mode Telegram bot

- The project now includes `telegram_webhook.py` — a Flask blueprint that accepts Telegram update POSTs at the path:
  `/telegram/webhook/<TELEGRAM_TOKEN>`
- Telegram **requires HTTPS** for webhooks. Use a public HTTPS endpoint (ngrok, Cloud Run, AWS, GCP, etc.) and set your bot webhook URL:
  `https://your-domain/telegram/webhook/<TELEGRAM_TOKEN>` (use your actual TELEGRAM_TOKEN in the URL).
- Example to register webhook:
  ```bash
  curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" -d "url=https://your-domain/telegram/webhook/$TELEGRAM_TOKEN"
  ```
- The webhook handler mimics the polling bot commands (status, create, addcp, simulate, list, remove_sub, remove_sub). It runs command handling asynchronously to avoid holding the HTTP request.



## Deploying to Render (production-ready)

This repo includes `render.yaml` and a `Dockerfile` to deploy the service to Render.com.

### Environment variables (set these on Render dashboard)
- `ADMIN_USER` (default 'admin')
- `ADMIN_PASSWORD` (set a strong password)
- `TELEGRAM_TOKEN` (if using webhook or bot)
- `APP_BASE_URL` (your Render service URL, e.g. https://consignment-tracker.onrender.com)
- `SMTP_*` for email sending

### Build & start (Render)
Render will run `build.sh` (which builds the frontend) and then start the app via Gunicorn. The built frontend is copied to `static/admin-app` and served at `/admin/app`.
The Telegram webhook path will be available at `/telegram/webhook/<TELEGRAM_TOKEN>` — set your webhook URL in Telegram accordingly (must be HTTPS).
