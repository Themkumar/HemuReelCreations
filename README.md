# HemuReelCreations (HRC)

Turns a finished Telugu episode of **The Hemu Mark Reality Check** into ten 9:16 reels (1080x1920,
30 fps, at most 60 s each). Every reel is exported with karaoke captions in **Telugu, Hindi,
English, Tamil and Kannada**, plus a **layered DaVinci Resolve project** in which every graphic,
caption, logo and sound sits on its own track. The audio always stays Telugu.

![What a reel looks like](docs/img/reel_layout.svg)

---

## The whole process on one page

![From podcast to finished reels](docs/img/pipeline.svg)

| # | Who | What you type (in Terminal) | What happens | How long |
|---|---|---|---|---|
| 0 | You | `cd ~/HemuReelCreations && source .venv/bin/activate` | Opens the HRC workspace. **Do this every time you open a new Terminal.** | 1 s |
| 1 | You | edit `config.yaml` → `input.video`, `input.srt` | Tells HRC which episode to use | 1 min |
| 2 | Mac | `hrc ingest` | Reads the subtitles, finds camera changes and faces | 5–10 min |
| 3 | Mac | `hrc align` | Finds the exact time of every spoken word | ~10 min |
| 4 | Mac | `hrc select prepare` | Writes the transcript brief into `cache/select/` | 10 s |
| 5 | Claude | (in the project chat) | Claude reads the brief and writes `reels_plan.json`: cuts, headlines, graphics, which moments get a sound | — |
| 6 | You | `hrc select check` then `hrc approve` | Checks lengths and snaps cuts between words. **Nothing renders before you approve.** | 10 s |
| 7 | Mac + Claude | `hrc translate prepare` → Claude fills translations → `hrc translate build` | Subtitle files for all 5 languages in `subs/` | — |
| 8 | Mac | `hrc assets` | Downloads the photos the graphics use (free sources, licence noted) | 1–2 min |
| 9 | Mac | `hrc render` | Renders every reel in 5 languages + the Resolve pack | ~3–5 min per reel |
| 10 | Mac | `hrc qa` | Checks length, loudness, captions, faces → `output/qa_report.md` | 1 min |
| 11 | You | DaVinci Resolve | Import the layered timeline, polish, export (see below) | your call |

Work on some reels only: add `--reel reel02,reel05`. Redo a step from scratch: add `--force`.
Every step writes plain files you can open and edit; running a step again skips work whose inputs
didn't change.

### First-time setup (once per Mac)

```bash
cd ~/HemuReelCreations
bash setup_mac.sh            # ffmpeg, Python packages, fonts check, then `hrc doctor`
source .venv/bin/activate
hrc doctor                   # every line should say OK
```

Music: put royalty-free tracks in `assets/music/<mood>/` (moods: inspirational, tense, emotional,
funny, calm, energetic). Without music the reels render with speech and sound effects only.
Your own sound effects go in `assets/sfx/<kind>/` and are used instead of HRC's built-in ones.

---

## What you get

```
output/
  qa_report.md                       checks for every file, with frame grabs
  reel02/
    reel02_te.mp4  reel02_hi.mp4  reel02_en.mp4  reel02_ta.mp4  reel02_kn.mp4   ready to post
    reel02_clean.mp4                 no captions
    reel02_mix.wav                   the finished sound
    resolve_te/                      the layered Resolve project (see below)
```

---

## Polishing a reel in DaVinci Resolve

![One track per element](docs/img/resolve_tracks.svg)

**Import**

1. Open DaVinci Resolve and create or open a project. Set the project to 1080x1920, 30 fps:
   *File → Project Settings → Master Settings → Timeline resolution: 1080 x 1920 (custom), 30 fps*.
2. *File → Import → Timeline…* and pick `output/<reel>/resolve_te/<reel>_te.fcpxml`.
3. When Resolve asks, leave **"Automatically import source clips into media pool"** ticked.
   All media is in the `media/` folder next to the .fcpxml, so nothing goes offline.
4. Open `EDIT_SHEET.md` in the same folder. It lists every track and clip with its time, text,
   animation and sound.

**What is on each track**

| Track | Contents | Things you can do |
|---|---|---|
| V1 | Podcast video, cut at every join; the ending stamp after it | Trim, reorder, put a Resolve transition on any edit point (markers name the suggested one), colour grade |
| V2 | Each graphic alone, with transparency (`gfx/gNN.mov`) | Move, scale, retime, fade, change opacity, delete or replace it |
| V3 | You, cut out from the background, only while a graphic is behind you | Delete one to bring that graphic in front of you |
| V4 | Graphic parts that sit in front of you (counters) | Same as V2 |
| V5 | Corner logo (the white stamp) | Move, resize, change opacity, turn off |
| V6 | Headline above the video, as a picture | Turn off (select it, press **D**) when you use V7 instead |
| V7 | Headline as Resolve text, **off** by default | Select it, press **D** to turn it on, then change words, font, size and colour in the Inspector |
| V8 | Karaoke captions with transparency | Move or turn off. For captions you style yourself: *File → Import → Subtitle…* and pick `media/captions_te.srt` |
| A1 | Speech (cleaned) | Level, EQ, noise reduction |
| A2 | Music, already ducked under the speech | Level, swap the song |
| A3 and up | One clip per sound effect, gain already set | Move, change volume, delete, or drag in your own sound |

