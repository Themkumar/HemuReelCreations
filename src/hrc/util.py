"""Small helpers: JSON IO, caching, ffmpeg/ffprobe wrappers, time formatting."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from pathlib import Path

from . import log


def read_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def fingerprint(*items) -> str:
    """Hash of files (by size+mtime) and plain values, used to skip cached work."""
    h = hashlib.sha1()
    for item in items:
        if isinstance(item, Path) and item.exists():
            st = item.stat()
            h.update(f"{item}:{st.st_size}:{int(st.st_mtime)}".encode())
        else:
            h.update(json.dumps(item, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def cached(out: Path, fp: str, force: bool = False) -> bool:
    """True when `out` exists and was built from the same inputs."""
    stamp = out.with_name(out.name + ".fp")
    if force or not out.exists() or not stamp.exists():
        return False
    return stamp.read_text().strip() == fp


def mark(out: Path, fp: str) -> None:
    out.with_name(out.name + ".fp").write_text(fp)


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    proc = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n{proc.stderr[-3000:]}")
    return proc


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not installed. On Mac: brew install ffmpeg")
    return exe


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path]).stdout
    return json.loads(out)


def video_info(path: Path) -> dict:
    data = probe(path)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in v.get("r_frame_rate", "30/1").split("/"))
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": num / den if den else 30.0,
        "duration": float(data["format"].get("duration", 0)),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def video_encoder_args(cfg, preview: bool = False) -> list[str]:
    """VideoToolbox on Apple Silicon, libx264 elsewhere."""
    codec = cfg.at("export.codec", "h264")
    hw = cfg.at("export.hw_accel", "auto")
    if preview:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.at("preview.crf", 28)), "-pix_fmt", "yuv420p"]
    if hw in ("auto", "videotoolbox") and is_apple_silicon():
        enc = "hevc_videotoolbox" if codec == "hevc" else "h264_videotoolbox"
        extra = ["-tag:v", "hvc1"] if codec == "hevc" else []
        return ["-c:v", enc, "-b:v", cfg.at("export.bitrate", "12M"), "-pix_fmt", "yuv420p", *extra]
    enc = "libx265" if codec == "hevc" else "libx264"
    return ["-c:v", enc, "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


def torch_device(pref: str = "auto") -> str:
    if pref != "auto":
        return pref
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def ts(sec: float) -> str:
    """Seconds -> HH:MM:SS.mmm for logs."""
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def parse_ts(text: str) -> float:
    """'HH:MM:SS(.mmm)' or 'MM:SS' or seconds -> seconds."""
    text = str(text).strip().replace(",", ".")
    parts = text.split(":")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec
