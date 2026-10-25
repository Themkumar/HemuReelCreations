"""Speech-tied GFX behind the speaker: timeline building and per-frame sprite rendering."""
from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
SCRIPT_FONT = [((0x0C00, 0x0C7F), "NotoSansTelugu"), ((0x0900, 0x097F), "NotoSansDevanagari"),
               ((0x0B80, 0x0BFF), "NotoSansTamil"), ((0x0C80, 0x0CFF), "NotoSansKannada")]
ANIM_IN = ("typewriter", "fade", "glitch", "zoom", "slide")
SFX_FOR_ANIM = {"pop": "pop", "slide": "whoosh", "glitch": "glitch", "zoom": "hit", "fade": "shimmer", "typewriter": "type"}
ANIMS_FOR = {"text": ("typewriter", "glitch", "fade", "zoom"), "image": ("zoom", "slide", "fade"),
             "broll": ("zoom", "fade"), "counter": ("fade", "zoom")}
CURRENCY = "₹$€£¥"


# ---------- easing ----------
def ease_out_back(x: float, s: float = 1.7) -> float:
    x -= 1
    return x * x * ((s + 1) * x + s) + 1


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


# ---------- fonts and sprites ----------
def font_for(text: str, size: int, weight: int = 900, display=False) -> ImageFont.FreeTypeFont:
    name = "NotoSans"
    for (lo, hi), fam in SCRIPT_FONT:
        if any(lo <= ord(c) <= hi for c in text):
            name = fam
            break
    if display == "type" and name == "NotoSans" and (FONT_DIR / "SpecialElite-400.ttf").exists():
        name, weight = "SpecialElite", 400  # typewriter face
    elif display and name == "NotoSans" and (FONT_DIR / "Anton-400.ttf").exists():
        name, weight = "Anton", 400  # tall condensed headline face for Latin text
    path = FONT_DIR / f"{name}-{weight}.ttf"
    if not path.exists():
        path = FONT_DIR / f"{name}-700.ttf"
    try:
        return ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:
        return ImageFont.truetype(str(path), size)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def text_sprite(text: str, size: int, fill=(255, 255, 255), stroke=(0, 0, 0), glow=None, max_w: int = 0,
                display=False, box=None) -> np.ndarray:
    font = font_for(text, size, display=display)
    # shrink to fit width
    while max_w and size > 24:
        bbox = font.getbbox(text, stroke_width=size // 14)
        if bbox[2] - bbox[0] <= max_w:
            break
        size = int(size * 0.9)
        font = font_for(text, size, display=display)
    sw = max(2, size // 14)
    bbox = font.getbbox(text, stroke_width=sw)
    pad = size // 2
    w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    org = (pad - bbox[0], pad - bbox[1])
    if box:  # solid highlight box behind the word (news-style)
        m = size // 8
        d.rectangle([pad - m, pad - m, w - pad + m, h - pad + m], fill=(*box, 235))
    if glow:
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(g).text(org, text, font=font, fill=(*glow, 255), stroke_width=sw * 2, stroke_fill=(*glow, 255))
        img = Image.alpha_composite(img, g.filter(ImageFilter.GaussianBlur(size / 6)))
        d = ImageDraw.Draw(img)
    d.text(org, text, font=font, fill=(*fill, 255), stroke_width=sw, stroke_fill=(*stroke, 255))
    return np.array(img)


def text_block_sprite(text: str, size: int, max_w: int, **kw) -> np.ndarray:
    """Big kinetic text: one line if it fits, otherwise two balanced lines, shrinking only as a last resort."""
    one = text_sprite(text, size, **kw)
    words = text.split()
    if one.shape[1] <= max_w or len(words) < 2:
        return one if one.shape[1] <= max_w else text_sprite(text, size, max_w=max_w, **kw)
    best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    lines = [trim(text_sprite(" ".join(words[:best]), size, max_w=max_w, **kw), size // 8),
             trim(text_sprite(" ".join(words[best:]), size, max_w=max_w, **kw), size // 8)]
    w = max(l.shape[1] for l in lines)
    gap = -int(size * 0.12)
    h = lines[0].shape[0] + lines[1].shape[0] + gap
    out = np.zeros((h, w, 4), np.uint8)
    y = 0
    for l in lines:
        x = (w - l.shape[1]) // 2
        region = out[y:y + l.shape[0], x:x + l.shape[1]]
        a = l[..., 3:4] / 255.0
        region[:] = (l * a + region * (1 - a)).astype(np.uint8)
        region[..., 3] = np.maximum(region[..., 3], l[..., 3])
        y += l.shape[0] + gap
    return out


def image_sprite(path: Path, max_w: int, max_h: int, glow=(255, 255, 255)) -> np.ndarray:
    img = Image.open(path).convert("RGBA")
    img.thumbnail((max_w, max_h))
    a = np.array(img)
    if a[..., 3].min() == 255:  # no transparency: round corners + border
        r = max(12, img.width // 14)
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
        a[..., 3] = np.array(mask)
        pad = 10
        canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).rounded_rectangle([0, 0, canvas.width - 1, canvas.height - 1], r + pad, fill=(*glow, 255))
        canvas.alpha_composite(Image.fromarray(a), (pad, pad))
        a = np.array(canvas)
    # soft drop glow
    pil = Image.fromarray(a)
    shadow = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    shadow.putalpha(pil.getchannel("A").filter(ImageFilter.GaussianBlur(12)).point(lambda v: v * 0.6))
    out = Image.new("RGBA", (pil.width + 40, pil.height + 40), (0, 0, 0, 0))
    out.alpha_composite(shadow, (24, 28))
    out.alpha_composite(pil, (20, 20))
    return np.array(out)


def arrow_sprite(length: int, color=(255, 212, 0)) -> np.ndarray:
    w, h = length + 40, 140
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = h // 2
    d.line([(20, y), (length - 40, y)], fill=(*color, 255), width=22)
    d.polygon([(length - 70, y - 50), (length + 20, y), (length - 70, y + 50)], fill=(*color, 255))
    return np.array(img.filter(ImageFilter.GaussianBlur(0.8)))


def ring_sprite(radius: int, color=(255, 59, 92)) -> np.ndarray:
    s = radius * 2 + 40
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([20, 20, s - 20, s - 20], outline=(*color, 255), width=14)
    return np.array(img)


def pin_sprite(label: str, color=(255, 59, 92)) -> np.ndarray:
    pin = Image.new("RGBA", (160, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(pin)
    d.ellipse([20, 10, 140, 130], fill=(*color, 255))
    d.polygon([(32, 100), (128, 100), (80, 210)], fill=(*color, 255))
    d.ellipse([55, 45, 105, 95], fill=(255, 255, 255, 255))
    txt = Image.fromarray(text_sprite(label, 64, glow=color))
    out = Image.new("RGBA", (max(160, txt.width), 220 + txt.height), (0, 0, 0, 0))
    out.alpha_composite(pin, ((out.width - 160) // 2, 0))
    out.alpha_composite(txt, ((out.width - txt.width) // 2, 200))
    return np.array(out)


# ---------- blending ----------
def blend(dst: np.ndarray, sprite: np.ndarray, cx: float, cy: float, scale: float = 1.0, alpha: float = 1.0,
          angle: float = 0.0) -> None:
    """Alpha-blend an RGBA sprite onto an RGB float32 frame, centred at (cx, cy)."""
    if alpha <= 0.01 or scale <= 0.02:
        return
    spr = sprite
    if abs(scale - 1) > 1e-3 or abs(angle) > 1e-3:
        h, w = spr.shape[:2]
        if abs(angle) > 1e-3:
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
            cos, sin = abs(m[0, 0]), abs(m[0, 1])
            nw, nh = int(h * sin + w * cos) + 2, int(h * cos + w * sin) + 2
            m[0, 2] += nw / 2 - w / 2
            m[1, 2] += nh / 2 - h / 2
            spr = cv2.warpAffine(spr, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
        else:
            spr = cv2.resize(spr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_LINEAR)
    h, w = spr.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    H, W = dst.shape[:2]
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if xa >= xb or ya >= yb:
        return
    s = spr[ya - y0:yb - y0, xa - x0:xb - x0].astype(np.float32)
    a = (s[..., 3:4] / 255.0) * alpha
    dst[ya:yb, xa:xb] = dst[ya:yb, xa:xb] * (1 - a) + s[..., :3] * a


def glow_aura(dst: np.ndarray, cx: float, cy: float, radius: float, color, strength: float) -> None:
    H, W = dst.shape[:2]
    yy, xx = np.ogrid[:H, :W]
    d = np.sqrt((xx - cx) ** 2 + ((yy - cy) * 0.8) ** 2) / max(radius, 1)
    a = np.clip(1 - d, 0, 1) ** 2 * strength
    dst += (np.array(color, np.float32)[None, None, :] - dst * 0.3) * a[..., None]


def vignette(dst: np.ndarray, strength: float = 0.35) -> None:
    H, W = dst.shape[:2]
    yy, xx = np.ogrid[:H, :W]
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    dst *= (1 - strength * d ** 2)[..., None]


class Particles:
    """Slow floating bokeh particles for the background layer."""

    def __init__(self, W, H, n=26, seed=0, color=(255, 212, 0)):
        rng = np.random.default_rng(seed)
        self.p = rng.random((n, 2)) * [W, H]
        self.v = (rng.random((n, 2)) - 0.5) * [0.6, 1.4] - [0, 0.6]
        self.r = rng.random(n) * 10 + 4
        self.W, self.H, self.color = W, H, color

    def draw(self, dst: np.ndarray, strength: float) -> None:
        if strength <= 0:
            return
        self.p = (self.p + self.v) % [self.W, self.H]
        layer = np.zeros(dst.shape[:2], np.float32)
        for (x, y), r in zip(self.p, self.r):
            cv2.circle(layer, (int(x), int(y)), int(r), 1.0, -1, lineType=cv2.LINE_AA)
        layer = cv2.GaussianBlur(layer, (0, 0), 3) * 0.5 * strength
        dst += (np.array(self.color, np.float32)[None, None, :] - dst) * layer[..., None]


# ---------- timeline ----------
def find_phrase(words: list[dict], phrase: str, after: float = -1.0) -> dict | None:
    """First word whose text starts the phrase (or contains it), at or after `after`."""
    target = phrase.split()
    if not target:
        return None
    for i, w in enumerate(words):
        if w["start"] < after:
            continue
        seq = " ".join(x["word"] for x in words[i:i + len(target)])
        if seq.startswith(target[0][: max(2, len(target[0]) - 2)]) and (len(target) == 1 or target[-1][:2] in seq):
            return {"start": w["start"], "end": words[min(len(words) - 1, i + len(target) - 1)]["end"]}
    joined = phrase.replace(" ", "")
    for w in words:
        if w["start"] >= after and (joined in w["word"] or w["word"] in joined) and len(w["word"]) > 2:
            return {"start": w["start"], "end": w["end"]}
    return None


def build_timeline(reel: dict, words: list[dict], manifest: dict, fps: int, duration: float,
                   lead_frames: int = 1, hold: float = 2.2, seed: int = 0) -> list[dict]:
    """Turn visual moments into timed GFX events (one major element at a time)."""
    rng = random.Random(seed)
    events, last_anim, after = [], None, -1.0
    assets = manifest.get(reel["id"], {})
    for k, vm in enumerate(reel.get("visual_moments", [])):
        hit = find_phrase(words, vm["phrase"], after) or find_phrase(words, vm["phrase"])
        if hit:
            after = hit["end"]
        if not hit:
            continue
        t_in = max(0.0, hit["start"] - lead_frames / fps)
        kind = vm.get("type", "text")
        asset = assets.get(str(k), {}) if isinstance(assets, dict) else {}
        if kind in ("image", "broll") and not asset.get("file"):
            kind = "text"  # fallback motion graphic
        choices = [a for a in ANIMS_FOR.get(kind, ANIM_IN) if a != last_anim] or list(ANIM_IN)
        anim = vm.get("anim") or rng.choice(choices)
        last_anim = anim
        anchor = {"image": "beside_head", "broll": "top_split", "text": "behind_head", "counter": "beside_head",
                  "arrow": "beside_head", "map": "beside_head"}.get(kind, "beside_head")
        events.append({
            "id": f"g{k:02d}", "type": kind, "anchor": anchor, "anim": anim,
            "t_in": round(t_in, 3), "t_out": round(min(duration, max(hit["end"] + 0.6, t_in + hold)), 3),
            "text": vm.get("label") or vm["phrase"], "value": vm.get("value"),
            "file": asset.get("file"), "label": vm.get("label") if kind in ("image", "broll") else None, "sfx_in": SFX_FOR_ANIM[anim], "major": True,
            "style": vm.get("style", "auto"), "original": asset.get("original"),
        })
        events[-1]["sfx"] = _sfx_for(events[-1])
    events.sort(key=lambda e: e["t_in"])
    for a, b in zip(events, events[1:]):  # no overlap between major elements
        if a["t_out"] > b["t_in"] - 0.12:
            a["t_out"] = round(max(a["t_in"] + 0.6, b["t_in"] - 0.12), 3)
    return [e for e in events if e["t_out"] - e["t_in"] >= 0.5]


def type_duration(text: str) -> float:
    return min(1.3, max(0.4, 0.055 * len(text.replace("*", ""))))


def parse_value(v) -> tuple[str, float, str, int]:
    """'₹50,000' -> ('₹', 50000, '', 0); '2.5%' -> ('', 2.5, '%', 1); '10K+' -> ('', 10, 'K+', 0)."""
    s = str(v or "0").strip()
    i = 0
    while i < len(s) and not (s[i].isdigit() or s[i] == "."):
        i += 1
    j = i
    while j < len(s) and (s[j].isdigit() or s[j] in ".,"):
        j += 1
    num = s[i:j].replace(",", "")
    try:
        val = float(num) if num else 0.0
    except ValueError:
        val = 0.0
    dec = len(num.split(".")[1]) if "." in num else 0
    return s[:i].strip() + ("" if not s[:i].strip() or s[:i].strip() in CURRENCY else " "), val, s[j:], dec


def format_value(prefix: str, val: float, suffix: str, dec: int) -> str:
    body = f"{val:,.{dec}f}" if dec else f"{int(round(val)):,}"
    if prefix == "₹" and not dec:  # Indian grouping: 1,00,000
        n = str(int(round(val)))
        body = n if len(n) <= 3 else (",".join([n[:-3][::-1][i:i + 2][::-1] for i in range(0, len(n) - 3, 2)][::-1]) + "," + n[-3:])
    return f"{prefix}{body}{suffix}"


def _sfx_for(ev: dict) -> list[dict]:
    """Sounds tied to one graphic: matched to how it animates, kept soft and cinematic."""
    t0, anim, out = ev["t_in"], ev["anim"], []
    if ev["type"] == "counter":
        out.append({"t": t0, "kind": "count", "gain_db": -1})
        if any(c in str(ev.get("value") or "") for c in CURRENCY):
            out.append({"t": t0, "kind": "whoosh", "gain_db": -8})
        return out
    if ev.get("style") == "backdrop" or (ev["type"] in ("image", "broll") and anim == "zoom"):
        out.append({"t": t0, "kind": "whoosh", "gain_db": -3})
        out.append({"t": t0 + 0.12, "kind": "hit", "gain_db": -6})
    if anim == "typewriter":
        text = (ev.get("text") if ev["type"] == "text" else ev.get("label")) or ""
        chars = [c for c in text.replace("*", "") if not c.isspace()]
        n = len(chars)
        if n:
            dur = type_duration(text)
            for i in range(0, n, max(1, n // 14)):
                out.append({"t": t0 + dur * i / n, "kind": "type", "gain_db": -6})
    elif anim in SFX_FOR_ANIM and not out:
        out.append({"t": t0, "kind": SFX_FOR_ANIM[anim], "gain_db": -2 if anim == "zoom" else 0})
    return out


class GfxRenderer:
    """Renders the GFX layer for one frame, given the tracked head position."""

    def __init__(self, events: list[dict], W: int, H: int, fps: int, colors: list[str], seed: int = 0):
        self.events, self.W, self.H, self.fps = events, W, H, fps
        self.U = min(W, H)  # size unit, so portrait and landscape graphics match
        self.landscape = W > H
        self.c1, self.c2 = hex_rgb(colors[0]), hex_rgb(colors[1] if len(colors) > 1 else colors[0])
        self.particles = Particles(W, H, seed=seed, color=self.c1)
        self.sprites: dict[str, np.ndarray] = {}
        self.side: dict[str, int] = {}
        self.layouts: dict[str, dict] = {}
        self.rng = np.random.default_rng(seed)
        self.shake_px = 3.0 * min(W, H) / 1080

    def _sprite(self, ev: dict, head: dict) -> np.ndarray:
        if ev["id"] in self.sprites:
            return self.sprites[ev["id"]]
        W = self.W
        t = ev["type"]
        if t in ("image", "broll") and ev.get("file"):
            if t == "broll":
                mw, mh = (W * 0.9, self.H * 0.3) if not self.landscape else (W * 0.4, self.H * 0.6)
            else:
                mw, mh = (W * 0.5, self.H * 0.3) if not self.landscape else (W * 0.3, self.H * 0.55)
            spr = image_sprite(Path(ev["file"]), int(mw), int(mh))
            if ev.get("label"):
                lab = text_sprite(ev["label"].replace("*", ""), int(self.U * 0.075), glow=self.c2, max_w=int(mw))
                spr = _stack(spr, lab)
        elif t == "text":
            spr = text_block_sprite(ev["text"].replace("*", ""), int(self.U * 0.3), int(W * 0.94), fill=self.c1, glow=self.c2)
        elif t == "arrow":
            spr = arrow_sprite(int(self.U * 0.28), self.c1)
        elif t == "map":
            spr = pin_sprite(ev["text"], self.c2)
        else:  # counter: drawn per frame, sprite is the label
            spr = text_sprite(ev["text"], int(self.U * 0.085), glow=self.c2, max_w=int(W * 0.5))
        self.sprites[ev["id"]] = spr
        return spr

    def active(self, t: float) -> list[dict]:
        return [e for e in self.events if e["t_in"] <= t < e["t_out"]]

    def shake(self, t: float) -> tuple[float, float]:
        """Small camera shake for 5 frames after each GFX hits."""
        for e in self.events:
            dt = t - e["t_in"]
            if 0 <= dt < 5 / self.fps and e["anim"] in ("zoom", "glitch", "pop"):
                k = (1 - dt * self.fps / 5) * self.shake_px
                return float(self.rng.uniform(-k, k)), float(self.rng.uniform(-k, k))
        return 0.0, 0.0

    # ---- layout -------------------------------------------------------------------------------
    def _band(self, head: dict) -> tuple[float, float]:
        """Portrait: the strip between the top safe edge and the top of the speaker's hair."""
        top = self.H * 0.075  # below the phone's top bar and the channel logo
        bot = head["y"] - max(head["size"], self.U * 0.12) * 0.62
        return top, bot

    def _layout(self, ev: dict, head: dict) -> dict:
        """Size and place an element once, on its first frame, so it never jumps while on screen."""
        if ev["id"] in self.layouts:
            return self.layouts[ev["id"]]
        W, H, U = self.W, self.H, self.U
        hx, hy, hs = head["x"], head["y"], max(head["size"], U * 0.12)
        t = ev["type"]
        lay: dict = {"mode": "free"}
        if self.landscape and t in ("image", "broll", "text", "counter"):
            side = -1 if hx > W / 2 else 1  # the open side of the frame
            room = (hx - hs * 0.8) if side < 0 else (W - hx - hs * 0.8)
            room = max(room, W * 0.3)
            top_lim = H * 0.05 if side < 0 else H * 0.2  # right side: stay under the channel logo
            if t == "counter":  # big number over its label, on the open side (never behind the head)
                return self._side_counter(ev, lay, side, room, hx, hs, top_lim)
            label = ev.get("label") if t != "text" else ev["text"]
            if t != "text" and ev.get("file") and self._wants_backdrop(ev):
                lay["backdrop"] = backdrop_image(Path(ev.get("original") or ev["file"]), W, H, self.c2)
            elif t != "text" and ev.get("file"):
                lay["img"] = trim(image_sprite(Path(ev["file"]), int(room * 0.85), int(H * 0.42)))
            typed = ev.get("anim") == "typewriter" and "img" not in lay
            stack = stack_text(label or "", int(min(room * 0.9, W * 0.46)), int(H * (0.42 if "img" in lay else 0.7)),
                               U, self.c2, typewriter=typed, meta=typed) if label else None
            if typed and stack is not None:
                stack, lay["lines"] = stack
            spr = stack
            if "img" in lay:
                spr = _stack(lay["img"], stack) if stack is not None else lay["img"]
            if spr is None:
                spr = np.zeros((2, 2, 4), np.uint8)
            cx = (hx - hs * 0.8) / 2 if side < 0 else hx + hs * 0.8 + (W - hx - hs * 0.8) / 2
            cx = min(max(cx, spr.shape[1] / 2 + W * 0.03), W - spr.shape[1] / 2 - W * 0.03)
            cy = min(max(H * 0.5, spr.shape[0] / 2 + top_lim), H - spr.shape[0] / 2 - H * 0.05)
            lay.update(mode="side", spr=spr, cx=cx, cy=cy)
            self.layouts[ev["id"]] = lay
            return lay
        if not self.landscape and ev["anchor"] != "top_split":
            top, bot = self._band(head)
            band = bot - top
            if band >= U * 0.14:  # enough sky above the head: use it, like trending podcast reels
                lay["mode"] = "band"
                if t == "text":
                    # bottom ~25% of the letters tucks behind the hair: the "text behind the person" look
                    spr = fit_text(ev["text"].replace("*", ""), int(W * 0.94), band * 1.33, int(U * 0.3), int(U * 0.1),
                                   fill=self.c1, glow=self.c2)
                    lay.update(spr=spr, cx=W / 2, cy=top + spr.shape[0] / 2)
                elif t in ("image", "broll") and ev.get("file"):
                    ih = band * 1.15
                    img = trim(image_sprite(Path(ev["file"]), int(W * 0.42), int(ih)))
                    spr = img
                    if ev.get("label"):
                        lab = fit_text(ev["label"], int(W * 0.94 - img.shape[1]), ih * 0.6, int(U * 0.12),
                                       int(U * 0.05), glow=self.c2)
                        spr = _row(img, lab)
                    lay.update(spr=spr, cx=W / 2, cy=top + spr.shape[0] / 2)
                elif t == "counter":
                    target = band * 1.05
                    lab = fit_text(ev["text"], int(W * 0.9), target * 0.3, int(U * 0.1), int(U * 0.045), glow=self.c2)
                    nsize = _size_for_height(str(ev.get("value") or "0"), target - lab.shape[0] * 0.8, int(U * 0.24))
                    lay.update(spr=lab, num_size=nsize, cx=W / 2)
                    num_h = trim(text_sprite("8,888", nsize)).shape[0]
                    lay["num_cy"] = top + num_h / 2
                    lay["cy"] = top + num_h * 0.95 + lab.shape[0] / 2
                else:
                    lay["mode"] = "free"
        if lay["mode"] == "free":
            spr = self._sprite(ev, head)
            if self.landscape and t == "text":  # keep big words clear of the captions
                spr = fit_text(ev["text"], int(W * 0.94), H * 0.5, int(U * 0.3), int(U * 0.1), fill=self.c1, glow=self.c2)
            lay["spr"] = spr
            lay["num_size"] = int(U * 0.22)
        self.layouts[ev["id"]] = lay
        return lay

    def _side_counter(self, ev: dict, lay: dict, side: int, room: float, hx: float, hs: float, top_lim: float) -> dict:
        W, H, U = self.W, self.H, self.U
        max_w = int(min(room * 0.9, W * 0.46))
        final = format_value(*parse_value(ev.get("value")))
        lab = fit_text(ev["text"], max_w, H * 0.12, int(U * 0.1), int(U * 0.045), glow=self.c2)
        nsize = int(U * 0.3)
        while nsize > 40 and trim(text_sprite(final + "8", nsize)).shape[1] > max_w:
            nsize = int(nsize * 0.92)
        num_h = trim(text_sprite(final, nsize)).shape[0]
        total = num_h + lab.shape[0]
        w = max(lab.shape[1], trim(text_sprite(final, nsize)).shape[1])
        cx = (hx - hs * 0.8) / 2 if side < 0 else hx + hs * 0.8 + (W - hx - hs * 0.8) / 2
        cx = min(max(cx, w / 2 + W * 0.03), W - w / 2 - W * 0.03)
        top = min(max(H * 0.45 - total / 2, top_lim), H * 0.95 - total)
        lay.update(mode="side", front=True, spr=lab, num_size=nsize, cx=cx, num_cy=top + num_h / 2,
                   cy=top + num_h + lab.shape[0] / 2)
        self.layouts[ev["id"]] = lay
        return lay

    def draw_behind(self, layer: np.ndarray, t: float, head: dict) -> list[tuple]:
        """Draw onto the background layer. Returns boxes of drawn elements (for QA)."""
        boxes = []
        self.front_ops: list[tuple] = []
        any_active = bool(self.active(t))
        self.particles.draw(layer, 0.6 if any_active else 0.25)
        hx, hy, hs = head["x"], head["y"], max(head["size"], self.U * 0.12)
        for ev in self.active(t):
            local = t - ev["t_in"]
            out_left = ev["t_out"] - t
            p_in = clamp01(local / 0.28)
            p_out = clamp01(out_left / 0.4)
            alpha = ease_out_cubic(p_out)  # every element fades out softly
            scale = 1.0
            dx = dy = 0.0
            anim = ev["anim"]
            if anim == "pop":
                scale = ease_out_back(p_in)
            elif anim == "zoom":  # settles in from slightly big, like a camera push
                q = ease_out_cubic(clamp01(local / 0.4))
                scale = 1 + 0.3 * (1 - q); alpha *= clamp01(local / 0.25)
            elif anim == "slide":
                dx = (1 - ease_out_cubic(p_in)) * self.W * 0.35; alpha *= clamp01(local / 0.2)
            elif anim == "fade":
                q = ease_out_cubic(clamp01(local / 0.5))
                alpha *= q; dy = (1 - q) * self.U * 0.04
            elif anim == "glitch":
                alpha *= 1.0 if local > 0.2 or self.rng.random() > 0.3 else 0.25
            scale *= 0.97 + 0.03 * p_out
            lay = self._layout(ev, head)
            spr = lay["spr"]
            if anim == "typewriter" and lay.get("lines"):
                spr = self._typed(ev, lay, local)
            elif anim == "glitch" and (local < 0.35 or (local % 1.7) < 0.1):
                spr = glitch_fx(spr, self.rng, 1.0 if local < 0.35 else 0.5)
            sh, sw = spr.shape[0] * scale, spr.shape[1] * scale
            num_cy = None
            if lay.get("backdrop") is not None:  # the picture replaces the wall behind the speaker
                a_bd = clamp01(local / 0.3) * clamp01(out_left / 0.25)
                bd = lay["backdrop"]
                z = 1.0 + 0.05 * clamp01(local / max(0.5, ev["t_out"] - ev["t_in"]))  # slow push-in
                bh, bw = bd.shape[:2]
                ch_, cw_ = int(bh / z), int(bw / z)
                y0, x0 = (bh - ch_) // 2, (bw - cw_) // 2
                crop = bd[y0:y0 + ch_, x0:x0 + cw_]
                frame_bd = cv2.resize(crop, (self.W, self.H), interpolation=cv2.INTER_LINEAR).astype(np.float32)
                layer *= (1 - a_bd)
                layer += frame_bd * a_bd
            if lay["mode"] == "side":
                bob = math.sin(local * 2.0) * self.U * 0.004
                cx, cy = lay["cx"], lay["cy"] + bob
                num_cy = lay.get("num_cy", 0) + bob
                if ev["anim"] == "slide":
                    dx *= 1 if lay["cx"] > self.W / 2 else -1
            elif lay["mode"] == "band":
                bob = math.sin(local * 2.0) * self.U * 0.004
                cx, cy = lay["cx"], lay["cy"] + bob
                num_cy = lay.get("num_cy", 0) + bob
                if ev["anchor"] == "behind_head":
                    glow_aura(layer, hx, hy, hs * 2.6, self.c2, 0.3 * alpha)
            else:
                bob = math.sin(local * 2.4) * self.U * 0.008
                above = max(sh / 2 + self.H * 0.04, hy - hs * 0.75 - sh * 0.25)
                if ev["anchor"] == "behind_head":
                    glow_aura(layer, hx, hy, hs * 2.6, self.c2, 0.35 * alpha)
                    if self.landscape:  # sit on top of the head, only the bottom of the letters tucked behind
                        cx, cy = self.W / 2, hy - hs * 0.62 - sh * 0.38 + bob
                    else:
                        cx, cy = self.W / 2 + (hx - self.W / 2) * 0.25, above + bob
                elif ev["anchor"] == "top_split":
                    cx, cy = self.W / 2, max(sh / 2 + self.H * 0.04, self.H * 0.17)
                elif not self.landscape:
                    cx, cy = self.W / 2 + (hx - self.W / 2) * 0.2, above + bob
                else:
                    side = self.side.setdefault(ev["id"], -1 if hx > self.W / 2 else 1)
                    cx = hx + side * (hs * 0.9 + sw * 0.5)
                    cy = hy - hs * 0.3 + bob
                    dx *= side
                cx, cy = cx + (hx - self.W / 2) * 0.08, cy
                extra_top = 0.0
                if ev["type"] == "counter":
                    extra_top = trim(text_sprite("8,888", lay["num_size"])).shape[0] * scale
                # keep the whole element (counter number included) on screen and above the captions
                top_margin = self.H * 0.04 if ev["anchor"] == "behind_head" else self.U * 0.15  # clear of the logo
                lo = top_margin + extra_top + sh / 2
                hi_ = self.H * (0.7 if self.landscape else 0.6) - sh / 2
                cy = min(max(cy, lo), max(lo, hi_))
                cx = min(max(cx, sw / 2 + self.W * 0.02), self.W - sw / 2 - self.W * 0.02)
                num_cy = cy - sh / 2 - extra_top / 2
            cx, cy = cx + dx, cy + dy
            front = bool(lay.get("front"))
            put = (lambda *a: self.front_ops.append(a)) if front else (lambda *a: blend(layer, *a))
            put(spr, cx, cy, scale, alpha)
            if ev["type"] == "counter" and ev.get("value"):
                prefix, target, suffix, dec = parse_value(ev["value"])
                cur = target * ease_out_cubic(clamp01(local / 1.0))
                num = format_value(prefix, cur, suffix, dec)
                if prefix.strip() in tuple(CURRENCY) and prefix.strip():
                    self._money_rain(layer, prefix.strip(), cx, num_cy, local, alpha)
                put(trim(text_sprite(num, lay["num_size"], fill=self.c1, glow=self.c2)),
                    cx, num_cy + dy, scale, alpha)
            elif ev["type"] == "arrow":
                blend(layer, self._label(ev), cx, cy - 110, scale, alpha)
            boxes.append((ev["id"] + ("!front" if front else ""), cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2))
        return boxes

    def _typed(self, ev: dict, lay: dict, local: float) -> np.ndarray:
        """Typewriter reveal: characters appear one by one, line after line (cached per step)."""
        lines = lay["lines"]
        total = sum(len(l["text"]) for l in lines)
        n = int(round(total * clamp01(local / type_duration(ev.get("text") or ""))))
        key = (ev["id"], n)
        cache = self.__dict__.setdefault("_typed_cache", {})
        if key in cache:
            return cache[key]
        full = lay["spr"]
        out = full.copy()
        left = n
        for l in lines:
            k = max(0, min(len(l["text"]), left))
            left -= len(l["text"])
            x_cut = int(l["w"] * k / max(1, len(l["text"]))) if k < len(l["text"]) else full.shape[1]
            out[l["y0"]:l["y1"], x_cut:, 3] = 0
            if 0 < k < len(l["text"]) or (k == 0 and left + len(l["text"]) == 0):
                cur_x = min(full.shape[1] - 4, x_cut + 2)  # blinking-free cursor bar
                out[l["y0"] + 6:l["y1"] - 6, cur_x:cur_x + max(3, self.U // 180)] = (*self.c1, 255)
        if len(cache) > 400:
            cache.clear()
        cache[key] = out
        return out

    def _money_rain(self, layer: np.ndarray, sym: str, cx: float, cy: float, local: float, alpha: float) -> None:
        """Currency symbols drifting up behind a money counter."""
        spr = self.__dict__.setdefault("_sym", {}).get(sym)
        if spr is None:
            spr = trim(text_sprite(sym, int(self.U * 0.07), fill=self.c1, glow=self.c2))
            self._sym[sym] = spr
        rng = np.random.default_rng(7)
        for i in range(9):
            x0 = cx + rng.uniform(-0.22, 0.22) * self.W
            speed = rng.uniform(0.12, 0.3) * self.H
            ph = rng.uniform(0, 1.5)
            y = cy + self.H * 0.25 - ((local + ph) * speed) % (self.H * 0.6)
            a = alpha * 0.55 * clamp01(local / 0.4)
            blend(layer, spr, x0, y, rng.uniform(0.6, 1.1), a)

    def draw_front(self, frame: np.ndarray) -> None:
        """Elements drawn over the speaker (after the cut-out), e.g. counters in the reel's video box."""
        for spr, cx, cy, scale, alpha in getattr(self, "front_ops", []):
            blend(frame, spr, cx, cy, scale, alpha)

    @staticmethod
    def _wants_backdrop(ev: dict) -> bool:
        """Photos make good backgrounds; icons and logos (transparent cut-outs) stay as floating pictures."""
        if ev.get("style") in ("backdrop", "sprite"):
            return ev["style"] == "backdrop"
        try:
            a = np.asarray(Image.open(ev.get("original") or ev["file"]).convert("RGBA"))[..., 3]
            return (a > 16).mean() > 0.97
        except Exception:
            return False

    @lru_cache(maxsize=64)
    def _label_cached(self, text: str) -> np.ndarray:
        return text_sprite(text, int(self.U * 0.08), glow=self.c2, max_w=int(self.W * 0.5))

    def _label(self, ev):
        return self._label_cached(ev["text"])


def _stack(top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    """Image with a caption under it."""
    w = max(top.shape[1], bottom.shape[1])
    gap = -int(bottom.shape[0] * 0.3)
    out = np.zeros((top.shape[0] + bottom.shape[0] + gap, w, 4), np.uint8)
    out[:top.shape[0], (w - top.shape[1]) // 2:(w - top.shape[1]) // 2 + top.shape[1]] = top
    y = top.shape[0] + gap
    region = out[y:y + bottom.shape[0], (w - bottom.shape[1]) // 2:(w - bottom.shape[1]) // 2 + bottom.shape[1]]
    a = bottom[..., 3:4] / 255.0
    region[..., :3] = (bottom[..., :3] * a + region[..., :3] * (1 - a)).astype(np.uint8)
    region[..., 3] = np.maximum(region[..., 3], bottom[..., 3])
    return out


def trim(spr: np.ndarray, margin: int = 6) -> np.ndarray:
    """Crop a sprite to its visible pixels (text sprites carry big padding)."""
    ys, xs = np.nonzero(spr[..., 3] > 8)
    if not len(ys):
        return spr
    y0, y1 = max(0, ys.min() - margin), min(spr.shape[0], ys.max() + margin + 1)
    x0, x1 = max(0, xs.min() - margin), min(spr.shape[1], xs.max() + margin + 1)
    return np.ascontiguousarray(spr[y0:y1, x0:x1])


def fit_text(text: str, max_w: int, max_h: float, start: int, min_size: int, **kw) -> np.ndarray:
    """Biggest text block (1 line, or 2 balanced lines) that fits max_w x max_h."""
    size = start
    while True:
        one = trim(text_sprite(text, size, **kw))
        cands = [one] + ([trim(text_block_sprite(text, size, max_w, **kw))] if len(text.split()) > 1 else [])
        for spr in cands:
            if spr.shape[0] <= max_h and spr.shape[1] <= max_w:
                return spr
        if size <= min_size:
            return trim(text_block_sprite(text, size, max_w, **kw))
        size = max(min_size, int(size * 0.92))


def _size_for_height(text: str, max_h: float, start: int) -> int:
    size = start
    while size > 30 and trim(text_sprite(text, size)).shape[0] > max_h:
        size = int(size * 0.9)
    return size


def _row(left: np.ndarray, right: np.ndarray, gap: int = 18) -> np.ndarray:
    """Image with its label beside it, vertically centred."""
    h = max(left.shape[0], right.shape[0])
    out = np.zeros((h, left.shape[1] + gap + right.shape[1], 4), np.uint8)
    for spr, x in ((left, 0), (right, left.shape[1] + gap)):
        y = (h - spr.shape[0]) // 2
        out[y:y + spr.shape[0], x:x + spr.shape[1]] = spr
    return out


def backdrop_image(path: Path, W: int, H: int, tint=(232, 37, 43)) -> np.ndarray:
    """A photo filling the frame behind the speaker: cover-cropped, softly blurred and darkened so the
    speaker and the big words stay the hero (the look of big podcast-clip channels)."""
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    h, w = img.shape[:2]
    s = max(W * 1.06 / w, H * 1.06 / h)
    img = cv2.resize(img, (max(W, int(w * s)), max(H, int(h * s))), interpolation=cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    y0, x0 = (h - int(H * 1.06)) // 2, (w - int(W * 1.06)) // 2
    img = img[max(0, y0):max(0, y0) + int(H * 1.06), max(0, x0):max(0, x0) + int(W * 1.06)]
    img = cv2.GaussianBlur(img, (0, 0), max(3.0, min(W, H) / 130))
    lum = img.mean(axis=2, keepdims=True)
    img = img * 0.55 + lum * 0.15  # a little desaturated
    img = img * 0.5 + np.array(tint, np.float32)[None, None] * 0.05  # darker, faint brand tint
    yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
    v = 1 - 0.45 * (((xx - img.shape[1] / 2) / (img.shape[1] / 2)) ** 2 + ((yy - img.shape[0] / 2) / (img.shape[0] / 2)) ** 2) / 2
    return np.clip(img * v[..., None], 0, 255).astype(np.uint8)


def stack_text(label: str, max_w: int, max_h: int, U: int, accent=(232, 37, 43), typewriter: bool = False,
               meta: bool = False):
    """Stacked kinetic words: short white words, the key word huge in the accent colour.
    Mark key words with *stars*; otherwise the longest word is the key word."""
    words = label.split()
    if not words:
        return np.zeros((2, 2, 4), np.uint8)
    marked = [w.startswith("*") and w.endswith("*") for w in words]
    words = [w.strip("*") for w in words]
    if not any(marked):
        k = max(range(len(words)), key=lambda i: len(words[i]))
        marked = [i == k for i in range(len(words))]
    # group into lines: key words on their own line, small words joined (max ~12 chars)
    lines: list[tuple[str, bool]] = []
    buf: list[str] = []
    for w, m in zip(words, marked):
        if m:
            if buf:
                lines.append((" ".join(buf), False)); buf = []
            lines.append((w, True))
        else:
            if buf and len(" ".join(buf + [w])) > 12:
                lines.append((" ".join(buf), False)); buf = []
            buf.append(w)
    if buf:
        lines.append((" ".join(buf), False))
    lines = lines[:4]
    scale = 1.0
    while True:
        sprs = []
        for txt, key in lines:
            size = int(U * (0.26 if key else 0.14) * scale)
            fill = accent if key else (255, 255, 255)
            shown = txt if (not txt.isascii() or typewriter) else txt.upper()
            sprs.append(trim(text_sprite(shown, size, fill=fill, stroke=(0, 0, 0), max_w=max_w,
                                         display="type" if typewriter else True), size // 10))
        h = sum(x.shape[0] for x in sprs)
        if h <= max_h or scale < 0.45:
            break
        scale *= 0.88
    w = max(x.shape[1] for x in sprs)
    out = np.zeros((h, w, 4), np.uint8)
    y = 0
    info = []
    for (txt, _), x in zip(lines, sprs):  # left-aligned like the reference edits
        out[y:y + x.shape[0], :x.shape[1]] = np.maximum(out[y:y + x.shape[0], :x.shape[1]], x)
        info.append({"text": txt, "y0": y, "y1": y + x.shape[0], "w": x.shape[1]})
        y += x.shape[0]
    return (out, info) if meta else out


def glitch_fx(spr: np.ndarray, rng, amount: float = 1.0) -> np.ndarray:
    """RGB split plus a few torn horizontal slices."""
    h, w = spr.shape[:2]
    s = int(2 + amount * w * 0.02)
    out = spr.copy()
    out[..., 0] = np.roll(spr[..., 0], s, axis=1)
    out[..., 2] = np.roll(spr[..., 2], -s, axis=1)
    out[..., 3] = np.maximum.reduce([spr[..., 3], np.roll(spr[..., 3], s, axis=1), np.roll(spr[..., 3], -s, axis=1)])
    for _ in range(int(2 + 3 * amount)):
        y0 = int(rng.integers(0, max(1, h - 4)))
        hh = int(rng.integers(3, max(4, h // 6)))
        out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], int(rng.integers(-s * 3, s * 3 + 1)), axis=1)
    return out
