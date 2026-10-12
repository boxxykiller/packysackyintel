"""Find EVE 'Local' chat windows on a screenshot using OCR + simple line detection (no game attachment)."""
import re

from PIL import Image, ImageChops

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
                    len(line) == 1                                         # alone on its line
                    or (nxt and re.match(r"^\[\d*\]?$", nxt))               # "Local [4]"
                    or (prv and not nxt and re.match(r"^\[\d+\]$", prv))):  # "... [62] Local"
                out.append(w)
    return out


def clean_system(tok):
    tok = tok.strip(" :;,.")
    return tok.replace("l", "I").upper()


def find_local_windows(img):
    """img: PIL RGB screenshot. Returns [{'system', 'tab', 'list'}] in image coordinates."""
    W, H = img.size
    gray = img.convert("L")
    lines = []
    for scale in (1, 1.5):               # small UI text: retry magnified if nothing is found
        scale = min(scale, ocr.MAX_DIM / max(W, H))
        lines = ocr.recognize(img, scale)
        if _tab_words(lines):
            break
    found = []
    for tab_word in _tab_words(lines):
        tx, ty, tw, th = tab_word[1:]
        verts = _fraction_lines(gray, (tx + 80, ty + 45, min(W - 1, tx + 1100), min(H, ty + 130)), "v")
        if not verts:
            continue
        div = verts[0]
        right = next((v for v in verts if v > div + 60), None)
        x0 = div + 3
        x1 = (right - 2) if right else min(W - 1, div + 260)
        y0 = int(ty + 28)
        horiz = _fraction_lines(gray, (x0, int(ty + 120), x1, min(H, int(ty + 1500))), "h")
        y1 = horiz[0] - 1 if horiz else min(H, y0 + 800)
        system = ""
        best_y = -1
        for ln in lines:                  # latest "Local : <system>" chat line inside this panel
            if (ln[0][0].lower() == "local" and len(ln) >= 2 and tx <= ln[0][1] < div
                    and ln[0][2] > ty + 30 and ln[0][2] > best_y):
                best_y, system = ln[0][2], clean_system(ln[1][0])
        found.append({"system": system, "tab": (tx, ty, tw, th), "list": (x0, y0, x1, y1)})
    return found
