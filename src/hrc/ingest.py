"""Stage A: parse the SRT, extract audio, detect shots and face positions."""
from __future__ import annotations

import re
from pathlib import Path

from . import log, srt
from .util import cached, ffmpeg, fingerprint, mark, run, video_info, write_json

FACE_SAMPLE_FPS = 5


def run_stage(cfg, force: bool = False, video: Path | None = None, srt_path: Path | None = None) -> dict:
    cache = cfg.dir("cache")
    video = video or cfg.path("input.video")
    srt_path = srt_path or cfg.path("input.srt")
    for p in (video, srt_path):
        if not p.exists():
            raise FileNotFoundError(f"Input not found: {p}. Put your files in input/ or set them in config.yaml.")

    info = video_info(video)
    write_json(cache / "video_info.json", info)
    log.info("Video: %dx%d at %.2f fps, %.1f minutes.", info["width"], info["height"], info["fps"], info["duration"] / 60)

    cues_out = cache / "cues.json"
    fp = fingerprint(srt_path)
    if not cached(cues_out, fp, force):
        cues = srt.load(srt_path)
        n = srt.fix_overlaps(cues)
        if n:
            log.info("%d cues overlapped the next one (rolling captions); trimmed them so each line ends when the next starts.", n)
        write_json(cues_out, cues)
        mark(cues_out, fp)
        log.info("Read %d subtitle cues from %s.", len(cues), srt_path.name)
    else:
        log.info("Subtitle cues already parsed, skipping.")

    audio_out = cache / "audio_16k.wav"
    fp = fingerprint(video, "a16k")
    if not cached(audio_out, fp, force):
        log.info("Extracting audio (16 kHz mono for alignment)...")
        run([ffmpeg(), "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", audio_out])
        mark(audio_out, fp)

    shots_out = cache / "shots.json"
    fp = fingerprint(video, "shots")
    if not cached(shots_out, fp, force):
        log.info("Detecting camera changes...")
        shots = detect_shots(video, info["duration"])
        write_json(shots_out, shots)
        mark(shots_out, fp)
        log.info("Found %d shots (camera switches).", len(shots))

    faces_out = cache / "faces.json"
    fp = fingerprint(video, "faces", FACE_SAMPLE_FPS)
    if not cached(faces_out, fp, force):
        log.info("Finding face positions (%d samples per second). This takes a while on a long video.", FACE_SAMPLE_FPS)
        from .tracking import detect_faces

        faces = detect_faces(video, sample_fps=FACE_SAMPLE_FPS)
        write_json(faces_out, faces)
        mark(faces_out, fp)
        hit = sum(1 for f in faces["samples"] if f["faces"])
        log.info("Faces found in %d of %d samples.", hit, len(faces["samples"]))
    return info


def detect_shots(video: Path, duration: float, threshold: float = 0.25) -> list[dict]:
    """Use ffmpeg's scene score to find camera cuts."""
    proc = run([
        ffmpeg(), "-v", "info", "-i", video, "-an", "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ])
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]
    bounds = [0.0] + [t for t in times if t > 0.5] + [duration]
    return [{"start": round(a, 3), "end": round(b, 3)} for a, b in zip(bounds, bounds[1:]) if b - a > 0.2]
