"""Zoomable / pannable mini map widget. Can be embedded in the main window or popped out on its own."""
import tkinter as tk
from tkinter import ttk

import skyhooks
import theme

VIEW_NEARBY = "Nearby systems"
VIEW_REGION = "Whole region"
BG = theme.WELL
HUB_COLORS = {"Thera": theme.VIOLET, "Turnur": theme.CYAN}
SEC_COLORS = ("#4ADE80", "#F59E0B", "#F43F5E")          # high / low / null sec
GATE = "#3A5378"
SKYHOOK = theme.WARN
SKYHOOK_TEXT = theme.WARN_TEXT
NAME = "#C3CEDF"


class MapView(ttk.Frame):
    def __init__(self, parent, app, popout=False):
        super().__init__(parent, style="CardBody.TFrame")
        self.app = app
        self.popout = popout
        self.view = tk.StringVar(value=VIEW_NEARBY)
        self.jumps = tk.IntVar(value=7)
        self.names = tk.BooleanVar(value=True)
        self.show_wh = tk.BooleanVar(value=True)
        self.show_sky = tk.BooleanVar(value=True)
        self.fontsize = tk.IntVar(value=10)
        self.info = tk.StringVar(value="Waiting for your current system…")
        self.report = tk.StringVar(value="")
        self.hover = tk.StringVar(value="")
        self.details = tk.BooleanVar(value=False)      # report / wormhole text under the map (off = bigger map)
        self.zoom, self.pan = 1.0, [0.0, 0.0]
        self.drawn = []                      # [(x, y, system id)] for hover lookups
        self._layout = (None, {})            # (key, {system id: (x, y)}) schematic positions, see layout()
        self._drag = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self, style="CardBody.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        cb = ttk.Combobox(bar, textvariable=self.view, values=[VIEW_NEARBY, VIEW_REGION], state="readonly", width=15)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.refit())
        ttk.Label(bar, text="Jumps", style="Card.Muted.TLabel").pack(side="left", padx=(12, 4))
        ttk.Spinbox(bar, from_=1, to=15, width=3, textvariable=self.jumps, command=self.refit).pack(side="left")
        ttk.Label(bar, text="Text", style="Card.Muted.TLabel").pack(side="left", padx=(12, 4))
        ttk.Spinbox(bar, from_=7, to=22, width=3, textvariable=self.fontsize, command=self.draw).pack(side="left")
        ttk.Checkbutton(bar, text="Names", variable=self.names, command=self.draw,
                        style="Card.TCheckbutton").pack(side="left", padx=(14, 0))
        ttk.Checkbutton(bar, text="Wormholes", variable=self.show_wh, command=self.draw,
                        style="Card.TCheckbutton").pack(side="left", padx=(12, 0))
        ttk.Checkbutton(bar, text="Skyhooks", variable=self.show_sky, command=self.draw,
                        style="Card.TCheckbutton").pack(side="left", padx=(12, 0))
        ttk.Checkbutton(bar, text="Details", variable=self.details, command=self.show_details,
                        style="Card.TCheckbutton").pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Fit", width=4, command=self.refit).pack(side="left", padx=(12, 0))
        if not popout:
            ttk.Button(bar, text="Pop out", command=app.pop_out_map).pack(side="right")
        ttk.Label(self, textvariable=self.info, wraplength=600, style="Card.TLabel").grid(row=1, column=0, sticky="w",
                                                                                         pady=(8, 0))
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=1, highlightbackground=theme.BORDER,
                                highlightcolor=theme.BORDER, width=560, height=340)
        self.canvas.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        self.report_lbl = ttk.Label(self, textvariable=self.report, wraplength=600, justify="left",
                                    style="Card.TLabel")
        self.hover.trace_add("write", lambda *a: self.draw_hover())

        c = self.canvas
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        c.bind("<Motion>", self.on_move)
        c.bind("<Double-Button-1>", lambda e: self.refit())

    def show_details(self):
        if self.details.get():
            self.report_lbl.grid(row=3, column=0, sticky="w", pady=(6, 0))
        else:
            self.report_lbl.grid_remove()

    def draw_hover(self):
        """Hover / help line drawn on the map itself (bottom-left) so it takes no space."""
        c = self.canvas
        c.delete("hover")
        text = self.hover.get()
        if not text:
            return
        t = c.create_text(10, c.winfo_height() - 10, text=text, anchor="sw", fill=theme.MUTED,
                          font=(theme.FONT, 9), tags="hover")
        l, tp, r, b = c.bbox(t)
        c.create_rectangle(l - 6, tp - 3, r + 6, b + 3, fill=theme.SURFACE, outline=theme.BORDER, tags="hover")
        c.tag_raise(t)

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
        hooks = "".join(f"   |   skyhook {skyhooks.describe(h)}" for h, _ in self.app.live_skyhooks() if h["sid"] == sid)
        self.hover.set(f"{s['name']}  ({u.regions.get(s['region'], '?')}, security {s['sec']:.1f}){far}{holes}{hooks}")

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

    def skyhook_summary(self, dist):
        """Lines about raidable skyhooks: how many are open / opening soon, and the nearest few by gate jumps."""
        hooks = self.app.live_skyhooks()
        if not hooks or not self.show_sky.get():
            return []
        n_open = sum(1 for _, st in hooks if st[0] == "open")
        out = [f"Raidable skyhooks: {n_open} open now, {len(hooks) - n_open} opening soon"]
        u = self.app.uni
        reach = sorted(((dist[h["sid"]], h) for h, _ in hooks if h["sid"] in dist), key=lambda t: t[0])[:3]
        for d, h in reach:
            s = u.sys[h["sid"]]
            far = "here" if d == 0 else f"{d} jump{'s' if d != 1 else ''}"
            out.append(f"   {s['name']} ({u.regions.get(s['region'], '?')}): {far}, "
                       f"{skyhooks.describe(h)}")
        return out

    # ---- drawing ---------------------------------------------------------------------------
    def layout(self, ids, cur, aspect):
        """Schematic positions in -1..1, filling the map. Starts from the real star map, stretched to
        fill the view, then pushes systems apart (and pulls gate neighbours together) so nothing overlaps.
        Cached until the system set or the shape of the map changes, so zoom / pan stay instant."""
        key = (cur, tuple(sorted(ids)), round(aspect, 1))
        if self._layout[0] == key:
            return self._layout[1]
        u = self.app.uni
        cx, cy = u.sys[cur]["x"], u.sys[cur]["y"]
        dx = max((abs(u.sys[s]["x"] - cx) for s in ids), default=0) or 1
        dy = max((abs(u.sys[s]["y"] - cy) for s in ids), default=0) or 1
        home = {s: [(u.sys[s]["x"] - cx) / dx * aspect, (u.sys[s]["y"] - cy) / dy] for s in ids}
        p = {s: list(v) for s, v in home.items()}
        idset = set(ids)
        links = [(a, b) for a in ids for b in u.adj.get(a, ()) if b in idset and a < b]
        gap = 1.9 / max(2.0, len(ids) ** 0.5)               # wanted spacing between neighbouring systems
        order = list(ids)
        for it in range(80):
            step = 0.5 * (1 - it / 80) + 0.05
            move = {s: [0.0, 0.0] for s in ids}
            for i, a in enumerate(order):                   # push apart anything closer than `gap`
                ax, ay = p[a]
                for b in order[i + 1:]:
                    vx, vy = ax - p[b][0], ay - p[b][1]
                    d2 = vx * vx + vy * vy
                    if d2 < gap * gap:
                        d = d2 ** 0.5 or 1e-6
                        f = (gap - d) / d * 0.5
                        move[a][0] += vx * f
                        move[a][1] += vy * f
                        move[b][0] -= vx * f
                        move[b][1] -= vy * f
            for a, b in links:                              # gates: keep neighbours near each other
                vx, vy = p[b][0] - p[a][0], p[b][1] - p[a][1]
                d = (vx * vx + vy * vy) ** 0.5
                if d > gap * 1.6:
                    f = (d - gap * 1.6) / d * 0.15
                    move[a][0] += vx * f
                    move[a][1] += vy * f
                    move[b][0] -= vx * f
                    move[b][1] -= vy * f
            for s in ids:                                   # and a gentle pull back to the real geography
                move[s][0] += (home[s][0] - p[s][0]) * 0.03
                move[s][1] += (home[s][1] - p[s][1]) * 0.03
                if s != cur:
                    p[s][0] += move[s][0] * step
                    p[s][1] += move[s][1] * step
        xs, ys = [v[0] for v in p.values()], [v[1] for v in p.values()]   # fill the whole map area
        mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        hx, hy = (max(xs) - min(xs)) / 2 or 1, (max(ys) - min(ys)) / 2 or 1
        out = {s: ((v[0] - mx) / hx, (v[1] - my) / hy) for s, v in p.items()}
        self._layout = (key, out)
        return out

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
        # schematic layout centred on the current system, stretched to fill the whole map area
        lay = self.layout(ids, cur, (W - 2 * pad) / max(1, H - 2 * pad))
        kx, ky = (W / 2 - pad - 40) * self.zoom, (H / 2 - pad) * self.zoom

        def pos(s):
            return W / 2 + self.pan[0] + lay[s][0] * kx, H / 2 + self.pan[1] - lay[s][1] * ky

        idset = set(ids)
        live = app.live_reports()
        reported = {sid for sid, _ in live}
        for a in ids:                                                  # gates
            for b in u.adj.get(a, ()):
                if b in idset and a < b:
                    c.create_line(*pos(a), *pos(b), fill=GATE, width=2 if len(ids) <= 80 else 1)
        fs = self.fontsize.get()
        small = ("Segoe UI", max(7, fs - 1))
        bold = ("Segoe UI", fs, "bold")
        radius = 4 if len(ids) > 120 else 5
        show_names = self.names.get()
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

        placed = []

        def put_label(x, y, text, fill):
            """Label on a dark chip next to (x, y), trying a few spots so labels don't overlap."""
            for dx, dy, anchor in ((11, -11, "w"), (11, 11, "w"), (-11, -11, "e"), (-11, 11, "e"),
                                   (0, -17, "s"), (0, 17, "n"), (11, -26, "w"), (11, 26, "w")):
                t = c.create_text(x + dx, y + dy, text=text, fill=fill, anchor=anchor, font=bold, tags="lbl")
                l, tp, r, b = c.bbox(t)
                clash = (any(not (r < pl or l > pr or b < pt or tp > pb) for pl, pt, pr, pb in placed)
                         or l < 4 or r > W - 4 or tp < 2 or b > H - 2)            # keep it inside the map
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
        sky_here = {}                                                  # system id -> soonest-ending / opening hook
        if self.show_sky.get():
            for h, st in app.live_skyhooks():
                if h["sid"] in idset:
                    sky_here.setdefault(h["sid"], []).append(st)
        for sid, sts in sky_here.items():
            x, y = pos(sid)
            opened = sorted(m for k, m in sts if k == "open")
            col = SKYHOOK if opened else theme.DIM
            c.create_rectangle(x - 7, y - 7, x + 7, y + 7, outline=col, width=2)
            more = f" x{len(sts)}" if len(sts) > 1 else ""
            text = (f"Skyhook{more} {skyhooks.short(opened[0])} left" if opened
                    else f"Skyhook{more} in {skyhooks.short(min(m for _, m in sts))}")
            put_label(x, y, text, SKYHOOK_TEXT if opened else theme.MUTED)
        if show_names:
            # each name goes right, left, above or below its dot - wherever it covers no label, name or other
            # dot; if there is no room it is left out (zoom in, or hover the dot, to see it)
            names = sorted((t for t in self.drawn if t[2] != cur and t[2] not in reported),
                           key=lambda t: (t[0] - cxp) ** 2 + (t[1] - cyp) ** 2)        # closest to you first
            for x, y, s in names:
                for ox, oy, anchor in ((radius + 3, 0, "w"), (-radius - 3, 0, "e"), (0, -radius - 2, "s"),
                                       (0, radius + 2, "n")):
                    t = c.create_text(x + ox, y + oy, text=u.sys[s]["name"], fill=NAME, anchor=anchor, font=small,
                                      tags="sysname")
                    l, tp, r, b = c.bbox(t)
                    hits_label = any(not (r < pl or l > pr or b < pt or tp > pb) for pl, pt, pr, pb in placed)
                    hits_dot = any(l - radius <= dx_ <= r + radius and tp - radius <= dy_ <= b + radius
                                   for dx_, dy_, s2 in self.drawn if s2 != s)
                    if not hits_label and not hits_dot:
                        placed.append((l - 2, tp - 1, r + 2, b + 1))
                        break
                    c.delete(t)

        c.tag_raise("chip")
        c.tag_raise("lbl")
        c.create_oval(cxp - 14, cyp - 14, cxp + 14, cyp + 14, outline=theme.mix(BG, theme.PRIMARY, .35), width=2)
        c.create_oval(cxp - 9, cyp - 9, cxp + 9, cyp + 9, outline="#FFFFFF", width=2)
        c.create_oval(cxp - 4, cyp - 4, cxp + 4, cyp + 4, fill=theme.PRIMARY, outline="")

        sec_cur = u.sys[cur]["sec"]
        self.info.set(f"You: {u.sys[cur]['name']}  -  {u.regions.get(region, '?')}  (security {sec_cur:.1f})   "
                      f"red rings = intel reports, last {app.report_ttl() // 60} min"
                      + ("   -   orange squares = raidable skyhooks" if self.show_sky.get() and app.skyhooks else ""))
        lines = []
        for sid, r in live[:4]:
            d = dist.get(sid)
            when = int(app.report_age(r["stamp"]) // 60)
            far = "in system" if d == 0 else f"{d} jumps" if d is not None else "no gate route"
            lines.append(f"{u.sys[sid]['name']}: {far} ({when}m ago)")
        if off_map:
            lines.append("Not on this view: " + ", ".join(off_map))
        lines += self.wormhole_summary(dist)
        lines += self.skyhook_summary(dist)
        self.report.set("\n".join(lines) if lines else "No recent intel reports.")
        self.draw_hover()
