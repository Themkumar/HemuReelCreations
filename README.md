# HemuReelCreations (HRC)

Turns a finished Telugu episode of **The Hemu Mark Reality Check** into ten 9:16 reels (1080x1920,
30 fps, at most 60 s each) in the style of the big Indian podcast reels: a full-screen close-up,
**English captions** (small white words with the key word big in a glowing italic serif), giant words
behind your head, graphics that land on the exact frame of the word, and 1–2 s reaction shots of the
listener. Every reel also comes with a **layered DaVinci Resolve project** (every graphic, caption, logo
and sound on its own track) and a small `_phone.mp4` to watch on your phone. The audio stays Telugu.

The other caption languages (Telugu, Hindi, Tamil, Kannada) still work: add them to `reel.languages`.

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
| 3b | Mac | `hrc realign` | Second pass: words WhisperX squashed together are re-timed from the sound itself, and the start of every sound is saved so graphics land on the exact frame | 1–2 min |
| 3c | Mac | `hrc reactions` | Learns who is who, and finds moments where the listener is alone on screen, mouth still, while the other talks: the reaction-shot library | ~10 min |
| 4 | Mac | `hrc select prepare` | Writes the transcript brief into `cache/select/` | 10 s |
| 5 | Claude | (in the project chat) | Claude reads the brief and writes `reels_plan.json`: cuts, headlines, graphics, which moments get a sound | — |
| 6 | You | `hrc select check` then `hrc approve` | Checks lengths and snaps cuts between words. **Nothing renders before you approve.** | 10 s |
| 7 | Mac + Claude | `hrc translate prepare` → Claude fills the English → `hrc translate build` | English caption files in `subs/`. Mark the big key word of a line with stars: `in my *résumé*` | — |
| 8 | Mac | `hrc assets` | Downloads 5 HD photos (2400 px+, licences that allow monetised use) per photo moment and draws a numbered sheet per reel in `assets/sheets/` | 2–4 min |
| 8b | You or Claude | look at `assets/sheets/reelNN.jpg` | Not the best photo? Add `"pick": 3` to that moment in `reels_plan.json`, run `hrc assets` again (instant) | 1 min |
| 9 | Mac | `hrc render` | Frames the close-up, adds reaction shots, cuts you out only where a graphic sits behind you, renders the English reel, a phone copy and the Resolve pack | ~6–12 min per reel |
| 10 | Mac | `hrc qa` | Checks length, loudness, captions, faces → `qa_report.md` | 1 min |
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
qa_report.md                         checks for every file (in the HRC folder)
output/
  reel02/
    reel02_en.mp4                    ready to post
    reel02_en_phone.mp4              small copy (about 3 MB) to watch on your phone
    reel02_clean.mp4                 no captions
    reel02_mix.wav                   the finished sound
    resolve_en/                      the layered Resolve project (see below)
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
| V2 | Photo backgrounds, rendered with the look from `config.yaml` (`gfx.backdrop`), slow push-in and fades | Grade it on the Color page, change opacity, retime, delete |
| V3 | The same photo as an untouched HD still (twice the timeline size), **off** | Select it, press **D**, turn V2 off: now zoom, reframe and grade the full-quality photo yourself. The original download is in `media/photos/` |
| V4 | Each graphic alone, with transparency (`gfx/gNN.mov`): words, counters, pictures | Move, scale, retime, fade, change opacity, delete or replace it |
| V5 | You cut out, **plus the mic, cups, bottles and other things in front of you**, only while a graphic is behind you | Delete one to bring that graphic in front of you |
| V6 | Graphic parts that sit in front of you (counters) | Same as V4 |
| V7 | Corner logo (the white stamp) | Move, resize, change opacity, turn off |
| V8 | Headline above the video, as a picture | Turn off (select it, press **D**) when you use V9 instead |
| V9 | Headline as Resolve text, **off** by default | Select it, press **D** to turn it on, then change words, font, size and colour in the Inspector |
| V10 | Karaoke captions with transparency | Move or turn off. For captions you style yourself: *File → Import → Subtitle…* and pick `media/captions_te.srt` |
| V11 and up | The words of every graphic as Resolve text, **off** by default | To restyle a graphic yourself: turn its V4 clip off, turn this one on (**D**), then set words, font, colour and size in the Inspector and add any title animation from the Effects library (Fusion titles, Text+ "Write On" for a typewriter) |
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

