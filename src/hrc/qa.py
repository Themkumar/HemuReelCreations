"""Final QA: duration, loudness, subtitle sync and script rendering (frame grabs), GFX vs face, matte flicker."""
from __future__ import annotations

import re
from pathlib import Path

from . import log, plan as planlib
from .util import ffmpeg, read_json, run, video_info


def loudness(path: Path) -> float | None:
    proc = run([ffmpeg(), "-v", "info", "-i", path, "-af", "ebur128", "-f", "null", "-"])
    m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", proc.stderr)
    return float(m[-1]) if m else None


def run_stage(cfg, only: str | None = None, preview: bool = False) -> Path:
    plan = planlib.load(cfg)
    lines = ["# HRC QA report", ""]
    lo, hi = cfg.at("reel.min_sec"), cfg.at("reel.max_sec")
    target = cfg.at("audio.target_lufs", -14)
    for reel in planlib.selected(plan, only):
        odir = cfg.dir("output") / reel["id"]
        rinfos = {}
        from .render import suffix
        for fmt in ("reel", "portrait", "landscape"):
            rd = cfg.dir("renders") / (reel["id"] + suffix(fmt, preview))
            if (rd / "render.json").exists():
                ri = read_json(rd / "render.json")
                if ri.get("format", "portrait") == fmt or (fmt == "portrait" and "video_box" not in ri):
                    rinfos[fmt] = ri
        rinfo = rinfos.get("reel") or rinfos.get("portrait") or rinfos.get("landscape") or {}
        lines += [f"## {reel['id']} - {reel.get('title', '')}", ""]
        files = sorted(f for f in odir.glob(f"{reel['id']}_*.mp4") if f.stem.endswith("_preview") == preview)
        if not files:
            lines += ["- ❌ No exported files found.", ""]
            continue
        grabs = odir / "qa_frames"
        grabs.mkdir(exist_ok=True)
        for f in files:
            info = video_info(f)
            d = info["duration"]
            ok_d = lo - 2 <= d <= hi + 0.1
            lufs = loudness(f)
            ok_l = lufs is not None and abs(lufs - target) <= 1.5
            lines.append(f"- {'✅' if ok_d and ok_l else '⚠️'} `{f.name}`: {d:.1f}s ({'ok' if ok_d else f'outside {lo}-{hi}s'}), "
                         f"{info['width']}x{info['height']}, loudness {lufs if lufs is not None else '?'} LUFS "
                         f"({'ok' if ok_l else f'target {target}'})")
            lang = f.stem.split("_")[1] if "_" in f.stem else ""
            sub = cfg.dir("subs") / f"{reel['id']}_{lang}.json"
            if sub.exists():
                chunks = read_json(sub)["chunks"]
                pick = [chunks[len(chunks) // 4], chunks[len(chunks) // 2], chunks[-1]] if chunks else []
                for j, ch in enumerate(pick):
                    if not ch["words"]:
                        continue
                    w = ch["words"][len(ch["words"]) // 2]
                    t = (w["start"] + w["end"]) / 2
                    png = grabs / f"{f.stem}_{j}.png"
                    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", f, "-frames:v", "1", png])
                last = chunks[-1]["end"] if chunks else 0
                sync = "ok" if last <= d + 0.1 else f"last subtitle ends at {last:.1f}s, after the video ends"
                lines.append(f"  - subtitles: {len(chunks)} chunks, sync {sync}. Frame grabs in `{grabs.relative_to(cfg.root)}` "
                             f"(check the {lang} glyphs by eye).")
        for fmt, ri in rinfos.items():
            lines.append(f"- {fmt}: GFX over the face {ri.get('face_overlap_frames', '?')} frames, matte flicker "
                         f"{(ri.get('matte') or {}).get('flicker')}")
        flick = (rinfo.get("matte") or {}).get("flicker")
        lines.append(f"- matte: {(rinfo.get('matte') or {}).get('engine', '?')}, flicker score "
                     f"{'n/a' if flick is None else f'{flick:.4f}'} {'✅' if flick is not None and flick < 0.02 else ''}")
        lines.append(f"- GFX covering the face: {rinfo.get('face_overlap_frames', '?')} frames "
                     f"{'✅' if rinfo.get('face_overlap_frames') == 0 else '⚠️ check'}")
        lines.append(f"- transitions: {', '.join(t['name'] for t in rinfo.get('transitions', [])) or 'none'}")
        music = (rinfo.get("audio") or {}).get("music")
        lines.append(f"- music: {Path(music).name if music else 'none'}; SFX events: {len((rinfo.get('audio') or {}).get('sfx', []))}")
        lines.append("")
    out = cfg.root / "qa_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("QA report written to %s", out.name)
    return out
