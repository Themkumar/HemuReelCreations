"""Draws the README pictures in docs/img/: pipeline.svg, reel_layout.svg, reel_mix.svg, story_arc.svg.
reel_mix.svg reads reels_plan.json, so run it again after a new plan:  python docs/tools/make_docs_svg.py"""
import json
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "docs" / "img"
BG, FG, MUTED, RED, YEL = "#0F1115", "#FFFFFF", "#AAB0BB", "#E8252B", "#FFD400"
LOOK = {"classic": ("#B8C0CC", "Classic", "yellow karaoke captions, stacked Anton words"),
        "glow": ("#FFE45C", "Glow", "small white words + key word in glowing yellow serif"),
        "slab": (RED, "Slab", "one huge red slab word, one-word colour captions")}
WHO = {"YOU": "#1F6FEB", "YOUR MAC": "#2EA043", "CLAUDE": "#D97757", "CLAUDE+YOUR MAC": "#8250DF"}


def svg(w, h, body, title=None):
    t = f'<text x="40" y="52" fill="{FG}" font-size="28" font-weight="700">{title}</text>' if title else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="Helvetica, Arial, sans-serif"><rect width="{w}" height="{h}" rx="18" fill="{BG}"/>'
            f'{t}{"".join(body)}</svg>')


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pipeline():
    steps = [
        ("Put the episode in place", "video + SRT paths in config.yaml", "YOU"),
        ("hrc ingest · align · realign", "subtitles, camera changes, faces, exact word times", "YOUR MAC"),
        ("hrc reactions", "listener reaction shots", "YOUR MAC"),
        ("hrc select prepare", "transcript + brief for Claude", "YOUR MAC"),
        ("Pick the 6 strongest reels", "rank, ~60 s (story reels up to 90 s), a look each: 2 classic · 2 glow · 2 slab", "CLAUDE"),
        ("hrc select check + hrc approve", "you read the list and approve. Nothing renders before this", "YOU"),
        ("hrc translate prepare → build", "Claude writes the English, the Mac times every word", "CLAUDE+YOUR MAC"),
        ("hrc assets", "HD photos for the picture moments", "YOUR MAC"),
        ("hrc render", "English reel in its look + layered Resolve pack", "YOUR MAC"),
        ("hrc qa", "length, loudness, captions, faces → qa_report.md", "YOUR MAC"),
        ("Polish in Resolve", "import the .fcpxml, restyle any layer to your taste, export", "YOU"),
    ]
    body, y = [], 84
    for i, (k, v) in enumerate([("You", "YOU"), ("Your Mac", "YOUR MAC"), ("Claude", "CLAUDE")]):
        x = 520 + i * 125
        body.append(f'<rect x="{x}" y="34" width="14" height="14" rx="3" fill="{WHO[v]}"/>'
                    f'<text x="{x + 20}" y="46" fill="{MUTED}" font-size="14">{k}</text>')
    for i, (head, sub, who) in enumerate(steps):
        col = WHO[who]
        body.append(f'<rect x="40" y="{y}" width="820" height="58" rx="12" fill="#1A1D24" stroke="{col}" stroke-width="2"/>'
                    f'<circle cx="76" cy="{y + 29}" r="17" fill="{col}"/>'
                    f'<text x="76" y="{y + 35}" fill="#fff" font-size="16" font-weight="700" text-anchor="middle">{i + 1}</text>'
                    f'<text x="108" y="{y + 25}" fill="#fff" font-size="17" font-weight="700" font-family="Menlo, monospace">{esc(head)}</text>'
                    f'<text x="108" y="{y + 45}" fill="{MUTED}" font-size="13">{esc(sub)}</text>'
                    f'<text x="846" y="{y + 34}" fill="{col}" font-size="12" font-weight="700" text-anchor="end">{who}</text>')
        if i < len(steps) - 1:
            body.append(f'<line x1="76" y1="{y + 58}" x2="76" y2="{y + 72}" stroke="#555" stroke-width="2"/>')
        y += 72
    return svg(900, y + 16, body, "From podcast to finished reels")


