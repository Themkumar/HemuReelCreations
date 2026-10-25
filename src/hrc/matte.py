"""Cut-out matte for each reel: Apple Vision person segmentation on the Mac (most accurate),
then Robust Video Matting on MPS, then MediaPipe.

With `matte.objects` (Apple Vision, macOS 14+), things in front of the speaker join the cut-out:
the mic, cups, bottles, a laptop. Vision finds every foreground object; an object is kept when it
is not too big (walls, the whole table), not the person, and (with a depth model) as near the
camera as the speaker or nearer. Objects are steadied over 5 frames so they never flicker, and the
edges are refined against the picture (guided filter) for clean hair and mic edges.

Runs on the reframed plate and writes mattes/<reel>.mp4 (grey = cut-out alpha).
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


VISION_SRC = Path(__file__).with_name("vision_matte.swift")


def _vision_binary() -> Path | None:
    """Compile the Swift helper once (needs Xcode Command Line Tools: swiftc)."""
    import platform
    import shutil

    if platform.system() != "Darwin":
        return None
    out = Path.home() / ".cache" / "hrc" / "vision_matte"
    if out.exists() and out.stat().st_mtime >= VISION_SRC.stat().st_mtime:
        return out
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([swiftc, "-O", str(VISION_SRC), "-o", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        log.warn("Could not build the Apple Vision helper: %s", r.stderr.strip()[-400:])
        return None
    return out


def pick_engine(pref: str) -> str:
    if pref != "auto":
        return pref
    if _vision_binary():
        return "vision"
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


def _read_block(stream):
    hdr = stream.read(8)
    if len(hdr) < 8:
        return None
    w, h = (int(v) for v in np.frombuffer(hdr, "<u4"))
    if w == 0:
        return np.zeros((0, 0), np.uint8)
    return np.frombuffer(stream.read(w * h), np.uint8).reshape(h, w)


def guided_filter(I: np.ndarray, p: np.ndarray, r: int = 8, eps: float = 1e-3) -> np.ndarray:
    """Edge-aware smoothing of the alpha p, guided by the grey picture I (both float 0-1)."""
    import cv2

    k = (2 * r + 1, 2 * r + 1)
    mean = lambda x: cv2.boxFilter(x, -1, k)  # noqa: E731
    mI, mp = mean(I), mean(p)
    var = mean(I * I) - mI * mI
    cov = mean(I * p) - mI * mp
    a = cov / (var + eps)
    b = mp - a * mI
    return np.clip(mean(a) * I + mean(b), 0, 1)


class _Depth:
    """Depth Anything V2 (small), if it can be loaded: near things get high values."""

    def __init__(self, every: int = 3):
        self.every, self.n, self.last, self.pipe = every, 0, None, None
        try:
            import torch
            from transformers import pipeline

            dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
            self.pipe = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf", device=dev)
            log.info("Depth model loaded (%s): objects in front of the speaker are judged by distance.", dev)
        except Exception as exc:
            log.info("No depth model (%s); objects are judged by size and position instead.", str(exc)[:80])

    def __call__(self, frame: np.ndarray, size: tuple[int, int]) -> np.ndarray | None:
        if self.pipe is None:
            return None
        if self.last is None or self.n % self.every == 0:
            import cv2
            from PIL import Image

            small = cv2.resize(frame, (518, int(518 * frame.shape[0] / frame.shape[1])), interpolation=cv2.INTER_AREA)
            d = self.pipe(Image.fromarray(small))["predicted_depth"]
            d = d.squeeze().float().cpu().numpy()
            self.last = (d - d.min()) / max(1e-6, d.max() - d.min())
        self.n += 1
        import cv2
        return cv2.resize(self.last, size, interpolation=cv2.INTER_LINEAR)


def select_objects(person: np.ndarray, labels: np.ndarray, depth: np.ndarray | None,
                   max_area: float = 0.22, tol: float = 0.06) -> list[int]:
    """Which of Vision's foreground objects sit in front of (or level with) the speaker."""
    import cv2

    lh, lw = labels.shape
    P = cv2.resize(person, (lw, lh), interpolation=cv2.INTER_AREA) > 0.5
    have_person = P.mean() > 0.01
    dp = float(np.median(depth[P])) if depth is not None and have_person else None
    keep = []
    for lab in np.unique(labels):
        if lab == 0:
            continue
        m = labels == lab
        area = m.mean()
        if area < 0.0004 or area > max_area:
            continue  # specks, and walls / the whole table
        if have_person and P[m].mean() > 0.5:
            continue  # that is the person (already in the cut-out)
        if depth is not None:
            dl = float(np.median(depth[m]))
            ok = dl >= dp - tol if dp is not None else dl >= float(np.percentile(depth, 60))
        else:
            ys = np.nonzero(m)[0]
            ok = ys.min() > 0 and ys.mean() > lh * 0.25  # not hanging from the top edge (lamps, shelves)
        if ok:
            keep.append(int(lab))
    return keep


