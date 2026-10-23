"""Raidable Orbital Skyhooks (reagent silo open to theft), from CCP's public ESI route /skyhooks/raidable.
The list holds every skyhook whose theft window is open now or opens within the next couple of hours."""
import datetime
import json
import urllib.request

URL = "https://esi.evetech.net/skyhooks/raidable"
HEADERS = {"User-Agent": "PackySackyIntelTool", "Accept": "application/json",
           "X-Compatibility-Date": "2026-05-19"}


def _when(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def fetch(timeout=15):
    """Return [{sid, planet, start, end}] (start / end as UTC datetimes) for every raidable skyhook."""
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    out = []
    for s in data.get("skyhooks", []):
        win = s.get("theft_vulnerability") or {}
        if not s.get("solar_system_id") or not win.get("start") or not win.get("end"):
            continue
        out.append({"sid": int(s["solar_system_id"]), "planet": s.get("planet_id"),
                    "start": _when(win["start"]), "end": _when(win["end"])})
    return out


def status(hook, now=None):
    """('open', minutes left) while the theft window is open, ('soon', minutes until it opens), or None once over."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if hook["end"] <= now:
        return None
    if hook["start"] <= now:
        return "open", int((hook["end"] - now).total_seconds() // 60)
    return "soon", int((hook["start"] - now).total_seconds() // 60)


def short(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def describe(hook, now=None):
    """'open, 1h05m left' / 'opens in 40m' / '' (window over)."""
    st = status(hook, now)
    if not st:
        return ""
    return f"open, {short(st[1])} left" if st[0] == "open" else f"opens in {short(st[1])}"
