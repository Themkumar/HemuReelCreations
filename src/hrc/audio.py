"""Audio mix: speech cleanup, music ducked under speech (sidechain), SFX at events, -14 LUFS."""
from __future__ import annotations

import random
from pathlib import Path

from . import log, sfx
from .util import ffmpeg, run

MUSIC_EXT = ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.flac", "*.ogg")


# Relative loudness per sound type, so the mix stays cinematic rather than cartoonish.
KIND_DB = {"type": -6, "glitch": -6, "pop": -8, "whoosh": -3, "swish": -5, "hit": -1, "impact": -4,
           "riser": -4, "count": -3, "shimmer": -5, "ding": -9, "stamp": 2}


def pick_music(music_dir: Path, mood: str, rng: random.Random) -> Path | None:
    for folder in (music_dir / mood, music_dir):
        files = sorted(f for ext in MUSIC_EXT for f in folder.glob(ext))
        if files:
            return rng.choice(files)
    return None


def stems(cfg, speech: Path, duration: float, music: str | None, out_dir: Path) -> dict:
    """Separate tracks for editing in Resolve: cleaned speech and the music ducked under it.
    They are not loudness-normalised (the finished mix is); see the README for levelling."""
    ac = cfg.at("audio", {})
    out_dir.mkdir(parents=True, exist_ok=True)
    clean = "highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2" \
        if ac.get("speech_cleanup", True) else "anull"
    sp = out_dir / "speech.wav"
    run([ffmpeg(), "-y", "-v", "error", "-i", speech, "-af",
         f"aresample=48000,aformat=channel_layouts=stereo,{clean},apad=whole_dur={duration:.3f},atrim=0:{duration:.3f}",
         "-c:a", "pcm_s24le", sp])
    res = {"speech": sp, "music": None}
    if music:
        mu = out_dir / "music.wav"
        fc = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,{clean},apad=whole_dur={duration:.3f}[sc];"
              f"[1:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{duration:.3f},"
              f"volume={ac.get('music_db', -20)}dB,afade=t=in:d=0.6,afade=t=out:st={max(0, duration - 1.2):.3f}:d=1.2[mus];"
              f"[mus][sc]sidechaincompress=threshold=0.03:ratio={ac.get('duck_ratio', 8)}:attack=20:release=350[out]")
        run([ffmpeg(), "-y", "-v", "error", "-i", speech, "-stream_loop", "-1", "-i", music, "-filter_complex", fc,
             "-map", "[out]", "-t", f"{duration:.3f}", "-c:a", "pcm_s24le", mu])
        res["music"] = mu
    return res


def mix(cfg, speech: Path, duration: float, events: list[dict], mood: str, out: Path, seed: int = 0) -> dict:
    """speech: audio already cut to reel timeline. events: [{t, kind}] in reel seconds."""
    rng = random.Random(seed)
    ac = cfg.at("audio", {})
    sfx_dir = cfg.path("paths.assets") / "sfx"
    sfx.ensure_library(sfx_dir)
    music = pick_music(cfg.path("paths.assets") / "music", mood, rng)

    inputs = ["-i", speech]
    chains = []
    clean = "highpass=f=80,afftdn=nf=-25,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=2" \
        if ac.get("speech_cleanup", True) else "anull"
    # apad: the speech runs silent through the ending stamp, so the mix lasts the whole reel
    chains.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,{clean},apad=whole_dur={duration:.3f},asplit=2[sp][sc]")
    mix_in = ["[sp]"]
    idx = 1
    used = {"music": str(music) if music else None, "sfx": []}
    if music:
        inputs += ["-stream_loop", "-1", "-i", music]
        chains.append(
            f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{duration:.3f},"
            f"volume={ac.get('music_db', -20)}dB,afade=t=in:d=0.6,afade=t=out:st={max(0, duration - 1.2):.3f}:d=1.2[mus];"
            f"[mus][sc]sidechaincompress=threshold=0.03:ratio={ac.get('duck_ratio', 8)}:attack=20:release=350[duck]")
        mix_in.append("[duck]")
        idx += 1
    else:
        chains[-1] = chains[-1].replace("asplit=2[sp][sc]", "anull[sp]")
        log.warn("No music found in assets/music/%s/ (or assets/music/). Rendering without music.", mood)
    for ev in events:
        f = sfx.pick(sfx_dir, ev["kind"], rng)
        if not f:
            continue
        lead = 0.35 if ev["kind"] in ("whoosh", "riser") else 0.0
        if ev["kind"] == "riser":
            lead = 1.4
        t = max(0.0, ev["t"] - lead)
        ms = int(t * 1000)
        inputs += ["-i", f]
        chains.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                      f"volume={ac.get('sfx_db', -6) + KIND_DB.get(ev['kind'], 0) + ev.get('gain_db', 0)}dB,"
                      f"adelay={ms}|{ms}[s{idx}]")
        mix_in.append(f"[s{idx}]")
        gain = ac.get('sfx_db', -6) + KIND_DB.get(ev['kind'], 0) + ev.get('gain_db', 0)
        used["sfx"].append({"t": round(ev["t"], 3), "start": round(t, 3), "kind": ev["kind"], "file": f.name,
                            "path": str(f), "gain_db": round(gain, 1)})
        idx += 1
    lufs = ac.get("target_lufs", -14)
    chains.append(f"{''.join(mix_in)}amix=inputs={len(mix_in)}:normalize=0:duration=first,"
                  f"atrim=0:{duration:.3f},loudnorm=I={lufs}:TP=-1.0:LRA=11,aresample=48000[out]")
    run([ffmpeg(), "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chains), "-map", "[out]",
         "-c:a", "pcm_s16le", out])
    return used
