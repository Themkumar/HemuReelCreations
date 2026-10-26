"""Draws the README pictures pipeline.svg and story.svg in docs/img/.

Run from the HRC folder: python docs/tools/make_diagrams.py  (reel_layout.svg and reel_mix.svg: make_docs_svg.py; resolve_tracks.svg: make_tracks_svg.py)
"""
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "docs" / "img"
BG, CARD, LINE, MUTED = "#0F1115", "#1A1D24", "#2A2D35", "#AAB"
RED, YELLOW, BLUE, PURPLE, GREEN = "#E8302A", "#FFD400", "#1F6FEB", "#8250DF", "#3DDC6A"
SANS = "Helvetica, Arial, sans-serif"
IMPACT = "Anton, Impact, sans-serif"
SLAB = "'Alfa Slab One', Rockwell, Georgia, serif"
SERIF = "'Playfair Display', Georgia, serif"


def svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{SANS}">\n<rect width="{w}" height="{h}" rx="18" fill="{BG}"/>\n{body}\n</svg>\n')


def t(x, y, s, size=14, fill="#fff", weight=400, anchor="start", family=None, extra=""):
    fam = f' font-family="{family}"' if family else ""
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{fam}{extra}>{s}</text>')


def person(cx, top, scale=1.0, cap=False, shirt="#25324A"):
    """A simple seated speaker: head, neck, shoulders (drawn in front of the big words)."""
    r = 26 * scale
    hy = top + r
    out = [f'<rect x="{cx - 62 * scale}" y="{hy + r + 4 * scale}" width="{124 * scale}" height="{90 * scale}" '
           f'rx="{34 * scale}" fill="{shirt}"/>',
           f'<rect x="{cx - 9 * scale}" y="{hy + r - 6 * scale}" width="{18 * scale}" height="{16 * scale}" fill="#C8916A"/>',
           f'<circle cx="{cx}" cy="{hy}" r="{r}" fill="#D9A47A"/>',
           f'<path d="M{cx - r} {hy - 4 * scale} q{r} {-r * 1.5} {2 * r} 0" fill="#1B1B1B"/>']
    if cap:  # dark cap with a red brim, sitting on top of the head
        out.append(f'<path d="M{cx - r * 1.02} {hy - r * 0.25} A{r * 1.02} {r * 1.02} 0 0 1 {cx + r * 1.02} {hy - r * 0.25} z" '
                   f'fill="#111"/>'
                   f'<rect x="{cx - r * 1.02}" y="{hy - r * 0.32}" width="{r * 2.5}" height="{r * 0.2}" rx="2" fill="{RED}"/>')
    return "\n".join(out)


def mic(x, y, s=1.0):
    return (f'<line x1="{x}" y1="{y}" x2="{x + 34 * s}" y2="{y + 46 * s}" stroke="#444" stroke-width="{4 * s}"/>'
            f'<rect x="{x - 8 * s}" y="{y - 22 * s}" width="{16 * s}" height="{30 * s}" rx="{7 * s}" fill="#222" '
            f'stroke="#555"/>')


