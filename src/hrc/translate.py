"""Stage D: subtitle chunks and translations.

`hrc translate prepare` cuts each reel's Telugu words into 3-6 word chunks and writes
cache/translate/<reel>.json with empty hi/en/ta/kn fields.
Chat mode: Claude fills those fields in the project thread. API mode: HRC asks the Anthropic API.
`hrc translate build` turns them into subs/<reel>_<lang>.json with per-word karaoke timing.
"""
from __future__ import annotations

import json
import os

from . import align, log, plan as planlib
from .util import read_json, write_json

LANG_NAMES = {"te": "Telugu", "hi": "Hindi", "en": "English", "ta": "Tamil", "kn": "Kannada"}
_BREAK = set(",.!?;:।…")


def chunk_words(words: list[dict], lo: int = 3, hi: int = 6, pause: float = 0.35) -> list[list[dict]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 9
        ends_sentence = w["word"][-1:] in _BREAK
        new_cue = nxt is not None and nxt.get("cue") != w.get("cue")
        if new_cue or len(cur) >= hi or (len(cur) >= lo and (ends_sentence or gap > pause)) or gap > 1.0:
            chunks.append(cur); cur = []
    if cur:
        if chunks and len(cur) < lo and len(chunks[-1]) + len(cur) <= hi + 1 and chunks[-1][-1].get("cue") == cur[0].get("cue"):
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    return chunks


def prepare(cfg, only: str | None = None) -> None:
    plan = planlib.approved(cfg)
    words = read_json(cfg.dir("cache") / "words.json")
    lo, hi = cfg.at("translate.chunk_words", [3, 6])
    tdir = cfg.dir("cache") / "translate"
    tdir.mkdir(exist_ok=True)
    langs = [l for l in cfg.at("reel.languages") if l != "te"]
    for reel in planlib.selected(plan, only):
        rw = planlib.reel_words(reel, words)
        path = tdir / f"{reel['id']}.json"
        old = {c["te"]: c for c in read_json(path)["chunks"]} if path.exists() else {}
        given = reel.get("translations", {})  # {Telugu cue text: {hi, en, ta, kn}} written with the plan
        chunks = []
        for i, ch in enumerate(chunk_words(rw, lo, hi)):
            te = " ".join(w["word"] for w in ch)
            entry = {"id": i, "start": ch[0]["start"], "end": ch[-1]["end"], "te": te}
            pre = given.get(te) or next((v for k, v in given.items() if te in k), {})
            for l in langs:
                entry[l] = old.get(te, {}).get(l, "") or pre.get(l, "")
            chunks.append(entry)
        write_json(path, {"reel": reel["id"], "languages": langs,
                          "note": "Fill each language with a natural spoken translation of 'te'. Keep names/brands.",
                          "chunks": chunks})
        log.info("%s: %d subtitle chunks ready for translation.", reel["id"], len(chunks))
    if cfg.at("translate.mode") == "api":
        _api_translate(cfg, tdir, langs, only, plan)


def _api_translate(cfg, tdir, langs, only, plan) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("translate.mode is 'api' but ANTHROPIC_API_KEY is not set.")
    import anthropic  # type: ignore

    client = anthropic.Anthropic()
    for reel in planlib.selected(plan, only):
        path = tdir / f"{reel['id']}.json"
        data = read_json(path)
        todo = [{"id": c["id"], "te": c["te"]} for c in data["chunks"]]
        prompt = (f"Translate each Telugu subtitle chunk into {', '.join(LANG_NAMES[l] for l in langs)}. "
                  "Natural spoken tone, short, keep names and brands. Keep the context of the whole list. "
                  f"Reply with JSON: a list of objects with id and keys {langs}.\n\n{json.dumps(todo, ensure_ascii=False)}")
        msg = client.messages.create(model=cfg.at("select.api_model"), max_tokens=16000,
                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        rows = {r["id"]: r for r in json.loads(text[text.find("["): text.rfind("]") + 1])}
        for c in data["chunks"]:
            for l in langs:
                c[l] = rows.get(c["id"], {}).get(l, c[l])
        write_json(path, data)
        log.info("%s translated by API.", reel["id"])


def timed_words(text: str, start: float, end: float) -> list[dict]:
    """Spread a chunk's time across its words by spoken length."""
    ws = text.split()
    if not ws:
        return []
    weights = [align.weight(w) for w in ws]
    total, dur = sum(weights), end - start
    out, t = [], start
    for w, k in zip(ws, weights):
        d = dur * k / total
        out.append({"w": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def split_lines(chunk: dict, max_chars: int, max_lines: int = 2) -> list[dict]:
    """Break a timed chunk into caption screens. Each screen has up to `max_lines` lines of at most
    `max_chars`; every word gets a "line" index so line 2 always sits under line 1."""
    rows, cur = [], []
    for w in chunk["words"]:
        if cur and len(" ".join(x["w"] for x in cur + [w])) > max_chars:
            rows.append(cur); cur = []
        cur.append(w)
    if cur:
        if rows and len(cur) == 1 and len(" ".join(x["w"] for x in rows[-1] + cur)) <= max_chars + 3:
            rows[-1].extend(cur)
        else:
            rows.append(cur)
    screens = [rows[i:i + max_lines] for i in range(0, len(rows), max_lines)]
    pieces = []
    for i, scr in enumerate(screens):
        ws = [dict(w, line=li) for li, row in enumerate(scr) for w in row]
        start = chunk["start"] if i == 0 else ws[0]["start"]
        end = screens[i + 1][0][0]["start"] if i + 1 < len(screens) else chunk["end"]
        pieces.append({"start": round(start, 3), "end": round(end, 3), "words": ws})
    return pieces


MAX_CHARS = {"te": 16, "kn": 16, "ta": 15, "hi": 20, "en": 22}


def build(cfg, only: str | None = None) -> None:
    plan = planlib.approved(cfg)
    words = read_json(cfg.dir("cache") / "words.json")
    tdir = cfg.dir("cache") / "translate"
    sdir = cfg.dir("subs")
    for reel in planlib.selected(plan, only):
        emph = set(reel.get("emphasis_words", [])) | {vm["phrase"] for vm in reel.get("visual_moments", [])}
        data = read_json(tdir / f"{reel['id']}.json")
        rw = planlib.reel_words(reel, words)
        missing = []
        for lang in cfg.at("reel.languages"):
            chunks = []
            for c in data["chunks"]:
                if lang == "te":
                    ws = [{"w": w["word"], "start": w["start"], "end": w["end"]}
                          for w in rw if c["start"] - 1e-3 <= w["start"] <= c["end"] + 1e-3]
                    ws = ws or timed_words(c["te"], c["start"], c["end"])
                else:
                    text = c.get(lang, "").strip()
                    if not text:
                        missing.append(f"{lang}#{c['id']}")
                        continue
                    ws = timed_words(text, c["start"], c["end"])
                for w in ws:
                    w["emph"] = any(e and len(e) >= 2 and e in w["w"] for e in emph) if lang == "te" else False
                chunks.append({"start": c["start"], "end": c["end"], "words": ws})
            if lang != "te" and chunks:
                _carry_emphasis(chunks, data["chunks"], lang, emph)
            limit = (cfg.at("subtitles.max_chars") or {}).get(lang, MAX_CHARS.get(lang, 18))
            chunks = [p for ch in chunks for p in split_lines(ch, limit, cfg.at("subtitles.max_lines", 2))]
            write_json(sdir / f"{reel['id']}_{lang}.json", {"reel": reel["id"], "lang": lang, "chunks": chunks})
        if missing:
            log.warn("%s: %d chunks have no translation yet (%s...). Those lines will be blank.",
                     reel["id"], len(missing), ", ".join(missing[:5]))
        log.info("%s: subtitles built for %s.", reel["id"], ", ".join(cfg.at("reel.languages")))


def _carry_emphasis(chunks, src_chunks, lang, emph) -> None:
    """If a Telugu chunk had an emphasis word, emphasise the longest word of its translation."""
    by_time = {round(c["start"], 3): c for c in src_chunks}
    for ch in chunks:
        src = by_time.get(round(ch["start"], 3))
        if src and any(e and e in src["te"] for e in emph) and ch["words"]:
            max(ch["words"], key=lambda w: len(w["w"]))["emph"] = True
