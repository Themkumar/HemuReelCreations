from pathlib import Path

import numpy as np

from hrc import align, config, karaoke, srt, transitions, translate
from hrc.tracking import OneEuro

ROOT = Path(__file__).resolve().parents[1]
SRT = """1
00:00:01,000 --> 00:00:03,500
మన జీవితంలో ప్రతి క్షణం ముఖ్యమైనది

2
00:00:04,000 --> 00:00:06,000
<i>విజయం ఒక్క రోజులో రాదు</i>
"""


def test_srt_parse_and_roundtrip():
    cues = srt.parse("﻿" + SRT.replace("\n", "\r\n"))
    assert len(cues) == 2
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 3.5
    assert cues[1]["text"] == "విజయం ఒక్క రోజులో రాదు"
    assert srt.parse(srt.dump(cues)) == cues


def test_slice_rebases():
    cues = srt.parse(SRT)
    part = srt.slice_cues(cues, 2.0, 5.0)
    assert part[0]["start"] == 0.0 and part[1]["start"] == 2.0


def test_proportional_words_cover_cue():
    cue = srt.parse(SRT)[0]
    ws = align.proportional(cue)
    assert [w["word"] for w in ws] == cue["text"].split()
    assert ws[0]["start"] == 1.0 and ws[-1]["end"] <= 3.5
    assert all(a["end"] <= b["start"] for a, b in zip(ws, ws[1:]))


def test_asr_mapping_monotonic():
    cue = srt.parse(SRT)[0]
    asr = [{"word": "మన", "start": 1.0, "end": 1.3, "probability": 0.9},
           {"word": "జీవితంలో", "start": 1.35, "end": 2.0, "probability": 0.8},
           {"word": "ప్రతిక్షణం", "start": 2.05, "end": 2.7, "probability": 0.7},
           {"word": "ముఖ్యమైనది", "start": 2.75, "end": 3.4, "probability": 0.9}]
    ws = align.map_asr_to_cue(cue, asr)
    assert len(ws) == 5
    assert all(a["start"] <= b["start"] for a, b in zip(ws, ws[1:]))
    assert 0.3 < ws[0]["confidence"] <= 1


def test_chunking_limits():
    words = [{"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.25, "cue": 1} for i in range(20)]
    chunks = translate.chunk_words(words, 3, 6)
    assert sum(len(c) for c in chunks) == 20
    assert all(len(c) <= 7 for c in chunks)


def test_ass_has_one_event_per_word():
    cfg = config.load(ROOT / "config.yaml")
    ws = translate.timed_words("మన జీవితంలో ప్రతి క్షణం", 0, 2)
    ass = karaoke.build_ass({"lang": "te", "chunks": [{"start": 0, "end": 2, "words": ws}]}, cfg)
    assert ass.count("Dialogue:") == 4
    assert "Noto Sans Telugu" in ass


def test_transitions_never_repeat():
    names = transitions.choose(40, "energetic", ["zoom_punch", "whip_pan", "glitch"], seed=3)
    assert all(a != b for a, b in zip(names, names[1:]))
    frame = np.full((96, 54, 3), 100, np.uint8)
    for n in transitions.SFX:
        assert transitions.apply(n, frame, 0.2, frame).shape == frame.shape


def test_one_euro_smooths():
    f = OneEuro(30, 0.6, 0.01)
    rng = np.random.default_rng(0)
    out = [f(100 + rng.normal(0, 5)) for _ in range(120)]
    assert np.std(out[30:]) < 3


def test_fix_rolling_overlaps():
    cues = srt.parse("1\n00:00:00,320 --> 00:00:04,400\nఒకటి\n\n2\n00:00:02,399 --> 00:00:06,960\nరెండు\n")
    assert srt.fix_overlaps(cues) == 1
    assert cues[0]["end"] == 2.399


def test_karaoke_events_are_contiguous_and_two_line():
    cfg = config.load(ROOT / "config.yaml")
    ws = translate.timed_words("మన జీవితంలో ప్రతి క్షణం చాలా ముఖ్యమైనది తెలుసా", 0, 3)
    pieces = translate.split_lines({"start": 0, "end": 3, "words": ws}, 16, 2)
    assert all(max(w["line"] for w in p["words"]) <= 1 for p in pieces)
    ass = karaoke.build_ass({"lang": "te", "chunks": pieces}, cfg)
    ev = [l.split(",")[1:3] for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert all(a[1] == b[0] for a, b in zip(ev, ev[1:]))  # no gaps -> no flicker
    assert "\\N" in ass and "\\an8" in ass and "\\t(" not in ass  # stacked lines, no width-changing pop


def test_reel_layout_headline_and_captions_below_video():
    cfg = config.load(ROOT / "config.yaml")
    ws = translate.timed_words("three thousand rejections", 0, 2)
    box = (0, 480, 1080, 772)
    ass = karaoke.build_ass({"lang": "en", "chunks": [{"start": 0, "end": 2, "words": ws}]}, cfg, 1080, 1920,
                            box, "*3000 rejections* in Italy", 10.0)
    head = [l for l in ass.splitlines() if ",Headline," in l and l.startswith("Dialogue")]
    assert len(head) == 1 and "3000" in head[0] and "&H002B25E8" in head[0]  # accent red on the key words
    assert "*" not in head[0]
    ys = [int(l.split("\\pos(")[1].split(",")[1].split(")")[0]) for l in ass.splitlines() if ",Karaoke," in l]
    assert ys and all(y > box[1] + box[3] for y in ys)  # captions sit under the video box
