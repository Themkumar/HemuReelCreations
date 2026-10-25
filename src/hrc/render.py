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


def build_plate(cfg, reel: dict, rdir: Path, W: int, H: int, fps: int, emph_times: list[float], preview: bool) -> dict:
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

    def place(img: np.ndarray) -> np.ndarray:
        if not off:
            return img
        if heads:
            heads[-1]["y"] = round(heads[-1]["y"] + off, 1)
        return _with_top_band(img, W, H, off)

    paths = [crop_path(faces, shots, seg["start"], seg["end"], fps, sw, sh, W, Hv,
                       cfg.at("reframe.smoothing_min_cutoff", 0.6), cfg.at("reframe.smoothing_beta", 0.01),
                       cfg.at("reframe.head_room", 0.30), cfg.at("reframe.deadzone", 0.12),
                       cfg.at("reframe.max_pan", 0.012)) for seg in segs]
    if card_n:
        fr0 = next(_reader(video, segs[0]["start"], 0.2, fps, sw, sh))
        first = _with_top_band(_crop(fr0, paths[0][0], 1.0, W, Hv, []), W, H, off) if off else _crop(fr0, paths[0][0], 1.0, W, H, [])
        for cf in _context_card(first, reel["context_card"], W, H, card_n, cfg.at("project.brand_colors")):
            wr.stdin.write(cf.tobytes())
    fi = card_n
    for seg, path in zip(segs, paths):
        n = int(round((seg["end"] - seg["start"]) * fps))
        k, last = 0, None
        for fr in _reader(video, seg["start"], seg["end"] - seg["start"] + 0.2, fps, sw, sh):
            if k >= n:
                break
            last = fr
            wr.stdin.write(place(_crop(fr, path[min(k, len(path) - 1)], zoom[fi], W, Hv, heads)).tobytes())
            k += 1; fi += 1
        while k < n and last is not None:  # pad if the decoder returned a frame or two short
            wr.stdin.write(place(_crop(last, path[-1], zoom[min(fi, total - 1)], W, Hv, heads)).tobytes())
            k += 1; fi += 1
    wr.stdin.close(); wr.wait()
    card_head = heads[0] if heads else {"x": W / 2, "y": H * 0.3, "size": W * 0.15}
    heads = [card_head] * card_n + heads
    write_json(rdir / "heads.json", heads)
    return {"frames": total, "card_frames": card_n}


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
    heads.append({"x": round((hx_src - x) * s, 1), "y": round((hy_src - y) * s, 1), "size": round(head["size"] * z, 1)})
    return np.ascontiguousarray(out)


