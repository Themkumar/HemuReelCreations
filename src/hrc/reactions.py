"""Reaction shots: short cutaways (1-2 s, never more than 2 s) to the person listening.

`hrc reactions` scans the podcast once (5 frames a second, small):
  * tells the two people apart by how they look (hair / cap / clothes colours above and around the face),
  * measures how much each person's mouth moves (head movement subtracted),
  * finds stretches where one person is alone on screen with a still mouth while somebody talks:
    that is them listening. These become the reaction library (cache/reactions.json).

At render time, `plan_reel` picks a few moments in each reel (while the speaker talks, away from
graphics, joins and camera switches) and a listener clip for each. The speaker's sound carries on;
only the picture cuts away.
"""
from __future__ import annotations

import platform
import subprocess

import numpy as np

from . import log, plan as planlib
from .util import ffmpeg, read_json, write_json

FPS = 5.0
AW, AH = 960, 540  # analysis frame size


# ---------- analysis ----------
def _frames(video, start: float = 0.0, dur: float | None = None):
    hw = ["-hwaccel", "videotoolbox"] if platform.system() == "Darwin" else []
    cmd = [ffmpeg(), "-v", "error", *hw, "-ss", f"{start:.3f}", "-i", str(video)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vf", f"fps={FPS},scale={AW}:{AH}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = AW * AH * 3
    k = 0
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield start + k / FPS, np.frombuffer(buf, np.uint8).reshape(AH, AW, 3)
        k += 1
    proc.stdout.close(); proc.wait()


def _box(f: dict, fx0: float, fx1: float, fy0: float, fy1: float) -> tuple[int, int, int, int]:
    """Part of a face box in analysis pixels: fractions of the face width/height around its centre."""
    cx, cy, w, h = f["cx"] * AW, f["cy"] * AH, f["w"] * AW, f["h"] * AH
    x0, x1 = int(max(0, cx + fx0 * w)), int(min(AW, cx + fx1 * w))
    y0, y1 = int(max(0, cy + fy0 * h)), int(min(AH, cy + fy1 * h))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def describe(rgb: np.ndarray, f: dict) -> np.ndarray:
    """Colour signature of a person: hair or cap above the face, and the clothes below it."""
    import cv2

    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    feats = []
    for box in (_box(f, -0.7, 0.7, -1.2, -0.35), _box(f, -1.0, 1.0, 0.9, 2.2)):
        x0, y0, x1, y1 = box
        reg = hsv[y0:y1, x0:x1].reshape(-1, 3)
        if len(reg) < 10:
            feats.append(np.zeros(8 * 3 + 3, np.float32))
            continue
        hh = np.histogram(reg[:, 0], 8, (0, 180), weights=reg[:, 1] / 255.0)[0]
        hs = np.histogram(reg[:, 1], 8, (0, 256))[0]
        hv = np.histogram(reg[:, 2], 8, (0, 256))[0]
        h = np.concatenate([hh, hs, hv]).astype(np.float32)
        h /= max(1e-6, h.sum())
        feats.append(np.concatenate([h, reg.mean(0).astype(np.float32) / 255.0]))
    return np.concatenate(feats)


def _mouth(grey: np.ndarray, f: dict) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    x0, y0, x1, y1 = _box(f, -0.28, 0.28, 0.12, 0.5)
    u0, v0, u1, v1 = _box(f, -0.35, 0.35, -0.45, -0.05)
    m = cv2.resize(grey[y0:y1, x0:x1], (24, 14), interpolation=cv2.INTER_AREA).astype(np.float32)
    u = cv2.resize(grey[v0:v1, u0:u1], (24, 14), interpolation=cv2.INTER_AREA).astype(np.float32)
    return m, u


def kmeans2(X: np.ndarray, iters: int = 30, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Two groups (the two people). Farthest-point start, then plain k-means."""
    rng = np.random.default_rng(seed)
    c0 = X[rng.integers(len(X))]
    c1 = X[int(np.argmax(((X - c0) ** 2).sum(1)))]
    C = np.stack([c0, c1])
    lab = np.zeros(len(X), int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        new = d.argmin(1)
        if (new == lab).all() and _ > 0:
            break
        lab = new
        for k in range(2):
            if (lab == k).any():
                C[k] = X[lab == k].mean(0)
    return lab, C


def analyse(video, faces: dict, shots: list[dict], env: np.ndarray | None, duration: float) -> dict:
    import cv2

    samples = faces.get("samples", [])
    ft = np.array([s["t"] for s in samples]) if samples else np.zeros(0)
    rows = []  # one per (time, face): t, cx, cy, h, n_faces, act, desc
    prev: dict = {}
    log.info("Scanning %.0f minutes of video for who is on screen and who is listening...", duration / 60)
    for t, rgb in _frames(video):
        if not len(ft):
            break
        j = int(np.argmin(np.abs(ft - t)))
        if abs(ft[j] - t) > 0.15:
            continue
        fs = [f for f in samples[j]["faces"] if f["h"] > 0.08]
        if not fs or len(fs) > 2:
            prev = {}
            continue
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        cur = {}
        for f in fs:
            m, u = _mouth(grey, f)
            key = round(f["cx"], 1)
            act = None
            p = prev.get(key)
            if p is not None and abs(p[2] - f["cx"]) < 0.04:
                dm = float(np.abs(m - p[0]).mean())
                du = float(np.abs(u - p[1]).mean())
                act = max(0.0, dm - 0.8 * du)  # mouth movement beyond what the whole head did
            cur[key] = (m, u, f["cx"])
            rows.append({"t": round(t, 2), "cx": f["cx"], "cy": f["cy"], "h": f["h"], "w": f["w"], "n": len(fs),
                         "act": act, "desc": describe(rgb, f)})
        prev = cur
    if not rows:
        return {"people": 0, "shots": [], "clips": []}
    singles = [r for r in rows if r["n"] == 1]
    X = np.stack([r["desc"] for r in (singles or rows)])
    _, C = kmeans2(X)
    for r in rows:
        r["id"] = int(((C - r["desc"][None]) ** 2).sum(1).argmin())
    # who is on screen in each camera shot
    shot_info = []
    for s in shots:
        rs = [r for r in rows if s["start"] <= r["t"] < s["end"]]
        if not rs:
            shot_info.append({**s, "kind": "none", "id": None})
            continue
        two = sum(1 for r in rs if r["n"] == 2) / len(rs) > 0.5
        ids = [r["id"] for r in rs if r["n"] == 1]
        sid = int(np.bincount(ids, minlength=2).argmax()) if ids else None
        shot_info.append({**s, "kind": "two" if two else "single", "id": None if two else sid})
    clips = find_clips(rows, shot_info, env)
    counts = {str(k): sum(1 for r in rows if r["id"] == k and r["n"] == 1) for k in (0, 1)}
    log.info("Two people found (%s single-shot samples). %d listening clips in the library.",
             " / ".join(str(v) for v in counts.values()), len(clips))
    return {"people": 2, "counts": counts, "shots": shot_info, "clips": clips,
            "samples": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k != "desc"}
                        for r in rows]}


def find_clips(rows: list[dict], shots: list[dict], env: np.ndarray | None, win: float = 1.6,
               step: float = 0.2, margin: float = 0.15) -> list[dict]:
    """Stretches of `win` seconds where one person is alone on screen, their mouth is still and
    someone is talking (they are listening)."""
    by_id: dict = {0: [], 1: []}
    for r in rows:
        if r["n"] == 1 and r["act"] is not None:
            by_id[r["id"]].append(r["act"])
    # still mouth: well under what this person's mouth does when they talk (head movement already removed)
    hi = {k: (float(np.percentile(v, 90)) if len(v) > 20 else 1.0) for k, v in by_id.items()}
    q = {k: max(float(np.percentile(v, 35)) if len(v) > 20 else 0.0, 0.08 * hi[k]) for k, v in by_id.items()}
    clips = []
    ts = np.array([r["t"] for r in rows])
    for s in shots:
        if s.get("kind") != "single" or s.get("id") is None or s["end"] - s["start"] < win + 2 * margin:
            continue
        pid = s["id"]
        t = s["start"] + margin
        best = None
        while t + win <= s["end"] - margin:
            i0, i1 = np.searchsorted(ts, t), np.searchsorted(ts, t + win)
            rs = [r for r in rows[i0:i1] if r["n"] == 1 and r["id"] == pid]
            if len(rs) >= int(win * FPS * 0.8):
                acts = [r["act"] for r in rs if r["act"] is not None]
                if acts and max(acts) < hi[pid] and float(np.mean(acts)) <= q[pid]:
                    talk = 1.0
                    if env is not None:
                        a, b = int(t * 100), int((t + win) * 100)
                        talk = float((env[a:b] > 0.35).mean()) if b > a else 0.0
                    if talk > 0.4:
                        move = float(np.std([r["cy"] for r in rs]) + np.std([r["cx"] for r in rs]))
                        score = (1 - float(np.mean(acts)) / max(1e-6, hi[pid])) + min(move * 20, 0.4) + 0.2 * talk
                        if best is None or score > best["score"]:
                            mid = rs[len(rs) // 2]
                            best = {"id": pid, "start": round(t, 2), "end": round(t + win, 2), "score": round(score, 3),
                                    "cx": round(float(np.median([r["cx"] for r in rs])), 4),
                                    "cy": round(float(np.median([r["cy"] for r in rs])), 4),
                                    "h": round(float(np.median([r["h"] for r in rs])), 4), "shot": [s["start"], s["end"]],
                                    "mid_t": mid["t"]}
            t += step
        if best:
            clips.append(best)
    return sorted(clips, key=lambda c: -c["score"])


def run_stage(cfg, force: bool = False) -> dict:
    cache = cfg.dir("cache")
    out = cache / "reactions.json"
    if out.exists() and not force:
        log.info("Reaction library already built, skipping (use --force to redo).")
        return read_json(out)
    info = read_json(cache / "video_info.json")
    env = None
    if (cache / "envelope.npy").exists():
        env = np.load(cache / "envelope.npy")
    res = analyse(cfg.path("input.video"), read_json(cache / "faces.json"), read_json(cache / "shots.json"), env,
                  info["duration"])
    write_json(out, res)
    return res


# ---------- per reel ----------
def speaker_of(reel: dict, lib: dict) -> int | None:
    """The person who is on screen alone the most during the reel: the one talking."""
    tally = np.zeros(2)
    for seg in planlib.segments(reel):
        for s in lib.get("shots", []):
            if s.get("kind") == "single" and s.get("id") is not None:
                ov = min(seg["end"], s["end"]) - max(seg["start"], s["start"])
                if ov > 0:
                    tally[s["id"]] += ov
    return int(tally.argmax()) if tally.sum() > 0 else None


def plan_reel(cfg, reel: dict, rw: list[dict], events: list[dict], lib: dict, used: set | None = None) -> list[dict]:
    """Moments in the reel for a reaction cutaway, each with a listener clip."""
    rc = cfg.at("reactions", {}) or {}
    if not rc.get("enabled", True) or not lib or not lib.get("clips"):
        return []
    who = speaker_of(reel, lib)
    if who is None:
        return []
    listener = 1 - who
    pool = [c for c in lib["clips"] if c["id"] == listener and not _inside(c, reel)]
    if not pool:
        return []
    used = used if used is not None else set()
    dmin, dmax = float(rc.get("min_sec", 1.0)), min(2.0, float(rc.get("max_sec", 2.0)))
    target = min(dmax, max(dmin, float(rc.get("length_sec", 1.5))))
    every = float(rc.get("every_sec", 16.0))
    content = planlib.content_duration(reel)
    blocked = [(e["t_in"] - 0.5, e["t_out"] + 0.5) for e in events]
    joins = planlib.cut_points(reel)
    switches = []
    for s in lib.get("shots", []):
        rt = planlib.to_reel_time(reel, s["start"])
        if rt is not None:
            switches.append(rt)
    starts = [w["start"] for w in rw]
    out = []
    t = float(rc.get("first_after_sec", 6.0))
    while t < content - 4.0 and len(out) < int(rc.get("max_per_reel", 3)):
        best = None
        for s0 in starts:
            if not (t - 3 <= s0 <= t + 5):
                continue
            ends = [x for x in starts if s0 + dmin <= x <= s0 + dmax]
            s1 = min(ends, key=lambda x: abs(x - s0 - target)) if ends else s0 + target
            s1 = min(s1, s0 + dmax)
            if s1 > content - 2.5:
                continue
            if any(a < s1 and s0 < b for a, b in blocked):
                continue
            if any(s0 - 0.6 < c < s1 + 0.6 for c in joins + switches):
                continue
            talk = sum(max(0.0, min(w["end"], s1) - max(w["start"], s0)) for w in rw) / (s1 - s0)
            if talk < 0.6:
                continue  # only while the speaker is talking
            cost = abs(s0 - t)
            if best is None or cost < best[0]:
                best = (cost, s0, s1)
        if best:
            _, s0, s1 = best
            # the best listening clips, picked per reel so each reel shows different ones (same pick every run)
            top = pool[:max(6, min(len(pool), 40))]
            seed = sum(map(ord, reel["id"]))
            k = (seed * 7 + len(out) * 13) % len(top)
            while top[k]["start"] in used and len(used) < len(top):
                k = (k + 1) % len(top)
            clip = top[k]
            used.add(clip["start"])
            dur = round(s1 - s0, 3)
            src0 = clip["start"] + max(0.0, (clip["end"] - clip["start"] - dur) / 2)
            out.append({"t0": round(s0, 3), "t1": round(s1, 3), "src": round(src0, 3), "cx": clip["cx"],
                        "cy": clip["cy"], "h": clip["h"], "who": listener})
            t = s1 + every
        else:
            t += 4.0
    return out


def _inside(clip: dict, reel: dict) -> bool:
    """The clip's moment is already part of the reel (it would show twice)."""
    return any(clip["start"] < s["end"] and s["start"] < clip["end"] for s in planlib.segments(reel))