**Changing fonts and colours of the baked layers** (captions, graphics text, headline picture):
edit `config.yaml` (fonts under `subtitles.fonts`, colours under `project.brand_colors`, headline
size under `layout.reel`), then rebuild just that pack in about a minute:

```bash
hrc resolve --reel reel02 --lang te       # or --lang te,en for several languages
```

Re-import the new .fcpxml (Resolve keeps your old timeline, so you can compare).

**Levels:** the separate tracks are not loudness-matched like the finished `.mp4`. When you're done,
select all audio clips and use *Normalize Audio Levels* (−14 LUFS for Instagram and YouTube), or
put a limiter on the main bus.

**Export:** *Deliver → Custom*, H.264 or H.265, 1080x1920, 30 fps, 12–16 Mbit/s, AAC 320 kbit/s.

By default the Resolve pack is built in Telugu only (about 0.5–1 GB per reel). Add languages in
`config.yaml` → `export.resolve_langs`, or for one reel: `hrc resolve --reel reel02 --lang en`.

---

## How the look is built (and the settings that control it)

| Element | What HRC does | Setting in `config.yaml` |
|---|---|---|
| Layout | Landscape video in a 9:16 frame, headline bar above, captions below | `layout.reel.*` |
| Framing | Steady "tripod" framing that follows the face slowly, punch-in zooms on emphasis words | `reframe.*` |
| Clean cuts | A camera angle never flashes on screen for less than 0.4 s; the crop changes on the exact frame | `reframe.min_shot_sec` |
| Cut-out | Apple Vision person segmentation (RVM or MediaPipe as fallbacks) so graphics sit behind you | `matte.engine` |
| Graphics | Photo backgrounds, counters (₹/$/€ count up), stacked words with typewriter, glitch or fade | per moment in `reels_plan.json` |
| Captions | 1–2 lines, active word in yellow, key words in red, never blink between lines | `subtitles.*` |
| Sound | Speech cleanup, ducked music, sound effects only on 2–4 key moments, −14 LUFS | `audio.*` |
| Brand | Your round stamp as the corner logo, and a stamp slam with sound at the end | `project.logo*`, `project.end_stamp_size` |

In `reels_plan.json` each visual moment can carry:
`"anim"` (typewriter, glitch, fade, zoom, slide), `"style"` (backdrop or sprite for pictures),
`"sfx": true` (make sure it gets a sound) or `"sfx": false` (keep it silent).
A reel can switch off its ending with `"outro": false`.

---

## When something goes wrong

| You see | Do this |
|---|---|
| `command not found: hrc` | Run `source .venv/bin/activate` inside `~/HemuReelCreations` first |
| "The reel plan is not approved yet" | Run `hrc select check` and `hrc approve` |
| "no hi subtitles yet" | Run `hrc translate build` (translations must be filled first) |
| A reel is over 60 s | Trim a segment in `reels_plan.json`, then `hrc select check && hrc approve` |
| Resolve shows clips as offline | Keep `media/` next to the .fcpxml; or right-click the bin → *Relink Selected Clips* and pick `media/` |
| A graphic covers the face | Delete its V3 cut-out clip back in, or move the V2 clip; check `qa_report.md` |
| Telugu letters look broken in graphics | `brew install libraqm`, then `pip install --force-reinstall pillow` |
| No music | Add files to `assets/music/<mood>/` and render again |

## Limits

- Translated captions are timed across each line, so they are close but not word-exact; Telugu is word-exact.
- Two people in one wide shot: the framing follows the biggest, steadiest face.
- Auto graphics are clean templates, not hand-made motion design. Polish your best reels in Resolve.
- Web photos come from free sources (Openverse, Wikimedia). Anything flagged ⚠ in
  `assets/manifest.json` (logos, news, film stills, people) needs your call before posting.
- Hair edges can shimmer on fast movement; the matte flicker score is in `qa_report.md`.

## Version history

- **v0.6** Layered DaVinci Resolve pack (`hrc resolve`), illustrated README, 10 reels from HemuXSrinadh_Part1.
- **v0.5** Clean camera cuts, steady captions, sound only on key moments, brand stamp logo and ending stamp.
- **v0.4** Typewriter, glitch and fade text, money counters, softer cinematic sounds, Apple Vision cut-out, no opening text card.
- **v0.3** Reel layout: landscape video in a 9:16 frame with headline and captions, photo backgrounds behind the speaker.
- **v0.2** Steady framing, flicker-free 1–2 line captions, portrait and landscape formats.
