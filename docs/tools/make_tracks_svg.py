"""Draws docs/img/resolve_tracks.svg (the Resolve track map in the README)."""
from pathlib import Path

ROWH, TOP, X0, X1 = 46, 78, 380, 870
G = [(390, 60), (540, 70), (700, 60), (800, 50)]          # graphic times
P = [(540, 70)]                                           # the photo moment
rows = [
    ("V11+", "Graphic words as text (off)", "turn on: words, font, colour, any animation", "#FFD400", G, 0.3),
    ("V10", "Karaoke captions", "captions_te.mov (transparent) + .srt", "#FFD400", [(X0, X1 - X0)], 0.85),
    ("V9", "Headline as text (off)", "turn on to edit words, font, colour", "#E8252B", [(X0, X1 - X0)], 0.35),
    ("V8", "Headline picture", "headline_te.png", "#E8252B", [(X0, X1 - X0)], 0.85),
    ("V7", "Corner logo", "brand/logo.png", "#FFFFFF", [(X0, X1 - X0)], 0.85),
    ("V6", "Graphic parts in front", "gNN_front.mov (counters)", "#8250DF", [G[0], G[3]], 0.85),
    ("V5", "Cut-out: you + mic, cups...", "gNN_person.mov (keeps graphics behind)", "#2EA043", G, 0.85),
    ("V4", "Graphics", "gNN.mov, one per graphic", "#8250DF", G, 0.85),
    ("V3", "Photo, untouched HD still (off)", "photos/gNN_photo_hd.png (2x size)", "#D29922", P, 0.35),
    ("V2", "Photo background", "gNN_photo.mov (look, push-in, fades)", "#D29922", P, 0.85),
    ("V1", "Video + ending stamp", "01_video.mp4 cut at every join, outro.mp4", "#1F6FEB",
     [(380, 140), (524, 116), (644, 156), (804, 66)], 0.85),
    None,
    ("A1", "Speech", "audio/speech.wav", "#2EA043", [(X0, X1 - X0)], 0.85),
    ("A2", "Music", "audio/music.wav", "#1F6FEB", [(X0, X1 - X0)], 0.85),
    ("A3+", "Sound effects", "audio/sfx/NN_kind.wav, one clip each", "#E8252B",
     [(390, 70), (519, 44), (622, 63), (774, 30)], 0.85),
]
H = TOP + ROWH * len(rows) + 20
out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{H}" viewBox="0 0 900 {H}" '
       'font-family="Helvetica, Arial, sans-serif">',
       f'<rect width="900" height="{H}" rx="18" fill="#0F1115"/>',
       '<text x="30" y="48" fill="#fff" font-size="26" font-weight="700">Your reel in DaVinci Resolve: one track per element</text>']
y = TOP
for r in rows:
    if r is None:
        out.append(f'<line x1="20" y1="{y + 14}" x2="880" y2="{y + 14}" stroke="#444" stroke-dasharray="6 6"/>')
        y += ROWH * 0.6
        continue
    tag, name, sub, col, bars, op = r
    out.append(f'<text x="30" y="{y + 21}" fill="#889" font-size="15" font-weight="700" font-family="Menlo, monospace">{tag}</text>')
    out.append(f'<text x="92" y="{y + 15}" fill="#fff" font-size="14" font-weight="700">{name}</text>'
               f'<text x="92" y="{y + 31}" fill="#889" font-size="11">{sub}</text>')
    for x, w in bars:
        dash = ' stroke="#fff" stroke-opacity="0.5" stroke-dasharray="4 3"' if op < 0.5 else ""
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="32" rx="6" fill="{col}" fill-opacity="{op}"{dash}/>')
    y += ROWH
out.append("</svg>")
s = "\n".join(out).replace(f'height="{H}"', f'height="{int(y + 20)}"').replace(f'0 0 900 {H}', f'0 0 900 {int(y + 20)}')
Path(__file__).resolve().parents[1].joinpath("img", "resolve_tracks.svg").write_text(s)