def make_matte(plate: Path, out: Path, engine: str, downsample: float = 0.4, smooth: float = 0.6,
               objects: bool = True, refine: bool = True, max_area: float = 0.22, depth_model: bool = True) -> dict:
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
    mres_extra: dict = {}
    if engine == "vision":
        import cv2
        from collections import deque

        binp = _vision_binary()
        if not binp:
            log.warn("Apple Vision helper unavailable; falling back.")
            w.stdin.close(); w.wait()
            return make_matte(plate, out, "rvm" if pick_engine("auto") == "vision" else pick_engine("auto"), downsample, smooth)
        args = [str(binp), str(W), str(H), "accurate"] + (["objects"] if objects else [])
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        import threading

        frames_q: deque = deque()

        def feed():
            try:
                for fr in _frames(plate, W, H):
                    frames_q.append(fr)
                    proc.stdin.write(fr.tobytes())
            finally:
                proc.stdin.close()

        th = threading.Thread(target=feed, daemon=True); th.start()
        depth = _Depth() if objects and depth_model else None
        win: deque = deque()  # (person alpha, object alpha, grey) for the 5-frame object median
        next_emit, base = 0, [0]  # next frame to write; frame number of win[0]
        stats = {"frames": 0, "object_frames": 0, "object_px": 0.0}

        def emit(person, obj_stack, grey):
            a = person
            if obj_stack and any(x.max() > 0 for x in obj_stack):
                o = np.median(np.stack(obj_stack), axis=0) if len(obj_stack) > 2 else obj_stack[len(obj_stack) // 2]
                a = np.maximum(a, o)
                if o.max() > 0.5:
                    stats["object_frames"] += 1
                    stats["object_px"] += float((o > 0.5).mean())
            if refine:
                a = guided_filter(grey, a.astype(np.float32), r=6, eps=2e-3)
                a = np.clip((a - 0.04) / 0.92, 0, 1)
            w.stdin.write((a * 255).astype(np.uint8).tobytes())
            stats["frames"] += 1

        while True:
            pm = _read_block(proc.stdout)
            if pm is None:
                break
            om = lb = None
            if objects:
                om, lb = _read_block(proc.stdout), _read_block(proc.stdout)
            fr = frames_q.popleft() if frames_q else None
            if pm.size == 0:
                a = prev if prev is not None else np.zeros((H, W), np.float32)
            else:
                a = cv2.resize(pm, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
                a = cv2.GaussianBlur(np.clip((a - 0.15) / 0.7, 0, 1), (0, 0), 1.2)
            prev, a = _smooth(prev, a, smooth, diffs)
            obj = np.zeros((H, W), np.float32)
            if objects and om is not None and om.size and lb is not None and lb.size:
                d = depth(fr, (lb.shape[1], lb.shape[0])) if (depth is not None and fr is not None) else None
                keep = select_objects(a, lb, d, max_area)
                if keep:
                    sel = np.isin(lb, keep).astype(np.uint8) * 255
                    sel = cv2.resize(sel, (W, H), interpolation=cv2.INTER_LINEAR)
                    sel = cv2.dilate(sel, np.ones((5, 5), np.uint8)).astype(np.float32) / 255.0
                    soft = cv2.resize(om, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
                    obj = np.clip(soft * sel, 0, 1)
            grey = (cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0) if fr is not None \
                else np.zeros((H, W), np.float32)
            win.append((a, obj, grey))
            while len(win) - (next_emit - base[0]) > 2:
                k = next_emit - base[0]
                emit(win[k][0], [x[1] for x in list(win)[max(0, k - 2):k + 3]] if objects else [], win[k][2])
                next_emit += 1
                while next_emit - base[0] > 2:  # keep two frames of history for the median
                    win.popleft(); base[0] += 1
        while next_emit - base[0] < len(win):  # the last frames (shorter window at the end)
            k = next_emit - base[0]
            emit(win[k][0], [x[1] for x in list(win)[max(0, k - 2):k + 3]] if objects else [], win[k][2])
            next_emit += 1
        th.join(); proc.wait()
        mres_extra = {"objects": bool(objects), "object_frames": stats["object_frames"],
                      "object_share": round(stats["object_px"] / max(1, stats["object_frames"]), 4),
                      "depth": bool(depth is not None and depth.pipe is not None), "refined": bool(refine)}
    elif engine == "rvm":
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
    res = {"engine": engine, "flicker": flicker}
    if engine == "vision":
        res.update(mres_extra)
        if objects:
            log.info("Objects in front of the speaker (mic, cups...) kept in %d of %d frames.",
                     mres_extra["object_frames"], stats["frames"])
    return res


def _smooth(prev, a, s, diffs):
    """Temporal smoothing where the matte barely changes (kills edge shimmer, keeps real motion)."""
    if prev is not None:
        d = np.abs(a - prev)
        k = s * np.clip(1 - d * 6, 0, 1)
        a = prev * k + a * (1 - k)
        diffs.append(float(np.mean(np.abs(a - prev))))
    return a, a
