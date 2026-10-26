"""Stages F + G: reframe plate, person matte, GFX behind the speaker, transitions, audio mix.

Per reel this writes, in renders/<reel>/:
  plate.mp4      9:16 face-tracked crop with punch-in zooms (no GFX)
  heads.json     tracked head position per frame
  clean.mp4      final picture without subtitles (GFX, transitions, shake, vignette)
  mix.wav        speech + ducked music + SFX at -14 LUFS
  render.json    what was placed where (GFX timeline, transitions, SFX, music, QA numbers)
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from . import audio, gfx, log, matte, plan as planlib, transitions
from .tracking import crop_path
from .util import (cached, ffmpeg, fingerprint, mark, read_json, run, video_encoder_args, write_json)


def _reader(video: Path, start: float, dur: float, fps: int, W: int, H: int):
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
           "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    proc.stdout.close(); proc.wait()


def _thumbs(video: Path, start: float, dur: float, fps: int):
    """Tiny grey frames, decoded exactly like _reader, for finding the true camera-switch frames."""
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
           "-vf", f"fps={fps},scale=64:36,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    n = len(raw) // (64 * 36)
    return np.frombuffer(raw[: n * 64 * 36], np.uint8).reshape(n, 36, 64).astype(np.float32)


def switch_frames(diffs: np.ndarray, hints: list[int], search: int = 3) -> list[int]:
    """Frame indices where the picture switches camera. `diffs[k]` = change from frame k-1 to k.
    Each detected cut is snapped to the frame with the biggest change near it (so the crop
    switches on exactly the same frame as the picture), and strong unseen switches are added."""
    n = len(diffs)
    if n < 3:
        return []
    out = set()
    for k0 in hints:
        lo, hi = max(1, k0 - search), min(n, k0 + search + 1)
        if lo < hi:
            k = lo + int(np.argmax(diffs[lo:hi]))
            if diffs[k] > 8:
                out.add(k)
    med = float(np.median(diffs[1:])) if n > 1 else 0.0
    thr = max(22.0, med * 6)
    for k in range(1, n):
        if diffs[k] > thr and diffs[k] == diffs[max(1, k - 2):k + 3].max():
            out.add(k)
    ks = sorted(out)
    merged = []
    for k in ks:  # one switch per 2 frames at most
        if merged and k - merged[-1] < 2:
            if diffs[k] > diffs[merged[-1]]:
                merged[-1] = k
            continue
        merged.append(k)
    return merged


def frame_map(n: int, switches: list[int], min_shot: int) -> list[int]:
    """Which source frame to show for each output frame, so no camera angle flashes on screen for
    less than `min_shot` frames: a sliver at the start shows the next shot's first frame, a sliver
    at the end or in the middle holds the frame before it."""
    src = list(range(n))
    bounds = [0] + [k for k in switches if 0 < k < n] + [n]
    for a, b in zip(bounds, bounds[1:]):
        if b - a >= min_shot or b - a <= 0:
            continue
        if a == 0:
            if b < n:
                for k in range(a, b):
                    src[k] = b
        else:
            for k in range(a, b):
                src[k] = src[a - 1]
    return src


def _writer(out: Path, W: int, H: int, fps: int, enc: list[str]):
    return subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(fps), "-i", "-", *enc, str(out)], stdin=subprocess.PIPE)


def zoom_envelope(n: int, fps: int, emph_times: list[float], scale: float, ramp: int) -> np.ndarray:
    z = np.ones(n)
    last = -99.0
    for t in sorted(emph_times):
        if t - last < 4.0:
            continue
        last = t
        i0 = int(t * fps)
        hold = int(0.9 * fps)
        for i in range(max(0, i0 - 1), min(n, i0 + ramp + hold + ramp * 2)):
            k = i - i0
            if k < ramp:
                v = gfx.ease_out_cubic(max(0, k + 1) / ramp)
            elif k < ramp + hold:
                v = 1.0
            else:
                v = 1 - (k - ramp - hold) / (ramp * 2)
            z[i] = max(z[i], 1 + (scale - 1) * max(0.0, v))
    return z


def _context_card(first: np.ndarray, text: str, W: int, H: int, n: int, colors) -> list[np.ndarray]:
    bg = cv2.GaussianBlur(first, (0, 0), 18).astype(np.float32) * 0.45
    spr = gfx.text_sprite(text, int(W * 0.085), fill=(255, 255, 255), glow=gfx.hex_rgb(colors[0]), max_w=int(W * 0.86))
    if len(text.split()) > 3 or spr.shape[1] >= W * 0.86:  # wrap longer cards onto 2 lines
        words = text.split(); mid = len(words) // 2
        a = gfx.text_sprite(" ".join(words[:mid]), int(W * 0.1), glow=gfx.hex_rgb(colors[0]), max_w=int(W * 0.9))
        b = gfx.text_sprite(" ".join(words[mid:]), int(W * 0.1), glow=gfx.hex_rgb(colors[0]), max_w=int(W * 0.9))
        sprites = [(a, H * 0.42), (b, H * 0.42 + a.shape[0] * 0.8)]
    else:
        sprites = [(spr, H * 0.45)]
    frames = []
    for i in range(n):
        f = bg.copy()
        p = gfx.clamp01(i / 8)
        for s, y in sprites:
            gfx.blend(f, s, W / 2, y, gfx.ease_out_back(p), 1.0)
        frames.append(np.clip(f, 0, 255).astype(np.uint8))
    return frames


def build_plate(cfg, reel: dict, rdir: Path, W: int, H: int, fps: int, emph_times: list[float], preview: bool,
                reactions: list[dict] | None = None) -> dict:
    cache = cfg.dir("cache")
    info = read_json(cache / "video_info.json")
    faces = read_json(cache / "faces.json")
    shots = read_json(cache / "shots.json")
    video = cfg.path("input.video")
    sw, sh = info["width"], info["height"]
    plate = rdir / "plate.mp4"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14" if not preview else "20", "-pix_fmt", "yuv420p"]
    wr = _writer(plate, W, H, fps, enc)
    heads = []
    card_n = int(round(planlib.context_card_sec(reel) * fps))
    total = card_n + sum(int(round((s["end"] - s["start"]) * fps)) for s in planlib.segments(reel))
    zoom = zoom_envelope(total, fps, emph_times, cfg.at("reframe.punch_in_scale", 1.12), cfg.at("reframe.punch_in_frames", 6))
    segs = planlib.segments(reel)
    # Portrait: the video sits a little lower and a soft band at the top gives graphics room above the head
    band = float(cfg.at("reframe.portrait_top_band", 0.18)) if H > W else 0.0
    Hv = H - int(round(H * band / 2)) * 2
    off = H - Hv

    grade = make_grade(cfg.at("grade"))

    def place(img: np.ndarray) -> np.ndarray:
        if grade is not None:
            img = grade(img)
        if not off:
            return img
        if heads:
            heads[-1]["y"] = round(heads[-1]["y"] + off, 1)
        return _with_top_band(img, W, H, off)

    min_shot = max(1, int(round(float(cfg.at("reframe.min_shot_sec", 0.4)) * fps)))
    maps, seg_shots, switch_times = [], [], []
    rt = planlib.context_card_sec(reel)
    for seg in segs:
        n = int(round((seg["end"] - seg["start"]) * fps))
        th = _thumbs(video, seg["start"], seg["end"] - seg["start"], fps)
        diffs = np.zeros(len(th), np.float32)
        if len(th) > 1:
            diffs[1:] = np.abs(th[1:] - th[:-1]).mean(axis=(1, 2))
        hints = [int(round((c["start"] - seg["start"]) * fps)) for c in shots if seg["start"] < c["start"] < seg["end"]]
        sw_k = switch_frames(diffs, hints)
        fmap = frame_map(n, sw_k, min_shot)
        maps.append(fmap)
        # exact frame times (a hair early) so the crop path switches on the same frame as the picture
        seg_shots.append([{"start": seg["start"] + (k - 0.25) / fps} for k in sw_k if 0 < k < n])
        shown = [k for k in range(1, n) if fmap[k] != fmap[k - 1] and any(fmap[k - 1] < x <= fmap[k] for x in sw_k)]
        switch_times += [round(rt + k / fps, 3) for k in shown]
        rt += seg["end"] - seg["start"]
        dropped = len([k for k in sw_k if 0 < k < n]) - len(shown)
        if dropped > 0:
            log.info("  clean cuts: removed %d camera flash(es) shorter than %d frames in segment %.1f-%.1fs",
                     dropped, min_shot, seg["start"], seg["end"])
    paths = [crop_path(faces, ss, seg["start"], seg["end"], fps, sw, sh, W, Hv,
                       cfg.at("reframe.smoothing_min_cutoff", 0.6), cfg.at("reframe.smoothing_beta", 0.01),
                       cfg.at("reframe.head_room", 0.30), cfg.at("reframe.deadzone", 0.12),
                       cfg.at("reframe.max_pan", 0.012)) for seg, ss in zip(segs, seg_shots)]
    if card_n:
        fr0 = next(_reader(video, segs[0]["start"], 0.2, fps, sw, sh))
        first = _with_top_band(_crop(fr0, paths[0][0], 1.0, W, Hv, []), W, H, off) if off else _crop(fr0, paths[0][0], 1.0, W, H, [])
        for cf in _context_card(first, reel["context_card"], W, H, card_n, cfg.at("project.brand_colors")):
            wr.stdin.write(cf.tobytes())
    react_at: dict[int, tuple[int, int]] = {}  # output frame -> (which listener cutaway, frame in it)
    for ri, r in enumerate(reactions or []):
        f0, f1 = int(round(r["t0"] * fps)), int(round(r["t1"] * fps))
        for k in range(f1 - f0):
            react_at[f0 + k] = (ri, k)
    r_cache: dict = {}

    def react_frame(ri: int, k: int):
        if ri not in r_cache:  # decode one cutaway at a time, only when it is reached
            r_cache.clear()
            r = reactions[ri]
            n = int(round(r["t1"] * fps)) - int(round(r["t0"] * fps))
            r_cache[ri] = reaction_frames(video, r, n, fps, sw, sh, W, Hv, float(cfg.at("reactions.head_room", 0.32)),
                                          float(cfg.at("reactions.push_in", 0.035)))
        frs, hd = r_cache[ri]
        return (frs[min(k, len(frs) - 1)], hd) if frs else (None, None)
    fi = card_n
    for seg, path, fmap in zip(segs, paths, maps):
        n = int(round((seg["end"] - seg["start"]) * fps))
        reader = _reader(video, seg["start"], seg["end"] - seg["start"] + 0.2, fps, sw, sh)
        cur, cur_i = None, -1
        for k in range(n):
            want = fmap[k]
            while cur_i < want:  # fmap never goes backwards, so one frame of memory is enough
                nxt = next(reader, None)
                if nxt is None:
                    break  # decoder came up a frame or two short: hold the last frame
                cur, cur_i = nxt, cur_i + 1
            if cur is None:
                break
            j = min(want, cur_i, len(path) - 1)
            img = _crop(cur, path[j], zoom[min(fi, total - 1)], W, Hv, heads)
            if fi in react_at:  # the listener's reaction replaces the picture; the speaker's sound goes on
                rimg, rhead = react_frame(*react_at[fi])
                if rimg is not None:
                    img, heads[-1] = rimg, dict(rhead)
            wr.stdin.write(place(img).tobytes())
            fi += 1
        reader.close()
    wr.stdin.close(); wr.wait()
    card_head = heads[0] if heads else {"x": W / 2, "y": H * 0.3, "size": W * 0.15}
    heads = [card_head] * card_n + heads
    write_json(rdir / "heads.json", heads)
    r_cuts = sorted({t for r in reactions or [] for t in (r["t0"], r["t1"])})
    write_json(rdir / "cuts.json", {"camera_switches": switch_times, "segment_joins": planlib.cut_points(reel),
                                    "reaction_cuts": r_cuts})
    return {"frames": total, "card_frames": card_n}


def make_grade(g: dict | None):
    """Cinematic look as a fast per-pixel function: a little darker, more contrast, warm skin, calmer
    colour. Built once per reel as lookup tables."""
    if not g or not g.get("enabled", True):
        return None
    x = np.arange(256, dtype=np.float32) / 255.0
    gain = 2 ** float(g.get("exposure", -0.15))
    if gain > 1:  # brighten with a soft highlight roll-off: shadows/mids lift, white never clips
        x = x * gain / (1 + (gain - 1) * x)
    else:
        x = np.clip(x * gain, 0, 1)
    c = float(g.get("contrast", 1.15))
    x = np.clip(0.5 + (x - 0.5) * c, 0, 1)
    x = x ** float(g.get("gamma", 1.08))  # deeper shadows, like a dark studio
    warm = float(g.get("warmth", 0.03))
    luts = [np.clip(x * (1 + warm) * 255, 0, 255), np.clip(x * 255, 0, 255), np.clip(x * (1 - warm) * 255, 0, 255)]
    luts = [l.astype(np.uint8) for l in luts]
    sat = float(g.get("saturation", 0.9))

    def apply(img: np.ndarray) -> np.ndarray:
        out = np.empty_like(img)
        for ch in range(3):
            out[..., ch] = cv2.LUT(img[..., ch], luts[ch])
        if abs(sat - 1) > 1e-3:
            grey = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)[..., None].astype(np.float32)
            out = np.clip(grey + (out.astype(np.float32) - grey) * sat, 0, 255).astype(np.uint8)
        return out
    return apply


def reaction_frames(video, r: dict, n: int, fps: int, src_w: int, src_h: int, W: int, H: int,
                    head_room: float = 0.32, push: float = 0.035):
    """The listener's cutaway: a steady 9:16 close-up on their face with a slow push-in."""
    aspect = W / H
    cw = min(src_w, src_h * aspect)
    ch = cw / aspect
    fx, fy = r["cx"] * src_w, r["cy"] * src_h
    x = float(np.clip(fx - cw / 2, 0, src_w - cw))
    y = float(np.clip(fy - ch * head_room, 0, src_h - ch))
    frames = []
    for k, fr in enumerate(_reader(video, r["src"], n / fps + 0.2, fps, src_w, src_h)):
        if k >= n:
            break
        z = 1 + push * (k / max(1, n - 1))
        cw_, ch_ = cw / z, ch / z
        x_ = x + (fx - x) * (1 - 1 / z)
        y_ = y + (fy - y) * (1 - 1 / z)
        crop = fr[int(y_):int(y_ + ch_), int(x_):int(x_ + cw_)]
        frames.append(cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA))
    while frames and len(frames) < n:
        frames.append(frames[-1])
    s = W / cw
    head = {"x": round((fx - x) * s, 1), "y": round((fy - y) * s, 1), "size": round(r["h"] * src_h * s, 1),
            "cam": [round(x, 2), round(y, 2), round(s, 5)], "reaction": True}
    return frames, head


