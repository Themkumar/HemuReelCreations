"""Cut-out matte for each reel: Apple Vision person segmentation on the Mac (most accurate),
then Robust Video Matting on MPS, then MediaPipe.

With `matte.objects` (Apple Vision, macOS 14+), things in front of the speaker join the cut-out:
the mic, cups, bottles, a laptop. Vision finds every foreground object; an object is kept when it
is not too big (walls, the whole table), not the person, and (with a depth model) as near the
camera as the speaker or nearer. Objects are steadied over 5 frames so they never flicker, and the
edges are refined against the picture (guided filter) for clean hair and mic edges.

With `matte.depth_objects`, a depth model also finds everything as near as the speaker (or nearer)
below their eye line: the mic and its arm, the mug, the tabletop. This catches things Vision's
instance pass merges into the person or misses. The two object masks are combined.

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


class _Refiner:
    """High-detail edges (hair, fingers, the mic's thin arm) from BiRefNet, used only in a band
    around the coarse cut-out: inside the band's core the matte stays solid, outside it stays empty,
    so the model can sharpen edges but never add a wall or drop an arm."""

    def __init__(self, model_name: str = "ZhengPeng7/BiRefNet-matting", size: int = 1024):
        self.model, self.size = None, size
        try:
            import torch
            from transformers import AutoModelForImageSegmentation

            self.dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
            self.model = AutoModelForImageSegmentation.from_pretrained(model_name, trust_remote_code=True)
            self.model.to(self.dev).eval()
            self.torch = torch
            log.info("Edge refinement model loaded (%s on %s).", model_name.split("/")[-1], self.dev)
        except Exception as exc:
            log.warn("No edge refinement model (%s). Using the guided filter only.", str(exc)[:120])

    def __call__(self, rgb: np.ndarray, coarse: np.ndarray, band_px: int = 18) -> np.ndarray:
        if self.model is None:
            return coarse
        import cv2

        H, W = coarse.shape
        hard = (coarse > 0.5).astype(np.uint8)
        if hard.sum() < 50:
            return coarse
        k = np.ones((band_px * 2 + 1, band_px * 2 + 1), np.uint8)
        outer = cv2.dilate(hard, k)
        core = cv2.erode(hard, k)
        ys, xs = np.nonzero(outer)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        side = max(y1 - y0, x1 - x0)  # square crop around the subject: the model sees it big
        cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
        y0, x0 = max(0, cy - side // 2), max(0, cx - side // 2)
        y1, x1 = min(H, y0 + side), min(W, x0 + side)
        crop = rgb[y0:y1, x0:x1]
        x = cv2.resize(crop, (self.size, self.size), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        t = self.torch.from_numpy(x.transpose(2, 0, 1)[None]).to(self.dev)
        with self.torch.no_grad():
            pred = self.model(t)
            pred = (pred[-1] if isinstance(pred, (list, tuple)) else pred).sigmoid()[0, 0].float().cpu().numpy()
        fine = np.zeros((H, W), np.float32)
        fine[y0:y1, x0:x1] = cv2.resize(pred, (x1 - x0, y1 - y0), interpolation=cv2.INTER_CUBIC)
        out = np.where(core > 0, 1.0, np.where(outer > 0, np.clip(fine, 0, 1), 0.0)).astype(np.float32)
        # where the model saw nothing but the coarse matte is sure (e.g. a mug the model treats as
        # background), keep the coarse value: refinement must never remove a whole object
        out = np.maximum(out, np.where(coarse > 0.9, cv2.erode(coarse, np.ones((5, 5), np.uint8)), 0))
        return out


def frame_ranges(times: list[tuple[float, float]], fps: float, n: int, handle: float = 0.3) -> list[tuple[int, int]]:
    """Frame ranges [a, b) that need a cut-out: each (t_in, t_out) plus `handle` seconds each side, merged."""
    rs = sorted((max(0, int((a - handle) * fps)), min(n, int(np.ceil((b + handle) * fps)))) for a, b in times)
    out: list[list[int]] = []
    for a, b in rs:
        if b <= a:
            continue
        if out and a <= out[-1][1] + 2:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


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


def depth_objects(person: np.ndarray, depth: np.ndarray | None, grey: np.ndarray | None = None,
                  tol: float = 0.06, region_top: float = 0.15, min_area: float = 0.001) -> np.ndarray:
    """Soft mask (0-1, plate size) of things as near as the speaker or nearer, below their eye line.

    person: person alpha (0-1), depth: 0-1 with near = high (same size), grey: picture for edge snapping.
    """
    import cv2

    H, W = person.shape
    empty = np.zeros((H, W), np.float32)
    if depth is None:
        return empty
    P = person > 0.7
    if P.mean() < 0.01:
        return empty  # nobody in the picture: no reference distance
    ys = np.nonzero(P.any(axis=1))[0]
    y0, y1 = int(ys.min()), int(ys.max())
    ph = max(1, y1 - y0)
    lower = P.copy()
    lower[: y0 + int(0.3 * ph)] = False  # the hair and cap don't set the distance
    dp = float(np.median(depth[lower])) if lower.any() else float(np.median(depth[P]))
    cand = (depth >= dp - tol) & (person < 0.9)  # soft person edges (mic over the chest) may join
    cand[: min(H, y0 + int(region_top * ph))] = False  # nothing above the eye line (lamps, shelves)
    m = cand.astype(np.uint8)
    k = max(3, int(round(min(H, W) * 0.006)) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area * H * W
    m = keep[lab].astype(np.float32)
    if not m.any():
        return empty
    if grey is not None:
        m = guided_filter(grey, m, r=8, eps=1e-3)
    return np.clip((m - 0.3) / 0.4, 0, 1).astype(np.float32)


def make_matte(plate: Path, out: Path, engine: str, downsample: float = 0.4, smooth: float = 0.6,
               objects: bool = True, refine: bool = True, max_area: float = 0.22, depth_model: bool = True,
               use_depth_objects: bool = True, depth_tol: float = -0.03, region_top: float = 0.15,
               ranges: list[tuple[int, int]] | None = None, refiner: str | None = None,
               n_frames: int | None = None) -> dict:
    """`ranges`: only these frame ranges get a cut-out (the rest of the matte is empty), so the slow,
    detailed work is spent where a graphic actually sits behind the speaker."""
    info = video_info(plate)
    W, H, fps = info["width"], info["height"], info["fps"]
    n_total = int(n_frames or round(info["duration"] * fps))
    need = None
    if ranges is not None:
        need = np.zeros(n_total + 8, bool)
        for a, b in ranges:
            need[a:b] = True
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
        zero = np.zeros((H, W), np.uint8).tobytes()
        written = [0]

        def feed():
            try:
                for gi, fr in enumerate(_frames(plate, W, H)):
                    if need is not None and (gi >= len(need) or not need[gi]):
                        continue
                    frames_q.append((gi, fr))
                    proc.stdin.write(fr.tobytes())
            finally:
                proc.stdin.close()

        th = threading.Thread(target=feed, daemon=True); th.start()
        depth = _Depth(every=2) if objects and depth_model else None
        ref = _Refiner(refiner) if refiner else None
        win: deque = deque()  # (person alpha, object alpha, grey, frame no., rgb) for the 5-frame object median
        next_emit, base = 0, [0]  # next frame to write; frame number of win[0]
        stats = {"frames": 0, "object_frames": 0, "object_px": 0.0, "refined": 0}

        def emit(person, obj_stack, grey, gi=None, rgb=None):
            a = person
            if obj_stack and any(x.max() > 0 for x in obj_stack):
                o = np.median(np.stack(obj_stack), axis=0) if len(obj_stack) > 2 else obj_stack[len(obj_stack) // 2]
                a = np.maximum(a, o)
                if o.max() > 0.5:
                    stats["object_frames"] += 1
                    stats["object_px"] += float((o > 0.5).mean())
            if ref is not None and ref.model is not None and rgb is not None:
                a = ref(rgb, a.astype(np.float32))
                a = guided_filter(grey, a, r=2, eps=1e-4)  # light snap to the picture, keeps fine hair
                stats["refined"] += 1
            elif refine:
                a = guided_filter(grey, a.astype(np.float32), r=6, eps=2e-3)
                a = np.clip((a - 0.04) / 0.92, 0, 1)
            if gi is not None:
                while written[0] < gi:  # frames with no graphic behind the speaker: no cut-out
                    w.stdin.write(zero); written[0] += 1
            w.stdin.write((a * 255).astype(np.uint8).tobytes())
            written[0] += 1
            stats["frames"] += 1

        while True:
            pm = _read_block(proc.stdout)
            if pm is None:
                break
            om = lb = None
            if objects:
                om, lb = _read_block(proc.stdout), _read_block(proc.stdout)
            gi, fr = frames_q.popleft() if frames_q else (None, None)
            if need is not None and win and gi is not None and gi - win[-1][3] > 1:
                # a new range starts: flush the old one and forget its history
                while next_emit - base[0] < len(win):
                    k = next_emit - base[0]
                    emit(win[k][0], [x[1] for x in list(win)[max(0, k - 2):k + 3]] if objects else [], win[k][2],
                         win[k][3], win[k][4])
                    next_emit += 1
                win.clear(); base[0] = next_emit; prev = None
            if pm.size == 0:
                a = prev if prev is not None else np.zeros((H, W), np.float32)
            else:
                a = cv2.resize(pm, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
                a = cv2.GaussianBlur(np.clip((a - 0.15) / 0.7, 0, 1), (0, 0), 1.2)
            prev, a = _smooth(prev, a, smooth, diffs)
            obj = np.zeros((H, W), np.float32)
            grey = (cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0) if fr is not None \
                else np.zeros((H, W), np.float32)
            dfull = depth(fr, (W, H)) if (objects and depth is not None and fr is not None) else None
            if objects and om is not None and om.size and lb is not None and lb.size:
                d = cv2.resize(dfull, (lb.shape[1], lb.shape[0]), interpolation=cv2.INTER_AREA) \
                    if dfull is not None else None
                keep = select_objects(a, lb, d, max_area)
                if keep:
                    sel = np.isin(lb, keep).astype(np.uint8) * 255
                    sel = cv2.resize(sel, (W, H), interpolation=cv2.INTER_LINEAR)
                    sel = cv2.dilate(sel, np.ones((5, 5), np.uint8)).astype(np.float32) / 255.0
                    soft = cv2.resize(om, (W, H), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
                    obj = np.clip(soft * sel, 0, 1)
            if objects and use_depth_objects and dfull is not None:
                obj = np.maximum(obj, depth_objects(a, dfull, grey, depth_tol, region_top))
            win.append((a, obj, grey, gi if gi is not None else next_emit + len(win), fr))
            while len(win) - (next_emit - base[0]) > 2:
                k = next_emit - base[0]
                emit(win[k][0], [x[1] for x in list(win)[max(0, k - 2):k + 3]] if objects else [], win[k][2],
                     win[k][3], win[k][4])
                next_emit += 1
                while next_emit - base[0] > 2:  # keep two frames of history for the median
                    win.popleft(); base[0] += 1
        while next_emit - base[0] < len(win):  # the last frames (shorter window at the end)
            k = next_emit - base[0]
            emit(win[k][0], [x[1] for x in list(win)[max(0, k - 2):k + 3]] if objects else [], win[k][2],
                 win[k][3], win[k][4])
            next_emit += 1
        th.join(); proc.wait()
        if need is not None:
            while written[0] < n_total:
                w.stdin.write(zero); written[0] += 1
        mres_extra = {"objects": bool(objects), "object_frames": stats["object_frames"],
                      "object_share": round(stats["object_px"] / max(1, stats["object_frames"]), 4),
                      "depth": bool(depth is not None and depth.pipe is not None), "refined": bool(refine),
                      "depth_objects": bool(objects and use_depth_objects and depth is not None and depth.pipe is not None),
                      "depth_tolerance": depth_tol, "matted_frames": stats["frames"],
                      "edge_refined_frames": stats["refined"], "ranges": [list(r) for r in (ranges or [])]}
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
