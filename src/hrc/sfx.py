"""SFX library. Your own files in assets/sfx/<type>/ are used first; otherwise HRC synthesises
royalty-free cinematic sounds with numpy: whooshes, booms, risers, soft ticks, typewriter keys,
money-counter ticks with a chime, shimmer for fades, and soft glitches."""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

SR = 48000
TYPES = ("whoosh", "hit", "riser", "pop", "glitch", "impact", "swish", "type", "ding", "count", "shimmer")


def _env(n, attack, release):
    e = np.ones(n)
    a, r = int(n * attack), int(n * release)
    if a:
        e[:a] = np.linspace(0, 1, a) ** 2
    if r:
        e[-r:] = np.linspace(1, 0, r) ** 2
    return e


def _lowpass(x, alpha):
    y = np.empty_like(x); acc = 0.0
    for i, v in enumerate(x):
        acc += alpha[i] * (v - acc) if hasattr(alpha, "__len__") else alpha * (v - acc)
        y[i] = acc
    return y


LIB_VERSION = 2  # bump when the synthesised sounds change, so old hrc_* files get rebuilt


def _bandnoise(rng, n, lo, hi):
    """Noise band-limited between two (per-sample) cutoffs, as fractions of the sample rate."""
    x = rng.standard_normal(n)
    return _lowpass(x, hi) - _lowpass(x, lo)


