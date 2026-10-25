"""Resolve pack: every part of a reel as its own editable layer, plus an FCPXML timeline that
puts them on separate tracks in DaVinci Resolve.

output/<reel>/resolve_<lang>/
  <reel>_<lang>.fcpxml       File > Import > Timeline in Resolve
  EDIT_SHEET.md              what every track and clip is, with times, text, fonts, colours, sounds
  media/01_video.mp4         the framed podcast video (ambient particles + vignette, no graphics)
  media/gfx/gNN.mov          each graphic on its own, with transparency (ProRes 4444)
  media/gfx/gNN_person.mov   the speaker cut out over that graphic (keeps the graphic behind him)
  media/gfx/gNN_front.mov    parts of a graphic that sit in front of the speaker (counters)
  media/brand/logo.png       corner logo; media/brand/stamp.png the stamp alone
  media/headline_<lang>.png  headline bar text (and an editable Text title, off by default)
  media/captions_<lang>.mov  karaoke captions with transparency; captions_<lang>.srt for a subtitle track
  media/outro.mp4            ending stamp
  media/audio/               speech.wav, music.wav, sfx/NN_kind.wav (one file per sound, gain applied)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape


def escape(text: str) -> str:
    """Escape text for XML attributes and bodies, including double quotes."""
    return _xml_escape(str(text), {'"': "&quot;"})

import cv2
import numpy as np

from . import audio, gfx, karaoke, log, matte, plan as planlib
from .util import ffmpeg, read_json, run, write_json

PRORES_ALPHA = ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
                "-vendor", "apl0"]


def _ff_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _rgba_writer(out: Path, W: int, H: int, fps: int):
    out.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                             "-r", str(fps), "-i", "-", *PRORES_ALPHA, str(out)], stdin=subprocess.PIPE)


def unmix(black: np.ndarray, white: np.ndarray) -> np.ndarray:
    """A layer drawn once over black and once over white -> straight RGBA with true transparency."""
    a = np.clip(1.0 - (white - black) / 255.0, 0.0, 1.0)
    rgb = np.where(a[..., :1] > 1e-3, black / np.maximum(a[..., :1], 1e-3), 0.0)
    return np.dstack([np.clip(rgb, 0, 255), a[..., :1] * 255]).astype(np.uint8) if a.ndim == 3 else None


def _canvas_rgba(img: np.ndarray, FW: int, FH: int, box) -> np.ndarray:
    bx, by, bw, bh = box
    out = np.zeros((FH, FW, 4), np.uint8)
    out[by:by + bh, bx:bx + bw] = img
    return out


def _frames_between(path: Path, W: int, H: int, f0: int, f1: int, fps: int):
    cmd = [ffmpeg(), "-v", "error", "-ss", f"{f0 / fps:.4f}", "-i", str(path), "-frames:v", str(max(0, f1 - f0)),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    proc.wait()


class _NoParticles:
    def draw(self, dst, strength):  # graphics clips carry no ambient particles (they live on the video layer)
        return None


def build_video_layer(cfg, rdir: Path, info: dict, out: Path, fps: int) -> None:
    """Plate + ambient particles behind the speaker + vignette, framed like the reel. No graphics."""
    FW, FH = info["size"]
    bx, by, bw, bh = info.get("video_box") or (0, 0, FW, FH)
    colors = cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"]
    parts = gfx.Particles(bw, bh, seed=info.get("seed", 0), color=gfx.hex_rgb(colors[0]))
    has_matte = (rdir / "matte.mp4").exists() and info.get("matte", {}).get("engine", "none") != "none"
    mats = matte._frames(rdir / "matte.mp4", bw, bh) if has_matte else None
    yy, xx = np.ogrid[:bh, :bw]
    vig = (1 - 0.35 * ((((xx - bw / 2) / (bw / 2)) ** 2 + ((yy - bh / 2) / (bh / 2)) ** 2) / 2))[..., None].astype(np.float32)
    canvas = np.zeros((FH, FW, 3), np.uint8)
    wr = subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{FW}x{FH}",
                           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "12",
                           "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    for fr in matte._frames(rdir / "plate.mp4", bw, bh):
        base = fr.astype(np.float32)
        layer = base.copy()
        parts.draw(layer, 0.25)
        if mats is not None:
            m = next(mats, None)
            a = (m[..., 0:1].astype(np.float32) / 255.0) if m is not None else 0.0
            layer = layer * (1 - a) + base * a
        layer *= vig
        canvas[by:by + bh, bx:bx + bw] = np.clip(layer, 0, 255).astype(np.uint8)
        wr.stdin.write(canvas.tobytes())
    wr.stdin.close(); wr.wait()


def build_gfx_layers(cfg, rdir: Path, info: dict, media: Path, fps: int) -> list[dict]:
    """One transparent clip per graphic (plus the speaker cut-out above it, and front parts)."""
    FW, FH = info["size"]
    box = info.get("video_box") or (0, 0, FW, FH)
    bw, bh = box[2], box[3]
    colors = cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"]
    heads = read_json(rdir / "heads.json")
    has_matte = (rdir / "matte.mp4").exists() and info.get("matte", {}).get("engine", "none") != "none"
    clips = []
    for ev in info.get("gfx", []):
        f0, f1 = int(round(ev["t_in"] * fps)), int(round(ev["t_out"] * fps))
        if f1 <= f0:
            continue
        rend = gfx.GfxRenderer([ev], bw, bh, fps, colors, info.get("seed", 0))
        rend.particles = _NoParticles()
        photo = None
        if ev.get("file") and ev["type"] in ("image", "broll") and rend._wants_backdrop(ev):
            photo = _photo_layers(cfg, ev, rend, heads, media, box, FW, FH, f0, f1, fps)
            rend.backdrop_mode = "skip"
        out = media / "gfx" / f"{ev['id']}.mov"
        out_p = media / "gfx" / f"{ev['id']}_person.mov"
        out_f = media / "gfx" / f"{ev['id']}_front.mov"
        wr = _rgba_writer(out, FW, FH, fps)
        wr_p = _rgba_writer(out_p, FW, FH, fps) if has_matte else None
        front_frames = []
        plates = _frames_between(rdir / "plate.mp4", bw, bh, f0, f1, fps)
        mats = _frames_between(rdir / "matte.mp4", bw, bh, f0, f1, fps) if has_matte else None
        for i in range(f0, f1):
            t = i / fps
            head = heads[min(i, len(heads) - 1)]
            blk = np.zeros((bh, bw, 3), np.float32)
            wht = np.full((bh, bw, 3), 255.0, np.float32)
            state = rend.rng.bit_generator.state
            rend.draw_behind(blk, t, head)
            ops = list(getattr(rend, "front_ops", []))
            rend.rng.bit_generator.state = state  # same glitch/random choices on both passes
            rend.draw_behind(wht, t, head)
            wr.stdin.write(_canvas_rgba(unmix(blk, wht), FW, FH, box).tobytes())
            if ops:
                fb, fw_ = np.zeros((bh, bw, 3), np.float32), np.full((bh, bw, 3), 255.0, np.float32)
                for op in ops:
                    gfx.blend(fb, *op); gfx.blend(fw_, *op)
                front_frames.append((i, _canvas_rgba(unmix(fb, fw_), FW, FH, box)))
            if wr_p is not None:
                pl, m = next(plates, None), next(mats, None)
                if pl is None or m is None:
                    rgba = np.zeros((bh, bw, 4), np.uint8)
                else:
                    rgba = np.dstack([pl, m[..., :1]])
                wr_p.stdin.write(_canvas_rgba(rgba, FW, FH, box).tobytes())
        wr.stdin.close(); wr.wait()
        if wr_p is not None:
            wr_p.stdin.close(); wr_p.wait()
        entry = {"id": ev["id"], "type": ev["type"], "anim": ev["anim"], "t_in": f0 / fps, "t_out": f1 / fps,
                 "text": ev.get("text"), "value": ev.get("value"), "style": ev.get("style"), "file": out.name,
                 "person": out_p.name if wr_p is not None else None, "front": None, "sound": ev.get("sound", False),
                 "image": Path(ev["file"]).name if ev.get("file") else None,
                 "photo": photo["clip"] if photo else None, "photo_hd": photo["still"] if photo else None,
                 "label": ev.get("label") or ev.get("text")}
        if front_frames:
            wf = _rgba_writer(out_f, FW, FH, fps)
            have = dict(front_frames)
            empty = np.zeros((FH, FW, 4), np.uint8)
            for i in range(f0, f1):
                wf.stdin.write(have.get(i, empty).tobytes())
            wf.stdin.close(); wf.wait()
            entry["front"] = out_f.name
        clips.append(entry)
        log.info("  layer %s (%s, %s) %.2f-%.2fs", ev["id"], ev["type"], ev["anim"], entry["t_in"], entry["t_out"])
    return clips


def _photo_layers(cfg, ev, rend, heads, media: Path, box, FW, FH, f0, f1, fps) -> dict:
    """A photo background on its own: the rendered clip (look, slow push-in, fades) and an untouched
    HD still at twice the timeline size, for grading or swapping in Resolve."""
    bx, by, bw, bh = box
    out = media / "gfx" / f"{ev['id']}_photo.mov"
    wr = _rgba_writer(out, FW, FH, fps)
    prend = gfx.GfxRenderer([ev], bw, bh, fps, ["#FFFFFF", "#FFFFFF"], 0)
    prend.particles = _NoParticles()
    prend.layouts = rend.layouts  # same layout (and the same photo) as the graphic itself
    prend.backdrop_mode = "only"
    for i in range(f0, f1):
        head = heads[min(i, len(heads) - 1)]
        blk = np.zeros((bh, bw, 3), np.float32)
        wht = np.full((bh, bw, 3), 255.0, np.float32)
        prend.draw_behind(blk, i / fps, head)
        prend.draw_behind(wht, i / fps, head)
        wr.stdin.write(_canvas_rgba(unmix(blk, wht), FW, FH, box).tobytes())
    wr.stdin.close(); wr.wait()
    src = Path(ev.get("original") or ev["file"])
    flat = {"blur": 0, "dim": 0, "desaturate": 0, "vignette": 0, "tint": 0, "push_in": 0}
    hd = gfx.backdrop_image(src, bw * 2, bh * 2, look=flat)
    canvas = np.zeros((FH * 2, FW * 2, 4), np.uint8)
    canvas[by * 2:by * 2 + bh * 2, bx * 2:bx * 2 + bw * 2, :3] = hd[:bh * 2, :bw * 2]
    canvas[by * 2:by * 2 + bh * 2, bx * 2:bx * 2 + bw * 2, 3] = 255
    still = media / "photos" / f"{ev['id']}_photo_hd.png"
    still.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(still), cv2.cvtColor(canvas, cv2.COLOR_RGBA2BGRA))
    full = media / "photos" / f"{ev['id']}_original{src.suffix.lower()}"
    shutil.copy2(src, full)
    return {"clip": out.name, "still": still.name}


def _render_ass_alpha(ass: Path, fonts: Path, out: Path, FW: int, FH: int, fps: int, dur: float, still_at=None):
    src = f"color=c=black@0.0:s={FW}x{FH}:r={fps}:d={dur:.3f},format=rgba"
    vf = f"ass='{_ff_path(ass)}':fontsdir='{_ff_path(fonts)}':alpha=1"
    if still_at is not None:
        run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf, "-ss", f"{still_at:.3f}",
             "-frames:v", "1", "-pix_fmt", "rgba", out])
    else:
        run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf, *PRORES_ALPHA, out])


def write_srt(subs: dict, out: Path) -> Path:
    lines = []
    for i, ch in enumerate([c for c in subs["chunks"] if c["words"]], 1):
        text, prev = [], ch["words"][0].get("line", 0)
        for w in ch["words"]:
            if w.get("line", 0) != prev:
                text.append("\n"); prev = w.get("line", 0)
            elif text:
                text.append(" ")
            text.append(w["w"])
        lines.append(f"{i}\n{_srt_t(ch['start'])} --> {_srt_t(ch['end'])}\n{''.join(text).strip()}\n")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def _srt_t(s: float) -> str:
    ms = int(round(max(0, s) * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def build_pack(cfg, reel: dict, rdir: Path, lang: str) -> Path:
    from . import render as R
    info = read_json(rdir / "render.json")
    info.setdefault("seed", sum(map(ord, reel["id"])))
    fps = cfg.at("reel.fps", 30)
    FW, FH = info["size"]
    box = info.get("video_box") or (0, 0, FW, FH)
    dur = float(info["duration"])
    outro = float(info.get("outro_sec", 0))
    content = dur - outro
    pack = cfg.dir("output") / reel["id"] / f"resolve_{lang}"
    media = pack / "media"
    if pack.exists():
        shutil.rmtree(pack)
    (media / "audio" / "sfx").mkdir(parents=True, exist_ok=True)
    (media / "brand").mkdir(parents=True, exist_ok=True)
    log.info("%s: building the Resolve pack (%s)...", reel["id"], lang)

    # pictures shared by all languages are built once per reel and copied
    shared = rdir / "resolve_shared"
    stamp_fp = (rdir / "render.json").stat().st_mtime
    marker = shared / f".built_{int(stamp_fp)}"
    if not marker.exists():
        if shared.exists():
            shutil.rmtree(shared)
        shared.mkdir(parents=True)
        build_video_layer(cfg, rdir, info, shared / "01_video.mp4", fps)
        clips = build_gfx_layers(cfg, rdir, info, shared, fps)
        write_json(shared / "gfx_layers.json", clips)
        if outro:
            run([ffmpeg(), "-v", "error", "-y", "-ss", f"{content:.3f}", "-i", rdir / "clean.mp4", "-an",
                 "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", shared / "outro.mp4"])
        logo = R._logo(cfg, box[2], box[3])
        if logo is not None:
            canvas = np.zeros((FH, FW, 4), np.float32)
            U = min(box[2], box[3])
            cx = box[0] + box[2] - logo.shape[1] * 0.5 - U * 0.03
            cy = box[1] + logo.shape[0] * 0.5 + U * 0.03
            h, w = logo.shape[:2]
            x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
            canvas[y0:y0 + h, x0:x0 + w] = logo
            canvas[..., 3] *= 0.85
            cv2.imwrite(str(shared / "logo.png"), cv2.cvtColor(canvas.astype(np.uint8), cv2.COLOR_RGBA2BGRA))
        st = R.brand_mark(cfg, int(min(FW, FH) * float(cfg.at("project.end_stamp_size", 0.58))))
        if st is not None:
            cv2.imwrite(str(shared / "stamp.png"), cv2.cvtColor(st, cv2.COLOR_RGBA2BGRA))
        marker.touch()
    for f in shared.rglob("*"):
        if f.is_file() and not f.name.startswith(".") and f.name != "gfx_layers.json":
            dst = media / ("brand" if f.suffix == ".png" and f.parent == shared else "") / f.relative_to(shared)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    clips = read_json(shared / "gfx_layers.json")

    # captions + headline for this language
    fonts = cfg.path("paths.assets") / "fonts"
    subs = read_json(cfg.dir("subs") / f"{reel['id']}_{lang}.json")
    heads = reel.get("headline") or {}
    if isinstance(heads, str):
        heads = {"en": heads}
    headline = heads.get(lang) or heads.get("en") or reel.get("title")
    cap_ass = pack / f"captions_{lang}.ass"
    cap_ass.write_text(karaoke.build_ass(subs, cfg, FW, FH, box, None, None), encoding="utf-8")
    _render_ass_alpha(cap_ass, fonts, media / f"captions_{lang}.mov", FW, FH, fps, content)
    write_srt(subs, media / f"captions_{lang}.srt")
    head_png = None
    if headline and tuple(box[2:]) != (FW, FH):
        h_ass = pack / f"headline_{lang}.ass"
        h_ass.write_text(karaoke.build_ass({"lang": lang, "chunks": []}, cfg, FW, FH, box, headline, 2.0), encoding="utf-8")
        head_png = media / f"headline_{lang}.png"
        _render_ass_alpha(h_ass, fonts, head_png, FW, FH, fps, 1.0, still_at=0.5)

    # audio: speech, music, and every sound effect as its own clip
    used = info.get("audio", {})
    st = audio.stems(cfg, rdir / "speech.wav", dur, used.get("music"), media / "audio")
    sfx_clips = []
    for k, s in enumerate(used.get("sfx", []), 1):
        src = Path(s.get("path") or (cfg.path("paths.assets") / "sfx" / s["kind"] / s["file"]))
        if not src.exists():
            continue
        dst = media / "audio" / "sfx" / f"{k:02d}_{s['kind']}.wav"
        run([ffmpeg(), "-v", "error", "-y", "-i", src, "-af", f"aresample=48000,aformat=channel_layouts=stereo,"
             f"volume={s.get('gain_db', -8)}dB", "-c:a", "pcm_s24le", dst])
        sfx_clips.append({**s, "clip": dst.name, "len": _audio_len(dst)})

    plan_cuts = planlib.cut_points(reel)
    segs = []
    starts = [0.0] + list(plan_cuts) + [content]
    names = {round(t["t"], 3): t["name"] for t in info.get("transitions", [])}
    for a, b in zip(starts, starts[1:]):
        if b - a > 1 / fps:
            segs.append((a, b))
    fcp = pack / f"{reel['id']}_{lang}.fcpxml"
    write_fcpxml(fcp, reel, lang, FW, FH, fps, dur, content, segs, clips, media, head_png, headline, st, sfx_clips,
                 names, cfg)
    write_sheet(pack / "EDIT_SHEET.md", reel, lang, info, clips, sfx_clips, segs, names, headline, cfg, content, dur)
    log.info("%s: Resolve pack ready: %s", reel["id"], fcp.relative_to(cfg.root))
    return pack


def _audio_len(p: Path) -> float:
    import wave
    try:
        with wave.open(str(p)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 1.0


def _title_xml(text: str, accent: str, latin: bool, font: str, size: int, sid: str = "h") -> tuple[str, str]:
    """Headline as an FCPXML Basic Title: each starred word in the accent colour."""
    r, g, b = (int(accent.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    parts, on = [], False
    for w in text.split():
        st, en = w.startswith("*"), w.endswith("*") and len(w) > 1
        word = w.strip("*")
        if latin:
            word = word.upper()
        if st:
            on = True
        parts.append((word, on))
        if en:
            on = False
    body = "".join(f'<text-style ref="{sid}ts{1 if c else 0}">{escape(wd)} </text-style>' for wd, c in parts)
    defs = (f'<text-style-def id="{sid}ts0"><text-style font="{escape(font)}" fontSize="{size}" fontColor="1 1 1 1" '
            f'alignment="center"/></text-style-def>'
            f'<text-style-def id="{sid}ts1"><text-style font="{escape(font)}" fontSize="{size}" fontColor="{r:.3f} {g:.3f} {b:.3f} 1" '
            f'alignment="center"/></text-style-def>')
    return f"<text>{body.strip()}</text>", defs


def write_fcpxml(out: Path, reel, lang, FW, FH, fps, dur, content, segs, clips, media: Path, head_png, headline, stems,
                 sfx_clips, names, cfg) -> Path:
    def fr(sec: float) -> str:
        return f"{int(round(sec * fps))}/{fps}s"

    res, items, rid = [], [], [2]

    def asset(path: Path, video=True, audio_=False, still=False, length: float | None = None, fmt: str = "") -> str:
        rid[0] += 1
        i = f"r{rid[0]}"
        dur_attr = "0s" if still else fr(length if length is not None else dur)
        a = f' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if audio_ else ""
        res.append(f'<asset id="{i}" name="{escape(path.stem)}" start="0s" duration="{dur_attr}" '
                   f'hasVideo="{1 if video else 0}"{a} format="{fmt or ("r2" if still else "r1")}">'
                   f'<media-rep kind="original-media" src="{escape(path.resolve().as_uri())}"/></asset>')
        return i

    # V1: the video, cut at every join so you can drop Resolve transitions on the edit points
    v = asset(media / "01_video.mp4", length=content)
    for a, b in segs:
        items.append(f'<asset-clip ref="{v}" lane="1" offset="{fr(a)}" start="{fr(a)}" duration="{fr(b - a)}" '
                     f'name="Video {a:.1f}-{b:.1f}s"/>')
    if (media / "outro.mp4").exists():
        o = asset(media / "outro.mp4", length=dur - content)
        items.append(f'<asset-clip ref="{o}" lane="1" offset="{fr(content)}" start="0s" duration="{fr(dur - content)}" '
                     f'name="Ending stamp"/>')
    accent = (cfg.at("project.brand_colors") or ["#FFD400", "#E8252B"])[1]
    gfont = cfg.at("resolve.graphic_font", "Anton")
    title_end: list[float] = []
    for c in clips:  # V2-V6: photo, photo HD still, graphic, speaker cut-out, front parts
        d = c["t_out"] - c["t_in"]
        label = (c.get("text") or c["type"]).replace("*", "")
        if c.get("photo"):
            ph = asset(media / "gfx" / c["photo"], length=d)
            items.append(f'<asset-clip ref="{ph}" lane="2" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                         f'name="{escape(c["id"])} photo"/>')
        if c.get("photo_hd"):
            hd = asset(media / "photos" / c["photo_hd"], still=True, fmt="r3")
            items.append(f'<video ref="{hd}" lane="3" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                         f'enabled="0" name="{escape(c["id"])} photo HD still (off)"/>')
        g = asset(media / "gfx" / c["file"], length=d)
        items.append(f'<asset-clip ref="{g}" lane="4" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                     f'name="{escape(c["id"] + " " + label)}"/>')
        if c.get("person"):
            p = asset(media / "gfx" / c["person"], length=d)
            items.append(f'<asset-clip ref="{p}" lane="5" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                         f'name="{c["id"]} cut-out (you + objects in front)"/>')
        if c.get("front"):
            f_ = asset(media / "gfx" / c["front"], length=d)
            items.append(f'<asset-clip ref="{f_}" lane="6" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                         f'name="{c["id"]} front"/>')
        words = " ".join(str(x) for x in (c.get("value"), c.get("label")) if x)
        if words:
            tl = next((k for k, e in enumerate(title_end) if e <= c["t_in"]), None)
            if tl is None:
                title_end.append(c["t_out"]); tl = len(title_end) - 1
            else:
                title_end[tl] = c["t_out"]
            latin = not any(0x0900 <= ord(ch) <= 0x0DFF for ch in words)  # no Indian script letters
            text, defs = _title_xml(words, accent, latin, gfont if latin else
                                    cfg.at(f"subtitles.fonts.{lang}", "Noto Sans"),
                                    int(cfg.at("resolve.graphic_size", 96)), f"g{c['id']}")
            items.append(f'<title ref="r9" lane="{11 + tl}" offset="{fr(c["t_in"])}" start="0s" duration="{fr(d)}" '
                         f'enabled="0" name="{escape(c["id"])} text (editable, off)">{text}{defs}</title>')
    if (media / "brand" / "logo.png").exists():
        lg = asset(media / "brand" / "logo.png", still=True)
        items.append(f'<video ref="{lg}" lane="7" offset="0s" start="0s" duration="{fr(content)}" name="Corner logo"/>')
    if head_png is not None:
        hp = asset(head_png, still=True)
        items.append(f'<video ref="{hp}" lane="8" offset="0s" start="0s" duration="{fr(content)}" name="Headline (picture)"/>')
    if headline:
        latin = lang == "en"
        font = "Anton" if latin else cfg.at(f"subtitles.fonts.{lang}", "Noto Sans")
        text, defs = _title_xml(headline, accent, latin, font, int(cfg.at("layout.reel.headline_size", 124) * 0.6))
        items.append(f'<title ref="r9" lane="9" offset="0s" start="0s" duration="{fr(content)}" enabled="0" '
                     f'name="Headline (editable text, off)">{text}{defs}</title>')
    cap = media / f"captions_{lang}.mov"
    if cap.exists():
        cp = asset(cap, length=content)
        items.append(f'<asset-clip ref="{cp}" lane="10" offset="0s" start="0s" duration="{fr(content)}" '
                     f'name="Karaoke captions {lang}"/>')
    # audio
    sp = asset(stems["speech"], video=False, audio_=True)
    items.append(f'<asset-clip ref="{sp}" lane="-1" offset="0s" start="0s" duration="{fr(dur)}" name="Speech"/>')
    if stems.get("music"):
        mu = asset(stems["music"], video=False, audio_=True)
        items.append(f'<asset-clip ref="{mu}" lane="-2" offset="0s" start="0s" duration="{fr(dur)}" name="Music"/>')
    lanes_end: list[float] = []
    for s in sfx_clips:
        t0, t1 = s["start"], s["start"] + s["len"]
        lane = next((i for i, e in enumerate(lanes_end) if e <= t0), None)
        if lane is None:
            lanes_end.append(t1); lane = len(lanes_end) - 1
        else:
            lanes_end[lane] = t1
        a = asset(media / "audio" / "sfx" / s["clip"], video=False, audio_=True, length=s["len"])
        items.append(f'<asset-clip ref="{a}" lane="{-3 - lane}" offset="{fr(t0)}" start="0s" duration="{fr(s["len"])}" '
                     f'name="SFX {escape(s["kind"])} ({s["gain_db"]} dB)"/>')
    markers = [f'<marker start="{fr(a)}" duration="1/{fps}s" value="Join: {escape(names.get(round(a, 3), "cut"))}"/>'
               for a, _ in segs[1:]]
    markers += [f'<marker start="{fr(c["t_in"])}" duration="1/{fps}s" value="{escape(c["id"] + " " + (c.get("text") or c["type"]).replace("*", ""))}"/>'
                for c in clips]
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="FFVideoFormatRateUndefined" frameDuration="1/{fps}s" width="{FW}" height="{FH}"/>
    <format id="r2" name="FFVideoFormatRateUndefined" width="{FW}" height="{FH}"/>
    <format id="r3" name="FFVideoFormatRateUndefined" width="{FW * 2}" height="{FH * 2}"/>
    <effect id="r9" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"/>
    {chr(10).join('    ' + r for r in res)}
  </resources>
  <library>
    <event name="HRC {escape(reel['id'])}">
      <project name="{escape(reel['id'])} {lang} - layered">
        <sequence format="r1" duration="{fr(dur)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
            <gap name="Reel" offset="0s" start="0s" duration="{fr(dur)}">
              {chr(10).join('              ' + m for m in markers)}
              {chr(10).join('              ' + i for i in items)}
            </gap>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    out.write_text(xml, encoding="utf-8")
    return out


def write_sheet(out: Path, reel, lang, info, clips, sfx_clips, segs, names, headline, cfg, content, dur) -> Path:
    colors = cfg.at("project.brand_colors") or ["#FFD400", "#E8252B"]
    font_cap = cfg.at(f"subtitles.fonts.{lang}", "Noto Sans")
    L = [f"# Edit sheet: {reel['id']} ({lang})", "",
         f"**{reel.get('title', '')}** · {dur:.1f} s · 1080x1920 · {cfg.at('reel.fps', 30)} fps", "",
         "## Tracks in Resolve", "",
         "| Track | What it is | How to change it |", "|---|---|---|",
         "| V1 | Podcast video, cut at each join, then the ending stamp | Trim, reorder, add a Resolve transition on an edit point (markers suggest one), grade |",
         "| V2 | Photo backgrounds, rendered (look, slow push-in, fades) | Grade it on the Color page, retime, change opacity, delete |",
         "| V3 | Same photo as an untouched HD still, **off** | Turn on (D), turn V2 off: then grade, zoom or reframe it freely in the Inspector |",
         "| V4 | Each graphic alone (text, counters, pictures), transparent | Move, scale, retime, change opacity, delete, or swap the clip |",
         "| V5 | You cut out, with the mic, cups and other objects in front, only while a graphic is behind you | Delete one to put that graphic in front of you |",
         "| V6 | Parts of a graphic that sit in front of you (counters) | Same as V4 |",
         "| V7 | Corner logo (still) | Move, scale or turn off |",
         "| V8 | Headline above the video (picture) | Turn off and turn on V9 to edit the words, font and colour in Resolve |",
         "| V9 | Headline as Resolve text, **off** | Select, turn on (D key), edit in the Inspector |",
         "| V10 | Karaoke captions (transparent) | Move or turn off; for a Resolve subtitle track import the .srt |",
         "| V11+ | Every graphic's words as Resolve text, **off** | Turn on (D) and turn off its V4 clip: then change words, font, size, colour and add any Resolve title animation |",
         "| A1 | Speech (cleaned) | Levels, EQ |", "| A2 | Music (ducked under speech) | Levels, swap the song |",
         "| A3+ | One clip per sound effect | Move, change volume, delete, swap |", "",
         "## Style used", "",
         f"- Highlight colour (active word, graphics): `{colors[0]}`. Accent colour (headline key words, key words): `{colors[1]}`.",
         f"- Caption font: {font_cap}. Headline font: {'Anton' if lang == 'en' else font_cap} (bold).",
         "- Graphic text font: Anton (typewriter style: Special Elite). Original photos at full size are in media/photos/.",
         "- To change fonts or colours in the baked layers, edit config.yaml and run `hrc resolve --reel "
         f"{reel['id']} --lang {lang}` (about a minute).", "",
         f"## Headline\n\n`{headline}` (words between `*` use the accent colour)", "",
         "## Graphics", "", "| Clip | Time | Type | Animation | Text / value | Picture | Sound |", "|---|---|---|---|---|---|---|"]
    for c in clips:
        L.append(f"| {c['id']} | {c['t_in']:.2f}-{c['t_out']:.2f}s | {c['type']} | {c['anim']} | "
                 f"{(c.get('text') or '').replace('|', '/')} {c.get('value') or ''} | {c.get('image') or ''} | "
                 f"{'yes' if c.get('sound') else ''} |")
    L += ["", "## Sound effects", "", "| Clip | Starts | Kind | Gain |", "|---|---|---|---|"]
    for s in sfx_clips:
        L.append(f"| {s['clip']} | {s['start']:.2f}s | {s['kind']} | {s['gain_db']} dB |")
    L += ["", "## Joins", ""]
    for a, b in segs:
        L.append(f"- {a:.2f}-{b:.2f}s" + (f" (join at {a:.2f}s: {names.get(round(a, 3), 'cut')})" if a else ""))
    L += ["", f"Ending stamp: {content:.2f}-{dur:.2f}s on V1.", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    return out