## Photos: ultra-HD and the right picture

- `hrc assets` only keeps photos at least **2400 px wide** (`assets.min_width`) whose licence allows monetised,
  edited use (CC0, public domain, CC BY, CC BY-SA, Pexels, Pixabay). Non-commercial (NC) and no-derivatives (ND)
  photos are skipped.
- For each photo moment it keeps **5 candidates** and draws `assets/sheets/<reel>.jpg`: numbered thumbnails with
  size and licence, the chosen one framed in green.
- To use another one, add `"pick": 3` (the number on the sheet) to that visual moment in `reels_plan.json` and run
  `hrc assets` again. To use your own photo, save it as `assets/images/<reel>/<moment number>.jpg`; it always wins.
- Free API keys give the best photos: put `PEXELS_API_KEY` and `PIXABAY_API_KEY` in your shell profile.
- The look behind you is set in `config.yaml` → `gfx.backdrop` (blur 0 = crisp; dim, desaturate, vignette, tint,
  push_in). In Resolve you can also grade the photo on V2 or use the untouched HD still on V3.

## The cut-out (you in front of the graphics)

Since v0.8 the cut-out is made **only on the frames where a graphic sits behind you** (plus 0.3 s on each side,
`matte.handle_sec`), so all the effort goes where it shows. On those frames a second model (BiRefNet,
`matte.edge_model`) redraws the edges at high resolution: single hairs, fingers, the thin mic arm. It only works in
a narrow band around the first cut-out, so it can sharpen an edge but never add the wall or drop your arm.
Faster model: `ZhengPeng7/BiRefNet_lite`. Off: `matte.edge_refine_model: false`. Cut out every frame again:
`matte.only_where_needed: false`.

A depth model (Depth Anything V2) measures how far every pixel is from the camera. Everything nearer than you,
below your eye line, joins the cut-out: the mic and its arm, the mug, a laptop, the tabletop. Your chair and the
wall stay behind (`matte.depth_tolerance: -0.03`; higher keeps more, lower keeps less). Apple Vision's object
finder adds anything it detects. Objects are steadied over 5 frames so they never flicker, and every edge is
cleaned against the picture (`matte.refine_edges`). Turn it off with `matte.depth_objects: false`.

If the mic arm now covers a graphic's words, move that graphic on V4 in Resolve or delete its V5 cut-out clip.

## How the look is built (and the settings that control it)

