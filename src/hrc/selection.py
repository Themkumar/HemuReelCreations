"""Stage C: reel selection.

`hrc select prepare` writes the transcript in timestamped chunks plus the brief.
In chat mode, Claude reads those files in the project thread and writes reels_plan.json.
In api mode, HRC sends them to the Anthropic API (needs ANTHROPIC_API_KEY).
`hrc select check` snaps cuts to word gaps and reports problems. Rendering waits for `hrc approve`.
"""
from __future__ import annotations

import json
import os

from . import log, plan as planlib
from .util import read_json, ts, write_json

BRIEF = """You are picking viral Instagram Reels / YouTube Shorts from a Telugu podcast.

Propose {cmin}-{cmax} reels, each {lo}-{hi} seconds (never over {hi}), that a first-time Telugu viewer understands
without watching the podcast. Use only complete statements: every segment starts at the start of a
thought and ends when it is finished. The reel must tell a whole story. Each needs a hook in the first 2 seconds, context, and a payoff.
Cut only on sentence boundaries (use the timestamps given). A reel may join 2-3 segments
from nearby parts of the talk if that tells the story better.

Return JSON exactly in this shape:
{{
  "approved": false,
  "reels": [{{
    "id": "reel01",
    "title": "short English working title",
    "segments": [{{"start": "00:12:03.400", "end": "00:12:48.900"}}],
    "hook": "Telugu hook line (as spoken, or a title card)",
    "headline": {{"te": "headline over the video, key words in *stars*", "en": "...", "hi": "...", "ta": "...", "kn": "..."}},
    "emotional_peak": "00:12:31.000",
    "why": "why this works, one sentence",
    "virality": 8.5,
    "mood": "inspirational | tense | emotional | funny | calm | energetic",
    "emphasis_words": ["Telugu words to make bigger"],
    "visual_moments": [{{
      "phrase": "exact Telugu word/phrase as in the transcript",
      "type": "image | text | counter | map | arrow | broll",
      "query": "English web search query for the asset",
      "label": "short text to show, optional",
      "value": "number for counters, optional"
    }}]
  }}]
}}
"""


def prepare(cfg) -> None:
    cache = cfg.dir("cache")
    cues = read_json(cache / "cues.json")
    out_dir = cache / "select"
    out_dir.mkdir(exist_ok=True)
    chunk_sec = cfg.at("select.chunk_minutes", 8) * 60
    chunks, cur, start = [], [], 0.0
    for c in cues:
        if cur and c["start"] - start > chunk_sec:
            chunks.append(cur); cur = []; start = c["start"]
        cur.append(f"[{ts(c['start'])} - {ts(c['end'])}] {c['text']}")
    if cur:
        chunks.append(cur)
    for i, lines in enumerate(chunks, 1):
        (out_dir / f"chunk_{i:02d}.txt").write_text("\n".join(lines), encoding="utf-8")
    brief = BRIEF.format(cmin=cfg.at("reel.count_min"), cmax=cfg.at("reel.count_max"),
                         lo=cfg.at("reel.min_sec"), hi=cfg.at("reel.max_sec"))
    (out_dir / "BRIEF.md").write_text(brief, encoding="utf-8")
    log.info("Wrote %d transcript chunks and the brief to %s.", len(chunks), out_dir)
    if cfg.at("select.mode") == "api":
        _api_select(cfg, brief, chunks)
    else:
        log.info("Chat mode: ask Claude in your project thread to read cache/select/ and write reels_plan.json.")


def _api_select(cfg, brief: str, chunks: list[list[str]]) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("select.mode is 'api' but ANTHROPIC_API_KEY is not set.")
    import anthropic  # type: ignore

    client = anthropic.Anthropic()
    transcript = "\n\n".join("\n".join(c) for c in chunks)
    msg = client.messages.create(
        model=cfg.at("select.api_model"), max_tokens=16000,
        messages=[{"role": "user", "content": f"{brief}\n\nTRANSCRIPT:\n{transcript}\n\nReply with the JSON only."}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    planlib.save(cfg, data)
    log.info("Saved %d proposed reels to reels_plan.json.", len(data.get("reels", [])))


def check(cfg) -> list[str]:
    words = read_json(cfg.dir("cache") / "words.json")
    plan = planlib.load(cfg)
    problems = planlib.validate(cfg, plan, words)
    plan["approved"] = False if problems else plan.get("approved", False)
    planlib.save(cfg, plan)
    log.info("Reel plan: %d reels.", len(plan["reels"]))
    planlib.log_summary(plan)
    for p in problems:
        log.warn(p)
    if not problems:
        log.info("Plan looks valid. When you're happy with it, run `hrc approve`.")
    return problems


def approve(cfg) -> None:
    plan = planlib.load(cfg)
    plan["approved"] = True
    planlib.save(cfg, plan)
    log.info("Reel plan approved. You can now run `hrc translate` and `hrc render`.")
