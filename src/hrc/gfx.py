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
ANIM_IN = ("pop", "slide", "glitch", "zoom")
SFX_FOR_ANIM = {"pop": "pop", "slide": "whoosh", "glitch": "glitch", "zoom": "hit"}


# ---------- easing ----------
def ease_out_back(x: float, s: float = 1.7) -> float:
    x -= 1
    return x * x * ((s + 1) * x + s) + 1


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


# ---------- fonts and sprites ----------
def font_for(text: str, size: int, weight: int = 900) -> ImageFont.FreeTypeFont:
    name = "NotoSans"
    for (lo, hi), fam in SCRIPT_FONT:
        if any(lo <= ord(c) <= hi for c in text):
            name = fam
            break
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


def text_sprite(text: str, size: int, fill=(255, 255, 255), stroke=(0, 0, 0), glow=None, max_w: int = 0) -> np.ndarray:
    font = font_for(text, size)
    # shrink to fit width
    while max_w and size > 24:
        bbox = font.getbbox(text, stroke_width=size // 14)
        if bbox[2] - bbox[0] <= max_w:
            break
        size = int(size * 0.9)
        font = font_for(text, size)
    sw = max(2, size // 14)
    bbox = font.getbbox(text, stroke_width=sw)
    pad = size // 2
    w, h = bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    org = (pad - bbox[0], pad - bbox[1])
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
    lines = [text_sprite(" ".join(words[:best]), size, max_w=max_w, **kw),
             text_sprite(" ".join(words[best:]), size, max_w=max_w, **kw)]
    w = max(l.shape[1] for l in lines)
    gap = -int(size * 0.35)
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
        anim = rng.choice([a for a in ANIM_IN if a != last_anim])
        last_anim = anim
        kind = vm.get("type", "text")
        asset = assets.get(str(k), {}) if isinstance(assets, dict) else {}
        if kind in ("image", "broll") and not asset.get("file"):
            kind = "text"  # fallback motion graphic
        anchor = {"image": "beside_head", "broll": "top_split", "text": "behind_head", "counter": "beside_head",
                  "arrow": "beside_head", "map": "beside_head"}.get(kind, "beside_head")
        events.append({
            "id": f"g{k:02d}", "type": kind, "anchor": anchor, "anim": anim,
            "t_in": round(t_in, 3), "t_out": round(min(duration, max(hit["end"] + 0.6, t_in + hold)), 3),
            "text": vm.get("label") or vm["phrase"], "value": vm.get("value"),
            "file": asset.get("file"), "label": vm.get("label") if kind in ("image", "broll") else None, "sfx_in": SFX_FOR_ANIM[anim], "major": True,
        })
    events.sort(key=lambda e: e["t_in"])
    for a, b in zip(events, events[1:]):  # no overlap between major elements
        if a["t_out"] > b["t_in"] - 0.12:
            a["t_out"] = round(max(a["t_in"] + 0.6, b["t_in"] - 0.12), 3)
    return [e for e in events if e["t_out"] - e["t_in"] >= 0.5]


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
                lab = text_sprite(ev["label"], int(self.U * 0.075), glow=self.c2, max_w=int(mw))
                spr = _stack(spr, lab)
        elif t == "text":
            spr = text_block_sprite(ev["text"], int(self.U * 0.3), int(W * 0.94), fill=self.c1, glow=self.c2)
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

    def draw_behind(self, layer: np.ndarray, t: float, head: dict) -> list[tuple]:
        """Draw onto the background layer. Returns boxes of drawn elements (for QA)."""
        boxes = []
        any_active = bool(self.active(t))
        self.particles.draw(layer, 0.6 if any_active else 0.25)
        hx, hy, hs = head["x"], head["y"], max(head["size"], self.U * 0.12)
        for ev in self.active(t):
            local = t - ev["t_in"]
            out_left = ev["t_out"] - t
            p_in = clamp01(local / 0.28)
            p_out = clamp01(out_left / 0.22)
            alpha = min(1.0, p_out * 1.2)
            scale = 1.0
            dx = dy = 0.0
            if ev["anim"] == "pop":
                scale = ease_out_back(p_in)
            elif ev["anim"] == "zoom":
                scale = 1 + 1.4 * (1 - ease_out_cubic(p_in)); alpha *= p_in
            elif ev["anim"] == "slide":
                dx = (1 - ease_out_cubic(p_in)) * self.W * 0.6
            elif ev["anim"] == "glitch":
                alpha *= 1.0 if p_in >= 1 or self.rng.random() > 0.35 else 0.0
                dx = self.rng.uniform(-30, 30) * (1 - p_in)
            scale *= 0.7 + 0.3 * p_out
            spr = self._sprite(ev, head)
            bob = math.sin(local * 2.4) * 10
            sh = spr.shape[0] * scale
            above = max(sh / 2 + self.H * 0.04, hy - hs * 0.75 - sh * 0.25)  # sits over the head, bottom tucked behind it
            if ev["anchor"] == "behind_head":
                glow_aura(layer, hx, hy, hs * 2.6, self.c2, 0.35 * alpha)
                cx, cy = self.W / 2 + (hx - self.W / 2) * 0.25, above + bob
            elif ev["anchor"] == "top_split":
                cx, cy = self.W / 2, max(sh / 2 + self.H * 0.04, self.H * 0.17)
            elif not self.landscape:  # portrait has no room beside the head: float above it
                cx, cy = self.W / 2 + (hx - self.W / 2) * 0.2, above + bob
            else:
                side = self.side.setdefault(ev["id"], -1 if hx > self.W / 2 else 1)
                cx = hx + side * (hs * 0.9 + spr.shape[1] * 0.5 * scale)
                cx = min(max(cx, spr.shape[1] * 0.5), self.W - spr.shape[1] * 0.5)
                cy = hy - hs * 0.3 + bob
                dx *= side
            cx, cy = cx + dx + (hx - self.W / 2) * 0.08, cy + dy  # parallax with the head
            blend(layer, spr, cx, cy, scale, alpha)
            if ev["type"] == "counter" and ev.get("value"):
                try:
                    target = float(str(ev["value"]).replace(",", "").rstrip("%+"))
                    suffix = "%" if str(ev["value"]).endswith("%") else ("+" if str(ev["value"]).endswith("+") else "")
                    cur = target * ease_out_cubic(clamp01(local / 1.0))
                    num = f"{int(cur):,}{suffix}" if target >= 10 else f"{cur:.1f}{suffix}"
                    blend(layer, text_sprite(num, int(self.U * 0.22), fill=self.c1, glow=self.c2),
                          cx, cy - spr.shape[0] * 0.9, scale, alpha)
                except ValueError:
                    pass
            elif ev["type"] == "arrow":
                blend(layer, self._label(ev), cx, cy - 110, scale, alpha)
            h, w = spr.shape[:2]
            boxes.append((ev["id"], cx - w * scale / 2, cy - h * scale / 2, cx + w * scale / 2, cy + h * scale / 2))
        return boxes

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
