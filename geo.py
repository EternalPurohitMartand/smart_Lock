"""IP geolocation — ip-api.com primary, ipinfo.io fallback. stdlib only."""
import json
import urllib.request
import urllib.error

_CACHE = {}  # ip -> {lat, lon, city, region, country, ip}
_TIMEOUT = 4  # seconds


def _get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartLock/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _ipapi(ip):
    data = _get(f"http://ip-api.com/json/{ip}?fields=status,lat,lon,city,regionName,country")
    if data and data.get("status") == "success":
        return {
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "city": data.get("city", ""),
            "region": data.get("regionName", ""),
            "country": data.get("country", ""),
            "ip": ip,
        }
    return None


def _ipinfo(ip, token=""):
    url = f"https://ipinfo.io/{ip}/json" + (f"?token={token}" if token else "")
    data = _get(url)
    if data and "loc" in data:
        parts = data["loc"].split(",")
        return {
            "lat": float(parts[0]) if len(parts) == 2 else None,
            "lon": float(parts[1]) if len(parts) == 2 else None,
            "city": data.get("city", ""),
            "region": data.get("region", ""),
            "country": data.get("country", ""),
            "ip": ip,
        }
    return None


def resolve_ip(ip, ipinfo_token=""):
    """Resolve IP to location. Returns dict or None. Cached."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return {"lat": None, "lon": None, "city": "Local", "region": "", "country": "Local", "ip": ip}
    if ip in _CACHE:
        return _CACHE[ip]
    loc = _ipapi(ip) or _ipinfo(ip, ipinfo_token)
    if loc:
        _CACHE[ip] = loc
    return loc


def get_client_ip(handler):
    """Extract real client IP from request headers."""
    forwarded = handler.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = handler.headers.get("X-Real-Ip")
    if real_ip:
        return real_ip.strip()
    return handler.client_address[0]
