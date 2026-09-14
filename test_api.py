"""Smoke test for the adaptive lock API. Run: python test_api.py (server must be running)."""
import json, urllib.request

BASE = "http://127.0.0.1:8000"
def call(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

h = call("/api/health"); assert h["ok"], h; print("health OK:", h)
cfg = call("/api/config"); assert abs(cfg["wC"] + cfg["wB"] + cfg["wH"] - 1) < 1e-6; print("config OK:", cfg["wC"], cfg["wB"], cfg["wH"])
# normal access with correct demo PIN 1000
r = call("/api/access/request", {"userId": "user_01", "credential": "1000", "hour": 10, "interArrivalMin": 180})
print("normal:", r["decision"], "R=", round(r.get("R", 0), 3)); assert r["decision"] == "GRANT", r
# unusual hour should escalate
r2 = call("/api/access/request", {"userId": "user_01", "credential": "1000", "hour": 3, "interArrivalMin": 5, "sessionNovelty": 1})
print("anomaly:", r2["decision"], "R=", round(r2.get("R", 0), 3)); assert r2["decision"] in ("STEP_UP", "DENY_ALERT"), r2
if r2["decision"] == "STEP_UP":
    v = call("/api/access/stepup", {"eventId": r2["eventId"], "otp": r2["demo_otp"]})
    print("stepup:", v); assert v["decision"] == "GRANT", v
# wrong PIN denied
r3 = call("/api/access/request", {"userId": "user_01", "credential": "wrong"})
print("wrong pin:", r3["decision"]); assert r3["decision"] == "DENY_ALERT", r3
print("ALL TESTS PASSED")
