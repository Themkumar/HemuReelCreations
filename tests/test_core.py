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


def test_clean_cuts_remove_short_camera_flashes():
    from hrc.render import frame_map, switch_frames
    import numpy as np
    # 5-frame sliver of the other camera at the start, a 3-frame flash mid-way, a 4-frame tail
    fm = frame_map(60, [5, 30, 33, 56], 12)
    assert fm[:6] == [5] * 6            # opening sliver shows the new shot's first frame
    assert fm[30:33] == [29, 29, 29]    # mid flash holds the frame before it
    assert fm[56:] == [55] * 4          # tail sliver holds
    assert fm == sorted(fm)             # never goes backwards (streamable)
    d = np.full(40, 2.0, np.float32); d[21] = 40.0; d[20] = 15.0
    assert switch_frames(d, [19]) == [21]  # detected cut snapped onto the real switch frame


def test_captions_never_blink_between_close_chunks():
    from hrc.karaoke import build_ass
    cfg = config.load(ROOT / "config.yaml")
    subs = {"lang": "en", "chunks": [
        {"start": 0.0, "end": 1.0, "words": [{"w": "one", "start": 0.0, "end": 0.5}, {"w": "two", "start": 0.5, "end": 1.0}]},
        {"start": 1.3, "end": 2.0, "words": [{"w": "three", "start": 1.3, "end": 2.0}]},
        {"start": 3.5, "end": 4.0, "words": [{"w": "four", "start": 3.5, "end": 4.0}]}]}
    ass = build_ass(subs, cfg, 1080, 1920)
    ev = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    times = [(l.split(",")[1], l.split(",")[2]) for l in ev]
    assert times[1][1] == times[2][0] == "0:00:01.30"   # chunk 1 hands straight over to chunk 2
    assert "\\fad" not in ev[2]                          # no fade-in after a tiny pause
    assert "\\fad" in ev[3]                              # real pause: soft fade-in


def test_sfx_only_on_key_moments():
    from hrc.render import choose_sfx
    cfg = config.load(ROOT / "config.yaml")
    reel = {"visual_moments": [{}, {}, {}, {"sfx": True}, {"sfx": False}]}
    ev = lambda i, ty, t, **k: {"id": f"g{i:02d}", "type": ty, "t_in": t, "sfx_in": "pop",
                                "sfx": [{"t": t, "kind": "whoosh"}], **k}
    events = [ev(0, "counter", 1.0), ev(1, "text", 3.0), ev(2, "image", 4.0, style="backdrop"),
              ev(3, "text", 12.0), ev(4, "counter", 20.0)]
    out = choose_sfx(cfg, reel, events)
    ts = sorted(x["t"] for x in out)
    assert 3.0 not in ts and 20.0 not in ts and 12.0 in ts
    assert all(b - a >= 5.0 for a, b in zip(ts, ts[1:]))


def test_resolve_unmix_recovers_transparency():
    import numpy as np
    from hrc.resolve import unmix
    col, a = np.array([200.0, 50.0, 10.0]), 0.4
    blk = np.zeros((4, 4, 3)) * (1 - a) + col * a
    wht = np.full((4, 4, 3), 255.0) * (1 - a) + col * a
    out = unmix(blk, wht)
    assert abs(int(out[0, 0, 3]) - 102) <= 1
    assert np.allclose(out[0, 0, :3], col, atol=2)


def test_snap_keeps_cut_already_between_words():
    from hrc.plan import snap_to_words
    words = [{"start": 0.0, "end": 1.0}, {"start": 5.0, "end": 5.4}, {"start": 5.45, "end": 6.0}]
    # 4.9 sits in the long pause before word 2: keep it, don't jump into word 2
    assert snap_to_words(4.9, words, "start") == 4.9
    # inside a word: move to the nearest gap
    assert snap_to_words(5.3, words, "end") == 5.425


def test_resolve_escape_quotes():
    from hrc.resolve import escape
    assert escape('"BEST" <A&B>') == "&quot;BEST&quot; &lt;A&amp;B&gt;"


def test_rank_keeps_big_commercial_photos():
    from hrc.assets import rank
    hits = [{"url": "a", "width": 1200, "height": 800, "license": "CC BY 4.0"},
            {"url": "b", "width": 4000, "height": 2600, "license": "CC BY-NC 2.0"},
            {"url": "c", "width": 3000, "height": 2000, "license": "CC BY-SA 4.0"},
            {"url": "d", "width": 5000, "height": 3300, "license": "Public domain"}]
    got = [h["url"] for h in rank(hits, 2400, backdrop=True)]
    assert got[0] in ("c", "d") and "a" not in got and "b" not in got


def test_select_objects_keeps_mic_drops_wall_and_person():
    import numpy as np
    from hrc.matte import select_objects
    person = np.zeros((100, 100), np.float32); person[30:100, 10:40] = 1
    labels = np.zeros((50, 50), np.uint8)
    labels[15:50, 5:20] = 1      # the person again
    labels[25:35, 30:34] = 2     # a mic
    labels[0:20, :] = 3          # a wall touching the top (too big)
    assert select_objects(person, labels, None) == [2]


def test_guided_filter_keeps_flat_alpha():
    import numpy as np
    from hrc.matte import guided_filter
    I = np.full((40, 40), 0.5, np.float32); p = np.zeros((40, 40), np.float32); p[:, 20:] = 1
    out = guided_filter(I, p, r=3)
    assert out[:, :15].max() < 0.05 and out[:, 25:].min() > 0.95


def test_depth_objects_keeps_near_things_below_eye_line():
    import numpy as np
    from hrc.matte import depth_objects

    H, W = 120, 160
    person = np.zeros((H, W), np.float32); person[20:100, 60:100] = 1
    depth = np.full((H, W), 0.1, np.float32)          # far wall
    depth[20:100, 60:100] = 0.5                         # the speaker
    depth[50:90, 30:45] = 0.7                           # a mic, nearer than the speaker
    depth[5:15, 120:150] = 0.9                          # a lamp up high (above the eye line)
    m = depth_objects(person, depth, None, tol=0.06, region_top=0.15)
    assert m[70, 37] > 0.9                              # mic kept
    assert m[10, 130] == 0                              # lamp above the eye line dropped
    assert m[5, 5] == 0 and m[60, 80] == 0              # wall dropped, person itself not doubled
    assert not depth_objects(np.zeros((H, W), np.float32), depth).any()   # nobody in frame: nothing
