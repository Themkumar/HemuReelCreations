"""Second timing pass ("hrc realign"): fix words that the first alignment squashed together.

WhisperX sometimes gives a run of low-confidence words almost the same start time (six words
inside 0.2 s, then one word that lasts 0.6 s). Graphics keyed to those words land early or late.

For each bad run we:
1. try WhisperX again on just that cue's audio (with padding), which often succeeds alone;
2. otherwise spread the run's words over the time where speech energy actually is, weighting
   each word by its spoken length, and put every boundary on the nearest dip in energy.

We also save a speech-onset curve (cache/onsets.npy, 100 values per second) so graphics can land
on the exact frame where a word's sound starts.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from . import log
from .align import weight
from .util import read_json, write_json

HOP = 0.01  # seconds per envelope value


# ---------- audio features ----------
def load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        sr, n, ch, sw = f.getframerate(), f.getnframes(), f.getnchannels(), f.getsampwidth()
        raw = f.readframes(n)
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    y = np.frombuffer(raw, dt).astype(np.float32) / float(np.iinfo(dt).max)
    if ch > 1:
        y = y.reshape(-1, ch).mean(1)
    return y, sr


def speech_envelope(y: np.ndarray, sr: int, lo: float = 300.0, hi: float = 3000.0) -> np.ndarray:
    """Log energy in the speech band, one value per 10 ms, scaled to 0..1."""
    win, hop = int(0.025 * sr), int(HOP * sr)
    n = max(1, 1 + (len(y) - win) // hop)
    out = np.zeros(n, np.float32)
    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = (freqs >= lo) & (freqs <= hi)
    step = 4096  # frames per batch, keeps memory small on an hour of audio
    for s in range(0, n, step):
        idx = np.arange(s, min(n, s + step))[:, None] * hop + np.arange(win)[None, :]
        idx = np.minimum(idx, len(y) - 1)
        spec = np.abs(np.fft.rfft(y[idx] * window, axis=1)) ** 2
        out[s:s + len(idx)] = np.log10(spec[:, band].sum(1) + 1e-9)
    k = np.ones(3, np.float32) / 3
    out = np.convolve(out, k, mode="same")
    lo_v, hi_v = np.percentile(out, 5), np.percentile(out, 99)
    return np.clip((out - lo_v) / max(1e-6, hi_v - lo_v), 0, 1)


def onset_strength(env: np.ndarray) -> np.ndarray:
    """How sharply speech energy rises at each 10 ms step."""
    d = np.diff(env, prepend=env[:1])
    d = np.maximum(d, 0)
    return np.convolve(d, np.array([0.25, 0.5, 0.25], np.float32), mode="same")


def nearest_onset(t: float, onset: np.ndarray, window: float = 0.08, floor: float = 0.04) -> float:
    """The strongest sound onset within +-window of t (or t itself when there is none)."""
    i0, i1 = max(0, int((t - window) / HOP)), min(len(onset), int((t + window) / HOP) + 1)
    if i1 <= i0:
        return t
    seg = onset[i0:i1]
    j = int(np.argmax(seg))
    return round((i0 + j) * HOP, 3) if seg[j] >= floor else t


# ---------- finding and fixing bad runs ----------
def is_bad(w: dict, prev: dict | None, min_dur: float = 0.09, low_conf: float = 0.35) -> bool:
    dur = w["end"] - w["start"]
    squashed = prev is not None and abs(w["start"] - prev["start"]) < 1e-3
    return dur < min_dur or squashed or (w.get("confidence", 1.0) < low_conf and dur < 0.15)


def bad_runs(words: list[dict]) -> list[tuple[int, int]]:
    """(first, last) index of each run of squashed words, plus the word right after it, which
    usually swallowed their time."""
    flags = [is_bad(w, words[i - 1] if i else None) for i, w in enumerate(words)]
    runs, i = [], 0
    while i < len(words):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and flags[j + 1]:
            j += 1
        a, b = i, min(len(words) - 1, j + 1)
        # stay inside one cue: never move words across a subtitle boundary
        cue = words[i].get("cue")
        while a < i and words[a].get("cue") != cue:
            a += 1
        while b > j and words[b].get("cue") != cue:
            b -= 1
        if runs and a <= runs[-1][1]:
            runs[-1] = (runs[-1][0], b)
        else:
            runs.append((a, b))
        i = j + 1
    return runs


def spread_by_energy(ws: list[dict], t0: float, t1: float, env: np.ndarray, snap: float = 0.06) -> list[dict]:
    """Give each word a share of the voiced time between t0 and t1, by spoken length,
    and move each boundary onto the closest energy dip."""
    i0, i1 = int(t0 / HOP), max(int(t0 / HOP) + 1, int(t1 / HOP))
    e = env[i0:i1].astype(np.float64)
    if len(e) < 2 or e.sum() <= 0:
        e = np.ones(max(2, i1 - i0))
    voiced = np.where(e > 0.25 * e.max(), e, e * 0.15)  # pauses get little time
    cum = np.concatenate([[0.0], np.cumsum(voiced)])
    cum /= cum[-1]
    wts = np.array([weight(w["word"]) for w in ws], np.float64)
    targets = np.cumsum(wts)[:-1] / wts.sum()
    bounds = [t0]
    for q in targets:
        k = int(np.searchsorted(cum, q))
        t = t0 + k * HOP
        # nearest local minimum of energy within +-snap
        a, b = max(i0, int((t - snap) / HOP)), min(i1, int((t + snap) / HOP) + 1)
        if b > a:
            t = (a + int(np.argmin(env[a:b]))) * HOP
        bounds.append(min(max(t, bounds[-1] + 0.04), t1 - 0.04 * (len(ws) - len(bounds))))
    bounds.append(t1)
    out = []
    for w, a, b in zip(ws, bounds, bounds[1:]):
        n = dict(w)
        n["start"], n["end"] = round(a, 3), round(max(b, a + 0.04), 3)
        n["retimed"] = "energy"
        out.append(n)
    return out


def _whisperx_cue(ws: list[dict], y: np.ndarray, sr: int, t0: float, t1: float, model) -> list[dict] | None:
    """Align one short stretch with WhisperX; None when it fails or is no better."""
    if model is None:
        return None
    try:
        import whisperx  # type: ignore
        pad = 0.3
        a, b = max(0, int((t0 - pad) * sr)), min(len(y), int((t1 + pad) * sr))
        seg = [{"start": 0.0, "end": (b - a) / sr, "text": " ".join(w["word"] for w in ws)}]
        res = whisperx.align(seg, model[0], model[1], y[a:b], "cpu", return_char_alignments=False)
        got = res["segments"][0].get("words", []) if res.get("segments") else []
        if len(got) != len(ws) or any("start" not in g for g in got):
            return None
        off = a / sr
        out = []
        for w, g in zip(ws, got):
            n = dict(w)
            n["start"], n["end"] = round(g["start"] + off, 3), round(g["end"] + off, 3)
            n["confidence"] = round(float(g.get("score", 0.5)), 3)
            n["retimed"] = "whisperx"
            out.append(n)
        if any(is_bad(x, out[i - 1] if i else None) for i, x in enumerate(out)):
            return None
        if out[0]["start"] < t0 - 0.25 or out[-1]["end"] > t1 + 0.25:
            return None
        return out
    except Exception as exc:  # pragma: no cover - depends on the Mac install
        log.debug("whisperx retry failed: %s", exc)
        return None


def realign(words: list[dict], env: np.ndarray, y=None, sr: int = 16000, model=None) -> tuple[list[dict], dict]:
    words = [dict(w) for w in words]
    stats = {"runs": 0, "words": 0, "whisperx": 0, "energy": 0}
    for a, b in bad_runs(words):
        ws = words[a:b + 1]
        t0 = ws[0]["start"]
        t1 = max(ws[-1]["end"], t0 + 0.3 * len(ws))  # squashed words really take ~0.3 s each
        if b + 1 < len(words):
            nxt = words[b + 1]["start"]
            # the speech runs until the next word; a long gap means a pause, which the energy spread skips
            t1 = nxt if nxt - t0 <= 0.6 * len(ws) + 1.5 else min(t1, nxt)
        if t1 - t0 < 0.04 * len(ws):
            continue
        fixed = _whisperx_cue(ws, y, sr, t0, t1, model) if y is not None else None
        if fixed:
            stats["whisperx"] += 1
        else:
            fixed = spread_by_energy(ws, t0, t1, env)
            stats["energy"] += 1
        words[a:b + 1] = fixed
        stats["runs"] += 1
        stats["words"] += len(ws)
    return words, stats


def run_stage(cfg, force: bool = False) -> list[dict]:
    cache = cfg.dir("cache")
    out = cache / "words.json"
    backup = cache / "words_first_pass.json"
    words = read_json(backup if backup.exists() else out)
    if (cache / "onsets.npy").exists() and backup.exists() and not force:
        log.info("Word timing second pass already done, skipping (use --force to redo).")
        return read_json(out)
    y, sr = load_wav(cache / "audio_16k.wav")
    log.info("Measuring speech energy over %.0f minutes of audio...", len(y) / sr / 60)
    env = speech_envelope(y, sr)
    np.save(cache / "onsets.npy", onset_strength(env))
    np.save(cache / "envelope.npy", env)
    model = None
    if cfg.at("align.realign_whisperx", True):
        try:
            import whisperx  # type: ignore
            from .align import TELUGU_ALIGN_MODEL
            model = whisperx.load_align_model(language_code="te", device="cpu",
                                              model_name=cfg.at("align.align_model", TELUGU_ALIGN_MODEL))
        except Exception as exc:
            log.warn("WhisperX is not available for the second pass (%s); using speech energy only.", exc)
    if not backup.exists():
        write_json(backup, words)
    fixed, stats = realign(words, env, y, sr, model)
    write_json(out, fixed)
    log.info("Second timing pass: fixed %d squashed runs (%d words): %d by WhisperX, %d by speech energy.",
             stats["runs"], stats["words"], stats["whisperx"], stats["energy"])
    return fixed


def load_onsets(cfg) -> np.ndarray | None:
    p = cfg.dir("cache") / "onsets.npy"
    return np.load(p) if p.exists() else None