def phone(x, y, w, look, label):
    """One 9:16 reel at width w: headline, boxed 16:9 picture (word behind the speaker), captions, logo."""
    h = w * 16 / 9
    box_y, box_h = y + h * 0.33, w * 9 / 16
    cx = x + w / 2
    k = w / 300
    g = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#000" stroke="#333"/>']
    # headline
    g.append(t(cx, y + h * 0.17, f'3000 <tspan fill="{RED}">REJECTIONS</tspan>', 24 * k, weight=900,
               anchor="middle", family=IMPACT))
    g.append(t(cx, y + h * 0.17 + 28 * k, "IN ITALY", 24 * k, weight=900, anchor="middle", family=IMPACT))
    # picture box
    g.append(f'<rect x="{x}" y="{box_y}" width="{w}" height="{box_h}" fill="#3B4252"/>')
    g.append(f'<rect x="{x}" y="{box_y + box_h * 0.72}" width="{w}" height="{box_h * 0.28}" fill="#4A3B2C"/>')
    sx = cx + w * 0.12
    if look == "slab":
        g.append(t(cx, box_y + box_h * 0.62, "GONE", 74 * k, RED, 900, "middle", SLAB))
    elif look == "glow":
        g.append(t(cx, box_y + box_h * 0.42, "ONE", 30 * k, "#fff", 900, "middle", IMPACT))
        g.append(t(cx, box_y + box_h * 0.72, "GOOD DAY", 44 * k, YELLOW, 900, "middle", IMPACT,
                   ' filter="url(#glow)"'))
    else:
        g.append(t(cx, box_y + box_h * 0.40, "BEAUTIFUL", 34 * k, "#fff", 900, "middle", IMPACT))
        g.append(t(cx, box_y + box_h * 0.72, "MISTAKE", 34 * k, RED, 900, "middle", IMPACT))
    g.append(person(sx, box_y + box_h * 0.22, 0.95 * k, cap=True))
    g.append(person(x + w * 0.16, box_y + box_h * 0.30, 0.75 * k, cap=False, shirt="#2E3B2E"))
    g.append(mic(sx - 40 * k, box_y + box_h * 0.70, k))
    # logo
    g.append(f'<circle cx="{x + w - 22 * k}" cy="{box_y + 20 * k}" r="{13 * k}" fill="none" stroke="#fff" '
             f'stroke-width="2"/>')
    # captions
    cy = box_y + box_h + 50 * k
    if look == "slab":
        g.append(t(cx, cy, "it was", 15 * k, "#fff", 600, "middle"))
        g.append(t(cx, cy + 44 * k, "GONE", 44 * k, YELLOW, 900, "middle", IMPACT))
    elif look == "glow":
        g.append(t(cx, cy, "it took just", 15 * k, "#fff", 600, "middle"))
        g.append(t(cx, cy + 46 * k, "one good day", 34 * k, YELLOW, 700, "middle", SERIF,
                   ' font-style="italic" filter="url(#glow)"'))
    else:
        g.append(t(cx, cy + 10 * k, f'Italy was a <tspan fill="{YELLOW}">beautiful</tspan>', 21 * k, "#fff", 700,
                   "middle", SANS, f' stroke="#000" stroke-width="{1.2 * k}" paint-order="stroke"'))
        g.append(t(cx, cy + 40 * k, "mistake", 21 * k, "#fff", 700, "middle", SANS,
                   f' stroke="#000" stroke-width="{1.2 * k}" paint-order="stroke"'))
    g.append(t(cx, y + h + 30, label, 18, "#fff", 700, "middle"))
    return "\n".join(g), h


GLOW = ('<defs><filter id="glow" x="-30%" y="-60%" width="160%" height="220%">'
        '<feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/>'
        '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>')


def reel_layout():
    w = 380
    body, h = phone(30, 30, w, "slab", "")
    x0, y0 = 30, 30
    box_y, box_h = y0 + h * 0.33, w * 9 / 16
    notes = [
        (y0 + h * 0.15, "Headline", ["Anton, key word in red. Stays all reel long."]),
        (box_y + 18, "Corner logo", ["The Hemu Mark stamp, white."]),
        (box_y + box_h * 0.45, "Big word BEHIND you", ["Lands on the frame the word is spoken.",
                                                      "You, the mic and the table are cut out and",
                                                      "drawn on top, so text is never in front of you."]),
        (box_y + box_h * 0.93, "The whole 16:9 wide shot", ["Boxed, nothing cropped. Brightness lift only."]),
        (box_y + box_h + 60, "Captions", ["English, 1-2 lines, never flicker.", "Their style comes from the reel's look."]),
        (y0 + h - 40, "Ending", ["Stamp slam with one sound."]),
    ]
    g = [GLOW, body]
    lx = 470
    for yy, head, lines in notes:
        g.append(f'<line x1="{x0 + w - 6}" y1="{yy}" x2="{lx - 12}" y2="{yy}" stroke="{RED}" '
                 f'stroke-dasharray="4 4" stroke-width="1.5"/><circle cx="{x0 + w - 6}" cy="{yy}" r="4" fill="{RED}"/>')
        g.append(t(lx, yy - 4, head, 17, "#fff", 700))
        for j, line in enumerate(lines):
            g.append(t(lx, yy + 16 + 19 * j, line, 13.5, MUTED))
    g.append(t(470, 52, "One reel: 1080 x 1920, 30 fps", 22, "#fff", 700))
    g.append(t(470, 76, "Same layout for all 6 reels; only the text look changes.", 14, MUTED))
    return svg(830, int(h + 60), "\n".join(g))


