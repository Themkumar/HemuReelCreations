"""Face detection, One-Euro smoothing and the 16:9 -> 9:16 crop path."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import log


class OneEuro:
    """One-Euro filter (Casiez et al.): smooth when still, responsive when moving."""

    def __init__(self, freq: float, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0):
        self.freq, self.min_cutoff, self.beta, self.d_cutoff = freq, min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = 0.0

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau * freq)

    def reset(self, value=None):
        self.x, self.dx = value, 0.0

    def __call__(self, value: float) -> float:
        if self.x is None:
            self.x = value
            return value
        dx = (value - self.x) * self.freq
        a_d = self._alpha(self.d_cutoff, self.freq)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        a = self._alpha(cutoff, self.freq)
        self.x = a * value + (1 - a) * self.x
        return self.x


def _detector():
    """MediaPipe if installed, else OpenCV's bundled Haar cascade."""
    try:
        import mediapipe as mp  # type: ignore

        det = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)

        def mp_detect(rgb):
            res = det.process(rgb)
            out = []
            for d in res.detections or []:
                b = d.location_data.relative_bounding_box
                out.append({"cx": b.xmin + b.width / 2, "cy": b.ymin + b.height / 2, "w": b.width, "h": b.height,
                            "score": float(d.score[0])})
            return out

        return "mediapipe", mp_detect
    except Exception:
        pass
    tasks = _mp_tasks_detector()
    if tasks is not None:
        return "mediapipe-tasks", tasks
    if True:
        import cv2

        if not hasattr(cv2, "CascadeClassifier"):
            log.warn("No face detector available (install mediapipe, or opencv-python-headless<5). Using centre crop.")
            return "none", lambda rgb: []
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

        def cv_detect(rgb):
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape
            rects = casc.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(w // 20, w // 20))
            return [{"cx": (x + rw / 2) / w, "cy": (y + rh / 2) / h, "w": rw / w, "h": rh / h, "score": 0.8}
                    for (x, y, rw, rh) in rects]

        return "opencv-haar", cv_detect


MP_FACE_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"


def _mp_tasks_detector():
    """Newer MediaPipe (no `solutions`): the Tasks face detector, model downloaded once."""
    try:
        import urllib.request

        import mediapipe as mp  # type: ignore
        from mediapipe.tasks.python import BaseOptions, vision  # type: ignore

        model = Path.home() / ".cache" / "hrc" / "blaze_face_short_range.tflite"
        if not model.exists():
            model.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(MP_FACE_MODEL, model)
        det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(model)), min_detection_confidence=0.5))

        def detect(rgb):
            h, w = rgb.shape[:2]
            res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
            out = []
            for d in res.detections or []:
                b = d.bounding_box
                out.append({"cx": (b.origin_x + b.width / 2) / w, "cy": (b.origin_y + b.height / 2) / h,
                            "w": b.width / w, "h": b.height / h, "score": float(d.categories[0].score)})
            return out
        return detect
    except Exception:
        return None


def detect_faces(video: Path, sample_fps: float = 5, start: float = 0.0, end: float | None = None) -> dict:
    import cv2

    name, detect = _detector()
    log.info("Face detector: %s", name)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / sample_fps)))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    idx = int(round(start * fps))
    samples = []
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = idx / fps
        if end is not None and t > end:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            samples.append({"t": round(t, 3), "faces": detect(rgb)})
        idx += 1
    cap.release()
    return {"detector": name, "sample_fps": sample_fps, "samples": samples}


def _pick_face(faces: list[dict], prev_cx: float | None) -> dict | None:
    if not faces:
        return None
    if prev_cx is None:
        return max(faces, key=lambda f: f["w"] * f["h"])
    # prefer continuity, then size
    return min(faces, key=lambda f: abs(f["cx"] - prev_cx) * 2 - f["w"] * f["h"])