def reel_layout():
    """The v0.9 9:16 frame: logo and headline above the picture, graphics on the picture, captions under it."""
    W, H, ox, oy = 360, 640, 30, 80
    top, vh = oy + H * 0.33, W / 1.778
    b = [f'<rect x="{ox}" y="{oy}" width="{W}" height="{H}" rx="14" fill="#000" stroke="#333"/>',
         f'<circle cx="{ox + W - 40}" cy="{oy + 40}" r="20" fill="none" stroke="#fff" stroke-width="3"/>'
         f'<text x="{ox + W - 40}" y="{oy + 44}" fill="#fff" font-size="9" font-weight="700" text-anchor="middle">HEMU</text>',
         f'<text x="{ox + W / 2}" y="{top - 62}" fill="{YEL}" font-size="26" font-weight="900" text-anchor="middle" '
         f'font-family="Impact, Anton, sans-serif">3000 REJECTIONS</text>',
         f'<text x="{ox + W / 2}" y="{top - 28}" fill="#fff" font-size="26" font-weight="900" text-anchor="middle" '
         f'font-family="Impact, Anton, sans-serif">IN ITALY</text>',
         f'<rect x="{ox}" y="{top}" width="{W}" height="{vh}" fill="#3B4252"/>',
         f'<text x="{ox + W / 2}" y="{top + vh * 0.78}" fill="{RED}" font-size="56" font-weight="900" text-anchor="middle" '
         f'font-family="Impact, Anton, sans-serif">3,000</text>',
         f'<text x="{ox + W / 2}" y="{top + vh * 0.93}" fill="#fff" font-size="12" font-weight="700" text-anchor="middle">REJECTIONS</text>',
         f'<circle cx="{ox + W * 0.62}" cy="{top + vh * 0.38}" r="26" fill="#D9A47A"/>'
         f'<rect x="{ox + W * 0.62 - 48}" y="{top + vh * 0.55}" width="96" height="{vh * 0.45}" rx="22" fill="#25324A"/>',  # the person, drawn over the counter (it sits behind)
         f'<text x="{ox + W / 2}" y="{top + vh + 52}" fill="#fff" font-size="13" text-anchor="middle">in my</text>',
         f'<text x="{ox + W / 2}" y="{top + vh + 88}" fill="{YEL}" font-size="32" font-style="italic" font-weight="700" '
         f'text-anchor="middle" font-family="Georgia, serif">résumé</text>']
    notes = [(oy + 40, "Corner logo", "your stamp, outside the picture"),
             (top - 45, "Headline", "2 lines, key words coloured, outside the picture"),
             (top + vh * 0.38, "Video box", "the full 16:9 podcast, steady framing, punch-ins"),
             (top + vh * 0.75, "Graphics behind you", "words, big counters, photos at shoulder height;"),
             (top + vh * 0.75 + 17, "", "the cut-out keeps you, the mic and the table in front"),
             (top + vh + 70, "Captions", "in the reel's look (classic, glow or slab)"),
             (oy + H - 40, "Ending", "stamp slams into the centre with a sound")]
    for y, t, s in notes:
        if t:
            b.append(f'<line x1="{ox + W + 6}" y1="{y}" x2="{ox + W + 40}" y2="{y}" stroke="{RED}" stroke-width="2"/>'
                     f'<circle cx="{ox + W + 6}" cy="{y}" r="4" fill="{RED}"/>'
                     f'<text x="{ox + W + 50}" y="{y - 2}" fill="#fff" font-size="16" font-weight="700">{t}</text>')
        b.append(f'<text x="{ox + W + 50}" y="{y + 16}" fill="{MUTED}" font-size="13">{s}</text>')
    return svg(820, oy + H + 30, b, "One reel, 1080 x 1920")