def looks():
    w, gap = 250, 34
    parts, h = [GLOW], 0
    labels = [("classic", "CLASSIC: reel04, reel07"), ("glow", "GLOW: reel02, reel06"), ("slab", "SLAB: reel01, reel08")]
    for i, (lk, lab) in enumerate(labels):
        b, h = phone(30 + i * (w + gap), 70, w, lk, lab)
        parts.append(b)
    parts.append(t(30, 46, 'Three text looks, two reels each  ("look" in reels_plan.json)', 22, "#fff", 700))
    descs = [["Noto Sans karaoke lines,", "yellow word highlight,", "stacked Anton words"],
             ["Small white words, the key", "word big in a glowing", "yellow italic serif"],
             ["One huge red slab word", "behind you; one bold word", "at a time, colours change"]]
    for i, d in enumerate(descs):
        for j, line in enumerate(d):
            parts.append(t(30 + i * (w + gap) + w / 2, 70 + h + 56 + j * 19, line, 13.5, MUTED, anchor="middle"))
    return svg(30 * 2 + 3 * w + 2 * gap, int(70 + h + 130), "\n".join(parts))


def story():
    W = 980
    g = [t(30, 46, "How a reel is cut", 24, "#fff", 700),
         t(30, 70, "Every cut ends on a finished sentence. The video, the voice and the graphics move together.",
           14, MUTED)]

    def bar(y, title, sub, blocks, total):
        out = [t(30, y - 16, title, 18, "#fff", 700), t(30 + 9 * len(title) + 40, y - 16, sub, 13.5, MUTED)]
        x, scale = 30, (W - 60) / total
        for sec, name, note, col in blocks:
            bw = sec * scale
            out.append(f'<rect x="{x + 1}" y="{y}" width="{bw - 2}" height="58" rx="10" fill="{col}" '
                       f'fill-opacity="0.22" stroke="{col}"/>')
            out.append(t(x + 10, y + 24, name, 14, "#fff", 700))
            out.append(t(x + 10, y + 44, note, 12, MUTED))
            x += bw
        ticks = list(range(0, int(total) + 1, 15))
        for s in ticks:
            tx = 30 + s * scale
            out.append(f'<line x1="{tx}" y1="{y + 64}" x2="{tx}" y2="{y + 70}" stroke="#555"/>')
            out.append(t(tx, y + 84, f"{s}s", 11, "#777", anchor="middle"))
        return "\n".join(out)

    g.append(bar(130, "60-second reel", "the normal reel", [
        (6, "Hook", "0-2 s", RED),
        (17, "Context", "who, where, why it matters", BLUE),
        (27, "The point", "the statement, complete", PURPLE),
        (10, "Payoff", "last line + stamp", GREEN)], 90))
    g.append(bar(290, "90-second story", "only when it is one whole story", [
        (10, "Cold open", "a later line", RED),
        (12, "Host asks", "Hemu's question", YELLOW),
        (38, "The story, in order", "suspense: each cut asks the next question", BLUE),
        (15, "Turn", "the twist", PURPLE),
        (13, "Payoff", "answer + stamp", GREEN)], 90))
    g.append(t(30, 440, "Example: reel02 “One person changed everything” (88.75 s)", 15, "#fff", 700))
    rows = ["0:00  cold open: \u201cAll the odds were against me ... I'll go back to India\u201d",
            "0:08  Hemu asks: \u201cWhat is this 'one good day' concept?\u201d",
            "0:14  the story in order: 3000 rejection mails (the counter lands on 3000)",
            "0:31  the turn: Pietro, the one guy who believed in him",
            "1:00  \u201cbest suited guy for this job\u201d, everything changed",
            "1:15  payoff: \u201call it took was one guy\u201d, then the stamp"]
    for i, r in enumerate(rows):
        g.append(t(30, 466 + i * 22, r, 13.5, MUTED, family="Menlo, monospace"))
    return svg(W, 620, "\n".join(g))


