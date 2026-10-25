"""Stage B: word-level timing for the Telugu SRT text.

Engines, best first:
  whisperx        forced alignment of the SRT text itself with a Telugu wav2vec2 model
  mlx / faster_whisper   Whisper word timestamps, mapped onto the SRT words by character position
  proportional    no model: split each cue's time across its words by length (always works, least accurate)
"""
from __future__ import annotations

import difflib
import unicodedata
from pathlib import Path

from . import log
from .util import cached, fingerprint, is_apple_silicon, mark, read_json, torch_device, write_json

TELUGU_ALIGN_MODEL = "anuragshas/wav2vec2-large-xlsr-53-telugu"


def split_words(text: str) -> list[str]:
    return [w for w in text.split() if any(not unicodedata.category(c).startswith("P") for c in w)] or text.split()


def weight(word: str) -> float:
    """Spoken-length proxy: base characters count fully, vowel signs/viramas count half."""
    w = 0.0
    for c in word:
        cat = unicodedata.category(c)
        if cat.startswith("P"):
            continue
        w += 0.5 if cat in ("Mn", "Mc") else 1.0
    return max(w, 1.0) + 0.6  # +gap between words


def proportional(cue: dict, conf: float = 0.3) -> list[dict]:
    words = split_words(cue["text"])
    if not words:
        return []
    ws = [weight(w) for w in words]
    total, dur = sum(ws), cue["end"] - cue["start"]
    out, t = [], cue["start"]
    for w, k in zip(words, ws):
        d = dur * k / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3), "confidence": conf, "cue": cue["index"]})
        t += d
    return out


def map_asr_to_cue(cue: dict, asr_words: list[dict]) -> list[dict]:
    """Place SRT words on the timeline using ASR words that fall in the cue.

    Builds a per-character time line from the ASR words, then gives each SRT word the
    times at its relative character position. Confidence mixes ASR probability with
    how similar the ASR text is to the SRT text.
    """
    inside = [w for w in asr_words if cue["start"] - 0.3 <= (w["start"] + w["end"]) / 2 <= cue["end"] + 0.3]
    if not inside:
        return proportional(cue, conf=0.2)
    srt_words = split_words(cue["text"])
    asr_text = " ".join(w["word"].strip() for w in inside)
    sim = difflib.SequenceMatcher(None, "".join(srt_words), asr_text.replace(" ", "")).ratio()
    # character time line
    char_t = []
    for w in inside:
        n = max(1, len(w["word"].strip()))
        for k in range(n):
            char_t.append(w["start"] + (w["end"] - w["start"]) * k / n)
        char_t.append(w["end"])
    total_srt = sum(len(w) + 1 for w in srt_words)
    out, pos = [], 0
    probs = [w.get("probability", 0.7) for w in inside]
    base_conf = (sum(probs) / len(probs)) * (0.4 + 0.6 * sim)
    for w in srt_words:
        a = pos / total_srt
        b = (pos + len(w)) / total_srt
        ia = min(len(char_t) - 1, int(a * (len(char_t) - 1)))
        ib = min(len(char_t) - 1, int(b * (len(char_t) - 1)))
        s, e = char_t[ia], max(char_t[ib], char_t[ia] + 0.08)
        s, e = max(s, cue["start"] - 0.2), min(e, cue["end"] + 0.2)
        out.append({"word": w, "start": round(s, 3), "end": round(e, 3), "confidence": round(base_conf, 3), "cue": cue["index"]})
        pos += len(w) + 1
    return _monotonic(out)


def _monotonic(words: list[dict]) -> list[dict]:
    for a, b in zip(words, words[1:]):
        if b["start"] < a["start"]:
            b["start"] = a["start"]
        if a["end"] > b["start"]:
            a["end"] = b["start"]
        if a["end"] <= a["start"]:
            a["end"] = round(a["start"] + 0.05, 3)
    return words


def _pick_engine(pref: str) -> str:
    if pref != "auto":
        return pref
    for mod, name in (("whisperx", "whisperx"), ("mlx_whisper", "mlx"), ("faster_whisper", "faster_whisper")):
        try:
            __import__(mod)
            if name == "mlx" and not is_apple_silicon():
                continue
            return name
        except Exception:
            continue
    return "proportional"


