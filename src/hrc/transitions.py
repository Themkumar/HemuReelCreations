"""Transition library. Each runs over a few frames around a cut and has its own SFX.

`p` goes from -1 (start of the transition, old shot) through 0 (the cut) to +1 (end, new shot).
"""
from __future__ import annotations

import random

import cv2
import numpy as np

HALF = 5  # frames on each side of the cut

SFX = {"zoom_punch": "hit", "whip_pan": "whoosh", "glitch": "glitch", "rgb_split": "swish",
       "light_flash": "impact", "spin": "whoosh", "slide": "swish", "shape_wipe": "pop",
       "film_burn": "riser", "hard": None}
MOOD_WEIGHTS = {
    "energetic": {"zoom_punch": 3, "whip_pan": 3, "glitch": 2, "rgb_split": 2, "spin": 2, "light_flash": 1},
    "tense": {"glitch": 3, "rgb_split": 3, "zoom_punch": 2, "light_flash": 1, "whip_pan": 1},
    "emotional": {"film_burn": 3, "light_flash": 2, "shape_wipe": 1, "slide": 1},
    "funny": {"spin": 3, "zoom_punch": 3, "shape_wipe": 2, "whip_pan": 2},
    "inspirational": {"light_flash": 3, "whip_pan": 2, "zoom_punch": 2, "film_burn": 2, "shape_wipe": 1},
    "calm": {"slide": 3, "film_burn": 2, "shape_wipe": 2},
}


def choose(n_cuts: int, mood: str, library: list[str], seed: int = 0, fast_cuts: set[int] | None = None) -> list[str]:
    """Pick one transition per cut, weighted by mood, never the same twice in a row."""
    rng = random.Random(seed)
    weights = MOOD_WEIGHTS.get(mood, {})
    out, prev = [], None
    for i in range(n_cuts):
        if fast_cuts and i in fast_cuts:
            out.append("hard"); prev = "hard"; continue
        pool = [t for t in library if t != prev]
        w = [weights.get(t, 1) for t in pool]
        prev = rng.choices(pool, w)[0]
        out.append(prev)
    return out


def _zoom(img, s, cx=None, cy=None):
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((cx or w / 2, cy or h / 2), 0, s)
    return cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REFLECT)


def _hblur(img, k):
    k = int(k)
    return img if k < 3 else cv2.blur(img, (k, 1))


def apply(name: str, frame: np.ndarray, p: float, prev_frame: np.ndarray | None = None, seed: int = 0) -> np.ndarray:
    a = 1 - abs(p)  # 1 at the cut, 0 at the edges
    h, w = frame.shape[:2]
    if name == "zoom_punch":
        return _zoom(frame, 1 + 0.22 * a)
    if name == "whip_pan":
        shift = int(np.sign(p or 1) * a * w * 0.35)
        return _hblur(np.roll(frame, -shift if p < 0 else shift, axis=1), a * w * 0.12)
    if name == "rgb_split":
        o = int(a * 28)
        out = frame.copy()
        out[..., 0] = np.roll(frame[..., 0], o, axis=1)
        out[..., 2] = np.roll(frame[..., 2], -o, axis=1)
        return out
    if name == "glitch":
        rng = np.random.default_rng(seed + int(p * 100))
        out = apply("rgb_split", frame, p)
        for _ in range(int(a * 10)):
            y = rng.integers(0, h - 40); bh = rng.integers(10, 80)
            out[y:y + bh] = np.roll(out[y:y + bh], rng.integers(-80, 80), axis=1)
        return out
    if name == "light_flash":
        k = a ** 2
        return (frame * (1 - k) + 255 * k).astype(np.uint8)
    if name == "spin":
        m = cv2.getRotationMatrix2D((w / 2, h / 2), p * 30 * (1 if p < 0 else -1) * -1, 1 + 0.3 * a)
        return cv2.GaussianBlur(cv2.warpAffine(frame, m, (w, h), borderMode=cv2.BORDER_REFLECT), (0, 0), 0.1 + a * 6)
    if name == "slide":
        shift = int(a * h * 0.25) * (1 if p < 0 else -1)
        return cv2.blur(np.roll(frame, shift, axis=0), (1, max(1, int(a * 40))))
    if name == "shape_wipe":
        if p <= 0 or prev_frame is None:
            return frame
        r = int((p ** 1.5) * np.hypot(w, h) / 2)
        mask = np.zeros((h, w), np.uint8)
        cv2.circle(mask, (w // 2, int(h * 0.4)), r, 255, -1, lineType=cv2.LINE_AA)
        m = (mask / 255.0)[..., None]
        ring = np.zeros_like(mask); cv2.circle(ring, (w // 2, int(h * 0.4)), r, 255, 18, lineType=cv2.LINE_AA)
        out = frame * m + prev_frame * (1 - m)
        out[ring > 0] = (255, 212, 0)
        return out.astype(np.uint8)
    if name == "film_burn":
        yy, xx = np.ogrid[:h, :w]
        g = np.clip(1 - np.hypot(xx - w * 0.85, yy - h * 0.2) / (w * 1.1), 0, 1)
        k = (g * a * 1.4)[..., None]
        burn = np.array([255, 120, 30], np.float32)
        return np.clip(frame + burn * k, 0, 255).astype(np.uint8)
    return frame