def crop_path(faces: dict, shots: list[dict], t0: float, t1: float, fps: float, src_w: int, src_h: int,
              out_w: int, out_h: int, min_cutoff: float = 0.15, beta: float = 0.003, head_room: float = 0.30,
              deadzone: float = 0.12, max_pan: float = 0.012) -> list[dict]:
    """Per output frame: crop box in source pixels plus head position in output pixels.

    The crop behaves like a camera operator on a tripod: it holds still while the face stays inside
    a dead zone (deadzone x crop width around the centre) and only pans, gently and with a speed
    limit, when the face really moves away. GFX anchoring uses the (lightly smoothed) head
    position, so graphics still follow the speaker while the frame stays steady.
    """
    samples = [s for s in faces.get("samples", []) if t0 - 1 <= s["t"] <= t1 + 1]
    times = np.array([s["t"] for s in samples]) if samples else np.array([])
    cut_times = sorted(s["start"] for s in shots if t0 < s["start"] < t1)
    aspect = out_w / out_h
    base_h = src_h
    base_w = min(src_w, base_h * aspect)

    def face_at(t, prev_cx):
        if len(times):
            j = int(np.argmin(np.abs(times - t)))
            if abs(times[j] - t) < 0.6:
                return _pick_face(samples[j]["faces"], prev_cx)
        return None

    def shot_target(ts, te):
        """Median face position over the first ~1.5 s of a shot: where the 'camera' starts."""
        xs = [f["cx"] for f in (face_at(t, None) for t in np.arange(ts, min(te, ts + 1.5), 0.2)) if f]
        return float(np.median(xs)) * src_w if xs else None

    def split_panel(ts, te, target):
        """Split-screen shot (two people side by side, each in a half): the speaker's half, else None."""
        hits = seen = 0
        for t in np.arange(ts, min(te, ts + 3.0), 0.2):
            fs = [f for f in (samples[int(np.argmin(np.abs(times - t)))]["faces"] if len(times) else [])
                  if f["h"] > 0.05]
            if not fs:
                continue
            seen += 1
            left = [f for f in fs if f["cx"] < 0.42]
            right = [f for f in fs if f["cx"] > 0.58]
            if left and right and max(left[0]["h"], right[0]["h"]) < 2.5 * min(left[0]["h"], right[0]["h"]):
                hits += 1
        if target is None or seen == 0 or hits < 0.5 * seen:
            return None
        return (0.0, src_w / 2) if target < src_w / 2 else (src_w / 2, float(src_w))

    fx, fy = OneEuro(fps, min_cutoff, beta), OneEuro(fps, min_cutoff, beta)
    hx, hy = OneEuro(fps, 1.2, 0.02), OneEuro(fps, 1.2, 0.02)  # head follow for GFX
    bounds = [t0] + cut_times + [t1]
    prev_cx, prev_face, cam_x, vel = None, None, None, 0.0
    frames = []
    n = int(round((t1 - t0) * fps))
    next_cut = 0
    for i in range(n):
        t = t0 + i / fps
        new_shot = i == 0
        while next_cut < len(cut_times) and t >= cut_times[next_cut]:
            next_cut += 1
            new_shot = True
        if new_shot:
            fx.reset(); fy.reset(); hx.reset(); hy.reset(); prev_cx = None; vel = 0.0
            te_ = bounds[next_cut + 1] if next_cut + 1 < len(bounds) else t1
            cam_x = shot_target(t, te_)
            panel = split_panel(t, te_, cam_x)
            if panel:  # crop inside the speaker's half only; face a bit off-centre, room for graphics
                cw = min((panel[1] - panel[0]) * 0.8, src_h * aspect)
                lo_x, hi_x = panel[0], panel[1] - cw
                mid = (panel[0] + panel[1]) / 2
                bias = cw * 0.12 * (1 if cam_x >= mid else -1)
            else:
                cw, lo_x, hi_x, bias = base_w, 0.0, src_w - base_w, 0.0
            ch = cw / aspect
            if cam_x is not None:
                cam_x += bias
        face = face_at(t, prev_cx) or prev_face or {"cx": 0.5, "cy": 0.4, "w": 0.1, "h": 0.15}
        prev_cx, prev_face = face["cx"], face
        sx_face = fx(face["cx"] * src_w)
        cy = fy(face["cy"] * src_h)
        if cam_x is None:
            cam_x = sx_face + bias
        off = sx_face + bias - cam_x
        dz = deadzone * cw
        if abs(off) > dz:  # pan toward the face, eased, speed-limited
            want = (off - np.sign(off) * dz) * 0.08
            lim = max_pan * cw
            vel = 0.85 * vel + 0.15 * float(np.clip(want, -lim, lim))
        else:
            vel *= 0.8
        cam_x += vel
        x = float(np.clip(cam_x - cw / 2, lo_x, hi_x))
        y = float(np.clip(cy - ch * head_room, 0, src_h - ch))
        sx = out_w / cw
        head = {"x": (hx(face["cx"] * src_w) - x) * sx, "y": (hy(face["cy"] * src_h) - y) * sx,
                "size": face["h"] * src_h * sx}
        frames.append({"t": round(t, 4), "x": x, "y": y, "w": cw, "h": ch, "head": head})
    return frames
