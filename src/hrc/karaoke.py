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


STOP = set("a an the and or but so to of in on at for with is are was were be been it its i you he she we they "
           "my your his her our their me him us them this that these those do did does not no as by from up "
           "just very really then than if when what who how all can could would will i'd i'm it's don't".split())


def beats(words: list[dict], max_words: int = 3) -> list[list[dict]]:
    """Group a caption's words into short beats of 1-3 words. A hero word always ends its beat,
    so it lands as the big word under the small lead-in words."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt_hero = i + 1 < len(words) and words[i + 1].get("hero")
        if w.get("hero") or w["w"][-1:] in ",.!?;:" or (len(cur) >= max_words and not nxt_hero):
            out.append(cur); cur = []
    if cur:
        if out and len(cur) == 1 and not out[-1][-1].get("hero") and len(out[-1]) < max_words + 1:
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out


def _title(w: str) -> str:
    return w if any(c.isupper() for c in w[1:]) or not w[:1].isalpha() else w[:1].upper() + w[1:]


def build_ass_punch(subs: dict, cfg, width: int = 1080, height: int = 1920, duration: float | None = None,
                    box=None, headline: str | None = None) -> str:
    """Reference-reel captions: a few small white words that appear as they are spoken, and the key
    word of the line big, in a glowing italic serif, right under them."""
    sc = dict(cfg.at("subtitles", {}))
    ps = dict(sc.get("punch", {}))
    k = width / 1080
    lead_font, hero_font = ps.get("lead_font", "Inter SemiBold"), ps.get("hero_font", "Playfair Hero")
    lead_size, hero_size = int(ps.get("lead_size", 58) * k), int(ps.get("hero_size", 168) * k)
    hero_col = ass_color(ps.get("hero_color", "#F4E73A"))
    glow_col = ass_color(ps.get("glow_color", "#E8D700"))
    y_lead = int(height * float(ps.get("y", 0.6)))
    framed = box is not None and (box[2], box[3]) != (width, height)  # wide shot boxed in a 9:16 frame
    lay = cfg.at("layout.reel", {}) or {}
    if framed:  # captions in the dark area right under the picture: they never cover a face
        y_lead = int(box[1] + box[3] + height * float(lay.get("caption_gap", 0.03)))
    h_size = int(float(lay.get("headline_size", 96)) * k)
    accent = (cfg.at("project.brand_colors") or ["#FFD400", "#FF3B5C"])[0]
    y_hero = y_lead + int(lead_size * 0.55)
    x = width // 2
    max_w = int(ps.get("max_words", 3))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lead,{lead_font},{lead_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,0,{max(1, int(3 * k))},8,60,60,0,1
Style: Hero,{hero_font},{hero_size},{hero_col},{hero_col},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,40,40,0,1
Style: Glow,{hero_font},{hero_size},{glow_col},{glow_col},{glow_col},&H00000000,0,0,0,0,100,100,0,0,1,{max(2, int(10 * k))},0,8,40,40,0,1
Style: Headline,Anton,{h_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hold = float(sc.get("hold_gap_sec", 0.6))
    lead = float(sc.get("lead_sec", 0.05))
    all_beats = []
    for ch in sorted(subs["chunks"], key=lambda c: c["start"]):
        for b in beats([w for w in ch["words"] if w["w"].strip()], max_w):
            all_beats.append(b)
    all_beats = merge_short_beats(all_beats, float(ps.get("min_beat_sec", 0.65)), max_w + 1)
    lines = []
    for bi, b in enumerate(all_beats):
        t0 = max(0.0, b[0]["start"] - lead)
        nxt = all_beats[bi + 1][0]["start"] - lead if bi + 1 < len(all_beats) else None
        t1 = b[-1]["end"] + 0.25
        if nxt is not None and (nxt - t1 < hold or nxt < t1):
            t1 = nxt  # hand over to the next beat on the same frame: nothing blinks
        if t1 - t0 < 0.12:
            t1 = t0 + 0.12
        hero = b[-1] if b[-1].get("hero") else None
        small = b[:-1] if hero else b
        # lead words: each appears when it is spoken and stays for the beat
        if small:
            for i, w in enumerate(small):
                a = max(t0, w["start"] - lead)
                if a >= t1:
                    break
                txt = " ".join(_esc(_title(x["w"])) for x in small[: i + 1])
                e = max(a + 0.01, min(t1, small[i + 1]["start"] - lead)) if i + 1 < len(small) else t1
                lines.append(f"Dialogue: 2,{ass_time(a)},{ass_time(e)},Lead,,0,0,0,,{{\\an8\\pos({x},{y_lead})}}{txt}")
        if hero:
            a = max(t0, hero["start"] - lead)
            if a < t1:
                word = _esc(_title(hero["w"].strip("'\"‘’“”,.;:")))
                pop = "\\fad(180,0)\\fscx108\\fscy108\\t(0,320,0.6,\\fscx100\\fscy100)"  # slow, cinematic settle
                lines.append(f"Dialogue: 0,{ass_time(a)},{ass_time(t1)},Glow,,0,0,0,,"
                             f"{{\\an8\\pos({x},{y_hero})\\blur{int(14 * k)}\\alpha&H70&\\1a&HFF&{pop}}}{word}")
                lines.append(f"Dialogue: 1,{ass_time(a)},{ass_time(t1)},Hero,,0,0,0,,"
                             f"{{\\an8\\pos({x},{y_hero})\\blur0.6{pop}}}{word}")
    if framed and headline:  # the reel's title above the picture, key words in the brand yellow
        end = duration or max((c["end"] for c in subs["chunks"]), default=60.0) + 1
        ht = _headline_text(headline, True, accent, int(lay.get("headline_chars", 22)))
        hy = int(box[1] - height * float(lay.get("headline_gap", 0.025)))
        lines.insert(0, f"Dialogue: 3,{ass_time(0)},{ass_time(end)},Headline,,0,0,0,,{{\\an2\\pos({width // 2},{hy})}}{ht}")
    return head + "\n".join(lines) + "\n"


PUNCT = "'\"‘’“”,.;:!?…()"


def ref_beats(words: list[dict]) -> list[tuple[list[dict], dict]]:
    """Reference-reel beats: (small lead words, BIG word). Up to two short function words ("I'd just",
    "on my") ride small on top of the next real word, which goes big. A starred hero word is always big."""
    out, buf = [], []
    for i, w in enumerate(words):
        bare = w["w"].strip(PUNCT).lower()
        last = i == len(words) - 1
        ends = w["w"][-1:] in ",.!?;:"
        if not w.get("hero") and not ends and not last and len(buf) < 2 and (bare in STOP or len(bare) <= 2):
            buf.append(w)
            continue
        out.append((buf, w)); buf = []
    if buf:
        out.append((buf[:-1], buf[-1]))
    return out


def merge_short_beats(beats_: list, min_sec: float = 0.65, max_words: int = 4) -> list:
    """Glow look: a beat on screen shorter than min_sec ("uh,", "job,") would flash. It rides along
    as small lead words of the next beat instead (a hero word always keeps its own beat)."""
    out, carry = [], []
    for i, b in enumerate(beats_):
        b = carry + b
        carry = []
        nxt = beats_[i + 1] if i + 1 < len(beats_) else None
        if nxt and not b[-1].get("hero") and nxt[0]["start"] - b[0]["start"] < min_sec \
                and len(b) + len(nxt) <= max_words:
            carry = b
            continue
        out.append(b)
    if carry:
        out.append(carry)
    return out


def merge_quick_beats(beats_: list, min_sec: float = 0.65, max_chars: int = 13) -> list:
    """Words spoken fast would flash by one per beat. A beat shorter than min_sec takes the next
    beat's big word with it ("RIGHT THEN"), so every caption stays up long enough to read."""
    out = []
    i = 0
    while i < len(beats_):
        small, big = beats_[i]
        while i + 1 < len(beats_):
            n_small, n_big = beats_[i + 1]
            span = n_big["start"] - (small[0] if small else big)["start"] if not n_small else \
                n_small[0]["start"] - (small[0] if small else big)["start"]
            text = (big["w"] + " " + " ".join(x["w"] for x in n_small) + " " + n_big["w"]).split()
            if span >= min_sec or big.get("hero") or n_big.get("hero") or big["w"][-1:] in ".!?" \
                    or len(" ".join(text)) > max_chars:
                break
            big = {**big, "w": " ".join(text), "end": n_big["end"]}
            i += 1
        out.append((small, big))
        i += 1
    return out


