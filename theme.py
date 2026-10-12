"""Cotton Candy Genocide look for the app: deep-navy surfaces, electric-blue accent, cotton-candy gradient.
Rounded cards / buttons / checkboxes are anti-aliased images drawn at start-up (ttk 'image' elements)."""
import ctypes
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# -- palette (matches cottoncandygenocide.ca)
BG = "#060D18"            # window
SURFACE = "#0B1628"       # cards
RAISED = "#13233D"        # buttons, dropdowns
WELL = "#08111F"          # inputs, text areas, map
BORDER = "#1E2D45"
BORDER_HI = "#2E4568"
TEXT = "#EEF2F7"
MUTED = "#7A8DAB"
DIM = "#4A5B76"
PRIMARY = "#4A9EFF"
PRIMARY_HI = "#6DB2FF"
PRIMARY_LO = "#2F80E4"
FLOSS = "#FF9AD8"
PINK = "#FF5FA8"
VIOLET = "#A166FF"
CYAN = "#57E6FF"
GOOD = "#4ADE80"
WARN = "#F59E0B"
WARN_TEXT = "#FCD34D"
DANGER = "#F43F5E"
DANGER_TEXT = "#FDA4AF"
DANGER_BG = "#3B0F20"
GRADIENT = (FLOSS, PINK, VIOLET, CYAN)

FONT = "Segoe UI"
DISPLAY = "Segoe UI Black"
MONO = "Consolas"

_images = []              # keep PhotoImages alive


