"""Find EVE 'Local' chat windows on a screenshot using OCR + simple line detection (no game attachment)."""
import re

from PIL import Image, ImageChops, ImageOps

import ocr

TAB_RE = re.compile(r"^Local\s*(\[.*)?$", re.I)


def _fraction_lines(gray, box, axis, thresh=10, need=0.9):
    """Find 1-px straight lines inside box. axis 'v' -> returns x positions of vertical lines,
    'h' -> y positions of horizontal lines (as absolute coordinates)."""
    l, t, r, b = box
    if r - l < 3 or b - t < 3:
        return []
    crop = gray.crop(box)
    if axis == "v":
        d = ImageChops.difference(crop, ImageChops.offset(crop, 1, 0))
        frac = d.point(lambda v: 255 if v > thresh else 0).resize((crop.width, 1), Image.BOX)
        vals, origin = list(frac.get_flattened_data() if hasattr(frac, "get_flattened_data") else frac.getdata()), l
    else:
        d = ImageChops.difference(crop, ImageChops.offset(crop, 0, 1))
        frac = d.point(lambda v: 255 if v > thresh else 0).resize((1, crop.height), Image.BOX)
        vals, origin = list(frac.get_flattened_data() if hasattr(frac, "get_flattened_data") else frac.getdata()), t
    # ImageChops.offset wraps around, so the first/last couple of pixels are artifacts
    hits = [origin + i for i, v in enumerate(vals) if 2 <= i < len(vals) - 2 and v / 255 >= need]
    lines, prev = [], None
    for h in hits:                       # merge adjacent columns/rows into one line
        if prev is not None and h - prev <= 2:
            lines[-1].append(h)
        else:
            lines.append([h])
        prev = h
    return [sum(g) // len(g) for g in lines]


def _tab_words(lines):
    """Words that are the 'Local' channel tab (not the word 'Local' inside a chat message such as
    'Channel changed to Local : <system>')."""
    out = []
    for line in lines:
        for i, w in enumerate(line):
            t = w[0]
            nxt = line[i + 1][0] if i + 1 < len(line) else None
            prv = line[i - 1][0] if i > 0 else None
            if re.match(r"^local\[\d*\]?$", t, re.I):                    # "Local[4]"
                out.append(w)
            elif t.lower() == "local" and (
                    (len(line) == 1 and not _wrapped_message(lines, w))    # alone on its line
                    or (nxt and re.match(r"^\[\d*\]?$", nxt))               # "Local [4]"
                    or (prv and not nxt and re.match(r"^\[\d+\]$", prv))):  # "... [62] Local"
                out.append(w)
    return out


def _wrapped_message(lines, w):
    """True if a lone 'Local' word is really part of a wrapped 'Channel changed to Local : X' chat message
    (narrow chat windows put 'Local' on a line of its own, under '... changed to')."""
    for ln in lines:
        last = ln[-1]
        if (last[0].lower() == "to" and 0 < w[2] - last[2] <= 2.5 * w[4]
                and abs(ln[0][1] - w[1]) <= 4 * w[4]):
            return True
    return False


def clean_system(tok):
    tok = tok.strip(" :;,.")
    return tok.replace("l", "I").upper()


def _tab_count(lines, tab_word):
    """The member count shown on the tab ('Local [33]' / 'Local[33]'), or None if it can't be read."""
    for line in lines:
        if tab_word in line:
            i = line.index(tab_word)
            text = "".join(w[0] for w in line[i:i + 2])
            m = re.search(r"\[(\d{1,4})", text)
            return int(m.group(1)) if m else None
    return None


_COUNT_RE = re.compile(r"^\W{0,2}(\d{1,4})$")        # member-count header: person icon + number ('& 33')


def _member_header(img, tx, ty, th, count):
    """Find the member list's header (person icon + member count) in the strip just under the tab row.
    Returns (x, top, bottom, match) of the number, or None; match is 2 when it equals the tab's count, 1 when
    the tab's count looks like a misread of it ('[2' for 24), 0 for some other number near the tab. EVE always puts a channel's member list on the right of
    its chat, so only the right of the tab is searched - a neighbouring chat window to the left has its own
    member list (and count) that must never be mistaken for Local's. Nothing about the window's size is
    assumed: chat windows are resized and narrowed differently by every player."""
    W, H = img.size
    box = (int(tx), int(ty + th + 2), min(W, int(tx + 140 * th)), min(H, int(ty + th + 6 * th + 30)))
    if box[2] - box[0] < 20 or box[3] - box[1] < 8:
        return None
    hits = []
    strip = ImageOps.autocontrast(img.crop(box).convert("L")).convert("RGB")     # the count is dim grey text
    for ln in ocr.recognize(strip, 3):
        for w in ln:
            m = _COUNT_RE.match(w[0])
            if not m:
                continue
            n, x = m.group(1), box[0] + w[1]
            if count is not None and int(n) == count:
                match = 2
            elif count is not None and (n.startswith(str(count)) or str(count).startswith(n)):
                match = 1
            elif count is None and x <= tx + 60 * th:                 # tab's count unreadable: nearest number
                match = 0
            else:
                continue                                              # another window's count: never Local's
            hits.append((x, box[1] + w[2], box[1] + w[2] + w[4], match))
    # best match with the tab's count, then closest to the tab row (chat lines further down can contain bare
    # numbers too), then nearest the tab
    return min(hits, key=lambda h: (-h[3], round(h[1] / th), h[0] - tx), default=None)


def _vline_extent(gray, x, y, th):
    """How far a vertical window line at column x (passing through row y) runs up and down: (top, bottom).
    Short breaks (up to ~1.5 text lines) are bridged."""
    W, H = gray.size
    xs = range(max(0, x - 2), min(W - 1, x + 3))

    def edge(yy):
        return any(abs(gray.getpixel((xx, yy)) - gray.getpixel((xx + 1, yy))) > 10 for xx in xs)

    gap = int(th * 1.5) + 1
    ends = []
    for step, stop in ((-1, -1), (1, H)):
        end, miss, yy = y, 0, y
        while miss <= gap:
            yy += step
            if yy == stop:
                break
            if edge(yy):
                end, miss = yy, 0
            else:
                miss += 1
        ends.append(end)
    return ends[0], ends[1]


DEBUG = []           # details of the last search, for the log


def find_local_windows(img):
    """img: PIL RGB screenshot. Returns [{'system', 'tab', 'list'}] in image coordinates.

    Everything is measured from the picture itself: the 'Local' tab, the member list's count header, and the
    window lines around them - so it works for any chat window size, width or position."""
    W, H = img.size
    gray = img.convert("L")
    lines = []
    for scale in (1, 1.5):               # small UI text: retry magnified if nothing is found
        scale = min(scale, ocr.MAX_DIM / max(W, H))
        lines = ocr.recognize(img, scale)
        if _tab_words(lines):
            break
    found = []
    DEBUG.clear()
    for tab_word in _tab_words(lines):
        tx, ty, tw, th = tab_word[1:]
        count = _tab_count(lines, tab_word)
        # Only a confirmed member list counts: the person-icon + count header right of a real Local tab.
        # Without it nothing is guessed - guessing is how other windows (watch list, fleet, overview) got read.
        head = _member_header(img, tx, ty, th, count)
        if not head:
            DEBUG.append(f"tab=({tx:.0f},{ty:.0f}) th={th:.0f} count={count} header=None -> skipped (no member list)")
            continue
        hx, htop, hbot, match = head
        band_t, band_b = int(hbot + th), int(min(H, hbot + th * 8))          # a band of list rows under the header
        left = _fraction_lines(gray, (max(0, int(hx - 8 * th)), band_t, int(hx), band_b), "v")
        div = left[-1] if left else int(hx - 2.6 * th)                       # line just left of the header
        y0 = int(hbot + 3)
        ext_top, ext_bot = _vline_extent(gray, div, (band_t + band_b) // 2, th)
        x0 = div + 3
        right = _fraction_lines(gray, (int(x0 + 4 * th), band_t, min(W - 1, int(x0 + 60 * th)), band_b), "v")
        x1 = (right[0] - 2) if right else min(W - 1, int(x0 + 22 * th))
        # bottom of the panel = a line that runs on past the divider into the chat side. Member rows can have
        # their own highlighted backgrounds whose edges stop at the list, so those never end the list early.
        horiz = _fraction_lines(gray, (max(0, int(div - 2 * th)), int(y0 + th), x1, H), "h")
        y1 = horiz[0] - 1 if horiz else (ext_bot if ext_bot - y0 > 3 * th else H)
        DEBUG.append(f"tab=({tx:.0f},{ty:.0f}) th={th:.0f} count={count} header={head} div={div} "
                     f"right={right[:1]} divider_runs={ext_top}-{ext_bot} bottom_lines={horiz[:2]} "
                     f"-> list=({x0},{y0},{x1},{y1})")
        if x1 - x0 < 4 * th or y1 - y0 < 2 * th:
            continue
        system = ""
        best_y = -1
        chat_l, chat_r = (div - 100 * th, div) if div > tx else (x1, x1 + 100 * th)
        for ln in lines:                  # latest "Local : <system>" chat line inside this panel
            if (ln[0][0].lower() == "local" and len(ln) >= 2 and chat_l <= ln[0][1] < chat_r
                    and ln[0][2] > ty + 2 * th and ln[0][2] > best_y):
                best_y, system = ln[0][2], clean_system(ln[1][0])
        # 2 = header shows exactly the tab's count, 1 = the tab's count looks misread, 0 = some number near it
        found.append({"system": system, "tab": (tx, ty, tw, th), "list": (x0, y0, x1, y1), "score": match})
    found.sort(key=lambda f: -f["score"])
    return found
