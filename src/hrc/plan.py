"""reels_plan.json helpers: load, validate, snap cuts to word boundaries, map source time to reel time."""
from __future__ import annotations

from pathlib import Path

from . import log
from .util import parse_ts, read_json, write_json

MOODS = {"inspirational", "tense", "emotional", "funny", "calm", "energetic"}


def plan_path(cfg) -> Path:
    return cfg.root / "reels_plan.json"


def load(cfg) -> dict:
    p = plan_path(cfg)
    if not p.exists():
        raise FileNotFoundError("reels_plan.json not found. Run `hrc select prepare` and fill the plan first.")
    return read_json(p)


def save(cfg, plan: dict) -> None:
    write_json(plan_path(cfg), plan)


def segments(reel: dict) -> list[dict]:
    segs = reel.get("segments") or [{"start": reel["start"], "end": reel["end"]}]
    return [{"start": parse_ts(s["start"]), "end": parse_ts(s["end"])} for s in segs]


def duration(reel: dict) -> float:
    return sum(s["end"] - s["start"] for s in segments(reel)) + (context_card_sec(reel))


def context_card_sec(reel: dict) -> float:
    # The opening text card is off: it read as a cheap hook and showed Telugu in every language.
    # A reel can still opt in with "show_context_card": true.
    return 1.6 if reel.get("context_card") and reel.get("show_context_card", False) else 0.0


def to_reel_time(reel: dict, t: float) -> float | None:
    """Source time -> time in the finished reel (None if t is cut out)."""
    off = context_card_sec(reel)
    for s in segments(reel):
        if s["start"] <= t <= s["end"]:
            return off + (t - s["start"])
        off += s["end"] - s["start"]
    return None


def cut_points(reel: dict) -> list[float]:
    """Reel-time positions where one segment ends and the next begins."""
    off = context_card_sec(reel)
    pts = [off] if off else []
    for s in segments(reel)[:-1]:
        off += s["end"] - s["start"]
        pts.append(off)
    return pts


def snap_to_words(t: float, words: list[dict], side: str) -> float:
    """Move a cut to the nearest gap between words so we never cut mid-word."""
    best, best_d = t, 1e9
    for a, b in zip(words, words[1:]):
        gap_mid = (a["end"] + b["start"]) / 2
        d = abs(gap_mid - t)
        if d < best_d:
            best, best_d = gap_mid, d
    if best_d > 1.5:
        return t
    return round(best, 3)


def validate(cfg, plan: dict, words: list[dict]) -> list[str]:
    """Snap cuts, fill ids, return plain-English problems."""
    problems = []
    lo, hi = cfg.at("reel.min_sec", 30), cfg.at("reel.max_sec", 60)
    for i, reel in enumerate(plan.get("reels", []), 1):
        reel.setdefault("id", f"reel{i:02d}")
        reel.setdefault("mood", "inspirational")
        if reel["mood"] not in MOODS:
            problems.append(f"{reel['id']}: mood '{reel['mood']}' unknown, using 'inspirational'.")
            reel["mood"] = "inspirational"
        segs = segments(reel)
        for s in segs:
            s["start"] = snap_to_words(s["start"], words, "start")
            s["end"] = snap_to_words(s["end"], words, "end")
        reel["segments"] = segs
        reel.pop("start", None); reel.pop("end", None)
        d = duration(reel)
        reel["duration"] = round(d, 2)
        if d > hi:
            problems.append(f"{reel['id']}: {d:.1f}s is over the {hi}s limit. Trim a segment.")
        elif d < lo - 2:
            problems.append(f"{reel['id']}: {d:.1f}s is shorter than {lo}s.")
        for vm in reel.get("visual_moments", []):
            if not vm.get("phrase"):
                problems.append(f"{reel['id']}: a visual moment has no phrase.")
    return problems


def approved(cfg) -> dict:
    plan = load(cfg)
    if not plan.get("approved"):
        raise PermissionError("The reel plan is not approved yet. Review reels_plan.json, then run `hrc approve`.")
    return plan


def selected(plan: dict, only: str | None) -> list[dict]:
    reels = [r for r in plan["reels"] if r.get("enabled", True)]
    if only:
        wanted = set(only.split(","))
        reels = [r for r in reels if r["id"] in wanted]
        if not reels:
            raise KeyError(f"No reel with id {only}")
    return reels


def reel_words(reel: dict, words: list[dict]) -> list[dict]:
    """Words inside the reel's segments, with times converted to reel time."""
    out = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        rt = to_reel_time(reel, mid)
        if rt is None:
            continue
        s = to_reel_time(reel, max(w["start"], _seg_of(reel, mid)["start"]))
        e = to_reel_time(reel, min(w["end"], _seg_of(reel, mid)["end"]))
        out.append({**w, "start": round(s, 3), "end": round(e, 3), "src_start": w["start"]})
    return out


def _seg_of(reel: dict, t: float) -> dict:
    for s in segments(reel):
        if s["start"] <= t <= s["end"]:
            return s
    raise ValueError(t)


def log_summary(plan: dict) -> None:
    for r in plan.get("reels", []):
        log.info("  %s  %5.1fs  score %s  %s", r["id"], r.get("duration", 0), r.get("virality", "?"), r.get("hook", ""))