def reel_mix():
    plan = json.loads((ROOT / "reels_plan.json").read_text(encoding="utf-8"))
    reels = sorted([r for r in plan["reels"] if r.get("enabled", True)], key=lambda r: r.get("rank", 99))
    X0, X1, top, rowh = 300, 860, 128, 46
    sx = (X1 - X0) / 95
    b = []
    for i, (name, (col, label, sub)) in enumerate(LOOK.items()):
        b.append(f'<rect x="{40 + i * 280}" y="72" width="14" height="14" rx="3" fill="{col}"/>'
                 f'<text x="{60 + i * 280}" y="84" fill="{FG}" font-size="14" font-weight="700">{label}</text>')
    for sec, lab, col in ((60, "ideal ~60 s", "#2EA043"), (90, "max 90 s: complete stories", RED)):
        x = X0 + sec * sx
        b.append(f'<line x1="{x}" y1="{top - 14}" x2="{x}" y2="{top + rowh * len(reels)}" stroke="{col}" '
                 f'stroke-width="2" stroke-dasharray="6 5"/>'
                 f'<text x="{x}" y="{top - (34 if sec == 90 else 20)}" fill="{col}" font-size="12" text-anchor="{'end' if sec == 90 else 'middle'}">{lab}</text>')
    for i, r in enumerate(reels):
        y = top + i * rowh
        dur = float(r.get("duration", 0))  # the reel's spoken content; the brand ending adds about 2 s
        col = LOOK.get(r.get("look"), ("#888", "?", ""))[0]
        b.append(f'<text x="40" y="{y + 20}" fill="{MUTED}" font-size="13" font-weight="700">#{r.get("rank", "?")} {r["id"]}</text>'
                 f'<text x="40" y="{y + 36}" fill="{FG}" font-size="13">{esc(r.get("title", ""))[:32]}</text>'
                 f'<rect x="{X0}" y="{y + 6}" width="{dur * sx:.1f}" height="28" rx="6" fill="{col}"/>'
                 f'<text x="{X0 + dur * sx - 8:.1f}" y="{y + 25}" fill="#111" font-size="13" font-weight="700" '
                 f'text-anchor="end">{dur:.0f} s</text>')
    b.append(f'<text x="40" y="{top + rowh * len(reels) + 22}" fill="{MUTED}" font-size="12">Length of the spoken content; '
             f'the brand ending adds about 2 s.</text>')
    return svg(900, top + rowh * len(reels) + 40, b, "The 6 reels of an episode, strongest first")


def story_arc():
    beats = [("Cold open", "the most gripping line first", "\"Italy was a mistake...\"", RED),
             ("Host asks", "a question the viewer wants answered", "\"How did you get there?\"", YEL),
             ("Answer → question", "every answer opens the next question", "\"Germany said no...\"", "#8BB8FF"),
             ("Twist", "the turn nobody expects", "\"...a beautiful mistake\"", "#C792EA"),
             ("Payoff", "the lesson, then the stamp", "the story closes", "#2EA043")]
    b, x, w = [], 51, 150
    pts = []
    for i, (t, s, ex, col) in enumerate(beats):
        cx = x + i * (w + 12)
        lvl = [0.35, 0.5, 0.65, 0.9, 0.55][i]
        pts.append((cx + w / 2, 300 - lvl * 160))
        lines = textwrap.wrap(s, 24)[:2]
        b.append(f'<rect x="{cx}" y="320" width="{w}" height="110" rx="12" fill="#1A1D24" stroke="{col}" stroke-width="2"/>'
                 f'<text x="{cx + 10}" y="346" fill="{col}" font-size="13.5" font-weight="700">{t}</text>'
                 + "".join(f'<text x="{cx + 10}" y="{368 + k * 16}" fill="{MUTED}" font-size="11.5">{esc(l)}</text>'
                           for k, l in enumerate(lines))
                 + f'<text x="{cx + 10}" y="414" fill="#fff" font-size="11.5" font-style="italic">{esc(ex)}</text>')
    d = "M" + " L".join(f"{px:.0f},{py:.0f}" for px, py in pts)
    b.append(f'<path d="{d}" fill="none" stroke="{YEL}" stroke-width="4" stroke-linejoin="round"/>')
    for px, py in pts:
        b.append(f'<circle cx="{px:.0f}" cy="{py:.0f}" r="7" fill="{YEL}"/>')
    b.append(f'<text x="40" y="100" fill="{MUTED}" font-size="14">curiosity</text>'
             f'<text x="51" y="458" fill="{MUTED}" font-size="13">A 90 s reel is a story cut like a trailer: the host (no hat) '
             f'keeps asking, the viewer keeps watching.</text>'
             f'<text x="51" y="476" fill="{MUTED}" font-size="13">A 60 s reel keeps it simple: hook → context → payoff.</text>')
    return svg(900, 496, b, "How a 90-second story reel is cut")


if __name__ == "__main__":
    for name, fn in (("reel_layout", reel_layout), ("reel_mix", reel_mix)):  # pipeline + story: make_diagrams.py
        (IMG / f"{name}.svg").write_text(fn(), encoding="utf-8")
        print("wrote", name)