def pipeline():
    steps = [
        ("YOU", RED, "Put the episode in place", "Video in Master copy, SRT in SUbtitles_Srt, paths in config.yaml"),
        ("MAC", BLUE, "hrc ingest  /  hrc align  /  hrc realign", "Subtitles, camera changes, faces, the exact time of every word"),
        ("MAC", BLUE, "hrc select prepare", "Writes the transcript and the brief into cache/select/"),
        ("CLAUDE", PURPLE, "Pick the 6 strongest reels", "reels_plan.json: rank, look, cuts, headline, graphics"),
        ("YOU", RED, "hrc select check  +  hrc approve", "Read the list, approve. Nothing renders before this"),
        ("CLAUDE", PURPLE, "hrc translate prepare / build", "Claude writes the English, the Mac builds the caption files"),
        ("MAC", BLUE, "hrc assets", "HD photos (2400 px+) and a numbered sheet per reel"),
        ("YOU", RED, "hrc render --reel reel01,reel02", "Two reels at a time; you ask for the next two"),
        ("MAC", BLUE, "hrc qa", "Length, loudness, captions, faces -> qa_report.md"),
        ("YOU", RED, "Polish in DaVinci Resolve", "Import resolve_en/&lt;reel&gt;_en.fcpxml, change any layer, export"),
    ]
    W, top, step = 900, 84, 74
    g = [t(40, 52, "From podcast to 6 finished reels", 28, "#fff", 700)]
    for i, (name, col) in enumerate((("You", RED), ("Your Mac", BLUE), ("Claude", PURPLE))):
        x = 560 + i * 110
        g.append(f'<rect x="{x}" y="34" width="16" height="16" rx="4" fill="{col}"/>')
        g.append(t(x + 22, 47, name, 13, "#ccc", 700))
    for i, (who, col, head, sub) in enumerate(steps):
        y = top + i * step
        cy = y + 26
        if i < len(steps) - 1:
            g.append(f'<line x1="66" y1="{cy + 22}" x2="66" y2="{cy + step - 22}" stroke="#333" stroke-width="3"/>')
        g.append(f'<circle cx="66" cy="{cy}" r="22" fill="{col}"/>')
        g.append(t(66, cy + 7, str(i + 1), 18, "#fff", 700, "middle"))
        g.append(f'<rect x="104" y="{y}" width="760" height="54" rx="12" fill="{CARD}" stroke="{col}" '
                 f'stroke-opacity="0.5"/>')
        g.append(t(124, y + 23, head, 17, "#fff", 700, family="Menlo, monospace"))
        g.append(t(124, y + 44, sub, 14, MUTED))
        g.append(t(848, y + 23, {"MAC": "YOUR MAC"}.get(who, who), 12, col, 700, "end"))
    y = top + len(steps) * step + 6
    g.append(t(40, y + 14, "Steps 8 and 9 repeat in pairs, only when you ask: reel01+02, then reel04+08, then reel06+07.", 14, YELLOW, 700))
    return svg(W, y + 36, "\n".join(g))


if __name__ == "__main__":
    for name, fn in (("pipeline", pipeline), ("story", story)):
        (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print("wrote", OUT / f"{name}.svg")
