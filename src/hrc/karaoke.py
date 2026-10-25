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


def build_ass(subs: dict, cfg, width: int = 1080, height: int = 1920) -> str:
    sc = dict(cfg.at("subtitles", {}))
    if width > height:  # landscape: lower on screen, no right-edge UI to avoid
        sc.update(sc.get("landscape", {"y_position": 0.84, "bottom_safe": 0.05, "right_safe": 0.0}))
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

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    lead = sc.get("lead_sec", 0.05)  # highlight a hair early; it reads as "on time" to the eye
    for ch in subs["chunks"]:
        words = ch["words"]
        if not words:
            continue
        # contiguous, centisecond-exact event times: no gaps, no overlaps, so nothing blinks
        cuts = [ch["start"]] + [max(ch["start"], w["start"] - lead) for w in words[1:]] + [ch["end"]]
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
            intro = "\\fad(80,0)" if i == 0 else ""
            text = f"{{\\an8\\pos({x},{y}){intro}}}" + "".join(parts)
            lines.append(f"Dialogue: 0,{ass_time(cs[i] / 100)},{ass_time(cs[i + 1] / 100)},Karaoke,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


def write_ass(subs: dict, cfg, out: Path, width: int = 1080, height: int = 1920) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_ass(subs, cfg, width, height), encoding="utf-8")
    return out