def asset(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def _rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    """Blend colour a towards b by t (0..1)."""
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def gradient_at(t, stops=GRADIENT):
    t = min(1.0, max(0.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return mix(stops[i], stops[i + 1], t - i)


def _photo(im):
    p = ImageTk.PhotoImage(im)
    _images.append(p)
    return p


def _rrect(w, h, r, fill, outline=None, width=1, ss=4):
    im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), r * ss, fill=fill, outline=outline,
                                         width=width * ss if outline else 0)
    return im.resize((w, h), Image.LANCZOS)


def _check(size, r, fill, outline, tick, pad):
    ss = 4
    im = Image.new("RGBA", ((size + pad) * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size * ss - 1, size * ss - 1), r * ss, fill=fill, outline=outline, width=ss)
    if tick:
        k = size * ss
        d.line([(k * .25, k * .52), (k * .43, k * .70), (k * .76, k * .32)], fill=tick, width=int(k * .13),
               joint="curve")
    return im.resize(((size + pad), size), Image.LANCZOS)


def dark_titlebar(win):
    """Windows 11: dark title bar in the window colour (no-op elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))       # immersive dark mode
        r, g, b = _rgb(BG)
        col = ctypes.c_int(r | g << 8 | b << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), ctypes.sizeof(col))     # caption colour
        r, g, b = _rgb(BORDER)
        col = ctypes.c_int(r | g << 8 | b << 16)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(col), ctypes.sizeof(col))     # border colour
    except Exception:
        pass


def window(win, title=None):
    """Brand a Toplevel: background, dark title bar, icon."""
    win.configure(bg=BG)
    if title:
        win.title(title)
    try:
        win.iconbitmap(asset("logo.ico"))
    except Exception:
        pass
    win.after(10, lambda: dark_titlebar(win))


def apply(root):
    """Install the theme on the whole app. Call once, before building widgets."""
    s = max(1.0, root.winfo_fpixels("1i") / 96)
    px = lambda v: max(1, round(v * s))                                    # noqa: E731
    root.configure(bg=BG)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        tkfont.nametofont(name).configure(family=FONT, size=10)
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", PRIMARY)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))

    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=TEXT, fieldbackground=WELL, bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, troughcolor=WELL, focuscolor=PRIMARY,
                 selectbackground=PRIMARY, selectforeground="#FFFFFF", insertcolor=TEXT,
                 arrowcolor=MUTED, font=(FONT, 10))
    st.map(".", foreground=[("disabled", DIM)])

    # -- frames / cards
    st.configure("TFrame", background=BG)
    st.configure("Header.TFrame", background=BG)
    rc = px(10)
    st.element_create("Card.bg", "image", _photo(_rrect(2 * rc + 4, 2 * rc + 4, rc, SURFACE, BORDER)),
                      border=rc, sticky="nsew")
    st.layout("Card.TFrame", [("Card.bg", {"sticky": "nsew"})])
    st.configure("Card.TFrame", background=BG)
    st.configure("CardBody.TFrame", background=SURFACE)

    # -- labels
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Muted.TLabel", background=BG, foreground=MUTED)
    st.configure("Brand.TLabel", background=BG, foreground=TEXT, font=(DISPLAY, 15))
    st.configure("Tagline.TLabel", background=BG, foreground=PRIMARY, font=(FONT, 9, "bold"))
    for name, fg, font in (("Card.TLabel", TEXT, (FONT, 10)), ("Card.Muted.TLabel", MUTED, (FONT, 9)),
                           ("Card.Title.TLabel", TEXT, (FONT, 11, "bold")),
                           ("Card.Hint.TLabel", MUTED, (FONT, 9)),
                           ("Card.Strong.TLabel", TEXT, (FONT, 10, "bold")),
                           ("Card.Alert.TLabel", DANGER_TEXT, (FONT, 10, "bold")),
                           ("Card.Good.TLabel", GOOD, (FONT, 10, "bold")),
                           ("Card.Warn.TLabel", WARN_TEXT, (FONT, 10, "bold"))):
        st.configure(name, background=SURFACE, foreground=fg, font=font)

    # -- buttons
    rb = px(8)
    n = 2 * rb + 4

    def button(style, normal, active, pressed, disabled, fg, dfg=DIM, bg=SURFACE):
        el = style.replace(".TButton", "") + ".Button.bg"
        st.element_create(el, "image", _photo(_rrect(n, n, rb, *normal)),
                          ("disabled", _photo(_rrect(n, n, rb, *disabled))),
                          ("pressed", _photo(_rrect(n, n, rb, *pressed))),
                          ("active", _photo(_rrect(n, n, rb, *active))),
                          border=rb, padding=(px(4), px(2)), sticky="nsew")
        st.layout(style, [(el, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        st.configure(style, background=bg, foreground=fg, padding=(px(10), px(4)), anchor="center",
                     font=(FONT, 10, "bold" if style.startswith("Primary") else "normal"))
        st.map(style, foreground=[("disabled", dfg)], background=[("active", bg)])

    button("TButton", (RAISED, BORDER), (mix(RAISED, BORDER_HI, .4), BORDER_HI), (WELL, BORDER_HI),
           (SURFACE, BORDER), TEXT)
    button("Header.TButton", (BG, BORDER), (SURFACE, PRIMARY), (WELL, PRIMARY), (BG, BORDER), MUTED, bg=BG)
    st.map("Header.TButton", foreground=[("disabled", DIM), ("active", TEXT)], background=[("active", BG)])
    button("Primary.TButton", (PRIMARY,), (PRIMARY_HI,), (PRIMARY_LO,), (RAISED, BORDER), "#FFFFFF")
    button("Danger.TButton", (DANGER,), (mix(DANGER, "#FFFFFF", .15),), (mix(DANGER, "#000000", .2),),
           (RAISED, BORDER), "#FFFFFF")

    # -- checkbuttons
    cb = px(16)
    gap = px(8)
    off = _photo(_check(cb, px(4), WELL, BORDER_HI, None, gap))
    off_hi = _photo(_check(cb, px(4), WELL, PRIMARY, None, gap))
    on = _photo(_check(cb, px(4), PRIMARY, PRIMARY, "#FFFFFF", gap))
    on_hi = _photo(_check(cb, px(4), PRIMARY_HI, PRIMARY_HI, "#FFFFFF", gap))
    st.element_create("Brand.Checkbutton.indicator", "image", off, ("selected", "active", on_hi),
                      ("selected", on), ("active", off_hi), sticky="")
    for style, bg in (("TCheckbutton", BG), ("Card.TCheckbutton", SURFACE)):
        st.layout(style, [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Brand.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
        st.configure(style, background=bg, foreground=TEXT, padding=(0, px(2)))
        st.map(style, background=[("active", bg)], foreground=[("disabled", DIM)])

    # -- inputs
    field = dict(fieldbackground=WELL, foreground=TEXT, background=RAISED, bordercolor=BORDER,
                 lightcolor=WELL, darkcolor=WELL, arrowcolor=MUTED, insertcolor=TEXT, padding=(px(6), px(4)))
    focus = dict(bordercolor=[("focus", PRIMARY), ("hover", BORDER_HI)],
                 lightcolor=[("focus", WELL)], darkcolor=[("focus", WELL)])
    for style in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(style, **field)
        st.map(style, **focus, fieldbackground=[("readonly", WELL), ("disabled", SURFACE)],
               foreground=[("readonly", TEXT), ("disabled", DIM)],
               selectbackground=[("readonly", WELL), ("!readonly", PRIMARY)],
               selectforeground=[("readonly", TEXT)],
               background=[("active", BORDER_HI), ("pressed", BORDER_HI)],
               arrowcolor=[("active", TEXT), ("disabled", DIM)])
    st.configure("TSpinbox", arrowsize=px(10))
    st.configure("TCombobox", arrowsize=px(12))

    # -- slider: thin rounded track + round knob
    th, kn = px(6), px(16)
    track_im = Image.new("RGBA", (4 * th, kn), (0, 0, 0, 0))
    track_im.paste(_rrect(4 * th, th, th // 2, BORDER), (0, (kn - th) // 2))
    track = _photo(track_im)
    ss = 4
    knob_im = Image.new("RGBA", (kn * ss, kn * ss), (0, 0, 0, 0))
    ImageDraw.Draw(knob_im).ellipse((ss, ss, kn * ss - ss, kn * ss - ss), fill="#FFFFFF", outline=PRIMARY,
                                    width=3 * ss)
    knob_hi_im = Image.new("RGBA", (kn * ss, kn * ss), (0, 0, 0, 0))
    ImageDraw.Draw(knob_hi_im).ellipse((ss, ss, kn * ss - ss, kn * ss - ss), fill=PRIMARY_HI, outline=PRIMARY,
                                       width=3 * ss)
    knob = _photo(knob_im.resize((kn, kn), Image.LANCZOS))
    knob_hi = _photo(knob_hi_im.resize((kn, kn), Image.LANCZOS))
    st.element_create("Brand.Horizontal.Scale.trough", "image", track, border=(th // 2, 0), sticky="ew")
    st.element_create("Brand.Horizontal.Scale.slider", "image", knob, ("active", knob_hi), ("pressed", knob_hi),
                      sticky="")
    st.layout("Horizontal.TScale", [("Brand.Horizontal.Scale.trough", {"sticky": "ew", "children": [
        ("Brand.Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])
    st.configure("Horizontal.TScale", background=SURFACE)

    # -- slim scrollbars without arrows
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        st.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
            (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nsew"})]})])
        st.configure(f"{orient}.TScrollbar", troughcolor=WELL, background=BORDER, bordercolor=WELL,
                     lightcolor=BORDER, darkcolor=BORDER, gripcount=0, arrowsize=px(9), width=px(9))
        st.map(f"{orient}.TScrollbar", background=[("active", BORDER_HI), ("pressed", PRIMARY)],
               lightcolor=[("active", BORDER_HI), ("pressed", PRIMARY)],
               darkcolor=[("active", BORDER_HI), ("pressed", PRIMARY)])

    # -- paned window
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", sashthickness=px(10), gripcount=0, background=BG, lightcolor=BG, bordercolor=BG,
                 handlesize=0)

    window(root)
    return s


def card(parent, title=None, hint=None, padding=14):
    """A rounded card. Returns (card, body); put the content in body (background SURFACE)."""
    outer = ttk.Frame(parent, style="Card.TFrame", padding=padding)
    outer.columnconfigure(0, weight=1)
    if title:
        head = ttk.Frame(outer, style="CardBody.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(head, text=title, style="Card.Title.TLabel").pack(side="left")
        if hint:
            ttk.Label(head, text=hint, style="Card.Hint.TLabel").pack(side="left", padx=(8, 0), pady=(2, 0))
    body = ttk.Frame(outer, style="CardBody.TFrame")
    body.grid(row=1, column=0, sticky="nsew")
    outer.rowconfigure(1, weight=1)
    outer.head = head if title else None
    return outer, body


def style_text(w, mono=False):
    """Dark look for classic tk Text / Listbox widgets."""
    w.configure(bg=WELL, fg=TEXT, relief="flat", borderwidth=0, highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=BORDER, selectbackground=mix(PRIMARY, WELL, .45),
                selectforeground="#FFFFFF", font=(MONO, 10) if mono else (FONT, 10))
    if isinstance(w, tk.Text):
        w.configure(insertbackground=TEXT, padx=10, pady=8, spacing1=2, spacing3=2)


class GradientBar(tk.Canvas):
    """Thin cotton-candy gradient strip."""

    def __init__(self, parent, height=3):
        super().__init__(parent, height=height, bg=BG, highlightthickness=0, borderwidth=0)
        self.bind("<Configure>", self._draw)

    def _draw(self, e):
        self.delete("all")
        w, h = e.width, e.height
        step = 2
        for x in range(0, w, step):
            self.create_rectangle(x, 0, x + step, h, fill=gradient_at(x / max(1, w - 1)), outline="")


class Pill(tk.Canvas):
    """Rounded status badge: coloured dot + label on a tinted background."""
    KINDS = {"idle": MUTED, "search": WARN, "watch": GOOD, "paused": WARN, "alert": DANGER}

    def __init__(self, parent, bg=BG):
        self.font = tkfont.Font(family=FONT, size=9, weight="bold")
        self.h = self.font.metrics("linespace") + 10
        super().__init__(parent, height=self.h, width=80, bg=bg, highlightthickness=0, borderwidth=0)
        self.parent_bg = bg

    def set(self, text, kind="idle"):
        col = self.KINDS.get(kind, kind)
        self.delete("all")
        h = self.h
        tw = self.font.measure(text)
        w = tw + h + 14
        self.configure(width=w)
        im = _rrect(w, h, h // 2, mix(self.parent_bg, col, .16), mix(self.parent_bg, col, .45))
        self._img = ImageTk.PhotoImage(im)
        self.create_image(0, 0, image=self._img, anchor="nw")
        r = max(3, h // 7)
        cx, cy = h // 2 + 3, h / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="")
        self.create_text(cx + r + 7, cy, text=text, anchor="w", fill=mix(col, "#FFFFFF", .35), font=self.font)


def logo(size):
    """Brand logo as a PhotoImage of the given pixel size."""
    im = Image.open(asset("logo.png")).convert("RGBA").resize((size, size), Image.LANCZOS)
    return _photo(im)
