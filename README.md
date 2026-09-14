# Adaptive Smart Lock — your own platform (paper-based, ESP32-ready)

Implements the research paper flow: `Access Request → Authentication → Context/Event Features → Anomaly (Isolation Forest lite) → Risk R → Grant / Step-up / Deny+Alert`.

Now with **Google OAuth login**, **IP geolocation tracking**, **admin-only location map**, and **super admin owner dashboard**.

## Quick Start

```bash
# 1. Set Google OAuth credentials (get from GOOGLE_OAUTH_SETUP.md)
export GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
export GOOGLE_CLIENT_SECRET=your-client-secret

# 2. (Optional) Set yourself as super admin
export SUPER_ADMIN_EMAIL=your-email@gmail.com

# 3. Run
python server.py
```

Open http://127.0.0.1:8000

## Features

### Google OAuth Login
- Full dashboard protection — must sign in to access
- First user to register a lock becomes **admin** for that lock
- **Super admin** (you) sees all users + lock IDs across all tenants
- See `GOOGLE_OAUTH_SETUP.md` for step-by-step setup

### IP Geolocation
- Every access request logs: IP, city, region, country, lat/lon
- Uses ip-api.com (primary) + ipinfo.io (fallback)
- Admin-only Leaflet map on Dashboard shows last access location
- Event Log shows location column for admins

### Multi-Tenant Model
- Each Google account = admin for their own lock
- Lock registration during first login
- Admin sees only their lock's data
- Super admin sees everything (Owner Dashboard tab)

### Owner Dashboard (Super Admin)
- Total events, total users, registered locks
- List of all lock owners with email, name, lock ID
- Recent events across all tenants with location + IP

## Demo PINs
- `user_01` → `1000`
- `user_02` → `1001`
- `user_03` → `1002`
- etc.

## Try the Paper Scenarios
1. **Normal day access**: Access tab → pick user, enter PIN → expect GRANT, low R
2. **Unusual hour**: set Hour=3 → expect STEP_UP or DENY_ALERT
3. **Burst**: set Inter-arrival=1 min → freq deviation raises C
4. **Failed attempts**: enter wrong PIN 3–4×, then correct PIN → H raises R
5. **Step-up**: copy `demo_otp` → Verify step-up → UNLOCKED
6. **Simulation Lab**: Run synthetic evaluation → Table 1 style metrics

## API (same for Node port + ESP32)
- `POST /api/access/request {userId, credential, hour?, interArrivalMin?, sessionNovelty?}`
- `POST /api/access/stepup {eventId, otp}`
- `POST /api/lock/command {command: LOCK|UNLOCK}` (admin only)
- `GET /api/lock/status`, `GET /api/events?limit=`, `GET/POST /api/config`
- `GET /api/auth/google/login`, `GET /api/auth/google/callback`, `GET /api/auth/me`
- `GET /api/admin/locations` (admin only), `GET /api/super/overview` (super admin only)
- `POST /api/lock/register {device_id, lock_name}` (authenticated users)
- `POST /api/sim/run`

## Deploy to Railway (5 minutes)

1. Push this project to GitHub (or any git host)
2. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**
3. Select your repo
4. Add PostgreSQL: **+ New** → **Database** → **PostgreSQL**
5. Set environment variables in Railway dashboard (Settings → Variables):
   ```
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   SUPER_ADMIN_EMAIL=your-email@gmail.com
   APP_URL=https://your-app.up.railway.app
   ```
6. Railway auto-deploys. Your app is live at `https://your-app.up.railway.app`
7. **Update Google OAuth redirect URI** in Google Cloud Console to:
   ```
   https://your-app.up.railway.app/api/auth/google/callback
   ```

### Environment Variables (Railway)

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Auto-set | PostgreSQL connection string (set by Railway PostgreSQL addon) |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `SUPER_ADMIN_EMAIL` | No | Your Google email for super admin access |
| `APP_URL` | Yes (deploy) | Your Railway app URL (e.g. `https://your-app.up.railway.app`) |
| `IPINFO_TOKEN` | No | ipinfo.io API token |
| `MQTT_BROKER` | No | MQTT broker address |

## Connect ESP32 Later
1. Keep Blynk today. When ready flash `esp32/ESP32_LOCK_FIRMWARE.ino`, set `API_BASE` to this PC
2. Optional MQTT: `set MQTT_BROKER=broker.hivemq.com` + `pip install paho-mqtt`
3. Topics: `smartlock/<deviceId>/command|status`
4. Website works in `simulation` mode until hardware publishes status

## Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth client secret |
| `SUPER_ADMIN_EMAIL` | No | Your Google email for super admin access |
| `IPINFO_TOKEN` | No | ipinfo.io API token (optional, ip-api.com used by default) |
| `MQTT_BROKER` | No | MQTT broker address (e.g. broker.hivemq.com) |
| `HOST` | No | Server host (default: 127.0.0.1) |
| `PORT` | No | Server port (default: 8000) |

## Paper Honesty (§4)
Software-only decision layer; weights/thresholds (`wC=0.35,wB=0.40,wH=0.25,τ1=0.50,τ2=0.65`) need recalibration on real data; BLE replay/relay needs protocol defenses (TLS, rolling codes), not just risk scoring.
