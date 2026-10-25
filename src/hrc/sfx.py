"""SFX library. Your own files in assets/sfx/<type>/ are used first; otherwise HRC
synthesises royalty-free whooshes, hits, risers, pops, glitches and impacts with numpy."""
from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

SR = 48000
TYPES = ("whoosh", "hit", "riser", "pop", "glitch", "impact", "swish")


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


def synth(kind: str, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind in ("whoosh", "swish"):
        dur = 0.55 if kind == "whoosh" else 0.3
        n = int(SR * dur)
        noise = rng.standard_normal(n)
        sweep = np.linspace(0.02, 0.35, n) ** 1.5 if kind == "whoosh" else np.linspace(0.3, 0.05, n)
        x = _lowpass(noise, sweep) * _env(n, 0.55, 0.4)
    elif kind == "hit":
        n = int(SR * 0.45); t = np.arange(n) / SR
        f = 120 * np.exp(-t * 18) + 45
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9) + 0.25 * rng.standard_normal(n) * np.exp(-t * 40)
    elif kind == "impact":
        n = int(SR * 1.1); t = np.arange(n) / SR
        boom = np.sin(2 * np.pi * np.cumsum(70 * np.exp(-t * 6) + 32) / SR) * np.exp(-t * 3.5)
        crack = _lowpass(rng.standard_normal(n), 0.5) * np.exp(-t * 25)
        x = boom + 0.6 * crack
    elif kind == "riser":
        n = int(SR * 1.6); t = np.arange(n) / SR
        f = 200 + 1400 * (t / t[-1]) ** 2
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.35
        air = _lowpass(rng.standard_normal(n), np.linspace(0.02, 0.5, n)) * 0.8
        x = (tone + air) * np.linspace(0, 1, n) ** 2 * _env(n, 0, 0.03)
    elif kind == "pop":
        n = int(SR * 0.12); t = np.arange(n) / SR
        x = np.sin(2 * np.pi * np.cumsum(900 * np.exp(-t * 60) + 300) / SR) * np.exp(-t * 45)
    elif kind == "glitch":
        n = int(SR * 0.35)
        x = np.zeros(n); pos = 0
        while pos < n:
            seg = rng.integers(200, 2500)
            kind_ = rng.integers(0, 3)
            t = np.arange(min(seg, n - pos)) / SR
            if kind_ == 0:
                x[pos:pos + len(t)] = np.sign(np.sin(2 * np.pi * rng.integers(80, 2000) * t)) * 0.5
            elif kind_ == 1:
                x[pos:pos + len(t)] = rng.standard_normal(len(t)) * 0.6
            pos += seg
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
    for kind in TYPES:
        d = sfx_dir / kind
        if any(d.glob("*.wav")) or any(d.glob("*.mp3")):
            continue
        for v in range(variants):
            write_wav(d / f"hrc_{kind}_{v + 1}.wav", synth(kind, seed=v))


def pick(sfx_dir: Path, kind: str, rng: random.Random) -> Path | None:
    d = sfx_dir / kind
    files = sorted([*d.glob("*.wav"), *d.glob("*.mp3"), *d.glob("*.m4a")])
    user = [f for f in files if not f.name.startswith("hrc_")]
    pool = user or files
    return rng.choice(pool) if pool else None
