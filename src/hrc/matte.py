"""Person matte for each reel: Robust Video Matting on MPS, MediaPipe as fallback.

Runs on the reframed 9:16 plate and writes mattes/<reel>.mp4 (grey = person alpha).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from . import log
from .util import ffmpeg, torch_device, video_info


def _frames(path: Path, W: int, H: int):
    proc = subprocess.Popen([ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    proc.wait()


def _writer(out: Path, W: int, H: int, fps: float):
    return subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
                             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(out)],
                            stdin=subprocess.PIPE)


def pick_engine(pref: str) -> str:
    if pref != "auto":
        return pref
    try:
        import torch  # noqa: F401
        return "rvm"
    except Exception:
        pass
    try:
        import mediapipe  # noqa: F401
        return "mediapipe"
    except Exception:
        return "none"


def make_matte(plate: Path, out: Path, engine: str, downsample: float = 0.4, smooth: float = 0.6) -> dict:
    info = video_info(plate)
    W, H, fps = info["width"], info["height"], info["fps"]
    engine = pick_engine(engine)
    if engine == "none":
        log.warn("No matting model installed (torch or mediapipe). GFX will be drawn without the person cut-out, "
                 "so they are kept away from the face instead of behind it.")
        return {"engine": "none", "flicker": None}
    log.info("Matting %s with %s...", plate.name, engine)
    w = _writer(out, W, H, fps)
    prev, diffs = None, []
    if engine == "rvm":
        import torch

        dev = torch_device("auto")
        model = torch.hub.load("PeterL1n/RobustVideoMatting", "mobilenetv3", trust_repo=True).to(dev).eval()
        rec = [None] * 4
        with torch.no_grad():
            for fr in _frames(plate, W, H):
                src = torch.from_numpy(fr).to(dev).permute(2, 0, 1).float().unsqueeze(0) / 255
                _, pha, *rec = model(src, *rec, downsample)
                a = pha[0, 0].clamp(0, 1).cpu().numpy()
                prev, a = _smooth(prev, a, smooth, diffs)
                w.stdin.write((a * 255).astype(np.uint8).tobytes())
    else:
        import mediapipe as mp

        seg = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
        import cv2

        for fr in _frames(plate, W, H):
            m = seg.process(fr).segmentation_mask
            a = cv2.GaussianBlur(np.clip((m - 0.3) / 0.4, 0, 1).astype(np.float32), (0, 0), 2)
            prev, a = _smooth(prev, a, smooth, diffs)
            w.stdin.write((a * 255).astype(np.uint8).tobytes())
    w.stdin.close(); w.wait()
    flicker = float(np.mean(diffs)) if diffs else 0.0
    log.info("Matte done. Flicker score %.4f (lower is steadier; under 0.02 is good).", flicker)
    return {"engine": engine, "flicker": flicker}


def _smooth(prev, a, s, diffs):
    """Temporal smoothing where the matte barely changes (kills edge shimmer, keeps real motion)."""
    if prev is not None:
        d = np.abs(a - prev)
        k = s * np.clip(1 - d * 6, 0, 1)
        a = prev * k + a * (1 - k)
        diffs.append(float(np.mean(np.abs(a - prev))))
    return a, a