| Element | What HRC does | Setting in `config.yaml` |
|---|---|---|
| Layout | Full-screen 9:16 close-up (v0.8). `fullbleed: false` brings back the wide box with a headline | `layout.reel.*` |
| Look | Darker room, deeper shadows, warm skin, stronger vignette | `grade.*` |
| Reaction shots | 1–2 s cutaways (never more than 2 s) to the listener while the speaker talks, about one every 16 s, never on a graphic, a join or a camera switch. The speaker's sound carries on | `reactions.*` |
| Graphic timing | Each graphic lands on the frame where its word's sound starts. Still off? Add `"nudge": 2` (frames later) or `-2` (earlier) to that moment | `gfx.snap_to_onset`, `"nudge"` |
| Tracking | Graphics are pinned to the room: when the framing pans or punches in, they move and grow with it | `gfx.track`, per moment `"track": false` |
| Framing | Steady "tripod" framing that follows the face slowly, punch-in zooms on emphasis words | `reframe.*` |
| Clean cuts | A camera angle never flashes on screen for less than 0.4 s; the crop changes on the exact frame | `reframe.min_shot_sec` |
| Cut-out | Apple Vision person + object segmentation (mic, cups, bottles stay in front), depth check, edge clean-up; RVM or MediaPipe as fallbacks | `matte.*` |
| Photos | HD photos (2400 px+), crisp by default, numbered candidate sheets | `assets.*`, `gfx.backdrop.*` |
| Graphics | Photo backgrounds, counters (₹/$/€ count up), stacked words with typewriter, glitch or fade | per moment in `reels_plan.json` |
| Captions | Reference style: up to 3 small white words appear as they are spoken; the line's key word (marked `*like this*` in the English) lands big in a glowing yellow italic serif under them. `style: classic` brings back the karaoke lines | `subtitles.style`, `subtitles.punch.*` |
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
| A graphic covers the face | Check that its V5 cut-out clip is on, or move the V4 clip; see `qa_report.md` |
| A graphic lands a little early or late | Add `"nudge": 2` (2 frames later) or `"nudge": -2` to that moment in `reels_plan.json`, then `hrc render --reel <reel>` |
| A reaction shot shows the listener talking | `hrc render --reel <reel> --force` after setting `reactions.max_per_reel` lower, or `reactions.enabled: false` |
| "No edge refinement model" | `pip install timm kornia einops` inside the venv (the model downloads once, ~900 MB) |
| Wrong or blurry photo | Open `assets/sheets/<reel>.jpg`, set `"pick": n` on that moment, `hrc assets`, then `hrc render --reel <reel>` |
| The cut-out misses an object or takes one too many | Raise or lower `matte.object_max_area`, or set `matte.depth: false`; `hrc render --reel <reel> --force` |
| Telugu letters look broken in graphics | `brew install libraqm`, then `pip install --force-reinstall pillow` |
| Telugu letters broken only when run with `nohup` | Run long jobs as `bash -c 'source .venv/bin/activate && hrc render'` in the background; plain `nohup hrc ...` loses the text-shaping library |
| No music | Add files to `assets/music/<mood>/` and render again |

## Limits

- English captions follow the speech (pauses skipped) but the words are English and the voice is Telugu, so each
  English word is placed by its share of the line, not by a matching spoken word.
- Reaction shots come from moments of the same episode where the listener is alone on screen; an episode
  filmed only as a wide two-shot has none.
- Two people in one wide shot: the framing follows the biggest, steadiest face.
- Auto graphics are clean templates, not hand-made motion design. Polish your best reels in Resolve.
- Web photos come from free sources (Openverse, Wikimedia). Anything flagged ⚠ in
  `assets/manifest.json` (logos, news, film stills, people) needs your call before posting.
- Hair edges can shimmer on fast movement; the matte flicker score is in `qa_report.md`.

## Version history

- **v0.8** English-only, reference-reel look: full-screen close-up with a cinematic grade, small-white + big-serif captions, second word-timing pass and sound-onset landing for graphics, graphics pinned to the room, 1–2 s listener reaction shots, cut-out only where needed with BiRefNet edges, phone previews.
- **v0.7.1** Depth-based object cut-out (mic arm, mug, tabletop), tuned on the real footage.
- **v0.7** Cut-out keeps the mic, cups and bottles in front; HD photos (2400 px+, commercial licences) with candidate sheets and `"pick"`; crisp, configurable photo look; photo and HD still on their own Resolve tracks; every graphic's words as editable Resolve text.
- **v0.6** Layered DaVinci Resolve pack (`hrc resolve`), illustrated README, 10 reels from HemuXSrinadh_Part1.
- **v0.5** Clean camera cuts, steady captions, sound only on key moments, brand stamp logo and ending stamp.
- **v0.4** Typewriter, glitch and fade text, money counters, softer cinematic sounds, Apple Vision cut-out, no opening text card.
- **v0.3** Reel layout: landscape video in a 9:16 frame with headline and captions, photo backgrounds behind the speaker.
- **v0.2** Steady framing, flicker-free 1–2 line captions, portrait and landscape formats.
