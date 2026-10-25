"""Karaoke subtitles as ASS (rendered by libass + HarfBuzz, so Indic scripts shape correctly)."""
from __future__ import annotations

from pathlib import Path

# Scripts have different visual heights at the same point size; even them out.
LANG_SCALE = {"te": 1.0, "hi": 1.12, "en": 1.0, "ta": 0.9, "kn": 1.08}


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")")


def _headline_text(text: str, latin: bool, accent: str, max_chars: int) -> str:
    """'THE MAN WHO *SHOT OSAMA*' -> ASS text with the starred words in the accent colour,
    broken into two balanced lines when long."""
    words = text.split()
    if latin:
        words = [w.upper() for w in words]
    # balanced 2-line break
    lines = [words]
    if len(" ".join(words).replace("*", "")) > max_chars and len(words) > 1:
        k = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        lines = [words[:k], words[k:]]
    out, on = [], False
    for li, ws in enumerate(lines):
        parts = []
        for w in ws:
            t = _esc(w)
            start, end = t.startswith("*"), t.endswith("*") and len(t) > 1
            t = t.strip("*")
            if start:
                on = True
            parts.append(f"{{\\c{ass_color(accent)}}}{t}{{\\c&H00FFFFFF&}}" if on else t)
            if end:
                on = False
        out.append(" ".join(parts))
    return "\\N".join(out)


def build_ass(subs: dict, cfg, width: int = 1080, height: int = 1920, box=None, headline: str | None = None,
              duration: float | None = None) -> str:
    sc = dict(cfg.at("subtitles", {}))
    framed = box is not None and (box[2], box[3]) != (width, height)  # reel: video box inside a 9:16 frame
    if width > height:  # landscape: lower on screen, no right-edge UI to avoid
        sc.update(sc.get("landscape", {"y_position": 0.84, "bottom_safe": 0.05, "right_safe": 0.0}))
    if framed:
        sc.update(sc.get("reel", {"font_scale": 0.85, "right_safe": 0.0}))
    lang = subs["lang"]
    font = sc.get("fonts", {}).get(lang, "Noto Sans")
    k = min(width, height) / 1080
    size = int(sc.get("font_size", 78) * k * LANG_SCALE.get(lang, 1.0) * sc.get("font_scale", 1.0))
    outline = max(1, int(sc.get("outline", 6) * k))
    hi_col, acc_col = (cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"])[:2]
    base = ass_color(sc.get("base_color", "#FFFFFF"))
    margin_lr = int(width * 0.08)
    margin_r = int(width * sc.get("right_safe", 0.12))
    y = int(height * sc.get("y_position", 0.66))
    y = min(y, int(height * (1 - sc.get("bottom_safe", 0.22))) - int(size * 1.3) * sc.get("max_lines", 2))
    x = (margin_lr + (width - margin_r)) // 2
    if framed:  # captions in the black area right under the picture: they never cover a face
        y = int(box[1] + box[3] + height * float(cfg.at("layout.reel.caption_gap", 0.03)))
        x = width // 2
    lay = cfg.at("layout.reel", {}) or {}
    latin = lang == "en"
    h_font = "Anton" if latin else font
    h_size = int(float(lay.get("headline_size", 96)) * k * (1.0 if latin else 0.72 * LANG_SCALE.get(lang, 1.0)))

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font},{size},{base},{base},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},5,{margin_lr},{margin_r},0,1
Style: Headline,{h_font},{h_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{0 if latin else -1},0,0,0,100,100,0,0,1,0,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    lead = sc.get("lead_sec", 0.05)  # highlight a hair early; it reads as "on time" to the eye
    hold = float(sc.get("hold_gap_sec", 0.6))  # a short pause keeps the caption up instead of blinking off
    chunks = [c for c in subs["chunks"] if c["words"]]
    for ci, ch in enumerate(chunks):
        words = ch["words"]
        start, end = ch["start"], ch["end"]
        prev_end = chunks[ci - 1]["end"] if ci else None
        nxt = chunks[ci + 1]["start"] if ci + 1 < len(chunks) else None
        if nxt is not None and nxt - end < hold:
            end = max(end, nxt)  # hand straight over to the next caption, no blank frame
        if ci and prev_end is not None and start - prev_end < hold:
            start = max(start, chunks[ci - 1]["_end"])  # begin exactly where the last one stopped
        ch["_end"] = end
        after_pause = ci == 0 or prev_end is None or start - prev_end >= hold
        # contiguous, centisecond-exact event times: no gaps, no overlaps, so nothing blinks
        cuts = [start] + [max(start, w["start"] - lead) for w in words[1:]] + [end]
        cs = [int(round(t * 100)) for t in cuts]
        for i in range(1, len(cs)):
            cs[i] = max(cs[i], cs[i - 1])
        for i, w in enumerate(words):
            if cs[i + 1] <= cs[i]:
                continue
            parts, prev_line = [], words[0].get("line", 0)
            for j, other in enumerate(words):
                t = _esc(other["w"])
                if other.get("line", 0) != prev_line:
                    parts.append("\\N"); prev_line = other.get("line", 0)
                elif j:
                    parts.append(" ")
                size = "\\fscx112\\fscy112" if other.get("emph") else ""  # constant across events: no reflow
                if j == i:
                    col = ass_color(acc_col if other.get("emph") else hi_col)
                    parts.append(f"{{\\c{col}{size}}}{t}{{\\r}}")
                elif other.get("emph"):
                    parts.append(f"{{\\c{ass_color(acc_col)}{size}}}{t}{{\\r}}")
                else:
                    parts.append(t)
            intro = "\\fad(120,0)" if i == 0 and after_pause else ""
            text = f"{{\\an8\\pos({x},{y}){intro}}}" + "".join(parts)
            lines.append(f"Dialogue: 0,{ass_time(cs[i] / 100)},{ass_time(cs[i + 1] / 100)},Karaoke,,0,0,0,,{text}")
    if framed and headline:
        end = duration or max((c["end"] for c in subs["chunks"]), default=60.0) + 1
        ht = _headline_text(headline, latin, acc_col, int(lay.get("headline_chars", 22 if latin else 18)))
        hy = int(box[1] - height * float(lay.get("headline_gap", 0.025)))
        lines.insert(0, f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Headline,,0,0,0,,{{\\an2\\pos({width // 2},{hy})}}{ht}")
    return head + "\n".join(lines) + "\n"


def write_ass(subs: dict, cfg, out: Path, width: int = 1080, height: int = 1920, box=None,
              headline: str | None = None, duration: float | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_ass(subs, cfg, width, height, box, headline, duration), encoding="utf-8")
    return out