def build_ass_ref(subs: dict, cfg, width: int, height: int, box=None, headline: str | None = None,
                  duration: float | None = None) -> str:
    """v0.8.2 reference look: just under the video box, one BIG bold word at a time (colour changing
    every beat, the starred hero words in red) with a small lead word over it (wide caps, or an italic
    serif on every other beat). The reel title sits above the box. Event times are contiguous, so
    nothing blinks between beats."""
    sc = dict(cfg.at("subtitles", {}))
    ps = dict(sc.get("punch", {}))
    lay = cfg.at("layout.reel", {}) or {}
    k = width / 1080
    big_font, small_font = ps.get("big_font", "Anton"), ps.get("small_font", "Inter SemiBold")
    alt_font = ps.get("alt_small_font", "Playfair Hero")
    big_size, small_size = int(ps.get("big_size", 150) * k), int(ps.get("small_size", 46) * k)
    colors = ps.get("colors") or ["#FFC928", "#3DDC6A", "#33D6C3", "#E8302A", "#FFFFFF"]
    hero_col = "#E8302A" if "#E8302A" in colors else colors[0]
    cycle = [c for c in colors if c != hero_col] or colors
    framed = box is not None and (box[2], box[3]) != (width, height)
    y0 = int(box[1] + box[3] + height * float(lay.get("caption_gap", 0.03))) if framed else int(height * float(ps.get("y", 0.6)))
    x = width // 2
    hi_col = (cfg.at("project.brand_colors") or ["#FFD400"])[0]
    h_size = int(float(lay.get("headline_size", 124)) * k)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Small,{small_font},{small_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,{max(1, int(5 * k))},0,1,0,{max(1, int(2 * k))},8,40,40,0,1
Style: SmallAlt,{alt_font},{int(small_size * 1.25)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,{max(1, int(2 * k))},8,40,40,0,1
Style: Big,{big_font},{big_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,{max(1, int(2 * k))},0,1,0,{max(1, int(4 * k))},8,40,40,0,1
Style: Headline,Anton,{h_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    hold = float(sc.get("hold_gap_sec", 0.6))
    lead = float(sc.get("lead_sec", 0.05))
    beats_ = []
    for ch in sorted(subs["chunks"], key=lambda c: c["start"]):
        beats_ += ref_beats([w for w in ch["words"] if w["w"].strip()])
    beats_ = merge_quick_beats(beats_, float(ps.get("min_beat_sec", 0.65)), int(ps.get("merge_chars", 13)))
    lines, ci = [], 0
    y_big = y0 + int(small_size * 1.15)
    for bi, (small, big) in enumerate(beats_):
        first = small[0] if small else big
        t0 = max(0.0, first["start"] - lead)
        if lines and bi:  # start exactly where the previous beat ended (no gap, no overlap)
            t0 = max(t0, prev_t1)
        nxt = (beats_[bi + 1][0] or [beats_[bi + 1][1]])[0] if bi + 1 < len(beats_) else None
        t1 = big["end"] + 0.3
        if nxt is not None:
            ns = nxt["start"] - lead
            if ns - t1 < hold or ns < t1:
                t1 = ns
        t1 = max(t1, t0 + 0.12)
        prev_t1 = t1
        word = big["w"].strip(PUNCT) or big["w"]
        word = word.upper()
        if big.get("hero"):
            col = hero_col
        else:
            col = cycle[ci % len(cycle)]; ci += 1
        fit = min(100, int(100 * (width * 0.9) / max(1, len(word) * big_size * 0.47)))  # Anton ~0.47 em/char
        tb = max(t0, big["start"] - lead) if small else t0
        alt = bi % 2 == 1
        for si, sm in enumerate(small):  # lead words appear as they are spoken and stay for the beat
            a = t0 if si == 0 else max(t0, sm["start"] - lead)
            if a >= t1:
                break
            sw = " ".join((x["w"].strip(PUNCT) or x["w"]) for x in small[: si + 1])
            st = sw[:1].upper() + sw[1:].lower() if alt else sw.upper()
            e = max(a + 0.01, min(t1, small[si + 1]["start"] - lead)) if si + 1 < len(small) else t1
            lines.append(f"Dialogue: 1,{ass_time(a)},{ass_time(e)},{'SmallAlt' if alt else 'Small'},,0,0,0,,"
                         f"{{\\an8\\pos({x},{y0})}}{_esc(st)}")
        if tb < t1:
            # cinematic landing: a soft fade with a slow settle, not a snap
            pop = (f"\\fad(180,0)\\fscx{int(fit * 1.06)}\\fscy{int(fit * 1.06)}"
                   f"\\t(0,320,0.6,\\fscx{fit}\\fscy{fit})")
            lines.append(f"Dialogue: 2,{ass_time(tb)},{ass_time(t1)},Big,,0,0,0,,"
                         f"{{\\an8\\pos({x},{y_big})\\c{ass_color(col)}{pop}}}{_esc(word)}")
    if framed and headline:
        end = duration or max((c["end"] for c in subs["chunks"]), default=60.0) + 1
        ht = _headline_text(headline, True, hi_col, int(lay.get("headline_chars", 20)))
        hy = int(box[1] - height * float(lay.get("headline_gap", 0.025)))
        lines.insert(0, f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Headline,,0,0,0,,{{\\an2\\pos({width // 2},{hy})}}{ht}")
    return head + "\n".join(lines) + "\n"


def build_ass(subs: dict, cfg, width: int = 1080, height: int = 1920, box=None, headline: str | None = None,
              duration: float | None = None) -> str:
    sc = dict(cfg.at("subtitles", {}))
    if sc.get("style") == "punch" and subs.get("lang") == "en":
        if (sc.get("punch") or {}).get("ref_style"):
            return build_ass_ref(subs, cfg, width, height, box, headline, duration)
        return build_ass_punch(subs, cfg, width, height, duration, box, headline)
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
