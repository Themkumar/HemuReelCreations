"""Stage H: one MP4 per subtitle language, a clean version, and an FCPXML for DaVinci Resolve."""
from __future__ import annotations

import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from . import karaoke, log, plan as planlib
from .util import ffmpeg, read_json, run, video_encoder_args


def _ff_path(p: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def export_reel(cfg, reel: dict, rdir: Path, preview: bool = False, langs: list[str] | None = None) -> list[Path]:
    info = read_json(rdir / "render.json")
    W, H = info["size"]
    from .render import tag as fmt_tag
    tag = fmt_tag(info.get("format", "portrait")) if "video_box" in info else ("_landscape" if info.get("format") == "landscape" else "")
    heads = reel.get("headline") or {}
    if isinstance(heads, str):
        heads = {"en": heads}
    odir = cfg.dir("output") / reel["id"]
    odir.mkdir(parents=True, exist_ok=True)
    fonts = cfg.path("paths.assets") / "fonts"
    enc = video_encoder_args(cfg, preview)
    abr = cfg.at("export.audio_bitrate", "320k")
    outs = []
    for lang in langs or cfg.at("reel.languages"):
        sub_file = cfg.dir("subs") / f"{reel['id']}_{lang}.json"
        if not sub_file.exists():
            log.warn("%s: no %s subtitles yet (run `hrc translate build`). Skipping that language.", reel["id"], lang)
            continue
        ass = rdir / f"{lang}.ass"
        headline = heads.get(lang) or heads.get("en") or reel.get("title")
        karaoke.write_ass(read_json(sub_file), cfg, ass, W, H, info.get("video_box"), headline,
                         (info.get("duration") or 0) - info.get("outro_sec", 0) or None)
        out = odir / (f"{reel['id']}_{lang}{tag}" + ("_preview" if preview else "") + ".mp4")
        vf = f"ass='{_ff_path(ass)}':fontsdir='{_ff_path(fonts)}'"
        run([ffmpeg(), "-y", "-v", "error", "-i", rdir / "clean.mp4", "-i", rdir / "mix.wav", "-vf", vf,
             *enc, "-c:a", "aac", "-b:a", abr, "-ar", "48000", "-movflags", "+faststart", "-shortest", out])
        outs.append(out)
        log.info("Exported %s", out.relative_to(cfg.root))
        if not preview and cfg.at("export.phone_preview", True):  # small copy to watch on a phone / share in chat
            ph = odir / f"{reel['id']}_{lang}{tag}_phone.mp4"
            run([ffmpeg(), "-y", "-v", "error", "-i", out, "-vf", "scale=540:-2", "-c:v", "libx264", "-preset", "slow",
                 "-crf", "27", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", ph])
    if not preview and cfg.at("export.clean_version", True):
        out = odir / f"{reel['id']}_clean{tag}.mp4"
        run([ffmpeg(), "-y", "-v", "error", "-i", rdir / "clean.mp4", "-i", rdir / "mix.wav", *enc,
             "-c:a", "aac", "-b:a", abr, "-movflags", "+faststart", "-shortest", out])
        outs.append(out)
        shutil.copy(rdir / "mix.wav", odir / f"{reel['id']}_mix.wav")
    if not preview and cfg.at("export.fcpxml", True):
        write_fcpxml(cfg, reel, odir, W, H, tag)
    return outs


def write_fcpxml(cfg, reel: dict, odir: Path, W: int, H: int, tag: str = "") -> Path:
    """FCPXML 1.9 with the original podcast clips cut to this reel's segments, plus the clean render
    on a lane above. Import in Resolve via File > Import > Timeline."""
    fps = cfg.at("reel.fps", 30)
    src = cfg.path("input.video").resolve()
    clean = (odir / f"{reel['id']}_clean{tag}.mp4").resolve()
    dur = planlib.duration(reel)
    src_dur = read_json(cfg.dir("cache") / "video_info.json")["duration"]

    def fr(sec):
        return f"{int(round(sec * fps))}/{fps}s"

    card = planlib.context_card_sec(reel)
    clips, off = [], card
    for s in planlib.segments(reel):
        d = s["end"] - s["start"]
        clips.append(f'<asset-clip ref="r2" offset="{fr(off)}" start="{fr(s["start"])}" duration="{fr(d)}" '
                     f'name="podcast {s["start"]:.1f}-{s["end"]:.1f}" format="r1"/>')
        off += d
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="FFVideoFormat{H}p{fps}" frameDuration="1/{fps}s" width="{W}" height="{H}"/>
    <asset id="r2" name="podcast" src="file://{escape(str(src))}" start="0s" duration="{fr(src_dur)}" hasVideo="1" hasAudio="1" format="r1"/>
    <asset id="r3" name="{reel['id']}_clean" src="file://{escape(str(clean))}" start="0s" duration="{fr(dur)}" hasVideo="1" hasAudio="1" format="r1"/>
  </resources>
  <library>
    <event name="HRC {escape(reel['id'])}">
      <project name="{escape(reel['id'])} - {escape(reel.get('title', ''))}">
        <sequence format="r1" duration="{fr(dur)}" tcStart="0s" tcFormat="NDF">
          <spine>
            <gap offset="0s" duration="{fr(dur)}" start="0s">
              <asset-clip ref="r3" lane="1" offset="0s" start="0s" duration="{fr(dur)}" name="HRC render (GFX, music, SFX)"/>
              {chr(10).join('              ' + c.replace('<asset-clip ', '<asset-clip lane="2" ') for c in clips)}
            </gap>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    out = odir / f"{reel['id']}{tag}.fcpxml"
    out.write_text(xml, encoding="utf-8")
    return out
