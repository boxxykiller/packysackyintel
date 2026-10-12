"""Tail EVE chat logs (Documents/EVE/logs/Chatlogs) for channels whose name contains 'intel'.
Only reads the log files EVE itself writes to disk; nothing is read from the game."""
import ctypes
import os
import re
import time
from ctypes import wintypes

NAME_FILTER = "intel"
MAX_AGE = 12 * 3600          # a channel's newest log counts as "active" if it was written to within this long
HISTORY_LINES = 15           # lines shown from each log when first attached
FILE_RE = re.compile(r"^(?P<chan>.+)_(?P<date>\d{8})_(?P<time>\d{6})(?:_(?P<char>\d+))?\.txt$")
LINE_RE = re.compile(r"^\ufeff?\[\s*(\d{4})\.(\d\d)\.(\d\d) (\d\d:\d\d:\d\d)\s*\]\s*(.+?)\s*>\s*(.*)$")


def chatlog_dir():
    """Documents/EVE/logs/Chatlogs, using the real Documents folder (works with OneDrive redirection)."""
    try:
        buf = ctypes.c_wchar_p()
        guid = (ctypes.c_byte * 16)(*bytes.fromhex("D0 9A D3 FD 8F 23 AF 46 AD B4 6C 85 48 03 69 C7"))  # FOLDERID_Documents
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf))
        docs = buf.value
    except Exception:
        docs = None
    for base in (docs, os.path.join(os.path.expanduser("~"), "Documents")):
        if base:
            p = os.path.join(base, "EVE", "logs", "Chatlogs")
            if os.path.isdir(p):
                return p
    return None


def _decode(raw):
    return raw[: len(raw) // 2 * 2].decode("utf-16-le", errors="replace")


class Tail:
    def __init__(self, path, channel, listener):
        self.path, self.channel, self.listener = path, channel, listener
        self.offset = 0
        self.partial = ""

    def parse(self, text):
        out = []
        for line in text.splitlines():
            m = LINE_RE.match(line)
            if m and m.group(5) != "EVE System":
                y, mo, d, hms, sender, msg = m.groups()
                out.append({"time": hms, "stamp": f"{y}{mo}{d}{hms}", "channel": self.channel,
                            "listener": self.listener, "sender": sender, "text": msg})
        return out

    def attach(self):
        """Read the existing log; return its last HISTORY_LINES messages and remember where we are."""
        size = os.path.getsize(self.path)
        with open(self.path, "rb") as fh:
            if size > 200_000:                         # huge log: only look at the tail
                fh.seek(size - 200_000 - (size % 2))
            raw = fh.read()
        self.offset = size - len(raw) % 2
        text = _decode(raw)
        m = re.search(r"Listener:\s*(.+)", text)
        if m:
            self.listener = m.group(1).strip()
        self.partial = ""
        if not text.endswith("\n"):                    # last line still being written
            text, _, self.partial = text.rpartition("\n")
        return self.parse(text)[-HISTORY_LINES:]

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:                         # truncated / replaced
            self.offset, self.partial = 0, ""
        if size - self.offset < 2:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            raw = fh.read(size - self.offset)
        self.offset += len(raw) - len(raw) % 2
        text = self.partial + _decode(raw)
        text, nl, self.partial = text.rpartition("\n")
        if not nl:                                     # no complete line yet
            self.partial, text = text, ""
        return self.parse(text)


class IntelFeed:
    def __init__(self):
        self.dir = chatlog_dir()
        self.tails = {}                                # (channel, char id) -> Tail

    def scan(self):
        """Look for the newest recently-active intel log per channel/character. Returns status text and
        any history messages from newly attached logs."""
        if not self.dir:
            return "Chat log folder not found", []
        newest = {}
        now = time.time()
        try:
            entries = list(os.scandir(self.dir))
        except OSError:
            return "Can't read chat log folder", []
        for e in entries:
            if NAME_FILTER not in e.name.lower():
                continue
            m = FILE_RE.match(e.name)
            if not m:
                continue
            try:
                age = now - e.stat().st_mtime
            except OSError:
                continue
            key = (m["chan"], m["char"] or "")
            if age <= MAX_AGE and (key not in newest or (m["date"], m["time"]) > newest[key][0]):
                newest[key] = ((m["date"], m["time"]), e.path)
        history = []
        for key, (_, path) in newest.items():
            t = self.tails.get(key)
            if t is None or t.path != path:            # new channel, or a newer session file replaced the old one
                t = Tail(path, key[0], "")
                history += t.attach()
                self.tails[key] = t
        for key in list(self.tails):
            if key not in newest:
                del self.tails[key]
        names = sorted({f"{t.channel} ({t.listener})" if t.listener else t.channel for t in self.tails.values()})
        status = ("Attached: " + ", ".join(names)) if names else "No active intel chat logs found"
        return status, sorted(history, key=lambda m: m["stamp"])

    def poll(self):
        msgs = []
        for t in self.tails.values():
            msgs += t.poll()
        return sorted(msgs, key=lambda m: m["stamp"])


LOCATION_RE = re.compile(r"Channel changed to Local : (.+?)\s*$", re.M)


def local_locations(directory):
    """{listener (lower case): system name} from EVE's own Local chat logs: the last
    'Channel changed to Local : <system>' line of each character's newest recent Local log."""
    out = {}
    if not directory:
        return out
    newest = {}
    now = time.time()
    try:
        for e in os.scandir(directory):
            if not e.name.startswith("Local_"):
                continue
            m = FILE_RE.match(e.name)
            if not m or m["chan"] != "Local":
                continue
            if now - e.stat().st_mtime > 24 * 3600:
                continue
            key = m["char"] or ""
            if key not in newest or (m["date"], m["time"]) > newest[key][0]:
                newest[key] = ((m["date"], m["time"]), e.path)
    except OSError:
        return out
    for _, path in newest.values():
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                head = _decode(fh.read(2000))
                fh.seek(max(0, size - 100_000 - (size % 2)))
                tail = _decode(fh.read())
        except OSError:
            continue
        who = re.search(r"Listener:\s*(.+)", head)
        locs = LOCATION_RE.findall(tail) or LOCATION_RE.findall(head)
        if who and locs:
            out[who.group(1).strip().lower()] = locs[-1].strip()
    return out