def composite(cfg, reel: dict, rdir: Path, W: int, H: int, fps: int, events: list[dict], has_matte: bool,
              preview: bool, seed: int) -> dict:
    plate = rdir / "plate.mp4"
    mfile = rdir / "matte.mp4"
    heads = read_json(rdir / "heads.json")
    colors = cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"]
    if not has_matte:  # without a cut-out, never put big words over the face
        for e in events:
            if e["anchor"] == "behind_head":
                e["anchor"] = "top_split"
    rend = gfx.GfxRenderer(events, W, H, fps, colors, seed)
    cuts = [int(round(t * fps)) for t in planlib.cut_points(reel)]
    names = transitions.choose(len(cuts), reel.get("mood", "inspirational"), cfg.at("transitions.library"), seed)
    yy, xx = np.ogrid[:H, :W]
    vig = (1 - 0.35 * ((((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2))[..., None].astype(np.float32)
    logo = _logo(cfg, W, H)
    enc = video_encoder_args(cfg, preview) if preview else ["-c:v", "libx264", "-preset", "fast", "-crf", "15", "-pix_fmt", "yuv420p"]
    wr = _writer(rdir / "clean.mp4", W, H, fps, enc)
    mats = matte._frames(mfile, W, H) if has_matte else None
    overlap = 0
    prev_frames = {}
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
        for _, x0, y0, x1, y1 in boxes:  # QA: does a front-drawn element cover the face?
            hx, hy, hs = head["x"], head["y"], head["size"] * 0.5
            if mats is None and x0 < hx + hs and x1 > hx - hs and y0 < hy + hs and y1 > hy - hs:
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
        if logo is not None:
            gfx_frame = frame.astype(np.float32)
            gfx.blend(gfx_frame, logo, W - logo.shape[1] * 0.5 - min(W, H) * 0.04, logo.shape[0] * 0.5 + min(W, H) * 0.03, 1.0, 0.85)
            frame = gfx_frame.astype(np.uint8)
        wr.stdin.write(np.ascontiguousarray(frame).tobytes())
    wr.stdin.close(); wr.wait()
    return {"transitions": [{"t": round(c / fps, 3), "name": n} for c, n in zip(cuts, names)],
            "face_overlap_frames": overlap}


def _logo(cfg, W: int, H: int):
    logo = cfg.at("project.logo")
    name = cfg.at("project.channel_name")
    for cand in ([cfg.root / logo, cfg.path("paths.assets") / "brand" / Path(logo).name] if logo else []):
        if cand.exists():
            return gfx.image_sprite(cand, int(min(W, H) * 0.26), int(min(W, H) * 0.1))
    if name:
        return gfx.text_sprite(name, int(min(W, H) * 0.035), max_w=int(min(W, H) * 0.4))
    return None


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


def dims(cfg, fmt: str, preview: bool) -> tuple[int, int]:
    W, H = (cfg.at("preview.width"), cfg.at("preview.height")) if preview else (cfg.at("reel.width"), cfg.at("reel.height"))
    return (max(W, H), min(W, H)) if fmt == "landscape" else (min(W, H), max(W, H))


def suffix(fmt: str, preview: bool) -> str:
    return ("_landscape" if fmt == "landscape" else "") + ("_preview" if preview else "")


def render_reel(cfg, reel: dict, words: list[dict], manifest: dict, preview: bool = False, force: bool = False,
                fmt: str = "portrait") -> Path:
    fps = cfg.at("reel.fps", 30)
    W, H = dims(cfg, fmt, preview)
    rdir = cfg.dir("renders") / (reel["id"] + suffix(fmt, preview))
    rdir.mkdir(parents=True, exist_ok=True)
    seed = sum(map(ord, reel["id"]))
    rw = planlib.reel_words(reel, words)
    duration = planlib.duration(reel)
    emph = set(reel.get("emphasis_words", []))
    emph_times = [w["start"] for w in rw if any(e and e in w["word"] for e in emph)]

    fp_plate = fingerprint(__import__("hrc").__version__, reel.get("segments"), reel.get("context_card"), emph_times, W, H, cfg.at("reframe"),
                           cfg.dir("cache") / "faces.json")
    if not cached(rdir / "plate.mp4", fp_plate, force):
        log.info("%s: framing %s with steady face tracking (%dx%d)...", reel["id"], fmt, W, H)
        build_plate(cfg, reel, rdir, W, H, fps, emph_times, preview)
        mark(rdir / "plate.mp4", fp_plate)

    fp_matte = fingerprint(fp_plate, cfg.at("matte"))
    mres = {"engine": "none", "flicker": None}
    if cached(rdir / "matte.mp4", fp_matte, force):
        mres = read_json(rdir / "matte.json")
    else:
        mres = matte.make_matte(rdir / "plate.mp4", rdir / "matte.mp4", cfg.at("matte.engine", "auto"),
                                cfg.at("matte.downsample", 0.4), cfg.at("matte.temporal_smooth", 0.6))
        write_json(rdir / "matte.json", mres)
        if mres["engine"] != "none":
            mark(rdir / "matte.mp4", fp_matte)
            mdir = cfg.dir("mattes"); (mdir / f"{reel['id']}.mp4").unlink(missing_ok=True)
            try:
                (mdir / f"{reel['id']}.mp4").symlink_to((rdir / "matte.mp4").resolve())
            except OSError:
                pass

    events = gfx.build_timeline(reel, rw, manifest, fps, duration, cfg.at("gfx.lead_frames", 1),
                                cfg.at("gfx.default_hold_sec", 2.2), seed)
    log.info("%s (%s): %d speech-tied graphics, compositing...", reel["id"], fmt, len(events))
    comp = composite(cfg, reel, rdir, W, H, fps, events, mres["engine"] != "none", preview, seed)

    sfx_events = [{"t": e["t_in"], "kind": e["sfx_in"]} for e in events]
    sfx_events += [{"t": tr["t"], "kind": transitions.SFX[tr["name"]]} for tr in comp["transitions"]
                   if transitions.SFX.get(tr["name"])]
    if planlib.context_card_sec(reel):
        sfx_events += [{"t": 0.05, "kind": "hit", "gain_db": -2}]
    speech = rdir / "speech.wav"
    speech_track(cfg, reel, speech)
    used = audio.mix(cfg, speech, duration, sfx_events, reel.get("mood", "inspirational"), rdir / "mix.wav", seed)
    write_json(rdir / "render.json", {"reel": reel["id"], "format": fmt, "size": [W, H], "duration": duration, "gfx": events,
                                      **comp, "audio": used, "matte": mres})
    log.info("%s: picture and sound ready (%d transitions, %d SFX, music: %s).", reel["id"],
             len(comp["transitions"]), len(used["sfx"]), Path(used["music"]).name if used["music"] else "none")
    return rdir
