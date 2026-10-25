"""SRT parsing: numbered cues with HH:MM:SS,mmm --> HH:MM:SS,mmm timestamps."""
from __future__ import annotations

import re
from pathlib import Path

_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_TAGS = re.compile(r"<[^>]+>|\{\\[^}]*\}|\[[^\]]*\]")  # html, ASS, and [laughs]/[music] notes


def _sec(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse(text: str) -> list[dict]:
    """Return [{index, start, end, text}] sorted by start. Tolerates BOM, CRLF, missing numbers."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        t_idx = next((i for i, ln in enumerate(lines) if _TIME.search(ln)), None)
        if t_idx is None:
            continue
        m = _TIME.search(lines[t_idx])
        start, end = _sec(*m.groups()[:4]), _sec(*m.groups()[4:])
        body = " ".join(_TAGS.sub("", ln) for ln in lines[t_idx + 1 :]).strip()
        body = re.sub(r"\s+", " ", body)
        if not body:
            continue
        idx = int(lines[0]) if t_idx == 1 and lines[0].isdigit() else len(cues) + 1
        cues.append({"index": idx, "start": round(start, 3), "end": round(max(end, start + 0.05), 3), "text": body})
    cues.sort(key=lambda c: c["start"])
    return cues


def load(path: Path) -> list[dict]:
    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))


def fmt_time(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def dump(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c['start'])} --> {fmt_time(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


def slice_cues(cues: list[dict], start: float, end: float, rebase: bool = True) -> list[dict]:
    """Cues overlapping [start, end], optionally shifted so `start` becomes 0."""
    out = []
    for c in cues:
        if c["end"] <= start or c["start"] >= end:
            continue
        s, e = max(c["start"], start), min(c["end"], end)
        off = start if rebase else 0.0
        out.append({**c, "start": round(s - off, 3), "end": round(e - off, 3)})
    return out


def fix_overlaps(cues: list[dict]) -> int:
    """Rolling captions (YouTube-style) overlap: each cue starts before the last one ends.
    The speech for a cue really runs until the next cue starts, so trim each end to that."""
    fixed = 0
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"] + 0.01:
            a["end"] = round(max(a["start"] + 0.2, b["start"]), 3)
            fixed += 1
    return fixed
