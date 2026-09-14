"""Smart Lock Adaptive Access Control - backend with Google OAuth + IP geolocation.
Run:  python server.py   -> http://localhost:8000

Multi-tenant: each Google account = admin for their lock(s).
Super admin (website owner) sees all users + lock IDs.
Location stored per event. Admin-only map on dashboard.
Deploy: Railway with PostgreSQL (DATABASE_URL env var). Local: SQLite fallback.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, quote
import urllib.request

import risk_engine as re
from sim_data import build_dataset
from geo import resolve_ip, get_client_ip

# ---------- config ----------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_PG = bool(DATABASE_URL)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
DEVICE_ID = os.environ.get("DEVICE_ID", "door01")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "")
MODE = os.environ.get("MODE", "simulation")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
IPINFO_TOKEN = os.environ.get("IPINFO_TOKEN", "")
SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "")
APP_URL = os.environ.get("APP_URL", "")
OAUTH_REDIRECT = f"{APP_URL}/api/auth/google/callback" if APP_URL else f"http://127.0.0.1:{PORT}/api/auth/google/callback"

OTP_STORE = {}
SESSION_STORE = {}
MODEL = re.IsolationForestLite()
MODEL_TRAINED = {"ok": False}

# ---------- DB abstraction (SQLite local / PostgreSQL production) ----------
if USE_PG:
    import psycopg2
    import psycopg2.extras

    def db():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn

    def db_exec(conn, sql, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def db_fetchone(conn, sql, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    def db_fetchall(conn, sql, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def db_insert_returning(conn, sql, params=()):
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def db_table_check(conn, table, where="1=1"):
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        return cur.fetchone()[0]
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "smartlock.db")

    def db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def db_exec(conn, sql, params=()):
        return conn.execute(sql, params)

    def db_fetchone(conn, sql, params=()):
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    def db_fetchall(conn, sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def db_insert_returning(conn, sql, params=()):
        cur = conn.execute(sql, params)
        return cur.lastrowid

    def db_table_check(conn, table, where="1=1"):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]

def init_db():
    c = db()
    if USE_PG:
        cur = c.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, pin TEXT,
          usual_start_hour REAL, usual_end_hour REAL, mean_interarrival_min REAL);
        CREATE TABLE IF NOT EXISTS events(id SERIAL PRIMARY KEY, ts REAL,
          user_id TEXT, credential_ok INTEGER, hour REAL, inter_arrival_min REAL,
          fail_count INTEGER, session_novelty INTEGER, c REAL, b REAL, h REAL, r REAL,
          decision TEXT, detail TEXT,
          client_ip TEXT, lat REAL, lon REAL, city TEXT, region TEXT, country TEXT);
        CREATE TABLE IF NOT EXISTS lock_state(id INTEGER PRIMARY KEY CHECK(id=1),
          state TEXT, updated_at REAL);
        CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY CHECK(id=1),
          wC REAL, wB REAL, wH REAL, tau1 REAL, tau2 REAL, mode TEXT, device_id TEXT);
        CREATE TABLE IF NOT EXISTS lock_owners(
          google_email TEXT PRIMARY KEY, google_name TEXT, google_picture TEXT,
          device_id TEXT NOT NULL, lock_name TEXT, registered_at REAL,
          is_super_admin INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS admins(
          google_email TEXT PRIMARY KEY, google_name TEXT, google_picture TEXT,
          role TEXT DEFAULT 'admin', created_at REAL);
        """)
        c.commit()
    else:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, pin TEXT,
          usual_start_hour REAL, usual_end_hour REAL, mean_interarrival_min REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
          user_id TEXT, credential_ok INTEGER, hour REAL, inter_arrival_min REAL,
          fail_count INTEGER, session_novelty INTEGER, c REAL, b REAL, h REAL, r REAL,
          decision TEXT, detail TEXT,
          client_ip TEXT, lat REAL, lon REAL, city TEXT, region TEXT, country TEXT);
        CREATE TABLE IF NOT EXISTS lock_state(id INTEGER PRIMARY KEY CHECK(id=1),
          state TEXT, updated_at REAL);
        CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY CHECK(id=1),
          wC REAL, wB REAL, wH REAL, tau1 REAL, tau2 REAL, mode TEXT, device_id TEXT);
        CREATE TABLE IF NOT EXISTS lock_owners(
          google_email TEXT PRIMARY KEY, google_name TEXT, google_picture TEXT,
          device_id TEXT NOT NULL, lock_name TEXT, registered_at REAL,
          is_super_admin INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS admins(
          google_email TEXT PRIMARY KEY, google_name TEXT, google_picture TEXT,
          role TEXT DEFAULT 'admin', created_at REAL);
        """)
        cols = {r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()}
        for col, typ in [("client_ip", "TEXT"), ("lat", "REAL"), ("lon", "REAL"),
                         ("city", "TEXT"), ("region", "TEXT"), ("country", "TEXT")]:
            if col not in cols:
                c.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")

    # ---- Migration: add phone/is_temporary to users table ----
    if USE_PG:
        try:
            c.execute("SELECT phone FROM users LIMIT 0")
        except Exception:
            c.execute("ALTER TABLE users ADD COLUMN phone TEXT")
            c.execute("ALTER TABLE users ADD COLUMN is_temporary INTEGER DEFAULT 0")
            c.commit()
    else:
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "phone" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        if "is_temporary" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN is_temporary INTEGER DEFAULT 0")
            c.commit()

    # ---- New table: temp_pins (one-time PINs) ----
    if USE_PG:
        c.execute("""CREATE TABLE IF NOT EXISTS temp_pins(
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            pin_hash TEXT,
            phone TEXT,
            created_at REAL,
            used INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )""")
    else:
        c.execute("""CREATE TABLE IF NOT EXISTS temp_pins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            pin_hash TEXT,
            phone TEXT,
            created_at REAL,
            used INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )""")

    # ---- New table: otp_codes ----
    if USE_PG:
        c.execute("""CREATE TABLE IF NOT EXISTS otp_codes(
            id SERIAL PRIMARY KEY,
            phone TEXT,
            otp TEXT,
            purpose TEXT,
            created_at REAL,
            verified INTEGER DEFAULT 0
        )""")
    else:
        c.execute("""CREATE TABLE IF NOT EXISTS otp_codes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            otp TEXT,
            purpose TEXT,
            created_at REAL,
            verified INTEGER DEFAULT 0
        )""")

    ph = "%s" if USE_PG else "?"
    upsert_cfg = f"INSERT INTO config VALUES(1,0.35,0.40,0.25,0.50,0.65,{ph},{ph})" if not db_table_check(c, "config") else f"UPDATE config SET mode={ph}, device_id={ph} WHERE id=1"
    if not db_table_check(c, "config"):
        db_exec(c, f"INSERT INTO config VALUES(1,0.35,0.40,0.25,0.50,0.65,{ph},{ph})", (MODE, DEVICE_ID))
    else:
        db_exec(c, f"UPDATE config SET mode={ph}, device_id={ph} WHERE id=1", (MODE, DEVICE_ID))

    if not db_table_check(c, "lock_state"):
        db_exec(c, f"INSERT INTO lock_state VALUES(1,'LOCKED',{ph})", (time.time(),))

    if db_table_check(c, "users") == 0:
        from sim_data import gen_users
        for u in gen_users()[:5]:
            db_exec(c, f"INSERT INTO users VALUES({ph},{ph},{ph},{ph},{ph},{ph})",
                    (u["id"], u["name"], u["pin"], u["usual_start_hour"],
                     u["usual_end_hour"], u["mean_interarrival_min"]))

    if SUPER_ADMIN_EMAIL:
        if USE_PG:
            db_exec(c, f"INSERT INTO lock_owners(google_email,google_name,device_id,lock_name,registered_at,is_super_admin) VALUES({ph},{ph},{ph},{ph},{ph},1) ON CONFLICT (google_email) DO NOTHING",
                    (SUPER_ADMIN_EMAIL, "Website Owner", DEVICE_ID, "Main Lock", time.time()))
            db_exec(c, f"INSERT INTO admins(google_email,google_name,role,created_at) VALUES({ph},{ph},'super_admin',{ph}) ON CONFLICT (google_email) DO NOTHING",
                    (SUPER_ADMIN_EMAIL, "Website Owner", time.time()))
        else:
            c.execute("INSERT OR IGNORE INTO lock_owners(google_email,google_name,device_id,lock_name,registered_at,is_super_admin) VALUES(?,?,?,?,?,1)",
                      (SUPER_ADMIN_EMAIL, "Website Owner", DEVICE_ID, "Main Lock", time.time()))
            c.execute("INSERT OR IGNORE INTO admins(google_email,google_name,role,created_at) VALUES(?,?, 'super_admin',?)",
                      (SUPER_ADMIN_EMAIL, "Website Owner", time.time()))
    c.commit()
    c.close()
    train_model()

def ph():
    return "%s" if USE_PG else "?"

def get_config():
    c = db()
    r = db_fetchone(c, "SELECT * FROM config WHERE id=1")
    c.close()
    return r

def get_user(uid):
    c = db()
    r = db_fetchone(c, f"SELECT * FROM users WHERE id={ph()}", (uid,))
    c.close()
    return r

def recent_fail_count(user_id, window=10):
    c = db()
    rows = db_fetchall(c, f"SELECT fail_count, credential_ok FROM events WHERE user_id={ph()} ORDER BY id DESC LIMIT {ph()}", (user_id, window))
    c.close()
    fails = sum(1 for r in rows if r["credential_ok"] == 0)
    mx = max([r["fail_count"] for r in rows] or [0])
    return max(fails, mx)

def last_interarrival(user_id, default=180.0):
    c = db()
    rows = db_fetchall(c, f"SELECT ts FROM events WHERE user_id={ph()} ORDER BY id DESC LIMIT 2", (user_id,))
    c.close()
    if len(rows) < 2:
        return default
    return max(0.5, (rows[0]["ts"] - rows[1]["ts"]) / 60.0)

def train_model():
    c = db()
    rows = db_fetchall(c, "SELECT hour, inter_arrival_min, fail_count, session_novelty FROM events LIMIT 6000")
    c.close()
    if len(rows) >= 40:
        feats = [MODEL.featurize(r["hour"], r["inter_arrival_min"], r["fail_count"], r["session_novelty"]) for r in rows]
        MODEL.fit(feats)
        MODEL_TRAINED["ok"] = True
    else:
        from sim_data import gen_users, gen_normal
        import random as R
        rnd = R.Random(7)
        feats = []
        for u in gen_users():
            for _ in range(60):
                e = gen_normal(u, rnd)
                feats.append(MODEL.featurize(e["hour"], e["inter_arrival_min"], e["fail_count"], e["session_novelty"]))
        MODEL.fit(feats)
        MODEL_TRAINED["ok"] = True

def log_event(user_id, cred_ok, hour, iv, fc, sn, C, B, H, R, decision, detail="", client_ip="", lat=None, lon=None, city="", region="", country=""):
    c = db()
    p = ph()
    if USE_PG:
        eid = db_insert_returning(c,
            f"""INSERT INTO events(ts,user_id,credential_ok,hour,inter_arrival_min,
            fail_count,session_novelty,c,b,h,r,decision,detail,client_ip,lat,lon,city,region,country)
            VALUES({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p}) RETURNING id""",
            (time.time(), user_id, 1 if cred_ok else 0, hour, iv, fc, sn, C, B, H, R, decision, detail,
             client_ip, lat, lon, city, region, country))
    else:
        eid = db_insert_returning(c,
            f"""INSERT INTO events(ts,user_id,credential_ok,hour,inter_arrival_min,
            fail_count,session_novelty,c,b,h,r,decision,detail,client_ip,lat,lon,city,region,country)
            VALUES({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})""",
            (time.time(), user_id, 1 if cred_ok else 0, hour, iv, fc, sn, C, B, H, R, decision, detail,
             client_ip, lat, lon, city, region, country))
    c.commit()
    c.close()
    return eid

def mqtt_publish(topic, payload):
    if not MQTT_BROKER:
        return False
    try:
        import paho.mqtt.publish as pub
        pub.single(topic, json.dumps(payload), hostname=MQTT_BROKER)
        return True
    except Exception as e:
        print("MQTT publish skipped:", e)
        return False

def evaluate(user_id, hour, iv, sn, cfg):
    user = get_user(user_id) or {"usual_start_hour": 7, "usual_end_hour": 21, "mean_interarrival_min": 180}
    F = recent_fail_count(user_id)
    C = re.context_score(hour, iv, user)
    C = re.blend_session_novelty(C, sn)
    B = MODEL.anomaly_score(MODEL.featurize(hour, iv, F, sn))
    H = re.history_score(F)
    R = re.compute_risk(C, B, H, cfg["wC"], cfg["wB"], cfg["wH"])
    dec = re.decide(R, cfg["tau1"], cfg["tau2"])
    return C, B, H, R, dec, F

# ---------- WhatsApp simulation ----------
def send_whatsapp(phone, message):
    print(f"[WHATSAPP → {phone}] {message}")
    return True

def generate_otp(phone):
    otp = f"{secrets.randbelow(900000)+100000}"
    c = db(); p = ph()
    db_exec(c, f"INSERT INTO otp_codes(phone,otp,purpose,created_at) VALUES({p},{p},{p},{p})",
            (phone, otp, "request_pin", time.time()))
    c.commit(); c.close()
    send_whatsapp(phone, f"Your verification code: {otp}")
    return otp

def generate_temp_pin(user_id, phone):
    raw = f"{secrets.randbelow(900000)+100000}"
    pin_hash = hashlib.sha256(raw.encode()).hexdigest()
    c = db(); p = ph()
    if USE_PG:
        pid = db_insert_returning(c,
            f"INSERT INTO temp_pins(user_id,pin_hash,phone,created_at,used,active) VALUES({p},{p},{p},{p},0,1) RETURNING id",
            (user_id, pin_hash, phone, time.time()))
    else:
        pid = db_insert_returning(c,
            f"INSERT INTO temp_pins(user_id,pin_hash,phone,created_at,used,active) VALUES({p},{p},{p},{p},0,1)",
            (user_id, pin_hash, phone, time.time()))
    c.commit(); c.close()
    send_whatsapp(phone, f"Your one-time PIN: {raw}\nSingle-use. One lock/unlock only.")
    return raw, pid

def verify_temp_pin(pin_entered, client_ip, loc):
    pin_hash = hashlib.sha256(pin_entered.encode()).hexdigest()
    c = db(); p = ph()
    rows = db_fetchall(c, f"SELECT * FROM temp_pins WHERE pin_hash={p} AND active=1 ORDER BY id DESC LIMIT 1", (pin_hash,))
    if not rows:
        c.close()
        eid = log_event("unknown", False, time.localtime().tm_hour, 180, 0, 0, 0, 0, 1.0, 1.0, "DENY_ALERT",
                        "invalid/temp PIN", client_ip, loc.get("lat"), loc.get("lon"), loc.get("city",""), loc.get("region",""), loc.get("country",""))
        return {"decision": "DENY_ALERT", "reason": "invalid or expired PIN"}
    row = rows[0]
    user_id = row["user_id"]
    hour = float(time.localtime().tm_hour + time.localtime().tm_min / 60)
    iv = last_interarrival(user_id)
    cfg = get_config()
    C, B, Hh, R, dec, F = evaluate(user_id, hour, iv, 1, cfg)
    eid = log_event(user_id, dec == "GRANT", hour, iv, F, 1, C, B, Hh, R, dec, "temp PIN access",
                    client_ip, loc.get("lat"), loc.get("lon"), loc.get("city",""), loc.get("region",""), loc.get("country",""))
    if dec == "GRANT":
        db_exec(c, f"UPDATE temp_pins SET used=1,active=0 WHERE id={row['id']}")
        db_exec(c, f"UPDATE lock_state SET state='UNLOCKED',updated_at={p} WHERE id=1", (time.time(),))
        mqtt_publish(f"smartlock/{cfg['device_id']}/command", {"cmd": "UNLOCK", "eventId": eid, "via": "temp_pin"})
        c.commit()
    else:
        c.commit()
    c.close()
    result = {"decision": dec, "R": R, "C": C, "B": B, "H": Hh, "eventId": eid, "via": "temp_pin"}
    if dec == "STEP_UP":
        otp_code = f"{secrets.randbelow(900000)+100000}"
        OTP_STORE[eid] = otp_code
        result["demo_otp"] = otp_code
    return result

# ---------- Google OAuth ----------
def _google_fetch(url, data=None):
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None

def google_get_user_info(code):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return None
    token_data = _google_fetch("https://oauth2.googleapis.com/token", {
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": OAUTH_REDIRECT, "grant_type": "authorization_code",
    })
    if not token_data or "access_token" not in token_data:
        return None
    req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo",
                                headers={"Authorization": f"Bearer {token_data['access_token']}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None

def create_session(user_info):
    token = secrets.token_urlsafe(32)
    c = db()
    email = user_info.get("email", "")
    owner = db_fetchone(c, f"SELECT * FROM lock_owners WHERE google_email={ph()}", (email,))
    is_super = bool(SUPER_ADMIN_EMAIL and email == SUPER_ADMIN_EMAIL)
    role = "super_admin" if is_super else ("admin" if owner else "user")
    device_id = owner["device_id"] if owner else ""
    SESSION_STORE[token] = {
        "user_id": email, "email": email,
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
        "role": role, "device_id": device_id, "ts": time.time(),
    }
    c.close()
    return token

def get_session(handler):
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "session" and v in SESSION_STORE:
            sess = SESSION_STORE[v]
            if time.time() - sess["ts"] < 86400:
                return sess
    return None

# ---------- HTTP ----------
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
MIME = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
        ".json": "application/json", ".png": "image/png", ".ico": "image/x-icon"}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_html(self, html, code=200):
        b = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def set_session_cookie(self, token):
        self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")

    def body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except Exception:
            n = 0
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        P = ph()

        if p.path == "/api/auth/google/login":
            if not GOOGLE_CLIENT_ID:
                return self.send_json({"error": "Google OAuth not configured."}, 503)
            params = urlencode({
                "client_id": GOOGLE_CLIENT_ID, "redirect_uri": OAUTH_REDIRECT,
                "response_type": "code", "scope": "openid email profile",
                "access_type": "offline", "prompt": "consent",
            })
            self.send_response(302)
            self.send_header("Location", f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
            self.end_headers()
            return

        if p.path == "/api/auth/google/callback":
            code = q.get("code", [None])[0]
            if not code:
                return self.send_html("<h3>Login failed</h3><p>No code received.</p><a href='/'>Back</a>", 400)
            info = google_get_user_info(code)
            if not info:
                return self.send_html("<h3>Login failed</h3><p>Could not verify credentials.</p><a href='/'>Back</a>", 400)
            token = create_session(info)
            self.send_response(302)
            self.send_header("Location", "/")
            self.set_session_cookie(token)
            self.end_headers()
            return

        if p.path == "/api/auth/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "session" and v in SESSION_STORE:
                    del SESSION_STORE[v]
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if p.path == "/api/auth/me":
            sess = get_session(self)
            if not sess:
                return self.send_json({"logged_in": False})
            return self.send_json({
                "logged_in": True, "email": sess["email"], "name": sess["name"],
                "picture": sess["picture"], "role": sess["role"], "device_id": sess["device_id"],
            })

        if p.path.startswith("/api/"):
            sess = get_session(self)

            if p.path == "/api/health":
                return self.send_json({"ok": True, "mode": get_config()["mode"],
                                       "model_trained": MODEL_TRAINED["ok"],
                                       "google_configured": bool(GOOGLE_CLIENT_ID)})

            if p.path == "/api/config":
                return self.send_json(get_config())

            if p.path == "/api/admin/users":
                if not sess or sess["role"] not in ("admin", "super_admin"):
                    return self.send_json({"error": "forbidden"}, 403)
                c = db()
                rows = db_fetchall(c, "SELECT id,name,phone,is_temporary FROM users ORDER BY id")
                c.close()
                return self.send_json({"users": rows})

            if p.path == "/api/admin/temp-pins":
                if not sess or sess["role"] not in ("admin", "super_admin"):
                    return self.send_json({"error": "forbidden"}, 403)
                c = db()
                rows = db_fetchall(c, "SELECT tp.id,tp.user_id,tp.phone,tp.created_at,tp.used,tp.active,u.name FROM temp_pins tp LEFT JOIN users u ON tp.user_id=u.id ORDER BY tp.id DESC LIMIT 50")
                c.close()
                return self.send_json({"pins": rows})

            if p.path == "/api/users":
                if not sess:
                    return self.send_json({"error": "unauthorized"}, 401)
                c = db()
                u = db_fetchall(c, "SELECT id,name,usual_start_hour,usual_end_hour,mean_interarrival_min FROM users")
                c.close()
                return self.send_json({"users": u})

            if p.path == "/api/lock/status":
                c = db()
                s = db_fetchone(c, "SELECT * FROM lock_state WHERE id=1")
                last = db_fetchone(c, "SELECT * FROM events ORDER BY id DESC LIMIT 1")
                c.close()
                return self.send_json({"state": s["state"], "updated_at": s["updated_at"],
                                       "mode": get_config()["mode"], "device_id": get_config()["device_id"],
                                       "last_event": last})

            if p.path == "/api/events":
                if not sess:
                    return self.send_json({"error": "unauthorized"}, 401)
                lim = int(q.get("limit", ["80"])[0])
                c = db()
                rows = db_fetchall(c, f"SELECT * FROM events ORDER BY id DESC LIMIT {P}", (lim,))
                c.close()
                return self.send_json({"events": rows})

            if p.path == "/api/admin/locations":
                if not sess or sess["role"] not in ("admin", "super_admin"):
                    return self.send_json({"error": "forbidden"}, 403)
                c = db()
                rows = db_fetchall(c, "SELECT id,ts,user_id,city,region,country,lat,lon,client_ip,decision FROM events WHERE lat IS NOT NULL ORDER BY id DESC LIMIT 50")
                c.close()
                return self.send_json({"locations": rows})

            if p.path == "/api/super/overview":
                if not sess or sess["role"] != "super_admin":
                    return self.send_json({"error": "forbidden"}, 403)
                c = db()
                owners = db_fetchall(c, "SELECT * FROM lock_owners ORDER BY registered_at DESC")
                ev_count = db_fetchone(c, "SELECT COUNT(*) as n FROM events")["n"]
                us_count = db_fetchone(c, "SELECT COUNT(*) as n FROM users")["n"]
                recent = db_fetchall(c, "SELECT * FROM events ORDER BY id DESC LIMIT 20")
                c.close()
                return self.send_json({"owners": owners, "total_events": ev_count,
                                       "total_users": us_count, "recent_events": recent})

            if p.path == "/api/lock/register":
                if not sess:
                    return self.send_json({"error": "unauthorized"}, 401)
                d = self.body()
                device_id = d.get("device_id", "").strip()
                lock_name = d.get("lock_name", "").strip()
                if not device_id:
                    return self.send_json({"error": "device_id required"}, 400)
                c = db()
                existing = db_fetchone(c, f"SELECT * FROM lock_owners WHERE google_email={P}", (sess["email"],))
                if existing:
                    c.close()
                    return self.send_json({"error": "You already own a lock."}, 400)
                if USE_PG:
                    db_exec(c, f"INSERT INTO lock_owners(google_email,google_name,google_picture,device_id,lock_name,registered_at,is_super_admin) VALUES({P},{P},{P},{P},{P},{P},0)",
                            (sess["email"], sess["name"], sess["picture"], device_id, lock_name or f"My Lock ({device_id})", time.time()))
                    db_exec(c, f"INSERT INTO admins(google_email,google_name,role,created_at) VALUES({P},{P},'admin',{P}) ON CONFLICT (google_email) DO NOTHING",
                            (sess["email"], sess["name"], time.time()))
                else:
                    c.execute("INSERT INTO lock_owners(google_email,google_name,google_picture,device_id,lock_name,registered_at,is_super_admin) VALUES(?,?,?,?,?,?,0)",
                              (sess["email"], sess["name"], sess["picture"], device_id, lock_name or f"My Lock ({device_id})", time.time()))
                    c.execute("INSERT OR IGNORE INTO admins(google_email,google_name,role,created_at) VALUES(?,?, 'admin',?)",
                              (sess["email"], sess["name"], time.time()))
                c.commit()
                c.close()
                sess["role"] = "admin"
                sess["device_id"] = device_id
                return self.send_json({"ok": True, "device_id": device_id})

        if p.path == "/api/admin/register-user":
            if not sess or sess["role"] not in ("admin", "super_admin"):
                return self.send_json({"error": "forbidden"}, 403)
            uid = d.get("userId", "").strip()
            name = d.get("name", "").strip()
            phone = d.get("phone", "").strip()
            is_temp = 1 if d.get("is_temporary") else 0
            if not uid or not name or not phone:
                return self.send_json({"error": "userId, name, phone required"}, 400)
            c = db(); p = ph()
            existing = db_fetchone(c, f"SELECT id FROM users WHERE id={p}", (uid,))
            if existing:
                c.close()
                return self.send_json({"error": "User ID already exists"}, 400)
            if USE_PG:
                db_exec(c, f"INSERT INTO users(id,name,phone,is_temporary,pin,usual_start_hour,usual_end_hour,mean_interarrival_min) VALUES({p},{p},{p},{p},'',7.0,21.0,180.0)",
                        (uid, name, phone, is_temp))
            else:
                db_exec(c, f"INSERT INTO users(id,name,phone,is_temporary,pin,usual_start_hour,usual_end_hour,mean_interarrival_min) VALUES({p},{p},{p},{p},'',7.0,21.0,180.0)",
                        (uid, name, phone, is_temp))
            c.commit(); c.close()
            return self.send_json({"ok": True, "userId": uid})

        if p.path == "/api/admin/delete-user":
            if not sess or sess["role"] not in ("admin", "super_admin"):
                return self.send_json({"error": "forbidden"}, 403)
            uid = d.get("userId", "").strip()
            if not uid:
                return self.send_json({"error": "userId required"}, 400)
            c = db(); p = ph()
            db_exec(c, f"DELETE FROM users WHERE id={p}", (uid,))
            db_exec(c, f"DELETE FROM temp_pins WHERE user_id={p}", (uid,))
            c.commit(); c.close()
            return self.send_json({"ok": True})

        if p.path == "/api/user/request-otp":
            if not sess:
                return self.send_json({"error": "unauthorized"}, 401)
            phone_input = str(d.get("phone", "")).strip()
            c = db(); p = ph()
            user = db_fetchone(c, f"SELECT * FROM users WHERE id={p}", (sess["email"],))
            c.close()
            if not user:
                return self.send_json({"error": "You are not registered. Ask admin."}, 404)
            if not user.get("phone"):
                return self.send_json({"error": "No phone registered. Ask admin."}, 400)
            if phone_input != user["phone"]:
                return self.send_json({"error": "Phone does not match registered number."}, 400)
            otp = generate_otp(user["phone"])
            masked = user["phone"][:3] + "****" + user["phone"][-2:] if len(user["phone"]) > 5 else user["phone"]
            return self.send_json({"ok": True, "phone_masked": masked, "demo_otp": otp,
                                   "message": f"OTP sent to {masked} (simulated)"})

        if p.path == "/api/user/verify-otp":
            if not sess:
                return self.send_json({"error": "unauthorized"}, 401)
            phone_input = str(d.get("phone", "")).strip()
            otp_input = str(d.get("otp", "")).strip()
            c = db(); p = ph()
            user = db_fetchone(c, f"SELECT * FROM users WHERE id={p}", (sess["email"],))
            c.close()
            if not user or not user.get("phone"):
                return self.send_json({"error": "Not registered or no phone."}, 400)
            if phone_input != user["phone"]:
                return self.send_json({"error": "Phone mismatch."}, 400)
            c = db()
            row = db_fetchall(c, f"SELECT * FROM otp_codes WHERE phone={p} AND otp={p} AND purpose='request_pin' AND verified=0 ORDER BY id DESC LIMIT 1",
                              (phone_input, otp_input))
            if not row:
                c.close()
                return self.send_json({"error": "Invalid or expired OTP."}, 400)
            db_exec(c, f"UPDATE otp_codes SET verified=1 WHERE id={row[0]['id']}")
            c.commit(); c.close()
            raw_pin, pid = generate_temp_pin(sess["email"], user["phone"])
            return self.send_json({"ok": True, "pin": raw_pin, "message": "One-time PIN generated. Single-use — one lock/unlock only."})

        if p.path == "/api/access/pin":
            pin_entered = str(d.get("pin", "")).strip()
            if not pin_entered or len(pin_entered) != 6:
                return self.send_json({"error": "6-digit PIN required"}, 400)
            result = verify_temp_pin(pin_entered, client_ip, loc)
            return self.send_json(result)

        if p.path == "/api/admin/revoke-pin":
            if not sess or sess["role"] not in ("admin", "super_admin"):
                return self.send_json({"error": "forbidden"}, 403)
            pin_id = int(d.get("pinId", 0))
            c = db(); p = ph()
            db_exec(c, f"UPDATE temp_pins SET active=0 WHERE id={p}", (pin_id,))
            c.commit(); c.close()
            return self.send_json({"ok": True})

        return self.send_json({"error": "not found"}, 404)

        path = p.path if p.path != "/" else "/index.html"
        fp = os.path.join(PUBLIC, path.lstrip("/").replace("..", ""))
        if os.path.isfile(fp):
            ext = os.path.splitext(fp)[1].lower()
            with open(fp, "rb") as f:
                b = f.read()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path)
        d = self.body()
        cfg = get_config()
        sess = get_session(self)
        client_ip = get_client_ip(self)
        loc = resolve_ip(client_ip, IPINFO_TOKEN) or {}
        P = ph()

        if p.path == "/api/config":
            if not sess or sess["role"] not in ("admin", "super_admin"):
                return self.send_json({"error": "forbidden"}, 403)
            c = db()
            db_exec(c, f"UPDATE config SET wC={P},wB={P},wH={P},tau1={P},tau2={P} WHERE id=1",
                    (float(d.get("wC", cfg["wC"])), float(d.get("wB", cfg["wB"])),
                     float(d.get("wH", cfg["wH"])), float(d.get("tau1", cfg["tau1"])),
                     float(d.get("tau2", cfg["tau2"]))))
            c.commit(); c.close()
            return self.send_json({"ok": True, "config": get_config()})

        if p.path == "/api/access/request":
            user_id = d.get("userId", "user_01")
            cred = str(d.get("credential", ""))
            user = get_user(user_id)
            cred_ok = bool(user)
            cdb = db()
            real_pin = db_fetchone(cdb, f"SELECT pin FROM users WHERE id={P}", (user_id,))
            cdb.close()
            pin_ok = (real_pin and cred == real_pin["pin"]) if cred else False
            hour = float(d.get("hour", time.localtime().tm_hour + time.localtime().tm_min / 60))
            iv = d.get("interArrivalMin", None)
            if iv is None:
                iv = last_interarrival(user_id)
            sn = int(d.get("sessionNovelty", 0))
            if not pin_ok:
                F = recent_fail_count(user_id) + 1
                H = re.history_score(F)
                eid = log_event(user_id, False, hour, iv, F, sn, 0.2, 0.5, H, 0.8, "DENY_ALERT", "invalid credential",
                                client_ip, loc.get("lat"), loc.get("lon"), loc.get("city",""), loc.get("region",""), loc.get("country",""))
                return self.send_json({"decision": "DENY_ALERT", "reason": "invalid credential",
                                       "R": 0.8, "C": 0.2, "B": 0.5, "H": H, "eventId": eid})
            C, B, Hh, R, dec, F = evaluate(user_id, hour, float(iv), sn, cfg)
            eid = log_event(user_id, True, hour, float(iv), F, sn, C, B, Hh, R, dec, "adaptive decision",
                            client_ip, loc.get("lat"), loc.get("lon"), loc.get("city",""), loc.get("region",""), loc.get("country",""))
            if dec == "GRANT":
                c = db(); db_exec(c, f"UPDATE lock_state SET state='UNLOCKED',updated_at={P} WHERE id=1", (time.time(),)); c.commit(); c.close()
                mqtt_publish(f"smartlock/{cfg['device_id']}/command", {"cmd": "UNLOCK", "eventId": eid, "risk": R})
                return self.send_json({"decision": "GRANT", "R": R, "C": C, "B": B, "H": Hh, "eventId": eid})
            if dec == "STEP_UP":
                otp = f"{secrets.randbelow(900000)+100000}"
                OTP_STORE[eid] = otp
                return self.send_json({"decision": "STEP_UP", "R": R, "C": C, "B": B, "H": Hh,
                                       "eventId": eid, "demo_otp": otp,
                                       "message": "Additional authentication required (enter demo OTP)"})
            mqtt_publish(f"smartlock/{cfg['device_id']}/command", {"cmd": "DENY", "eventId": eid, "risk": R})
            return self.send_json({"decision": "DENY_ALERT", "R": R, "C": C, "B": B, "H": Hh,
                                   "eventId": eid, "message": "Access denied + alert raised"})

        if p.path == "/api/access/stepup":
            eid = int(d.get("eventId", 0)); otp = str(d.get("otp", ""))
            if OTP_STORE.get(eid) == otp:
                del OTP_STORE[eid]
                c = db(); db_exec(c, f"UPDATE lock_state SET state='UNLOCKED',updated_at={P} WHERE id=1", (time.time(),))
                db_exec(c, f"UPDATE events SET decision='GRANT', detail='step-up passed' WHERE id={P}", (eid,))
                c.commit(); c.close()
                cfg2 = get_config()
                mqtt_publish(f"smartlock/{cfg2['device_id']}/command", {"cmd": "UNLOCK", "eventId": eid, "via": "stepup"})
                return self.send_json({"ok": True, "decision": "GRANT"})
            return self.send_json({"ok": False, "decision": "DENY_ALERT", "reason": "wrong OTP"}, 401)

        if p.path == "/api/lock/command":
            if not sess or sess["role"] not in ("admin", "super_admin"):
                return self.send_json({"error": "forbidden"}, 403)
            cmd = str(d.get("command", "LOCK")).upper()
            state = "UNLOCKED" if cmd == "UNLOCK" else "LOCKED"
            c = db(); db_exec(c, f"UPDATE lock_state SET state={P},updated_at={P} WHERE id=1", (state, time.time())); c.commit(); c.close()
            log_event(d.get("userId", sess["email"]), True, time.localtime().tm_hour, 180, 0, 0, 0, 0, 0, 0,
                      "GRANT", f"manual {cmd}", client_ip, loc.get("lat"), loc.get("lon"),
                      loc.get("city",""), loc.get("region",""), loc.get("country",""))
            mqtt_publish(f"smartlock/{cfg['device_id']}/command", {"cmd": cmd, "via": "dashboard"})
            return self.send_json({"ok": True, "state": state})

        if p.path == "/api/sim/run":
            users, train, test = build_dataset()
            umap = {u["id"]: u for u in users}
            feats = [MODEL.featurize(e["hour"], e["inter_arrival_min"], e["fail_count"], e["session_novelty"]) for e in train]
            MODEL.fit(feats)
            tp = fp = tn = fn = 0
            grants = stepups = denies = 0
            for e in test:
                u = umap[e["user_id"]]
                C = re.blend_session_novelty(re.context_score(e["hour"], e["inter_arrival_min"], u), e["session_novelty"])
                B = MODEL.anomaly_score(MODEL.featurize(e["hour"], e["inter_arrival_min"], e["fail_count"], e["session_novelty"]))
                Hh = re.history_score(e["fail_count"])
                R = re.compute_risk(C, B, Hh, cfg["wC"], cfg["wB"], cfg["wH"])
                dec = re.decide(R, cfg["tau1"], cfg["tau2"])
                grants += dec == "GRANT"; stepups += dec == "STEP_UP"; denies += dec == "DENY_ALERT"
                flagged = dec in ("STEP_UP", "DENY_ALERT")
                if e["label"] == 1 and flagged: tp += 1
                elif e["label"] == 1: fn += 1
                elif flagged: fp += 1
                else: tn += 1
            prec = tp / max(1, tp + fp); rec = tp / max(1, tp + fn)
            f1 = 2 * prec * rec / max(1e-9, prec + rec)
            return self.send_json({"tp": tp, "fp": fp, "tn": tn, "fn": fn,
                                   "precision": round(prec, 3), "recall": round(rec, 3),
                                   "f1": round(f1, 3), "fpr": round(fp / max(1, fp + tn), 3),
                                   "fnr": round(fn / max(1, fn + tp), 3),
                                   "grants": grants, "stepups": stepups, "denies": denies,
                                   "n_test": len(test)})
        return self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    init_db()
    db_type = "PostgreSQL" if USE_PG else "SQLite (local)"
    print(f"Smart Lock server on http://{HOST}:{PORT}  [db={db_type}]")
    print(f"  mode={get_config()['mode']}  mqtt={'on' if MQTT_BROKER else 'off'}  google={'configured' if GOOGLE_CLIENT_ID else 'not configured'}")
    print(f"  super_admin={SUPER_ADMIN_EMAIL or '(set SUPER_ADMIN_EMAIL env var)'}")
    print(f"  oauth_redirect={OAUTH_REDIRECT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
