"""Zoomable / pannable mini map widget. Can be embedded in the main window or popped out on its own."""
import tkinter as tk
from tkinter import ttk

import theme

VIEW_NEARBY = "Nearby systems"
VIEW_REGION = "Whole region"
BG = theme.WELL
HUB_COLORS = {"Thera": theme.VIOLET, "Turnur": theme.CYAN}
SEC_COLORS = ("#4ADE80", "#F59E0B", "#F43F5E")          # high / low / null sec
GATE = "#1C2B44"


class MapView(ttk.Frame):
    def __init__(self, parent, app, popout=False):
        super().__init__(parent, style="CardBody.TFrame")
        self.app = app
        self.popout = popout
        self.view = tk.StringVar(value=VIEW_NEARBY)
        self.jumps = tk.IntVar(value=6)
        self.names = tk.BooleanVar(value=True)
        self.show_wh = tk.BooleanVar(value=True)
        self.fontsize = tk.IntVar(value=10)
        self.info = tk.StringVar(value="Waiting for your current system…")
        self.report = tk.StringVar(value="")
        self.hover = tk.StringVar(value="")
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self.drawn = []                      # [(x, y, system id)] for hover lookups
        self._drag = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self, style="CardBody.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        cb = ttk.Combobox(bar, textvariable=self.view, values=[VIEW_NEARBY, VIEW_REGION], state="readonly", width=15)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refit())
        ttk.Label(bar, text="Jumps", style="Card.Muted.TLabel").pack(side="left", padx=(12, 4))
        ttk.Spinbox(bar, from_=1, to=30, width=3, textvariable=self.jumps, command=self.refit).pack(side="left")
        ttk.Label(bar, text="Text", style="Card.Muted.TLabel").pack(side="left", padx=(12, 4))
        ttk.Spinbox(bar, from_=7, to=22, width=3, textvariable=self.fontsize, command=self.draw).pack(side="left")
        ttk.Checkbutton(bar, text="Names", variable=self.names, command=self.draw,
                        style="Card.TCheckbutton").pack(side="left", padx=(14, 0))
        ttk.Checkbutton(bar, text="Wormholes", variable=self.show_wh, command=self.draw,
                        style="Card.TCheckbutton").pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Fit", width=4, command=self.refit).pack(side="left", padx=(12, 0))
        if not popout:
            ttk.Button(bar, text="Pop out", command=app.pop_out_map).pack(side="right")
        ttk.Label(self, textvariable=self.info, wraplength=600, style="Card.TLabel").grid(row=1, column=0, sticky="w",
                                                                                         pady=(8, 0))
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=1, highlightbackground=theme.BORDER,
                                highlightcolor=theme.BORDER, width=560, height=340)
        self.canvas.grid(row=2, column=0, sticky="nsew", pady=(6, 6))
        ttk.Label(self, textvariable=self.hover, style="Card.Muted.TLabel").grid(row=3, column=0, sticky="w")
        ttk.Label(self, textvariable=self.report, wraplength=600, justify="left", style="Card.TLabel").grid(
            row=4, column=0, sticky="w")

        c = self.canvas
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        c.bind("<Motion>", self.on_move)
        c.bind("<Double-Button-1>", lambda e: self.refit())

    # ---- interaction -----------------------------------------------------------------------
    def refit(self):
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self.draw()

    def on_wheel(self, e):
        f = 1.25 if e.delta > 0 else 0.8
        new = max(0.3, min(40.0, self.zoom * f))
        f = new / self.zoom
        W, H = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.pan[0] = (e.x - W / 2) - (e.x - W / 2 - self.pan[0]) * f
        self.pan[1] = (e.y - H / 2) - (e.y - H / 2 - self.pan[1]) * f
        self.zoom = new
        self.draw()

    def on_press(self, e):
        self._drag = (e.x, e.y, self.pan[0], self.pan[1])

    def on_drag(self, e):
        if self._drag:
            x0, y0, px, py = self._drag
            self.pan = [px + e.x - x0, py + e.y - y0]
            self.draw()

    def on_move(self, e):
        u = self.app.uni
        best = min(self.drawn, key=lambda t: (t[0] - e.x) ** 2 + (t[1] - e.y) ** 2, default=None)
        if not u or not best or (best[0] - e.x) ** 2 + (best[1] - e.y) ** 2 > 144:
            self.hover.set("Mouse wheel = zoom, drag = move, double-click = fit")
            return
        sid = best[2]
        s = u.sys[sid]
        d = u.distances(self.app.cur_sid).get(sid) if self.app.cur_sid else None
        far = "" if d is None else "  -  here" if d == 0 else f"  -  {d} jump{'s' if d != 1 else ''}"
        holes = "".join(f"   |   {w['hub']} wormhole {w['sig_in'] or ''}: {w['hours']}h left, {w['size']}"
                        for w in self.app.wormholes if w["sid"] == sid)
        self.hover.set(f"{s['name']}  ({u.regions.get(s['region'], '?')}, security {s['sec']:.1f}){far}{holes}")

    def wormhole_summary(self, dist):
        """Lines about the active Thera / Turnur holes: counts, and the nearest few by gate jumps."""
        holes = self.app.wormholes
        if not holes or not self.show_wh.get():
            return []
        hubs = {}
        for w in holes:
            hubs[w["hub"]] = hubs.get(w["hub"], 0) + 1
        head = "Wormholes: " + ", ".join(f"{n} {h}" for h, n in sorted(hubs.items())) + " active"
        reach = sorted(((dist[w["sid"]], w) for w in holes if w["sid"] in dist), key=lambda t: t[0])[:3]
        out = [head]
        for d, w in reach:
            out.append(f"   {w['hub']} -> {w['name']} ({w['region']}): {d} jump{'s' if d != 1 else ''}, "
                       f"{w['hours']}h left, {w['size']}")
        return out

    # ---- drawing ---------------------------------------------------------------------------
    def draw(self):
        app, c = self.app, self.canvas
        c.delete("all")
        self.drawn = []
        u, cur = app.uni, app.cur_sid
        W, H = c.winfo_width(), c.winfo_height()
        if W < 40 or H < 40:
            return
        if not u:
            c.create_text(W / 2, H / 2, text="Map data missing", fill=theme.MUTED, font=(theme.FONT, 10))
            return
        if cur is None:
            c.create_text(W / 2, H / 2, text="Waiting for your current system…", fill=theme.MUTED,
                          font=(theme.FONT, 10))
            return
        dist = u.distances(cur)
        region = u.sys[cur]["region"]
        if self.view.get() == VIEW_NEARBY:
            n = max(1, self.jumps.get())
            near = [s for s, d in dist.items() if d <= n and u.sys[s]["x"] is not None]
            cx, cy = u.sys[cur]["x"], u.sys[cur]["y"]

            def span(s):
                return max(abs(u.sys[s]["x"] - cx), abs(u.sys[s]["y"] - cy))

            # other regions sit far away on the universe map: keep only neighbours that are about as
            # close as the systems of this region, so the view stays zoomed in on what matters
            home = [s for s in near if u.sys[s]["region"] == region]
            reach = max((span(s) for s in home), default=0) * 1.1
            ids = [s for s in near if u.sys[s]["region"] == region or span(s) <= reach]
            pad = 46
        else:
            ids = [s for s in u.by_region[region] if u.sys[s]["x"] is not None]
            pad = 40
        # centre on the current system and zoom so everything in the view still fits around it
        cx, cy = u.sys[cur]["x"], u.sys[cur]["y"]
        dx = max((abs(u.sys[s]["x"] - cx) for s in ids), default=0) or 1
        dy = max((abs(u.sys[s]["y"] - cy) for s in ids), default=0) or 1
        k0 = min((W / 2 - pad) / dx, (H / 2 - pad) / dy)
        k = k0 * self.zoom

        def pos(s):
            return (W / 2 + self.pan[0] + (u.sys[s]["x"] - cx) * k,
                    H / 2 + self.pan[1] - (u.sys[s]["y"] - cy) * k)

        idset = set(ids)
        live = app.live_reports()
        reported = {sid for sid, _ in live}
        for a in ids:                                                  # gates
            for b in u.adj.get(a, ()):
                if b in idset and a < b:
                    c.create_line(*pos(a), *pos(b), fill=GATE, width=1)
        fs = self.fontsize.get()
        small = ("Segoe UI", max(6, fs - 2))
        bold = ("Segoe UI", fs, "bold")
        radius = 3 if len(ids) > 120 else 4
        show_names = self.names.get() and (self.zoom >= 1.0 or len(ids) <= 80)
        for s in ids:                                                  # systems, coloured by security
            sec = u.sys[s]["sec"]
            col = SEC_COLORS[0] if sec >= 0.45 else SEC_COLORS[1] if sec > 0 else SEC_COLORS[2]
            x, y = pos(s)
            if not (-20 < x < W + 20 and -20 < y < H + 20):
                continue
            self.drawn.append((x, y, s))
            if u.sys[s]["region"] == region:
                c.create_oval(x - radius, y - radius, x + radius, y + radius, fill=col, outline="")
            else:                                                      # other region: hollow dot
                c.create_oval(x - radius, y - radius, x + radius, y + radius, outline=col, width=1)
            if show_names and s != cur and s not in reported:
                c.create_text(x + radius + 3, y, text=u.sys[s]["name"], fill=theme.MUTED, anchor="w", font=small,
                              tags="sysname")

        placed = []

        def put_label(x, y, text, fill):
            """Label on a dark chip next to (x, y), trying a few spots so labels don't overlap."""
            for dx, dy, anchor in ((11, -11, "w"), (11, 11, "w"), (-11, -11, "e"), (-11, 11, "e"),
                                   (0, -17, "s"), (0, 17, "n"), (11, -26, "w"), (11, 26, "w")):
                t = c.create_text(x + dx, y + dy, text=text, fill=fill, anchor=anchor, font=bold, tags="lbl")
                l, tp, r, b = c.bbox(t)
                clash = any(not (r < pl or l > pr or b < pt or tp > pb) for pl, pt, pr, pb in placed)
                if not clash or (dx, dy) == (11, 26):
                    placed.append((l - 2, tp - 1, r + 2, b + 1))
                    c.create_rectangle(l - 4, tp - 1, r + 4, b + 1, fill=theme.SURFACE, outline=theme.BORDER,
                                       tags="chip")
                    return
                c.delete(t)

        cxp, cyp = pos(cur)
        put_label(cxp, cyp, u.sys[cur]["name"], "#FFFFFF")
        off_map = []
        for sid, r in live:
            d = dist.get(sid)
            label = f"{u.sys[sid]['name']} ({'here' if d == 0 else str(d) + 'j' if d is not None else '?'})"
            if sid in idset and sid != cur:
                x, y = pos(sid)
                c.create_oval(x - 8, y - 8, x + 8, y + 8, outline=theme.DANGER, width=2)
                put_label(x, y, label, theme.DANGER_TEXT)
            elif sid != cur:
                off_map.append(label)
        wh_here = [w for w in app.wormholes if w["sid"] in idset] if self.show_wh.get() else []
        for w in wh_here:
            x, y = pos(w["sid"])
            col = HUB_COLORS.get(w["hub"], theme.VIOLET)
            c.create_polygon(x, y - 9, x + 9, y, x, y + 9, x - 9, y, outline=col, fill="", width=2)
            put_label(x, y, f"{w['hub']} {w['hours']}h", col)
        c.tag_raise("chip")
        c.tag_raise("lbl")
        c.create_oval(cxp - 14, cyp - 14, cxp + 14, cyp + 14, outline=theme.mix(BG, theme.PRIMARY, .35), width=2)
        c.create_oval(cxp - 9, cyp - 9, cxp + 9, cyp + 9, outline="#FFFFFF", width=2)
        c.create_oval(cxp - 4, cyp - 4, cxp + 4, cyp + 4, fill=theme.PRIMARY, outline="")

        sec_cur = u.sys[cur]["sec"]
        self.info.set(f"You: {u.sys[cur]['name']}  -  {u.regions.get(region, '?')}  (security {sec_cur:.1f})   "
                      f"red rings = intel reports, last {app.report_ttl() // 60} min")
        lines = []
        for sid, r in live[:4]:
            d = dist.get(sid)
            when = int(app.report_age(r["stamp"]) // 60)
            far = "in system" if d == 0 else f"{d} jumps" if d is not None else "no gate route"
            lines.append(f"{u.sys[sid]['name']}: {far} ({when}m ago)")
        if off_map:
            lines.append("Not on this view: " + ", ".join(off_map))
        lines += self.wormhole_summary(dist)
        self.report.set("\n".join(lines) if lines else "No recent intel reports.")