def _asr_words(engine: str, audio: Path, model: str, device: str) -> list[dict]:
    if engine == "mlx":
        import mlx_whisper  # type: ignore

        res = mlx_whisper.transcribe(str(audio), path_or_hf_repo=f"mlx-community/whisper-{model}-mlx",
                                     language="te", word_timestamps=True)
        return [w for seg in res["segments"] for w in seg.get("words", [])]
    from faster_whisper import WhisperModel  # type: ignore

    wm = WhisperModel(model, device="cpu" if device == "mps" else device, compute_type="int8")
    segs, _ = wm.transcribe(str(audio), language="te", word_timestamps=True, vad_filter=True)
    return [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability} for s in segs for w in s.words]


def _whisperx_forced(cues: list[dict], audio: Path, device: str, align_model: str) -> list[dict]:
    import whisperx  # type: ignore

    dev = "cpu" if device == "mps" else device  # wav2vec2 CTC is stable on CPU; MPS lacks some ops
    model_a, meta = whisperx.load_align_model(language_code="te", device=dev, model_name=align_model)
    audio_np = whisperx.load_audio(str(audio))
    segs = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in cues]
    res = whisperx.align(segs, model_a, meta, audio_np, dev, return_char_alignments=False)
    out = []
    for cue, seg in zip(cues, res["segments"]):
        ws = seg.get("words", [])
        srt_words = split_words(cue["text"])
        if not ws or any("start" not in w for w in ws) or len(ws) != len(srt_words):
            fixed = map_asr_to_cue(cue, [{"word": w["word"], "start": w["start"], "end": w["end"],
                                         "probability": w.get("score", 0.5)} for w in ws if "start" in w])
            out.extend(fixed)
            continue
        for sw, w in zip(srt_words, ws):
            out.append({"word": sw, "start": round(w["start"], 3), "end": round(w["end"], 3),
                        "confidence": round(float(w.get("score", 0.5)), 3), "cue": cue["index"]})
    return _monotonic(out)


def align(cues: list[dict], audio: Path, engine: str, model: str, device: str, align_model: str) -> tuple[list[dict], str]:
    if engine == "whisperx":
        try:
            return _whisperx_forced(cues, audio, device, align_model), "whisperx-forced"
        except Exception as exc:
            log.warn("WhisperX forced alignment failed (%s). Falling back to Whisper word timestamps.", exc)
            engine = "faster_whisper"
    if engine in ("mlx", "faster_whisper"):
        try:
            asr = _asr_words(engine, audio, model, device)
            out = []
            for c in cues:
                out.extend(map_asr_to_cue(c, asr))
            return _monotonic(out), engine
        except Exception as exc:
            log.warn("Whisper (%s) failed (%s). Falling back to proportional timing.", engine, exc)
    out = []
    for c in cues:
        out.extend(proportional(c))
    return out, "proportional"


def review_regions(words: list[dict], threshold: float) -> list[dict]:
    """Group consecutive low-confidence words into regions for human review."""
    regions, cur = [], None
    for w in words:
        if w["confidence"] < threshold:
            if cur and w["start"] - cur["end"] < 2.0:
                cur["end"] = w["end"]; cur["words"].append(w["word"])
            else:
                cur = {"start": w["start"], "end": w["end"], "words": [w["word"]]}
                regions.append(cur)
        else:
            cur = None
    for r in regions:
        r["text"] = " ".join(r.pop("words"))
    return regions


def run_stage(cfg, force: bool = False) -> list[dict]:
    cache = cfg.dir("cache")
    cues = read_json(cache / "cues.json")
    audio = cache / "audio_16k.wav"
    out = cache / "words.json"
    engine = _pick_engine(cfg.at("align.engine", "auto"))
    fp = fingerprint(cache / "cues.json", audio, engine, cfg.at("align.model"))
    if cached(out, fp, force):
        log.info("Word timings already done, skipping.")
        return read_json(out)
    device = torch_device(cfg.at("align.device", "auto"))
    log.info("Aligning %d cues with engine '%s' on %s. On a 60-minute podcast this can take 20-60 minutes.",
             len(cues), engine, device)
    words, used = align(cues, audio, engine, cfg.at("align.model", "large-v3"), device,
                        cfg.at("align.align_model", TELUGU_ALIGN_MODEL))
    write_json(out, words)
    mark(out, fp)
    regions = review_regions(words, cfg.at("align.low_confidence", 0.45))
    write_json(cache / "align_review.json", {"engine": used, "regions": regions})
    if used == "proportional":
        log.warn("Using estimated (proportional) word timing. Karaoke highlights will be close but not exact. "
                 "Install whisperx or mlx-whisper for real alignment.")
    log.info("Saved %d words (engine: %s). %d low-confidence regions listed in cache/align_review.json.",
             len(words), used, len(regions))
    return words
