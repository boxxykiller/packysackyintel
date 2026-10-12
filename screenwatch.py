"""The Packy Sacky Intel Tool - finds the EVE Local chat on screen (pure OCR, no game attachment),
tracks who is in it, and alerts when someone neutral / hostile enters."""
import colorsys
import ctypes
import datetime
import difflib
import math
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageTk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import winsound

import intel
import locator
import minimap
import ocr
import theme
import universe
import updater
import wormholes
from version import VERSION

ALERT_STANDINGS = {"neutral", "hostile", "unknown"}      # corp (green), fleet (purple), allies (blue) are ignored
STANDING_LABEL = {"neutral": "neutral", "hostile": "HOSTILE", "unknown": "unknown",
                  "corp": "corp", "fleet": "fleet", "ally": "ally"}
RELOCATE_EVERY = 5.0          # seconds between full-screen searches while watching
REPORT_TTL = 600             # seconds an intel report stays on the map
GRACE_AFTER_JUMP = 6.0        # seconds to silently re-baseline after the system changes


def virtual_screen():
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def monitors():
    """Return [(x, y, w, h)] for every attached monitor, in virtual-screen pixels."""
    from ctypes import wintypes
    out = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HANDLE, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, rect, lparam):
        r = rect.contents
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    return out or [virtual_screen()]


