"""EVE universe data (systems, gates, regions) from the CCP static data export, for the mini map and jump counts."""
import csv
import difflib
import os
import re
import sys
from collections import deque


def data_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data")


def _rows(name):
    with open(os.path.join(data_dir(), name), encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


class Universe:
    def __init__(self):
        self.sys = {}          # id -> dict(name, region, x, y, sec)
        self.by_name = {}      # lower name -> id
        self.regions = {}      # id -> name
        self.adj = {}          # id -> set(ids)
        for r in _rows("mapRegions.csv"):
            self.regions[int(r["regionID"])] = r["regionName"]
        for r in _rows("mapSolarSystems.csv"):
            sid = int(r["solarSystemID"])
            try:
                x, y = float(r["position2Dx"]), float(r["position2Dy"])
            except ValueError:
                x = y = None                                   # wormhole systems have no map position
            self.sys[sid] = {"name": r["solarSystemName"], "region": int(r["regionID"]), "x": x, "y": y,
                             "sec": float(r["security"] or 0)}
            self.by_name[r["solarSystemName"].lower()] = sid
        for r in _rows("mapSolarSystemJumps.csv"):
            a, b = int(r["fromSolarSystemID"]), int(r["toSolarSystemID"])
            self.adj.setdefault(a, set()).add(b)
            self.adj.setdefault(b, set()).add(a)
        self.by_region = {}
        for sid, s in self.sys.items():
            self.by_region.setdefault(s["region"], []).append(sid)
        self._dist_cache = {}

    def lookup(self, name):
        """Exact system name (any case) -> id, or a close OCR-style misread ('W-lX39') -> id, else None."""
        if not name:
            return None
        key = name.strip().lower()
        if key in self.by_name:
            return self.by_name[key]
        fixed = key.replace("l", "i")
        if fixed in self.by_name:
            return self.by_name[fixed]
        m = difflib.get_close_matches(key, self.by_name.keys(), n=1, cutoff=0.85)
        return self.by_name[m[0]] if m else None

    def distances(self, src):
        """BFS gate-jump distance from src to every reachable system."""
        if src in self._dist_cache:
            return self._dist_cache[src]
        dist = {src: 0}
        q = deque([src])
        while q:
            cur = q.popleft()
            for nb in self.adj.get(cur, ()):
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    q.append(nb)
        if len(self._dist_cache) > 8:
            self._dist_cache.clear()
        self._dist_cache[src] = dist
        return dist

    def mention_spans(self, text, current=None):
        """System names mentioned in a chat line, as [(start, end, system id)] character spans. Distinctive names
        (with a digit or hyphen, e.g. 3-FKCZ) match anywhere; plain-word names (e.g. Jita, Old Man Star) only
        near the current system, to avoid false hits."""
        toks = []
        for m in re.finditer(r"[^\s,;:()\[\]<>]+", text):
            word = m.group().strip(".!?'\"")
            if word:
                toks.append((word, m.start() + m.group().index(word[0]), m.start() + m.group().index(word[0]) + len(word)))
        near = set()
        if current:
            near = {s for s, d in self.distances(current).items() if d <= 10}
        out, i = [], 0
        while i < len(toks):
            hit = None
            for n in (3, 2, 1):                                  # prefer multi-word names
                if i + n <= len(toks):
                    cand = " ".join(t[0] for t in toks[i:i + n]).lower()
                    sid = self.by_name.get(cand)
                    if sid is not None and len(cand) >= 3:
                        distinctive = bool(re.search(r"[\d-]", cand))
                        if distinctive or sid in near:
                            hit = (toks[i][1], toks[i + n - 1][2], sid, n)
                            break
            if hit:
                out.append(hit[:3])
                i += hit[3]
            else:
                i += 1
        return out

    def find_mentions(self, text, current=None):
        """Distinct system ids mentioned in a chat line (see mention_spans)."""
        found = []
        for _, _, sid in self.mention_spans(text, current):
            if sid not in found:
                found.append(sid)
        return found
