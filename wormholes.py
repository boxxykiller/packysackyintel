"""Active Thera / Turnur wormhole connections, from EVE-Scout's public signature feed."""
import json
import urllib.request

URL = "https://api.eve-scout.com/v2/public/signatures"
HEADERS = {"User-Agent": "PackySackyIntelTool", "Accept": "application/json"}
J_SPACE = {"c1", "c2", "c3", "c4", "c5", "c6", "c13", "thera", "pochven", "drifter"}


def fetch(timeout=15):
    """Return [{hub, sid, name, cls, region, hours, size, sig_hub, sig_in, wh_type}] for every active connection."""
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    out = []
    for s in data:
        if s.get("signature_type") != "wormhole" or not s.get("in_system_id"):
            continue
        out.append({"hub": s.get("out_system_name", "?"), "sid": int(s["in_system_id"]),
                    "name": s.get("in_system_name", "?"), "cls": (s.get("in_system_class") or "").lower(),
                    "region": s.get("in_region_name", ""), "hours": s.get("remaining_hours"),
                    "size": s.get("max_ship_size", ""), "sig_hub": s.get("out_signature", ""),
                    "sig_in": s.get("in_signature", ""), "wh_type": s.get("wh_type", "")})
    return out