def _with_top_band(img: np.ndarray, W: int, H: int, off: int) -> np.ndarray:
    """Put the video at the bottom of a W x H canvas; the top `off` rows get a soft backdrop made
    from the colours along the video's top edge (no shapes, so the person matte stays clean)."""
    out = np.empty((H, W, 3), np.uint8)
    out[off:] = img
    edge = img[: max(4, off // 6)].astype(np.float32).mean(axis=0)  # per-column colour of the top edge
    edge = cv2.GaussianBlur(edge[None], (0, 0), W / 40)[0]
    fade = np.linspace(0.35, 0.9, off, dtype=np.float32)[:, None, None]  # darker toward the top
    out[:off] = np.clip(edge[None] * fade, 0, 255).astype(np.uint8)
    blend = min(off, max(8, H // 60))  # soften the seam
    a = np.linspace(0, 1, blend, dtype=np.float32)[:, None, None]
    out[off:off + blend] = (out[off - 1:off].astype(np.float32) * (1 - a) + img[:blend] * a).astype(np.uint8)
    return out


def _crop(fr: np.ndarray, box: dict, z: float, W: int, H: int, heads: list) -> np.ndarray:
    head = box["head"]
    sx = box["w"] / W
    hx_src, hy_src = box["x"] + head["x"] * sx, box["y"] + head["y"] * sx
    cw, ch = box["w"] / z, box["h"] / z
    x = min(max(hx_src - (hx_src - box["x"]) / z, 0), fr.shape[1] - cw)
    y = min(max(hy_src - (hy_src - box["y"]) / z, 0), fr.shape[0] - ch)
    crop = fr[int(y):int(y + ch), int(x):int(x + cw)]
    out = cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA if z < 1.5 else cv2.INTER_LINEAR)
    s = W / cw
    heads.append({"x": round((hx_src - x) * s, 1), "y": round((hy_src - y) * s, 1), "size": round(head["size"] * z, 1),
                  "cam": [round(x, 2), round(y, 2), round(s, 5)]})
    return np.ascontiguousarray(out)


def composite(cfg, reel: dict, rdir: Path, W: int, H: int, fps: int, events: list[dict], has_matte: bool,
              preview: bool, seed: int, framing=None) -> dict:
    plate = rdir / "plate.mp4"
    mfile = rdir / "matte.mp4"
    heads = read_json(rdir / "heads.json")
    colors = cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"]
    if not has_matte:  # without a cut-out, never put big words over the face
        for e in events:
            if e["anchor"] == "behind_head":
                e["anchor"] = "top_split"
    rend = gfx.GfxRenderer(events, W, H, fps, colors, seed)
    rend.fullbleed = bool((cfg.at("layout.reel", {}) or {}).get("fullbleed")) and H > W
    style_renderer(cfg, rend)
    if not cfg.at("gfx.particles", True):
        rend.particles = _NoParticles()
    if cfg.at("gfx.track", True):
        rend.cams = heads
        cj = read_json(rdir / "cuts.json") if (rdir / "cuts.json").exists() else {}
        rend.switches = sorted(int(round(t * fps)) for t in cj.get("camera_switches", []) + cj.get("segment_joins", [])
                               + cj.get("reaction_cuts", []))
    cuts = [int(round(t * fps)) for t in planlib.cut_points(reel)]
    names = transitions.choose(len(cuts), reel.get("mood", "inspirational"), cfg.at("transitions.library"), seed)
    yy, xx = np.ogrid[:H, :W]
    vstr = float((cfg.at("grade") or {}).get("vignette", 0.35))
    vig = (1 - vstr * ((((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2))[..., None].astype(np.float32)
    logo = _logo(cfg, *(framing[:2] if framing else (W, H)))
    enc = video_encoder_args(cfg, preview) if preview else ["-c:v", "libx264", "-preset", "fast", "-crf", "15", "-pix_fmt", "yuv420p"]
    if framing:
        FW, FH, (bx, by, bw, bh) = framing
        canvas = np.zeros((FH, FW, 3), np.uint8)  # black frame: headline above, captions below
        if logo is not None:  # channel stamp in the top-right corner of the 9:16 frame, outside the picture
            cf = canvas.astype(np.float32)
            m = min(FW, FH) * 0.04
            gfx.blend(cf, logo, FW - logo.shape[1] * 0.5 - m, logo.shape[0] * 0.5 + m, 1.0, 0.9)
            canvas = cf.astype(np.uint8)
    wr = _writer(rdir / "clean.mp4", *(framing[:2] if framing else (W, H)), fps, enc)
    mats = matte._frames(mfile, W, H) if has_matte else None
    overlap = 0
    prev_frames = {}
    last_full = None
    for i, fr in enumerate(matte._frames(plate, W, H)):
        t = i / fps
        head = heads[min(i, len(heads) - 1)]
        base = fr.astype(np.float32)
        layer = base.copy()
        boxes = rend.draw_behind(layer, t, head)
        if mats is not None:
            m = next(mats, None)
            a = (m[..., 0:1].astype(np.float32) / 255.0) if m is not None else 0.0
            out = layer * (1 - a) + base * a
        else:
            out = layer
        rend.draw_front(out)
        for bid, x0, y0, x1, y1 in boxes:  # QA: does a front-drawn element cover the face?
            hx, hy, hs = head["x"], head["y"], head["size"] * 0.5
            if (mats is None or bid.endswith("!front")) and x0 < hx + hs and x1 > hx - hs and y0 < hy + hs and y1 > hy - hs:
                overlap += 1
        out *= vig
        dx, dy = rend.shake(t)
        frame = np.clip(out, 0, 255).astype(np.uint8)
        if dx or dy:
            M = np.float32([[1.02, 0, dx - W * 0.01], [0, 1.02, dy - H * 0.01]])
            frame = cv2.warpAffine(frame, M, (W, H), borderMode=cv2.BORDER_REFLECT)
        for c, name in zip(cuts, names):
            if c - transitions.HALF <= i < c:
                prev_frames[c] = frame.copy()
            if name != "hard" and abs(i - c) <= transitions.HALF:
                p = (i - c) / transitions.HALF
                frame = transitions.apply(name, frame, p, prev_frames.get(c), seed + c)
        if logo is not None and not framing:  # framed reel: the logo goes on the black canvas instead
            gfx_frame = frame.astype(np.float32)
            gfx.blend(gfx_frame, logo, W - logo.shape[1] * 0.5 - min(W, H) * 0.03, logo.shape[0] * 0.5 + min(W, H) * 0.03, 1.0, 0.85)
            frame = gfx_frame.astype(np.uint8)
        if framing:
            canvas[by:by + bh, bx:bx + bw] = frame
            wr.stdin.write(canvas.tobytes())
            last_full = canvas
        else:
            last_full = np.ascontiguousarray(frame)
            wr.stdin.write(last_full.tobytes())
    n_out = int(round(planlib.outro_sec(reel) * fps))
    if n_out and last_full is not None:
        FW_, FH_ = framing[:2] if framing else (W, H)
        stamp = brand_mark(cfg, int(min(FW_, FH_) * float(cfg.at("project.end_stamp_size", 0.58))))
        if stamp is not None:
            for of in outro_frames(last_full.copy(), stamp, n_out, fps, seed):
                wr.stdin.write(of.tobytes())
        else:
            for _ in range(n_out):
                wr.stdin.write(last_full.tobytes())
    wr.stdin.close(); wr.wait()
    return {"transitions": [{"t": round(c / fps, 3), "name": n} for c, n in zip(cuts, names)],
            "face_overlap_frames": overlap}


def style_renderer(cfg, rend) -> None:
    """Graphics style switches shared by the reel render and the Resolve pack."""
    if cfg.at("gfx.hero_word", False):
        rend.hero = {"font": str(cfg.at("gfx.hero_font", "AlfaSlabOne-400.ttf")),
                     "color": gfx.hex_rgb(cfg.at("gfx.hero_color", "#E8302A")),
                     "width": float(cfg.at("gfx.hero_width", 0.94))}
    rend.fullpic = bool(cfg.at("gfx.fullpic_images", False))
    rend.big_counters = bool(cfg.at("gfx.big_counters", True))
    rend.close_up_face = float(cfg.at("gfx.close_up_face", 0.22))


class _NoParticles:
    def draw(self, dst, strength):
        return None


def brand_mark(cfg, size: int, color: str | None = None) -> np.ndarray | None:
    """The channel stamp as a clean RGBA image `size` px wide. The ink is recoloured (white by
    default) so the navy stamp reads on dark video; transparency is kept as drawn."""
    logo = cfg.at("project.logo")
    for cand in ([cfg.root / logo, cfg.path("paths.assets") / "brand" / Path(logo).name] if logo else []):
        if cand.exists():
            from PIL import Image
            img = Image.open(cand).convert("RGBA")
            img = img.resize((size, max(1, int(round(img.height * size / img.width)))), Image.LANCZOS)
            a = np.array(img)
            if a[..., 3].min() < 250:  # transparent stamp: recolour the ink
                a[..., :3] = gfx.hex_rgb(color or cfg.at("project.logo_color", "#FFFFFF"))
            return a
    return None


def _logo(cfg, W: int, H: int):
    U = min(W, H)
    mark_ = brand_mark(cfg, int(U * float(cfg.at("project.logo_size", 0.15))))
    if mark_ is not None:
        from PIL import Image, ImageFilter
        pil = Image.fromarray(mark_)
        shadow = Image.new("RGBA", pil.size, (0, 0, 0, 0))  # soft dark halo so it reads on bright shots
        shadow.putalpha(pil.getchannel("A").filter(ImageFilter.GaussianBlur(4)).point(lambda v: v * 0.55))
        out = Image.new("RGBA", (pil.width + 16, pil.height + 16), (0, 0, 0, 0))
        out.alpha_composite(shadow, (9, 10)); out.alpha_composite(pil, (8, 8))
        return np.array(out)
    name = cfg.at("project.channel_name")
    if name:
        return gfx.text_sprite(name, int(U * 0.035), max_w=int(U * 0.4))
    return None


def outro_frames(last: np.ndarray, stamp: np.ndarray, n: int, fps: int, seed: int = 0):
    """Ending: the picture blurs and dims, then the brand stamp slams into the centre of the
    screen (falls, hits with a small shake, an ink ring and a few flecks), and holds."""
    FH, FW = last.shape[:2]
    rng = np.random.default_rng(seed)
    U = min(FW, FH)
    # full-screen backdrop: the last picture, blown up to cover the frame, blurred and dark
    nz = last.reshape(-1, 3).max(axis=1) > 8
    ys, xs = np.where(nz.reshape(FH, FW))
    pic = last[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else last
    k = max(FW / pic.shape[1], FH / pic.shape[0])
    big = cv2.resize(pic, (int(pic.shape[1] * k) + 2, int(pic.shape[0] * k) + 2), interpolation=cv2.INTER_LINEAR)
    y0, x0 = (big.shape[0] - FH) // 2, (big.shape[1] - FW) // 2
    bg = cv2.GaussianBlur(big[y0:y0 + FH, x0:x0 + FW], (0, 0), U / 30).astype(np.float32) * 0.32
    yy, xx = np.ogrid[:FH, :FW]
    bg *= (1 - 0.45 * (((xx - FW / 2) / (FW / 2)) ** 2 + ((yy - FH / 2) / (FH / 2)) ** 2) / 2)[..., None]
    lastf = last.astype(np.float32)
    cx, cy = FW / 2, FH / 2
    r_st = stamp.shape[1] / 2
    T_IN, T_HIT = 0.10, 0.30
    flecks = [(rng.uniform(0, 2 * np.pi), rng.uniform(0.25, 0.6) * U, rng.uniform(2, 5) * U / 1080 * 1.6)
              for _ in range(16)]
    for i in range(n):
        t = i / fps
        a_bg = gfx.clamp01(t / 0.22)
        f = lastf * (1 - a_bg) + bg * a_bg
        if t >= T_IN:
            if t < T_HIT:  # falling toward the paper: big and tilted, speeding up
                p = (t - T_IN) / (T_HIT - T_IN)
                sc, ang, al = 2.4 - 1.4 * p * p, -15 + 9 * p, min(1.0, p * 2.5)
            else:
                d = t - T_HIT
                sc = 1 - 0.07 * np.exp(-d * 16) * np.cos(d * 34) + 0.02 * gfx.clamp01(d / 1.5)
                ang, al = -6.0, 1.0
            if t >= T_HIT:  # ink ring + flecks
                d = t - T_HIT
                ring_a = 0.45 * (1 - gfx.clamp01(d / 0.45))
                if ring_a > 0.01:
                    ov = np.zeros_like(f)
                    cv2.circle(ov, (int(cx), int(cy)), int(r_st * (1.02 + 0.5 * gfx.ease_out_cubic(gfx.clamp01(d / 0.45)))),
                               (255, 255, 255), max(2, int(U * 0.006)), lineType=cv2.LINE_AA)
                    f += ov * ring_a
                fa = 0.8 * (1 - gfx.clamp01(d / 0.6))
                if fa > 0.01:
                    ov = np.zeros_like(f)
                    for th, dist, sz in flecks:
                        rr = r_st * 0.95 + dist * (1 - np.exp(-d * 7))
                        cv2.circle(ov, (int(cx + np.cos(th) * rr), int(cy + np.sin(th) * rr)), max(1, int(sz)),
                                   (255, 255, 255), -1, lineType=cv2.LINE_AA)
                    f += ov * fa
                if d < 1 / fps:  # one-frame lift of the whole picture on impact
                    f *= 1.12
            gfx.blend(f, stamp, cx, cy, float(sc), al, ang)
        frame = np.clip(f, 0, 255).astype(np.uint8)
        k_sh = i - int(round(T_HIT * fps))
        if 0 <= k_sh < 8:  # short camera shake on impact
            amp = U * 0.016 * (1 - k_sh / 8)
            M = np.float32([[1.02, 0, rng.uniform(-amp, amp) - FW * 0.01], [0, 1.02, rng.uniform(-amp, amp) - FH * 0.01]])
            frame = cv2.warpAffine(frame, M, (FW, FH), borderMode=cv2.BORDER_REFLECT)
        yield frame


STAMP_HIT = 0.30  # seconds into the outro when the stamp lands (keep in sync with outro_frames)


def speech_track(cfg, reel: dict, out: Path) -> None:
    """Cut the reel's segments out of the source audio, with silence for the context card."""
    video = cfg.path("input.video")
    parts, inputs = [], []
    card = planlib.context_card_sec(reel)
    segs = planlib.segments(reel)
    for i, s in enumerate(segs):
        inputs += ["-ss", f"{s['start']:.3f}", "-t", f"{s['end'] - s['start']:.3f}", "-i", str(video)]
        fade = f"afade=t=in:d=0.02,afade=t=out:st={max(0, s['end'] - s['start'] - 0.03):.3f}:d=0.03"
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,{fade}[a{i}]")
    cat = "".join(f"[a{i}]" for i in range(len(segs)))
    delay = int(card * 1000)
    fc = ";".join(parts) + f";{cat}concat=n={len(segs)}:v=0:a=1,adelay={delay}|{delay}[out]"
    run([ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s16le", str(out)])


def end_at_cuts(events: list[dict], cuts: list[float], window: float = 0.6) -> None:
    """A graphic that would fade out just after a camera switch ends exactly on the switch, so it
    never hangs over the new angle for a few frames."""
    for e in events:
        for c in sorted(cuts):
            if e["t_in"] + 0.8 <= c < e["t_out"] and e["t_out"] - c <= window:
                e["t_out"] = round(c, 3)
                break


# How much a graphic deserves a sound. Only the key beats of a reel get one; the rest stay silent.
SFX_WEIGHT = {"counter": 3, "image": 2, "broll": 2, "map": 2, "text": 1, "arrow": 0}


def choose_sfx(cfg, reel: dict, events: list[dict]) -> list[dict]:
    """Keep sounds for the moments that matter: a big number, a photo taking over the background,
    or anything the plan marks with "sfx": true. At most `audio.sfx_max` per reel, never closer
    than `audio.sfx_min_gap` seconds. "sfx": false silences a moment."""
    vms = reel.get("visual_moments", [])
    cands = []
    for e in events:
        k = int(e["id"][1:]) if e.get("id", "").startswith("g") and e["id"][1:].isdigit() else -1
        vm = vms[k] if 0 <= k < len(vms) else {}
        if vm.get("sfx") is False:
            continue
        w = SFX_WEIGHT.get(e["type"], 1) + (1 if e.get("style") == "backdrop" else 0)
        if vm.get("sfx") is True or vm.get("key"):
            w = 9
        if w >= 2:
            cands.append((w, e))
    keep, gap = [], float(cfg.at("audio.sfx_min_gap", 5.0))
    for w, e in sorted(cands, key=lambda c: (-c[0], c[1]["t_in"])):
        if len(keep) >= int(cfg.at("audio.sfx_max", 4)):
            break
        if all(abs(e["t_in"] - o["t_in"]) >= gap for o in keep):
            keep.append(e)
    out = []
    for e in sorted(keep, key=lambda e: e["t_in"]):
        snd = e.get("sfx") or [{"t": e["t_in"], "kind": e["sfx_in"]}]
        other = [x for x in snd if x["kind"] != "type"]
        out += other[:2] if other else snd[:6]  # a marked typewriter line keeps a short burst of keys
        e["sound"] = True
    return out


def dims(cfg, fmt: str, preview: bool) -> tuple[int, int]:
    """Size of the finished frame."""
    W, H = (cfg.at("preview.width"), cfg.at("preview.height")) if preview else (cfg.at("reel.width"), cfg.at("reel.height"))
    return (max(W, H), min(W, H)) if fmt == "landscape" else (min(W, H), max(W, H))


def video_box(cfg, fmt: str, preview: bool) -> tuple[int, int, int, int]:
    """Where the moving picture sits inside the frame: (x, y, w, h). For `reel` (the default), the
    landscape video sits as a wide box in a 9:16 frame, with a headline above and captions below."""
    W, H = dims(cfg, fmt, preview)
    lay = cfg.at("layout.reel", {}) or {}
    if fmt != "reel" or lay.get("fullbleed"):  # full-bleed: the close-up fills the whole 9:16 frame
        return 0, 0, W, H
    vw = W
    vh = int(round(vw / float(lay.get("video_aspect", 1.4)) / 2)) * 2
    vy = int(round(H * float(lay.get("video_top", 0.27)) / 2)) * 2
    return 0, vy, vw, vh


def tag(fmt: str) -> str:
    """File-name tag per format. The reel is the main product, so it has no tag."""
    return {"landscape": "_landscape", "portrait": "_portrait"}.get(fmt, "")


def suffix(fmt: str, preview: bool) -> str:
    return tag(fmt) + ("_preview" if preview else "")


def render_reel(cfg, reel: dict, words: list[dict], manifest: dict, preview: bool = False, force: bool = False,
                fmt: str = "portrait") -> Path:
    fps = cfg.at("reel.fps", 30)
    gfx.BACKDROP.update(cfg.at("gfx.backdrop") or {})
    FW, FH = dims(cfg, fmt, preview)
    box = video_box(cfg, fmt, preview)
    W, H = box[2], box[3]  # the picture itself; for `reel` it is framed inside FW x FH at the end
    rdir = cfg.dir("renders") / (reel["id"] + suffix(fmt, preview))
    rdir.mkdir(parents=True, exist_ok=True)
    seed = sum(map(ord, reel["id"]))
    rw = planlib.reel_words(reel, words)
    duration = planlib.duration(reel)
    emph = set(reel.get("emphasis_words", []))
    emph_times = [w["start"] for w in rw if any(e and e in w["word"] for e in emph)]

    from . import reactions as react, realign
    events = gfx.build_timeline(reel, rw, manifest, fps, duration, cfg.at("gfx.lead_frames", 1),
                                cfg.at("gfx.default_hold_sec", 2.2), seed,
                                onsets=realign.load_onsets(cfg) if cfg.at("gfx.snap_to_onset", True) else None)
    if cfg.at("gfx.hero_word", False):  # the big background word pops in on its word
        for e in events:
            if e["type"] == "text":
                e["anim"], e["sfx_in"] = "pop", "pop"
                e["sfx"] = gfx._sfx_for(e)
    if cfg.at("gfx.cinematic", True):  # words settle in slowly and stay readable: no glitches, no snaps
        for e in events:
            if e["anim"] in ("glitch", "pop", "slide"):
                e["anim"] = "zoom"
    lib_path = cfg.dir("cache") / "reactions.json"
    rplan = []
    if fmt != "landscape" and lib_path.exists() and (cfg.at("reactions.enabled", True)):
        lib = read_json(lib_path)
        rplan = react.plan_reel(cfg, reel, rw, events, lib)
        if rplan:
            log.info("%s: %d reaction shot(s) of the listener: %s", reel["id"], len(rplan),
                     ", ".join(f"{r['t0']:.1f}-{r['t1']:.1f}s" for r in rplan))

    fp_plate = fingerprint(__import__("hrc").__version__, reel.get("segments"), reel.get("context_card"), emph_times, W, H, cfg.at("reframe"),
                           cfg.at("grade"), rplan, cfg.dir("cache") / "faces.json")
    if not cached(rdir / "plate.mp4", fp_plate, force):
        log.info("%s: framing %s with steady face tracking (%dx%d)...", reel["id"], fmt, W, H)
        build_plate(cfg, reel, rdir, W, H, fps, emph_times, preview, rplan)
        mark(rdir / "plate.mp4", fp_plate)
    n_frames = int(round(planlib.content_duration(reel) * fps))
    cuts_info = read_json(rdir / "cuts.json") if (rdir / "cuts.json").exists() else {}
    end_at_cuts(events, cuts_info.get("camera_switches", []) + cuts_info.get("segment_joins", []))

    # the cut-out is made only where a graphic sits behind the speaker (plus short handles)
    only_needed = bool(cfg.at("matte.only_where_needed", True))
    ranges = matte.frame_ranges([(e["t_in"], e["t_out"]) for e in events], fps, n_frames,
                                float(cfg.at("matte.handle_sec", 0.3))) if only_needed else None
    refiner = cfg.at("matte.edge_model") if cfg.at("matte.edge_refine_model", True) else None
    fp_matte = fingerprint(fp_plate, cfg.at("matte"), ranges)
    mres = {"engine": "none", "flicker": None}
    if cached(rdir / "matte.mp4", fp_matte, force):
        mres = read_json(rdir / "matte.json")
    else:
        if ranges is not None:
            log.info("%s: cut-out on %d of %d frames (%d stretch(es) with a graphic behind the speaker).", reel["id"],
                     sum(b - a for a, b in ranges), n_frames, len(ranges))
        mres = matte.make_matte(rdir / "plate.mp4", rdir / "matte.mp4", cfg.at("matte.engine", "auto"),
                                cfg.at("matte.downsample", 0.4), cfg.at("matte.temporal_smooth", 0.6),
                                objects=bool(cfg.at("matte.objects", True)), refine=bool(cfg.at("matte.refine_edges", True)),
                                max_area=float(cfg.at("matte.object_max_area", 0.22)),
                                depth_model=bool(cfg.at("matte.depth", True)),
                                use_depth_objects=bool(cfg.at("matte.depth_objects", True)),
                                depth_tol=float(cfg.at("matte.depth_tolerance", -0.03)),
                                region_top=float(cfg.at("matte.object_region_top", 0.15)),
                                ranges=ranges, refiner=refiner, n_frames=n_frames)
        write_json(rdir / "matte.json", mres)
        if mres["engine"] != "none":
            mark(rdir / "matte.mp4", fp_matte)
            mdir = cfg.dir("mattes"); (mdir / f"{reel['id']}.mp4").unlink(missing_ok=True)
            try:
                (mdir / f"{reel['id']}.mp4").symlink_to((rdir / "matte.mp4").resolve())
            except OSError:
                pass

    log.info("%s (%s): %d speech-tied graphics, compositing...", reel["id"], fmt, len(events))
    comp = composite(cfg, reel, rdir, W, H, fps, events, mres["engine"] != "none", preview, seed,
                     framing=(FW, FH, box) if (FW, FH) != (W, H) else None)

    sfx_events = choose_sfx(cfg, reel, events)
    if cfg.at("transitions.sfx", False):
        sfx_events += [{"t": tr["t"], "kind": transitions.SFX[tr["name"]]} for tr in comp["transitions"]
                       if transitions.SFX.get(tr["name"])]
    if planlib.context_card_sec(reel):
        sfx_events += [{"t": 0.05, "kind": "hit", "gain_db": -2}]
    if planlib.outro_sec(reel):  # the brand stamp: a soft air pull, then the stamp landing
        t_out = planlib.content_duration(reel)
        sfx_events += [{"t": t_out + STAMP_HIT - 0.22, "kind": "swish", "gain_db": -10},
                       {"t": t_out + STAMP_HIT, "kind": "stamp"}]
    speech = rdir / "speech.wav"
    speech_track(cfg, reel, speech)
    used = audio.mix(cfg, speech, duration, sfx_events, reel.get("mood", "inspirational"), rdir / "mix.wav", seed)
    write_json(rdir / "render.json", {"reel": reel["id"], "format": fmt, "size": [FW, FH], "video_box": list(box),
                                      "seed": seed, "duration": duration, "outro_sec": planlib.outro_sec(reel), "gfx": events,
                                      **comp, "audio": used, "matte": mres, "reactions": rplan})
    log.info("%s: picture and sound ready (%d transitions, %d SFX, music: %s).", reel["id"],
             len(comp["transitions"]), len(used["sfx"]), Path(used["music"]).name if used["music"] else "none")
    return rdir
