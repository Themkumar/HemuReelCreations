"""3-minute sample workspace and an automatic demo plan, for testing before the full run."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from . import log, srt
from .util import ffmpeg, parse_ts, read_json, run, write_json


def make_sample(cfg, start: str, minutes: float) -> Path:
    t0 = parse_ts(start)
    dur = minutes * 60
    sdir = cfg.root / "sample"
    (sdir / "input").mkdir(parents=True, exist_ok=True)
    video = cfg.path("input.video")
    log.info("Cutting a %.0f-minute sample from %s at %s...", minutes, video.name, start)
    run([ffmpeg(), "-y", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{dur:.3f}", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "256k", sdir / "input" / "podcast.mp4"])
    cues = srt.slice_cues(srt.load(cfg.path("input.srt")), t0, t0 + dur)
    (sdir / "input" / "podcast.srt").write_text(srt.dump(cues), encoding="utf-8")
    data = yaml.safe_load((cfg.root / "config.yaml").read_text(encoding="utf-8"))
    data["input"]["video"] = "input/podcast.mp4"
    data["input"]["srt"] = "input/podcast.srt"
    data["paths"]["assets"] = str((cfg.root / data["paths"]["assets"]).resolve())
    data["reel"]["min_sec"], data["reel"]["max_sec"] = 25, 60
    (sdir / "config.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log.info("Sample ready in sample/ (%d cues). Use: hrc --config sample/config.yaml <command>", len(cues))
    return sdir


def demo_plan(cfg, length: float = 45.0) -> dict:
    """Pick one ~45 s demo reel on sentence boundaries with a few auto visual moments."""
    cues = read_json(cfg.dir("cache") / "cues.json")
    words = read_json(cfg.dir("cache") / "words.json")
    start_cue = next((c for c in cues if c["start"] >= 5), cues[0])
    end_cue = start_cue
    for c in cues:
        if c["start"] >= start_cue["start"] and c["end"] - start_cue["start"] <= length:
            end_cue = c
    s, e = start_cue["start"], end_cue["end"]
    inside = [w for w in words if s <= w["start"] < e]
    moments, last = [], -99.0
    seen = set()
    for w in sorted(inside, key=lambda w: -len(w["word"])):
        if w["word"] in seen:
            continue
        if all(abs(w["start"] - m["_t"]) > 9 for m in moments) and len(moments) < 4:
            kind = "counter" if any(ch.isdigit() for ch in w["word"]) else "text"
            seen.add(w["word"])
            moments.append({"phrase": w["word"].strip(",.!?"), "type": kind, "query": "", "_t": w["start"],
                            "value": "".join(ch for ch in w["word"] if ch.isdigit()) or None})
    moments.sort(key=lambda m: m["_t"])
    for m in moments:
        m.pop("_t")
    mid = s + (e - s) / 2
    plan = {"approved": True, "note": "Automatic demo plan for the sample test. The real plan comes from Stage C.",
            "reels": [{"id": "demo01", "title": "Sample demo", "segments": [
                {"start": round(s, 3), "end": round(mid - 0.01, 3)}, {"start": round(mid, 3), "end": round(e, 3)}],
                "hook": start_cue["text"], "context_card": "ఈ కథ తప్పక వినండి", "mood": "energetic", "virality": 0,
                "emphasis_words": [m["phrase"] for m in moments[:3]], "visual_moments": moments}]}
    from . import plan as planlib

    planlib.validate(cfg, plan, words)
    planlib.save(cfg, plan)
    log.info("Demo plan written: %.1fs, %d graphics.", plan["reels"][0]["duration"], len(moments))
    return plan
