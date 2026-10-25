# HemuReelCreations (HRC)

Turns a finished Telugu podcast episode of **The Hemu Mark Reality Check** into 9:16 reels
(1080x1920, 30 fps, 30–60 s), each exported with karaoke subtitles in Telugu, Hindi, English,
Tamil and Kannada. Audio always stays Telugu.

## Setup (once)

```bash
cd ~/HemuReelCreations
bash setup_mac.sh          # ffmpeg, venv, Python packages, then `hrc doctor`
source .venv/bin/activate
```

Set the episode in `config.yaml` → `input.video` and `input.srt`.
Put royalty-free music in `assets/music/<mood>/` (moods: inspirational, tense, emotional, funny, calm, energetic).
Your own SFX go in `assets/sfx/<whoosh|hit|riser|pop|glitch|impact|swish>/`; otherwise HRC makes its own.

## First test (3-minute sample)

```bash
hrc sample --start 00:36:00 --minutes 3          # the "3000 rejections" part of HemuXSrinadh_Part1
cd sample
cp ../sample_plan/reels_plan.json .              # sample reel picked + translated by Claude
hrc ingest && hrc align
hrc select check && hrc approve
hrc translate prepare && hrc translate build
hrc assets                                       # downloads the images Claude picked (Wikimedia etc.)
hrc preview --langs te,en                       # output/sample01/*_preview.mp4
hrc render && hrc qa                            # full quality, 5 languages, reel layout (landscape video in 9:16)
```

## Commands, in order

| Step | Command | What it does |
|---|---|---|
| check | `hrc doctor` | ffmpeg, libass, VideoToolbox, MPS, fonts, inputs |
| sample | `hrc sample --start 00:20:00 --minutes 3` | makes `sample/` with a 3-minute cut, then use `hrc --config sample/config.yaml …` |
| A | `hrc ingest` | SRT → `cache/cues.json`, audio, camera cuts, face positions |
| B | `hrc align` | word timings → `cache/words.json`; doubtful spots → `cache/align_review.json` |
| C | `hrc select prepare` | transcript chunks + brief in `cache/select/`; Claude writes `reels_plan.json` |
| C | `hrc select check` then `hrc approve` | snaps cuts to word gaps, checks lengths; **nothing renders until you approve** |
| D | `hrc translate prepare` → (Claude fills translations) → `hrc translate build` | `subs/<reel>_<lang>.json` |
| E | `hrc assets` | images for visual moments → `assets/manifest.json` with source + licence |
| preview | `hrc preview --reel reel01` | 540x960 draft with Telugu subs in a few minutes |
| F–H | `hrc render` | matte, GFX, transitions, SFX, music, 5 languages + clean + FCPXML |
| QA | `hrc qa` | `qa_report.md` + frame grabs per language |

Add `--reel reel01,reel03` to work on some reels, `--force` to ignore the cache.
Every stage writes JSON you can edit; re-running skips work whose inputs didn't change.

## Output

`output/<reel>/<reel>_<lang>.mp4` (te, hi, en, ta, kn), `<reel>_clean.mp4` (no subtitles),
`<reel>_mix.wav`, and `<reel>.fcpxml` (import in DaVinci Resolve: File → Import → Timeline).

## How the look is built

1. **Reframe**: face found 5×/s, smoothed with a One-Euro filter, crop resets on each camera switch; punch-in zoom on emphasis words.
2. **Matte**: Robust Video Matting on MPS (MediaPipe fallback) with temporal smoothing.
3. **GFX behind you**: each visual moment appears on the frame its word is spoken, animates in (pop/slide/glitch/zoom) with a matching SFX, holds, animates out. Anchored to your tracked head: beside the head with parallax, big words behind the head with a glow, counters, arrows, map pins, B-roll on top. One major element at a time.
4. **Transitions** at each cut, chosen by mood, never the same twice in a row, each with its own SFX.
5. **Audio**: speech cleanup, music ducked under speech (sidechain), SFX, loudness −14 LUFS.
6. **Subtitles**: ASS rendered by libass + HarfBuzz with Noto Sans fonts. Word-by-word highlight with a pop, emphasis words in the accent colour, kept out of the bottom 22% and right edge.

## What it can't do well (hand-polish in Resolve)

- Two people in one wide shot: the crop follows the biggest/steadiest face, not necessarily the speaker.
- Translated karaoke timing is spread across each chunk, so it is close but not word-exact.
- Auto GFX are clean templates, not designer motion graphics. Polish your best 2–3 reels by hand.
- Hair edges on fast movement may shimmer; check the matte flicker score in `qa_report.md`.
- No speed ramps or true match cuts yet. Planar tracking for screen replacement is not implemented.
- Web images: free sources only by default; anything flagged ⚠ (logos, news, film stills, people) needs your call.

## Reel layout (v0.3)

The default format is `reel`. It is a 9:16 frame:

- A **headline** at the top (`headline:` in the reel plan, one per language). Words in `*stars*` turn red.
- The **landscape video** as a wide box in the middle. Tracking is steady, and the speaker is cut out.
- **Karaoke captions** in the black area under the video, so they never cover a face.

At key moments, a **photo replaces the wall behind the speaker**. Big stacked words appear beside them: white, with the `*key word*` large and red. Photos (JPG) become backgrounds by default. Icons and logos (PNG/SVG) float as pictures. You can force either with `style: backdrop` or `style: sprite` on a visual moment.

Other formats are still available: `hrc render --formats landscape` gives a plain 16:9 file, and `--formats portrait` gives the face-tracked 9:16 crop.