def _init_gdi():
    from ctypes import wintypes
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    h = ctypes.c_void_p
    u.GetDC.argtypes, u.GetDC.restype = [h], h
    u.ReleaseDC.argtypes = [h, h]
    g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [h], h
    g.CreateCompatibleBitmap.argtypes, g.CreateCompatibleBitmap.restype = [h, ctypes.c_int, ctypes.c_int], h
    g.SelectObject.argtypes, g.SelectObject.restype = [h, h], h
    g.BitBlt.argtypes = [h, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, h, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
    g.GetDIBits.argtypes = [h, h, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    g.DeleteObject.argtypes = [h]
    g.DeleteDC.argtypes = [h]
    return u, g


_GDI = _init_gdi()


def grab_region(box):
    """Screen pixels of box (l, t, r, b) in virtual-screen coordinates. Copies only that rectangle with GDI;
    ImageGrab captures every monitor and then crops (~100 ms per call)."""
    l, t, r, b = (int(v) for v in box)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise ValueError(f"empty capture region {box}")
    u, g = _GDI
    sdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    old = g.SelectObject(mdc, bmp)
    try:
        g.BitBlt(mdc, 0, 0, w, h, sdc, l, t, 0x00CC0020)                    # SRCCOPY
        g.SelectObject(mdc, old)                                            # GetDIBits needs it deselected
        hdr = (ctypes.c_uint32 * 10)()                                      # BITMAPINFOHEADER, top-down 32 bpp
        hdr[0], hdr[1], hdr[2] = 40, w, ctypes.c_uint32(-h).value
        hdr[3] = 1 | (32 << 16)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not g.GetDIBits(sdc, bmp, 0, h, buf, hdr, 0):
            raise OSError("GetDIBits failed")
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, sdc)


def local_tab_visible(tab):
    """Cheap check that the 'Local' tab is still where we left it (the window wasn't closed/covered/moved)."""
    tx, ty = tab
    img = grab_region((int(tx) - 6, int(ty) - 6, int(tx) + 90, int(ty) + 24))
    return any(w[0].lower().startswith("local") for ln in ocr.recognize(img, 4) for w in ln)


def eve_clients():
    """[(account, (l, t, r, b))] for every open EVE client window, topmost first. Only the window's
    title bar text ('EVE - <account>') and size are read from Windows; nothing is read from the game."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            m = re.match(r"^EVE\s+-\s+(.+)$", buf.value)
            if m:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                out.append((m.group(1).strip(), (r.left, r.top, r.right, r.bottom)))
        return 1

    user32.EnumWindows(proc(cb), 0)
    return out


LOG = os.path.join(os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                                   else os.path.abspath(__file__)), "packy_log.txt")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def clean_name(text):
    text = re.sub(r"^[^A-Za-z0-9]+", "", text)                # standing-icon junk at the start
    text = re.sub(r"[^A-Za-z0-9 '.\-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# Characters the OCR commonly swaps for each other; names that differ only by these are the same name.
_CONFUSABLE = str.maketrans({"i": "l", "1": "l", "!": "l", "|": "l", "0": "o", "y": "g", "q": "g", "u": "g", "5": "s",
                             "2": "z", "e": "c"})


def name_key(name):
    k = name.lower().replace("rn", "m").replace("vv", "w")
    return re.sub(r"[^a-z0-9]", "", k).translate(_CONFUSABLE)


def same_name(a, b):
    """True if two OCR readings are very likely the same player (confusable letters, or one is cut short)."""
    return name_key(a) == name_key(b)


def is_cut_short(clipped, name, other):
    """A name running into the right edge of the list is cut off by the game: it may be the start of `other`."""
    a, b = name_key(name), name_key(other)
    return name in clipped and len(a) >= 6 and a != b and (b.startswith(a) or a.startswith(b))


def classify_standing(img, x0, x1, yc):
    """Look at the standing icon (columns x0..x1) on a row. Returns corp / fleet / ally / hostile / neutral / unknown."""
    return icon_standing(img, x0, x1, yc)[0]


def icon_standing(img, x0, x1, yc):
    """(standing, debug text). Classified by hue, so dark icons count too: EVE's icons are dim (green ~29,104,22,
    blue ~8,29,96) and a purple fleet icon has two strong channels, so a fixed max-min gap misses it."""
    xa, xb = max(0, int(x0) - 1), min(img.width, int(x1) + 2)
    y0, y1 = max(0, int(yc) - 4), min(img.height, int(yc) + 5)
    sat = []
    for x in range(xa, xb):
        for y in range(y0, y1):
            p = img.getpixel((x, y))
            hi, lo = max(p), min(p)
            if hi >= 45 and hi - lo >= 28 and (hi - lo) / hi >= 0.4:      # coloured, not grey / white text
                sat.append(p)
    if len(sat) < 6:
        return "neutral", f"icon px={len(sat)}"              # no icon = neutral
    r = sum(p[0] for p in sat) / len(sat)
    g = sum(p[1] for p in sat) / len(sat)
    b = sum(p[2] for p in sat) / len(sat)
    hue = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
    info = f"icon px={len(sat)} rgb=({r:.0f},{g:.0f},{b:.0f}) hue={hue:.0f}"
    if 80 <= hue < 170:
        return "corp", info                                  # green
    if 170 <= hue < 255:
        return "ally", info                                  # blue (+5 / +10 / alliance)
    if 255 <= hue < 335:
        return "fleet", info                                 # purple / magenta
    if hue >= 335 or hue < 50:
        return "hostile", info                               # red / orange
    return "unknown", info


def icon_columns(img):
    """Horizontal extent (l, r) of the coloured standing icons in the left edge of the list, or None."""
    strip = img.crop((0, 0, min(img.width, 44), img.height)).convert("HSV")
    _, sat, val = strip.split()
    mask = ImageChops.multiply(sat.point(lambda p: 255 if p > 110 else 0), val.point(lambda p: 255 if p > 55 else 0))   # icons are dim
    per_col = mask.resize((mask.width, 1), Image.BOX)                 # mean 0..255 per column
    need = 6 * 255 / max(1, mask.height)                              # at least ~6 coloured pixels in the column
    hits = [x for x, v in enumerate(per_col.get_flattened_data() if hasattr(per_col, "get_flattened_data")
                                    else per_col.getdata()) if v >= need]
    return (min(hits), max(hits) + 1) if hits else None


# Three different pre-processing passes; a name is only trusted when at least two of them read it the same way.
OCR_PASSES = ((6, Image.BILINEAR, True), (3, Image.BILINEAR, False), (2, Image.NEAREST, False))  # scale, resample, sharpen


def _ocr_rows(img, left, scale, resample, sharpen):
    """OCR the name column only (icons cropped away). Returns [(name, y_centre, top, bottom)] in list pixels."""
    crop = img.crop((left, 0, img.width, img.height))
    g = ImageOps.autocontrast(crop.convert("L"))
    g = g.resize((int(g.width * scale), int(g.height * scale)), resample)
    if sharpen:
        g = g.filter(ImageFilter.SHARPEN)
    g = g.convert("RGB")
    rows = []
    for words in ocr.recognize(g, 1):
        top = min(w[2] for w in words) / scale
        bottom = max(w[2] + w[4] for w in words) / scale
        name = clean_name(" ".join(w[0] for w in words))
        if len(name) >= 3 and not name.isdigit():
            right = max(w[1] + w[3] for w in words) / scale + left
            rows.append((name, (top + bottom) / 2, top, bottom, right >= img.width - 5))
    return rows


ICON_INFO = {}       # name -> icon colour details from the last read, for the log


def read_names(img, known=()):  # -> (names, unsure rows, clipped names)
    """Read the member list. Returns ({name: standing}, [rows the two passes disagreed on]).
    Rows cut off by the region edge are skipped. Also returns names that run into the right edge."""
    cols = icon_columns(img)
    icon_l, icon_r = cols if cols else (6, 16)
    left = icon_r + 3
    passes = [_ocr_rows(img, left, sc, rs, sh) for sc, rs, sh in OCR_PASSES]
    # rows are matched across passes by vertical position; take the reading at least two passes agree on
    anchor = max(passes, key=len)
    out, unsure, clipped = {}, [], set()
    for name, yc, top, bottom, cut in anchor:
        if top < 2 or bottom > img.height - 2:
            continue
        readings = []
        for rows in passes:
            near = [r for r in rows if abs(r[1] - yc) <= 8]
            if near:
                readings.append(min(near, key=lambda r: abs(r[1] - yc))[0])
        groups = []
        for r in readings:
            for g in groups:
                if same_name(g[0], r):
                    g.append(r)
                    break
            else:
                groups.append([r])
        best = max(groups, key=len)
        if len(best) < 2:
            unsure.append(name)
            continue
        pick = next((n for n in best if n in known), max(set(best), key=best.count))
        if pick not in out:
            out[pick], ICON_INFO[pick] = icon_standing(img, icon_l, icon_r, yc)
            if cut:
                clipped.add(pick)
    return out, unsure, clipped


def scan_screens():
    """Find every visible Local chat on every monitor. Returns [{'system','tab','list','monitor'}] in screen coords."""
    results = []
    clients = eve_clients()
    for i, (mx, my, mw, mh) in enumerate(monitors(), 1):
        img = grab_region((mx, my, mx + mw, my + mh))
        for w in locator.find_local_windows(img):
            tx, ty, tw, th = w["tab"]
            l, t, r, b = w["list"]
            px, py = mx + tx, my + ty
            account = next((a for a, (cl, ct, cr, cb_) in clients if cl <= px < cr and ct <= py < cb_), "")
            results.append({"system": w["system"], "monitor": i, "account": account,
                            "tab": (mx + tx, my + ty), "list": (mx + int(l), my + int(t), mx + int(r), my + int(b))})
    return results


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"The Packy Sacky Intel Tool  v{VERSION}")
        self.resizable(True, True)
        self.geometry("1180x840")
        self.minsize(900, 640)
        theme.apply(self)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self.attributes("-topmost", True)

        self.windows = []          # Local windows found by the last scan
        self.current = None        # the one being watched
        self.running = False
        self.job = None
        self.edges = []
        self.thumb = None
        self.scan_thread = None
        self.check_thread = None   # background capture + OCR of the member list
        self.list_vote = None      # a different member-list zone seen once; moved there if the next scan agrees
        self.auto_search = True    # keep searching, and start watching as soon as Local is found
        self.scan_result = None
        self.scan_for_watch = False
        self.last_relocate = 0.0
        self.paused = False        # locked window disappeared: waiting for it to come back
        self.misses = 0
        self.tab_misses = 0
        self.lock_account = ""
        self.grace_until = 0.0
        self.joined = {}           # name -> (time, standing) for people who entered after start-up
        self.known = None          # {name: standing} - everyone seen at start-up or already alerted
        self.pending = {}          # name key -> consecutive scans a candidate new name has been seen
        self.aliases = set()       # OCR misreadings of known names

        self.interval = tk.DoubleVar(value=1.0)
        self.beep = tk.BooleanVar(value=True)
        self.voice = tk.BooleanVar(value=True)
        self.volume = tk.IntVar(value=100)
        self.message = tk.StringVar(value="RUN FOR YOUR LIFE")
        self.popup = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Press 'Find Local windows' with EVE visible on screen.")
        self.diff_text = tk.StringVar(value="")
        self.names_text = tk.StringVar(value="")
        self.choice = tk.StringVar()
        self.lock = tk.BooleanVar(value=True)

        # -- header: logo, name, status pill, gradient accent
        hdr = ttk.Frame(self, style="Header.TFrame", padding=(16, 12, 16, 10))
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        hdr.columnconfigure(1, weight=1)
        ttk.Label(hdr, image=theme.logo(round(44 * self.winfo_fpixels("1i") / 96))).grid(
            row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Label(hdr, text="The Packy Sacky Intel Tool", style="Brand.TLabel").grid(row=0, column=1, sticky="sw")
        ttk.Label(hdr, text=f"COTTON CANDY GENOCIDE  ·  INTEL SERVICES  ·  v{VERSION}", style="Tagline.TLabel").grid(
            row=1, column=1, sticky="nw")
        self.update_btn = ttk.Button(hdr, text="Check for updates", command=self.check_updates,
                                     style="Header.TButton")
        self.update_btn.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 12))
        self.pill = theme.Pill(hdr)
        self.pill.grid(row=0, column=3, rowspan=2, sticky="e")
        self.set_state("IDLE", "idle")
        theme.GradientBar(self, height=2).grid(row=1, column=0, columnspan=2, sticky="ew")

        f = ttk.Frame(self, padding=(12, 12, 6, 12))
        f.grid(row=2, column=0, sticky="nsew")
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        # -- which window + start / stop
        wc, wf = theme.card(f, "Local window")
        wc.grid(row=0, column=0, sticky="ew")
        wf.columnconfigure(0, weight=1, uniform="btn")
        wf.columnconfigure(1, weight=1, uniform="btn")
        self.combo = ttk.Combobox(wf, textvariable=self.choice, state="readonly", width=40)
        self.combo.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.select(self.combo.current()))
        ttk.Checkbutton(wf, text="Lock to this account's window (pause if it disappears)",
                        variable=self.lock, style="Card.TCheckbutton").grid(row=1, column=0, columnspan=2,
                                                                            sticky="w", pady=(8, 0))
        ttk.Button(wf, text="Find Local windows", command=lambda: self.find_windows(True)).grid(
            row=2, column=0, sticky="ew", padx=(0, 4), pady=(12, 0))
        self.toggle = ttk.Button(wf, text="Start watching", command=self.toggle_watch, state="disabled",
                                 style="Primary.TButton")
        self.toggle.grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=(12, 0))

        # -- status + live preview
        sc, sf = theme.card(f, "Status")
        sc.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        sf.columnconfigure(0, weight=1)
        ttk.Label(sf, textvariable=self.status, wraplength=360, justify="left", style="Card.TLabel").grid(
            row=0, column=0, sticky="w")
        self.names_label = ttk.Label(sf, textvariable=self.names_text, wraplength=360, justify="left",
                                     style="Card.Alert.TLabel")
        self.names_label.grid(row=1, column=0, sticky="w")
        pf = tk.Frame(sf, width=360, height=96, bg=theme.WELL, highlightthickness=1,
                      highlightbackground=theme.BORDER)
        pf.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        pf.pack_propagate(False)
        self.preview = tk.Label(pf, text="live preview of the member list", bg=theme.WELL, fg=theme.DIM,
                                font=(theme.FONT, 9))
        self.preview.pack(expand=True)
        ttk.Label(sf, textvariable=self.diff_text, style="Card.Muted.TLabel").grid(row=3, column=0, sticky="w",
                                                                                   pady=(6, 0))

        # -- alert settings
        ac, af = theme.card(f, "Alerts", "neutral / hostile only")
        ac.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        af.columnconfigure(1, weight=1)
        checks = ttk.Frame(af, style="CardBody.TFrame")
        checks.grid(row=0, column=0, columnspan=3, sticky="w")
        for text, var in (("Voice", self.voice), ("Siren", self.beep), ("Popup", self.popup)):
            ttk.Checkbutton(checks, text=text, variable=var, style="Card.TCheckbutton").pack(side="left",
                                                                                            padx=(0, 18))
        ttk.Label(af, text="Message", style="Card.Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(af, textvariable=self.message).grid(row=1, column=1, columnspan=2, sticky="ew",
                                                      padx=(10, 0), pady=(10, 0))
        ttk.Label(af, text="Volume", style="Card.Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Scale(af, from_=0, to=100, variable=self.volume, orient="horizontal",
                  command=lambda v: self.volume.set(int(float(v)))).grid(row=2, column=1, sticky="ew",
                                                                         padx=10, pady=(10, 0))
        ttk.Button(af, text="Test voice", command=lambda: self.speak(self.message.get())).grid(
            row=2, column=2, sticky="e", pady=(10, 0))
        ttk.Label(af, text="Check every", style="Card.Muted.TLabel").grid(row=3, column=0, sticky="w",
                                                                          pady=(10, 0))
        every = ttk.Frame(af, style="CardBody.TFrame")
        every.grid(row=3, column=1, columnspan=2, sticky="w", padx=(10, 0), pady=(10, 0))
        ttk.Spinbox(every, from_=0.2, to=60, increment=0.5, width=6, textvariable=self.interval).pack(side="left")
        ttk.Label(every, text="seconds", style="Card.Muted.TLabel").pack(side="left", padx=(8, 0))

        # -- session list
        lc, lf = theme.card(f, "Local", "this session")
        lc.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(lf, height=6, width=50, activestyle="none")
        theme.style_text(self.listbox)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns", padx=(4, 0))

        # -- intel feed (right-hand side): tails EVE's own chat log files for channels named *intel*
        self.intel = intel.IntelFeed()
        self.intel_status = tk.StringVar(value="Looking for intel chat logs…")
        self.intel_scroll = tk.BooleanVar(value=True)
        rc = ttk.PanedWindow(self, orient="vertical")      # drag the divider to resize map vs feed
        rc.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=12)
        self.build_map(rc)
        ic, inf = theme.card(rc, "Intel feed", "times are EVE time")
        rc.add(ic, weight=1)
        inf.rowconfigure(1, weight=1)
        inf.columnconfigure(0, weight=1)
        ttk.Label(inf, textvariable=self.intel_status, wraplength=560, style="Card.Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.intel_text = tk.Text(inf, width=62, height=9, wrap="word", state="disabled")
        theme.style_text(self.intel_text, mono=True)
        isb = ttk.Scrollbar(inf, orient="vertical", command=self.intel_text.yview)
        self.intel_text.config(yscrollcommand=isb.set)
        self.intel_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        isb.grid(row=1, column=1, sticky="ns", padx=(4, 0), pady=(6, 0))
        ctl = ttk.Frame(inf, style="CardBody.TFrame")
        ctl.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(ctl, text="Auto-scroll", variable=self.intel_scroll, style="Card.TCheckbutton").pack(
            side="left")
        ttk.Button(ctl, text="Clear", command=self.intel_clear).pack(side="right")
        t = self.intel_text
        t.tag_config("time", foreground=theme.DIM)
        t.tag_config("chan", foreground=theme.WARN)
        t.tag_config("hist", foreground=theme.MUTED)
        t.tag_config("hit", background=theme.DANGER_BG, foreground="#FFFFFF")
        t.tag_config("note", foreground=theme.GOOD, font=(theme.MONO, 9, "italic"))
        t.tag_config("lsys", foreground=theme.CYAN, underline=True)        # system -> zKillboard
        t.tag_config("lplr", foreground=theme.FLOSS, underline=True)       # pilot  -> zKillboard
        t.tag_config("sender", foreground=theme.PRIMARY, font=(theme.MONO, 10, "bold"))
        t.tag_bind("link", "<Enter>", lambda e: t.config(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.config(cursor=""))
        t.tag_bind("link", "<Button-1>", self.open_link)
        self.link_urls = {}
        self.link_count = 0
        self.intel_ticks = 0
        self.after(500, self.intel_tick)

        self.place_away_from_game()
        self.update_state = None
        if "--updated" in sys.argv:
            self.status.set(f"Updated to v{VERSION}.")
        if updater.is_frozen() and "--updated" not in sys.argv and "--no-update" not in sys.argv:
            self.after(1500, self.start_update)             # check GitHub for a newer release
        self.after(300, self.find_windows)          # look for Local windows on launch

    def place_away_from_game(self):
        """Put this window on the monitor where it covers the least of the EVE client(s)."""
        try:
            clients = [r for _, r in eve_clients()]
            w, h = 1180, 840
            best = None
            for mx, my, mw, mh in monitors():
                ww, hh = min(w, mw - 40), min(h, mh - 80)
                rect = (mx + 20, my + 20, mx + 20 + ww, my + 20 + hh)
                overlap = sum(max(0, min(rect[2], c[2]) - max(rect[0], c[0])) * max(0, min(rect[3], c[3]) - max(rect[1], c[1]))
                              for c in clients)
                if best is None or overlap < best[0]:
                    best = (overlap, rect, ww, hh)
            if best:
                _, rect, ww, hh = best
                self.geometry(f"{ww}x{hh}{rect[0]:+d}{rect[1]:+d}")
        except Exception as ex:
            log(f"PLACEMENT ERROR {ex!r}")

    def set_state(self, text, kind):
        """Header status pill: idle / search / watch / paused / alert."""
        self.pill.set(text, kind)
        self.state_kind = kind

    def set_names(self, text, style="Card.Alert.TLabel"):
        self.names_label.configure(style=style)
        self.names_text.set(text)

    # ---- auto update -----------------------------------------------------------------------
    def check_updates(self):
        """'Check for updates' button: same as the launch check, but says what it found."""
        if self.update_state and self.update_state["phase"] in ("check", "download", "ready"):
            return
        self.start_update(manual=True)

    def start_update(self, manual=False):
        """Background: look for a newer GitHub release; if there is one, download it and restart into it.
        When running from source it only reports a newer version (there is no exe to swap)."""
        self.update_state = {"phase": "check", "progress": "", "error": None, "path": None, "info": None,
                             "manual": manual}
        st = self.update_state
        if manual:
            self.update_btn.config(text="Checking…", state="disabled")
        frozen = updater.is_frozen()

        def work():
            try:
                info = updater.check_latest()
                if not info:
                    st["phase"] = "none"
                    return
                st["info"] = info
                if not frozen:
                    st["phase"] = "source"
                    return
                st["phase"] = "download"
                st["path"] = updater.download(
                    info, lambda d, t: st.__setitem__("progress", f"{d * 100 // t}%"))
                st["phase"] = "ready"
            except Exception as ex:                          # offline, private repo, rate limit, bad download...
                st["error"], st["phase"] = repr(ex), "error"

        threading.Thread(target=work, daemon=True).start()
        self.after(300, self.poll_update)

    def poll_update(self):
        st = self.update_state
        if st["phase"] in ("check", "download"):
            if st["phase"] == "download":
                self.status.set(f"Downloading update v{st['info']['version']}… {st['progress']}")
            self.after(300, self.poll_update)
            return
        if st["phase"] != "ready":
            self.update_btn.config(text="Check for updates", state="normal")
        if st["phase"] == "error":
            log(f"UPDATE CHECK FAILED {st['error']}")
            if st["manual"]:
                self.status.set(f"Update check failed: {st['error']}")
        elif st["phase"] == "none":
            if st["manual"]:
                self.status.set(f"You're on the latest version (v{VERSION}).")
        elif st["phase"] == "source":
            self.status.set(f"v{st['info']['version']} is available (running from source - pull the repo "
                            f"or use the exe to update).")
        elif st["phase"] == "ready":
            log(f"UPDATING v{VERSION} -> v{st['info']['version']}")
            self.status.set(f"Installing v{st['info']['version']} and restarting…")
            self.update()
            try:
                updater.apply_and_restart(st["path"])
            except Exception as ex:
                log(f"UPDATE APPLY FAILED {ex!r}")
                self.status.set(f"Update downloaded but could not be installed: {ex}")
                self.update_btn.config(text="Check for updates", state="normal")
                return
            self.after(200, self.destroy)

    # ---- finding Local windows -------------------------------------------------------------
    def start_scan(self, for_watch):
        if self.scan_thread and self.scan_thread.is_alive():
            return
        self.scan_result = None
        self.scan_for_watch = for_watch

        def work():
            try:
                self.scan_result = scan_screens()
            except Exception as ex:
                self.scan_result = ex

        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()
        self.after(100, self.poll_scan)

    def poll_scan(self):
        if self.scan_thread.is_alive():
            self.after(100, self.poll_scan)
            return
        res = self.scan_result
        if self.scan_for_watch:
            self.relocated(res)
        else:
            self.found(res)

    def find_windows(self, manual=False):
        if manual:
            self.auto_search = True
        if self.running or not self.auto_search:
            return
        self.status.set("Searching all screens for Local chat windows…")
        self.set_state("SEARCHING", "search")
        self.start_scan(False)

    @staticmethod
    def label(w, n):
        return f"{n}. {w.get('account') or 'unknown account'}  -  {w['system'] or 'system ?'}"

    def found(self, res):
        if isinstance(res, Exception):
            self.status.set(f"Search error: {res}")
            self.set_state("ERROR", "alert")
            return
        self.windows = res
        self.combo["values"] = [self.label(w, i) for i, w in enumerate(res, 1)]
        if not res:
            self.current = None
            self.combo.set("")
            self.toggle.config(state="disabled")
            self.hide_outline()
            self.status.set("Searching for Local chat… make sure the Local tab and its member list are visible.")
            self.set_state("SEARCHING", "search")
            self.after(3000, self.find_windows)           # keep looking until it shows up
            return
        self.combo.current(0)
        self.select(0)
        n = len(res)
        log(f"FOUND {n} Local window(s): {[(w['account'], w['system']) for w in res]}")
        self.start()
        if n > 1:
            self.status.set(f"Found {n} Local windows - watching #1. Press Stop to pick a different one.")

    def select(self, idx):
        if not (0 <= idx < len(self.windows)):
            return
        self.current = dict(self.windows[idx])
        self.lock_account = self.current.get("account", "")
        self.toggle.config(state="normal")
        if self.running:                                  # switched window while watching: start over on it
            self.start()
            return
        self.show_outline(theme.WARN)
        self.show_preview(self.grab())

    # ---- mini map ----------------------------------------------------------------------------
    def build_map(self, parent):
        try:
            self.uni = universe.Universe()
        except Exception as ex:
            self.uni = None
            log(f"MAP DATA ERROR {ex!r}")
        self.cur_sid = None
        self.reports = {}          # system id -> {"stamp", "sender", "text"} (latest report per system)
        self.maps = []
        self.wormholes = []        # active Thera / Turnur connections (EVE-Scout)
        self.after(2000, self.refresh_wormholes)
        mc, mf = theme.card(parent, "Mini map")
        parent.add(mc, weight=3)
        mf.columnconfigure(0, weight=1)
        mf.rowconfigure(0, weight=1)
        view = minimap.MapView(mf, self)
        view.grid(row=0, column=0, sticky="nsew")
        self.maps.append(view)

    def pop_out_map(self):
        """Open the map in its own window (resize / move it anywhere, e.g. on another monitor)."""
        win = tk.Toplevel(self)
        theme.window(win, "The Packy Sacky Intel Tool - map")
        win.geometry("760x640")
        try:                                              # fill the screen that covers the least of the game / app
            app = (self.winfo_rootx(), self.winfo_rooty(),
                   self.winfo_rootx() + self.winfo_width(), self.winfo_rooty() + self.winfo_height())
            clients = [r for _, r in eve_clients()]

            def covered(m, rects):
                mx, my, mw, mh = m
                return sum(max(0, min(mx + mw, r[2]) - max(mx, r[0])) * max(0, min(my + mh, r[3]) - max(my, r[1]))
                           for r in rects)

            mx, my, mw, mh = min(monitors(), key=lambda m: (covered(m, clients), covered(m, [app])))
            win.geometry(f"{max(400, mw - 80)}x{max(300, mh - 120)}{mx + 40:+d}{my + 40:+d}")
            win.after(50, lambda: win.state("zoomed"))
        except Exception as ex:
            log(f"MAP PLACEMENT ERROR {ex!r}")
        theme.GradientBar(win, height=2).pack(fill="x")
        box, body = theme.card(win, "Mini map")
        box.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        view = minimap.MapView(body, self, popout=True)
        view.grid(row=0, column=0, sticky="nsew")
        view.view.set(self.maps[0].view.get())
        view.jumps.set(self.maps[0].jumps.get())
        self.maps.append(view)

        def closed():
            self.maps.remove(view)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", closed)
        self.after(100, view.draw)

    def refresh_wormholes(self):
        """Every 5 minutes, fetch the active Thera / Turnur wormholes in the background and redraw the map."""
        def work():
            try:
                self._wh_result = wormholes.fetch()
            except Exception as ex:                          # offline / API down: keep the last list
                self._wh_result = None
                log(f"WORMHOLE FETCH FAILED {ex!r}")

        self._wh_result = "pending"
        threading.Thread(target=work, daemon=True).start()
        self.after(500, self.poll_wormholes)

    def poll_wormholes(self):
        if self._wh_result == "pending":
            self.after(500, self.poll_wormholes)
            return
        if self._wh_result is not None:
            self.wormholes = self._wh_result
            log(f"WORMHOLES {len(self.wormholes)} active")
            self.draw_map()
        self.after(300_000, self.refresh_wormholes)

    def report_ttl(self):
        return REPORT_TTL

    def live_reports(self):
        """[(system id, report)] for reports newer than REPORT_TTL, newest first."""
        return sorted(((sid, r) for sid, r in self.reports.items() if self.report_age(r["stamp"]) <= REPORT_TTL),
                      key=lambda it: it[1]["stamp"], reverse=True)[:12]

    def update_location(self):
        """Work out which system the watched account is in: EVE's own Local chat log first, then the
        system name read from the Local window."""
        if not self.uni:
            return
        sid = None
        who = (self.current or {}).get("account") or self.lock_account
        locs = intel.local_locations(self.intel.dir)
        if who and who.lower() in locs:
            sid = self.uni.lookup(locs[who.lower()])
        if sid is None and self.current and self.current.get("system"):
            sid = self.uni.lookup(self.current["system"])
        if sid is not None and sid != self.cur_sid:
            self.cur_sid = sid
            log(f"LOCATION {self.uni.sys[sid]['name']}")
            for v in self.maps:                           # new system: re-centre and re-fit the jump radius
                v.zoom, v.pan = 1.0, [0.0, 0.0]
            self.rescan_reports()
            self.draw_map()

    def add_reports(self, msgs):
        """Pick system names out of intel messages and remember the latest report per system."""
        if not self.uni:
            return
        for m in msgs:
            for sid in self.uni.find_mentions(m["text"], self.cur_sid):
                old = self.reports.get(sid)
                if not old or m["stamp"] >= old["stamp"]:
                    self.reports[sid] = {"stamp": m["stamp"], "sender": m["sender"], "text": m["text"]}
        self.draw_map()

    def rescan_reports(self):
        """Current system changed: re-read the mentions (plain-word names depend on where you are)."""
        old = list(self.reports.values())
        self.reports = {}
        self.add_reports(old)

    @staticmethod
    def report_age(stamp):
        try:
            then = datetime.datetime.strptime(stamp, "%Y%m%d%H:%M:%S")
        except ValueError:
            return 10 ** 9
        return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - then).total_seconds()

    def draw_map(self):
        for v in list(self.maps):
            v.draw()

    # ---- intel feed ------------------------------------------------------------------------
    def intel_clear(self):
        self.intel_text.config(state="normal")
        self.intel_text.delete("1.0", "end")
        self.intel_text.config(state="disabled")

    STATUS_WORDS = {"nv", "nvis", "clr", "clear", "blue", "red", "gone", "spike", "status", "pls", "plz", "yes", "no",
                    "ok", "lol", "gate", "gates", "bubble", "cloaky", "camp", "camped", "+1", "+2", "+3", "+4", "+5"}

    def linkify(self, text):
        """Split an intel line into [(text, url or None, tag)]. System names link to that system's zKillboard
        page; capitalised names (intel separates entries with double spaces) link to a zKillboard name search."""
        spans = self.uni.mention_spans(text, self.cur_sid) if self.uni else []
        pieces, pos = [], 0

        def plain(chunk):
            if chunk:
                pieces.append((chunk, None, ""))

        def pilots(chunk):
            """Link name-like segments inside text that has no system names in it."""
            last = 0
            for m in re.finditer(r"\S+(?: \S+)*", chunk):
                words = m.group().split(" ")
                while words and (words[-1].lower() in self.STATUS_WORDS or re.fullmatch(r"[x+]\d+", words[-1].lower())):
                    words.pop()
                core = " ".join(words)
                namelike = (core and len(core) >= 3 and len(words) <= 4 and words[0][0].isupper()
                            and not (len(words) == 1 and core.isupper() and len(core) <= 3))
                plain(chunk[last:m.start()])
                if namelike:
                    pieces.append((core, "https://zkillboard.com/search/" + urllib.parse.quote(core) + "/", "lplr"))
                    plain(m.group()[len(core):])
                else:
                    plain(m.group())
                last = m.end()
            plain(chunk[last:])

        for start, end, sid in spans:
            pilots(text[pos:start])
            pieces.append((text[start:end], f"https://zkillboard.com/system/{sid}/", "lsys"))
            pos = end
        pilots(text[pos:])
        return pieces

    def insert_link(self, t, text, url, kind, extra=()):
        self.link_count += 1
        tag = f"u{self.link_count}"
        self.link_urls[tag] = url
        t.insert("end", text, (kind, "link", tag) + tuple(extra))

    def open_link(self, event):
        t = self.intel_text
        for tag in t.tag_names(f"@{event.x},{event.y}"):
            url = self.link_urls.get(tag)
            if url:
                webbrowser.open(url)
                return

    def intel_add(self, msgs, history=False):
        if not msgs:
            return
        self.add_reports(msgs)
        t = self.intel_text
        system = (self.current or {}).get("system") or ""
        pat = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(system)}(?![A-Za-z0-9-])", re.I) if system else None
        multi = len(self.intel.tails) > 1
        t.config(state="normal")
        for m in msgs:
            tags = ("hist",) if history else ()
            hit = bool(pat and pat.search(m["text"]))
            t.insert("end", m["time"] + " ", ("time",) + tags)
            if multi:
                t.insert("end", f"[{m['channel']}] ", ("chan",) + tags)
            self.insert_link(t, m["sender"], "https://zkillboard.com/search/" + urllib.parse.quote(m["sender"]) + "/",
                             "lplr", ("sender",))
            t.insert("end", ": ", ("sender",))
            for chunk, url, kind in self.linkify(m["text"]):
                if url:
                    self.insert_link(t, chunk, url, kind, tags + (("hit",) if hit else ()))
                else:
                    t.insert("end", chunk, tags + (("hit",) if hit else ()))
            t.insert("end", "\n", tags)
        extra = int(t.index("end-1c").split(".")[0]) - 1500                   # keep the last 1500 lines
        if extra > 0:
            t.delete("1.0", f"{extra + 1}.0")
        t.config(state="disabled")
        if self.intel_scroll.get():
            t.see("end")

    def intel_tick(self):
        try:
            self.intel_ticks += 1
            if self.intel_ticks % 5 == 1:                                     # look for new/rolled-over logs
                self.update_location()
                status, history = self.intel.scan()
                self.intel_status.set(status)
                self.intel_add(history, history=True)
            self.intel_add(self.intel.poll())
            if self.intel_ticks % 3 == 0:
                self.draw_map()                                               # ages out old reports
        except Exception as ex:
            self.intel_status.set(f"Intel feed error: {ex}")
            log(f"INTEL ERROR {ex!r}")
        self.after(1000, self.intel_tick)

    # ---- outline / preview -----------------------------------------------------------------
    def hide_outline(self):
        for w in self.edges:
            w.destroy()
        self.edges = []

    def show_outline(self, color):
        self.hide_outline()
        if not self.current:
            return
        l, t, r, b = self.current["list"]
        k = 3  # border drawn just outside the region so it is never captured
        for x, y, w, h in ((l - k, t - k, r - l + 2 * k, k), (l - k, b, r - l + 2 * k, k),
                           (l - k, t, k, b - t), (r, t, k, b - t)):
            e = tk.Toplevel(self)
            e.overrideredirect(True)
            e.attributes("-topmost", True)
            e.configure(bg=color)
            e.geometry(f"{w}x{h}{x:+d}{y:+d}")
            self.edges.append(e)

    def show_preview(self, img):
        im = img.convert("RGB")
        im.thumbnail((350, 90))
        self.thumb = ImageTk.PhotoImage(im)
        self.preview.config(image=self.thumb, text="")

    def grab(self):
        return grab_region(self.current["list"])

    # ---- watching --------------------------------------------------------------------------
    def toggle_watch(self):
        self.stop() if self.running else self.start()

    def start(self):
        if self.job:                                      # restarting on another window: drop the old loop
            self.after_cancel(self.job)
            self.job = None
        self.known = None
        self.joined = {}
        self.pending = {}
        self.aliases = set()
        self.grace_until = 0.0
        self.paused = False
        self.misses = 0
        self.tab_misses = 0
        self.list_vote = None
        self.last_relocate = time.time()
        self.listbox.delete(0, "end")
        self.names_text.set("")
        self.running = True
        self.toggle.config(text="Stop watching", style="Danger.TButton")
        who = self.current.get("account")
        self.status.set(f"WATCHING {who + ' - ' if who else ''}green box = member list. Alerts: neutral / hostile only.")
        self.set_state("WATCHING", "watch")
        self.show_outline(theme.GOOD)
        self.tick()

    def stop(self):
        self.running = False
        self.auto_search = False                          # user stopped it: don't auto-restart
        if self.job:
            self.after_cancel(self.job)
            self.job = None
        self.toggle.config(text="Start watching", style="Primary.TButton")
        self.paused = False
        if self.current:
            self.status.set("Stopped")
            self.show_outline(theme.WARN)
        self.set_state("STOPPED", "idle")

    def relocated(self, res):
        """Called after a background re-search while watching: follow the locked window if it moved,
        pause if it can't be found, resume when it comes back."""
        if not self.running:
            return
        if isinstance(res, Exception):
            res = []
        self.windows = res
        self.combo["values"] = [self.label(w, i) for i, w in enumerate(res, 1)]
        cands = res
        if self.lock.get() and self.lock_account:
            cands = [w for w in res if w.get("account") == self.lock_account]
        if not cands:
            self.misses += 1
            if self.misses >= 2 and not self.paused:
                self.pause()
            return
        self.misses = 0
        cx, cy = self.current["tab"]
        best = min(cands, key=lambda w: math.hypot(w["tab"][0] - cx, w["tab"][1] - cy))
        self.combo.set(self.label(best, res.index(best) + 1))
        if not self.lock.get():
            self.lock_account = best.get("account", "")
        old_sys, new_sys = self.current["system"], best["system"]
        best = dict(best)
        old_list = self.current["list"]
        diff = lambda a, b: max(abs(p - q) for p, q in zip(a, b))                  # noqa: E731
        same_place = math.hypot(best["tab"][0] - cx, best["tab"][1] - cy) <= 40
        if same_place and not self.paused:
            # the zone is re-detected from scratch each scan and wobbles a little (OCR / chat text): keep it
            # still unless it really changed, and only move it when two scans in a row agree
            if diff(best["list"], old_list) <= 12:
                best["tab"], best["list"] = self.current["tab"], old_list
                self.list_vote = None
            elif self.list_vote and diff(best["list"], self.list_vote) <= 12:
                self.list_vote = None
            else:
                self.list_vote = best["list"]
                best["tab"], best["list"] = self.current["tab"], old_list
        else:
            self.list_vote = None
        moved = best["list"] != old_list
        if moved:
            log(f"LIST ZONE {old_list} -> {best['list']}")
        self.current = best
        if self.paused:
            self.paused = False
            self.tab_misses = 0
            self.grace_until = time.time() + GRACE_AFTER_JUMP      # quietly re-learn after coming back
            log(f"RESUMED {self.lock_account or 'window'}")
            moved = True
        if moved:
            self.show_outline(theme.GOOD)
        if old_sys and new_sys and old_sys != new_sys:
            log(f"SYSTEM CHANGE {old_sys} -> {new_sys}: re-baselining")
            self.known = None
            self.joined = {}
            self.pending = {}
            self.listbox.delete(0, "end")
            self.set_names(f"Entered {new_sys} - learning who is here…", "Card.Muted.TLabel")
            self.grace_until = time.time() + GRACE_AFTER_JUMP
        who = self.current.get("account")
        self.status.set(f"WATCHING {who + ' in ' if who else ''}{new_sys or 'Local'}. Alerts: neutral / hostile only.")
        if self.state_kind != "alert":
            self.set_state("WATCHING", "watch")

    def pause(self):
        self.paused = True
        self.hide_outline()
        self.names_text.set("")
        who = self.lock_account or "the Local"
        tx, ty = self.current["tab"]
        covered = (self.winfo_rootx() <= tx <= self.winfo_rootx() + self.winfo_width()
                   and self.winfo_rooty() <= ty <= self.winfo_rooty() + self.winfo_height())
        if covered:
            self.status.set("PAUSED - this window is covering the Local tab. Move it off the game "
                            "(e.g. to another monitor); it resumes automatically.")
        else:
            self.status.set(f"PAUSED - {who} window not found. Will resume automatically when it reappears.")
        log(f"PAUSED: {self.lock_account or 'window'} disappeared")
        self.set_state("PAUSED", "paused")

    def update_names(self, seen, clipped=frozenset()):
        """seen: {name: standing}. Returns [(name, standing)] of never-seen-before players who have shown up in
        2 scans in a row. Position in the list doesn't matter. A reading that is just a known name with the
        usual OCR confusions (y/g, l/I/1, 0/O ...) or cut short is the same player, not a new one."""
        if self.known is None or time.time() < self.grace_until:      # (re)learning the room
            self.known = dict(seen) if self.known is None else {**self.known, **seen}
            self.pending = {}
            if len(self.known) == len(seen):
                log(f"BASELINE {len(seen)} names: {sorted(seen)}")
            return []
        missing = set(self.known) - set(seen)
        new = []
        now_keys = set()
        for n, st in seen.items():
            if n in self.known or n in self.aliases:
                continue
            canon = next((k for k in self.known if same_name(n, k) or is_cut_short(clipped, n, k)), None)
            if canon is None:                                         # also a close look-alike of someone who just vanished
                best = max(missing, key=lambda k: difflib.SequenceMatcher(None, n.lower(), k.lower()).ratio(),
                           default=None)
                if best and len(n) >= 6 and difflib.SequenceMatcher(None, n.lower(), best.lower()).ratio() >= 0.85:
                    canon = best
            if canon:
                self.aliases.add(n)
                log(f"IGNORED '{n}' as misread of '{canon}'")
                continue
            k = name_key(n)
            now_keys.add(k)
            self.pending[k] = self.pending.get(k, 0) + 1
            if self.pending[k] >= 2:
                self.known[n] = st
                new.append((n, st))
                self.pending.pop(k, None)
        self.pending = {k: c for k, c in self.pending.items() if k in now_keys}
        return new

    def tick(self):
        if not self.running:
            return
        try:
            now = time.time()
            if self.paused:                                   # waiting for the window: just keep looking
                if now - self.last_relocate >= 2.0:
                    self.last_relocate = now
                    self.start_scan(True)
                self.job = self.after(1000, self.tick)
                return
            if now - self.last_relocate >= RELOCATE_EVERY:
                self.last_relocate = now
                self.start_scan(True)
            if self.check_thread and self.check_thread.is_alive():      # previous check still running
                self.job = self.after(100, self.tick)
                return
            # screen capture + OCR (~150 ms) run on a worker so the window stays responsive
            tab, box, known = self.current["tab"], self.current["list"], set(self.known or ())
            res = {}

            def work():
                try:
                    res["visible"] = local_tab_visible(tab)
                    if res["visible"]:
                        res["img"] = grab_region(box)
                        res["read"] = read_names(res["img"], known)
                except Exception as ex:
                    res["error"] = ex

            self.check_thread = threading.Thread(target=work, daemon=True)
            self.check_thread.start()
            self.job = self.after(20, lambda: self.check_done(res, now))
        except Exception as ex:
            self.status.set(f"Error: {ex}")
            log(f"ERROR {ex!r}")
            self.job = self.after(int(max(0.2, self.interval.get()) * 1000), self.tick)

    def check_done(self, res, started):
        """Main thread: apply the result of one background check, then schedule the next one."""
        if self.check_thread.is_alive():
            self.job = self.after(20, lambda: self.check_done(res, started))
            return
        if not self.running:
            return
        if self.paused:
            self.job = self.after(1000, self.tick)
            return
        next_in = max(0.05, max(0.2, self.interval.get()) - (time.time() - started))
        try:
            now = time.time()
            if "error" in res:
                raise res["error"]
            if not res["visible"]:                            # window gone/covered: never read whatever is behind it
                self.tab_misses += 1
                if self.tab_misses >= 3:
                    self.start_scan(True)
                    self.misses = 1                           # next failed search pauses
                    self.hide_outline()
                    self.status.set("Local tab not visible - searching…")
                    self.set_state("SEARCHING", "search")
                    self.last_relocate = now
                    self.job = self.after(1000, self.tick)
                    return
            else:
                if self.tab_misses >= 3:
                    self.show_outline(theme.GOOD)
                    self.status.set("WATCHING")
                    self.set_state("WATCHING", "watch")
                self.tab_misses = 0
            self.show_preview(res["img"])
            seen, unsure, clipped = res["read"]
            new = self.update_names(seen, clipped)
            self.diff_text.set(f"Names visible: {len(seen)}   Known: {len(self.known)}   "
                               f"Unsure rows: {len(unsure)}   (last check {time.strftime('%H:%M:%S')})")
            for n, st in new:
                self.joined[n] = (time.strftime("%H:%M:%S"), st)
                log(f"JOINED '{n}' standing={st} alert={st in ALERT_STANDINGS} {ICON_INFO.get(n, '')}")
            self.refresh_list(seen)
            threats = [(n, st) for n, st in new if st in ALERT_STANDINGS]
            if threats:
                self.set_names("ALERT: " + ", ".join(f"{n} ({STANDING_LABEL[s]})" for n, s in threats)
                                    + f"  at {time.strftime('%H:%M:%S')}")
                self.alert(threats)
            elif new:
                self.set_names("Friendly entered: " + ", ".join(f"{n} ({STANDING_LABEL[s]})" for n, s in new),
                               "Card.Good.TLabel")
        except Exception as ex:
            self.status.set(f"Error: {ex}")
            log(f"ERROR {ex!r}")
        self.job = self.after(int(next_in * 1000), self.tick)

    def refresh_list(self, seen):
        """Local list: newcomers first (red = alerted, green = friendly), then everyone else seen this session."""
        lb = self.listbox
        top = lb.yview()[0]
        lb.delete(0, "end")
        for n, (t, st) in reversed(list(self.joined.items())):
            lb.insert("end", f"NEW {t}  {n}  [{STANDING_LABEL[st]}]")
            lb.itemconfig("end", fg=theme.DANGER_TEXT if st in ALERT_STANDINGS else theme.GOOD)
        for n in sorted(set(self.known or {}) - set(self.joined), key=str.lower):
            st = self.known[n]
            lb.insert("end", f"{n}  [{STANDING_LABEL[st]}]" + ("" if n in seen else "   (not visible)"))
            if n not in seen:
                lb.itemconfig("end", fg=theme.DIM)
        lb.yview_moveto(top)

    # ---- alerting --------------------------------------------------------------------------
    def siren(self):
        # loud two-tone alarm, ~4 seconds (level follows system volume)
        for _ in range(8):
            winsound.Beep(1500, 250)
            winsound.Beep(900, 250)

    def speak(self, text):
        # Windows built-in speech, run in the background so the UI doesn't freeze
        cmd = ("Add-Type -AssemblyName System.Speech; "
               "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               "$s.Volume = [int]$env:BSW_VOL; $s.Speak($env:BSW_TEXT)")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                             env={**os.environ, "BSW_TEXT": text, "BSW_VOL": str(self.volume.get())},
                             creationflags=0x08000000)
        except Exception as ex:
            self.status.set(f"Voice error: {ex}")

    def alert(self, threats):
        self.show_outline(theme.DANGER)
        self.set_state("HOSTILE IN LOCAL", "alert")
        self.after(8000, lambda: self.state_kind == "alert" and self.running and not self.paused
                   and self.set_state("WATCHING", "watch"))
        self.after(1500, lambda: self.running and self.show_outline(theme.GOOD))
        if self.voice.get():
            self.speak(" . ".join([self.message.get()] * 3))
        if self.beep.get():
            threading.Thread(target=self.siren, daemon=True).start()
        if self.popup.get():
            self.deiconify()
            self.lift()
            top = tk.Toplevel(self)
            theme.window(top, "Hostile in local")
            top.attributes("-topmost", True)
            who = ", ".join(f"{n} ({STANDING_LABEL[s]})" for n, s in threats)
            theme.GradientBar(top, height=2).pack(fill="x")
            box, body = theme.card(top, padding=18)
            box.pack(fill="both", expand=True, padx=14, pady=14)
            ttk.Label(body, text="HOSTILE IN LOCAL", style="Card.Alert.TLabel",
                      font=(theme.DISPLAY, 14)).pack(anchor="w")
            ttk.Label(body, text=self.message.get(), style="Card.Strong.TLabel").pack(anchor="w", pady=(6, 0))
            ttk.Label(body, text=who, style="Card.TLabel", wraplength=380, justify="left").pack(anchor="w",
                                                                                              pady=(2, 14))
            ttk.Button(body, text="Got it", command=top.destroy, style="Danger.TButton").pack(anchor="e")


if __name__ == "__main__":
    App().mainloop()