def _reverb(x: np.ndarray, rng, seconds: float = 1.4, wet: float = 0.35, damp: float = 4.0) -> np.ndarray:
    """Cheap hall reverb: convolve with decaying, darkened noise."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    ir = _lowpass(rng.standard_normal(n), 0.18) * np.exp(-t * damp)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    m = len(x) + n - 1
    size = 1 << (m - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:m]
    out = np.zeros(m)
    out[:len(x)] += x * (1 - wet)
    out += y * wet * (np.max(np.abs(x)) / (np.max(np.abs(y)) + 1e-9))
    return out


def _bell(t, f0, partials=((1, 1.0), (2.76, 0.5), (5.4, 0.25), (8.9, 0.12)), decay=5.0):
    return sum(a * np.sin(2 * np.pi * f0 * p * t) * np.exp(-t * decay * p ** 0.5) for p, a in partials)


def synth(kind: str, seed: int = 0) -> np.ndarray:
    """Cinematic, soft-edged sounds. Mono; panning is done in the mix."""
    rng = np.random.default_rng(seed)
    if kind in ("whoosh", "swish"):
        dur = 0.8 if kind == "whoosh" else 0.4
        n = int(SR * dur); t = np.linspace(0, 1, n)
        bell_curve = np.exp(-((t - 0.55) / 0.22) ** 2)  # swells in, peaks, falls away (pass-by)
        hi = 0.02 + 0.22 * bell_curve
        x = _bandnoise(rng, n, hi * 0.25, hi) * bell_curve
        x = _reverb(x, rng, 0.9, 0.3)
    elif kind == "hit":  # deep cinematic boom
        n = int(SR * 1.6); t = np.arange(n) / SR
        f = 38 + 50 * np.exp(-t * 9)
        sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 2.6)
        thump = _lowpass(rng.standard_normal(n), 0.04) * np.exp(-t * 30) * 2.5
        x = np.tanh((sub + thump) * 1.6)
        x = _reverb(x, rng, 1.8, 0.25, 3.0)
    elif kind == "impact":  # boom with a metallic shimmer tail
        n = int(SR * 2.0); t = np.arange(n) / SR
        boom = np.sin(2 * np.pi * np.cumsum(34 + 60 * np.exp(-t * 7)) / SR) * np.exp(-t * 2.2)
        metal = _bell(t, 310 + 40 * rng.random(), decay=3.0) * 0.12
        crack = _bandnoise(rng, n, 0.05, 0.4) * np.exp(-t * 35) * 0.5
        x = _reverb(np.tanh(1.4 * boom) + metal + crack, rng, 2.2, 0.35, 2.5)
    elif kind == "riser":  # reverse-swell into the hit
        n = int(SR * 1.4); t = np.linspace(0, 1, n)
        air = _bandnoise(rng, n, 0.01 + 0.1 * t, 0.05 + 0.4 * t) * t ** 2.5
        tone = sum(np.sin(2 * np.pi * np.cumsum(f0 * (1 + 1.5 * t ** 2)) / SR) for f0 in (220, 277, 330)) * 0.08 * t ** 3
        x = (air + tone) * _env(n, 0, 0.02)
    elif kind == "pop":  # soft UI tick, not a cartoon pop
        n = int(SR * 0.09); t = np.arange(n) / SR
        x = np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 90) * 0.6 + _bandnoise(rng, n, 0.08, 0.3) * np.exp(-t * 200)
        x = _reverb(x, rng, 0.4, 0.2, 8)
    elif kind == "type":  # one typewriter key
        n = int(SR * 0.07); t = np.arange(n) / SR
        click = _bandnoise(rng, n, 0.05 + 0.03 * rng.random(), 0.35) * np.exp(-t * 260)
        thock = np.sin(2 * np.pi * (140 + 40 * rng.random()) * t) * np.exp(-t * 90) * 0.5
        x = click + thock
    elif kind == "ding":  # carriage-return bell
        n = int(SR * 0.9); t = np.arange(n) / SR
        x = _bell(t, 1760, decay=4.0)
    elif kind == "count":  # rising money counter: accelerating ticks into a soft chime
        n = int(SR * 1.15); t = np.arange(n) / SR
        x = np.zeros(n)
        k, pos = 0, 0.0
        while pos < 0.95:
            i0 = int(pos * SR); m = min(int(0.02 * SR), n - i0)
            tt = np.arange(m) / SR
            x[i0:i0 + m] += np.sin(2 * np.pi * (2400 + 30 * k) * tt) * np.exp(-tt * 400) * 0.5
            pos += max(0.025, 0.11 * (1 - pos) ** 1.5); k += 1
        i0 = int(0.95 * SR)
        x[i0:] += _bell(t[: n - i0], 1318.5, decay=6.0) * 0.6 + _bell(t[: n - i0], 1975.5, decay=7.0) * 0.4
        x = _reverb(x, rng, 0.8, 0.25, 6)
    elif kind == "shimmer":  # airy sparkle for fades
        n = int(SR * 1.0); t = np.linspace(0, 1, n)
        x = _bandnoise(rng, n, 0.15, 0.45) * np.sin(np.pi * t) ** 2 * 0.5
        for _ in range(9):
            i0 = int(rng.random() * n * 0.7); m = int(SR * 0.25)
            tt = np.arange(min(m, n - i0)) / SR
            x[i0:i0 + len(tt)] += np.sin(2 * np.pi * rng.uniform(2500, 5200) * tt) * np.exp(-tt * 18) * 0.15
        x = _reverb(x, rng, 1.2, 0.4, 3)
    elif kind == "glitch":  # short digital stutter, softened
        n = int(SR * 0.3)
        x = np.zeros(n); pos = 0
        while pos < n:
            seg = int(rng.integers(300, 1800)); m = min(seg, n - pos)
            t = np.arange(m) / SR
            if rng.random() < 0.5:
                x[pos:pos + m] = np.sign(np.sin(2 * np.pi * rng.integers(150, 1200) * t)) * 0.35
            elif rng.random() < 0.7:
                x[pos:pos + m] = _bandnoise(rng, m, 0.05, 0.3) * 0.5
            pos += seg
        x = np.round(x * 12) / 12  # bit-crush
        x = _lowpass(x, 0.35) * _env(n, 0.02, 0.2)
    else:
        raise ValueError(kind)
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.9
    return x.astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    stereo = np.stack([pcm, pcm], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    return path


def ensure_library(sfx_dir: Path, variants: int = 3) -> None:
    marker = sfx_dir / f".hrc_sfx_v{LIB_VERSION}"
    fresh = not marker.exists()
    for kind in TYPES:
        d = sfx_dir / kind
        if fresh:  # rebuild HRC's own sounds; never touch the user's files
            for old in d.glob("hrc_*.wav"):
                old.unlink()
        if any(d.glob("*.wav")) or any(d.glob("*.mp3")):
            continue
        for v in range(variants if kind != "type" else 6):
            write_wav(d / f"hrc_{kind}_{v + 1}.wav", synth(kind, seed=v))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def pick(sfx_dir: Path, kind: str, rng: random.Random) -> Path | None:
    d = sfx_dir / kind
    files = sorted([*d.glob("*.wav"), *d.glob("*.mp3"), *d.glob("*.m4a")])
    user = [f for f in files if not f.name.startswith("hrc_")]
    pool = user or files
    return rng.choice(pool) if pool else None
