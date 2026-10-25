"""Stage E: find a picture for each visual moment, record source + licence, cut out the background.

No-key sources: Openverse and Wikimedia Commons. Optional keys: PEXELS_API_KEY, PIXABAY_API_KEY.
Anything you drop into assets/images/<reel>/<moment#>.(png|jpg) is used as-is and wins.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

from . import log, plan as planlib
from .util import read_json, write_json

UA = {"User-Agent": "HemuReelCreations/0.1 (personal reel tool)"}
RISKY = ("logo", "gmail", "google", "linkedin", "movie", "film still", "news", "celebrity", "brand", "poster", "actor", "actress", "cricket match")


def _commercial_ok(lic: str) -> bool:
    """Licences that allow monetised, edited use: CC0, public domain, CC BY, CC BY-SA, Pexels, Pixabay."""
    low = (lic or "").lower()
    words = set(re.split(r"[^a-z]+", low))
    if words & {"nc", "nd"} or any(x in low for x in ("non-commercial", "noncommercial", "fair use", "all rights")):
        return False
    return True


def _openverse(q, n=20):
    r = requests.get("https://api.openverse.org/v1/images/", params={
        "q": q, "page_size": n, "license_type": "commercial,modification", "category": "photograph",
        "size": "large", "mature": "false"}, headers=UA, timeout=30)
    r.raise_for_status()
    return [{"url": x["url"], "page": x.get("foreign_landing_url"), "width": x.get("width") or 0,
             "height": x.get("height") or 0,
             "license": f"CC {x.get('license', '').upper()} {x.get('license_version', '')}".strip(),
             "credit": x.get("creator") or "", "title": x.get("title") or "", "source": "openverse"}
            for x in r.json().get("results", [])]


def _wikimedia(q, n=20, thumb=3840):
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}",
        "gsrnamespace": 6, "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": thumb}, headers=UA, timeout=30)
    r.raise_for_status()
    pages = sorted((r.json().get("query", {}).get("pages", {}) or {}).values(), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        meta = ii.get("extmetadata", {})
        out.append({"url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                    "width": ii.get("width") or 0, "height": ii.get("height") or 0,
                    "license": meta.get("LicenseShortName", {}).get("value", "see page"),
                    "credit": re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")),
                    "title": p.get("title", ""), "source": "wikimedia",
                    "trademark": "trademark" in json.dumps(meta).lower()})
    return out


def _pexels(q, n=15):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.pexels.com/v1/search", params={"query": q, "per_page": n, "size": "large"},
                     headers={"Authorization": key}, timeout=30)
    r.raise_for_status()
    return [{"url": x["src"]["original"], "page": x["url"], "width": x.get("width", 0), "height": x.get("height", 0),
             "license": "Pexels License", "credit": x["photographer"], "title": x.get("alt", ""), "source": "pexels"}
            for x in r.json().get("photos", [])]


def _pixabay(q, n=15):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/", params={"key": key, "q": q, "per_page": n, "safesearch": "true",
                                                         "image_type": "photo", "min_width": 2400}, timeout=30)
    r.raise_for_status()
    return [{"url": x.get("largeImageURL"), "page": x["pageURL"], "width": x.get("imageWidth", 0),
             "height": x.get("imageHeight", 0), "license": "Pixabay Content License", "credit": x["user"],
             "title": x.get("tags", ""), "source": "pixabay"} for x in r.json().get("hits", [])]


def rank(hits: list[dict], min_w: int, backdrop: bool) -> list[dict]:
    """Keep big, commercially usable photos; best first. Search order (relevance) still counts most."""
    good = []
    for i, h in enumerate(hits):
        w, hh = h.get("width") or 0, h.get("height") or 0
        if not h.get("url") or not _commercial_ok(h.get("license", "")):
            continue
        if w and w < min_w:
            continue
        land = (w / hh) if w and hh else 1.5
        score = -i * 1.0 + min(w, 6000) / 1500 + (1.0 if backdrop and land >= 1.2 else 0)
        good.append((score, h))
    return [h for _, h in sorted(good, key=lambda x: -x[0])]


def _commons_file(name: str):
    """A specific Wikimedia Commons file picked by Claude (e.g. 'File:Italy location map.svg'), up to 3840 px."""
    title = name if name.startswith("File:") else f"File:{name}"
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": 3840}, headers=UA, timeout=20)
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata", {})
    if not ii:
        return []
    return [{"url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
             "width": ii.get("width") or 0, "height": ii.get("height") or 0,
             "license": meta.get("LicenseShortName", {}).get("value", "see page"),
             "credit": re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")),
             "trademark": "trademark" in json.dumps(meta).lower(), "source": "wikimedia (picked)"}]


SOURCES = {"openverse": _openverse, "wikimedia": _wikimedia, "pexels": _pexels, "pixabay": _pixabay}


def _cutout(path: Path) -> Path:
    try:
        from rembg import remove  # type: ignore
        from PIL import Image

        import numpy as np

        src = Image.open(path).convert("RGBA")
        small = np.asarray(src.resize((96, 96)))
        if small[..., 3].min() < 250 or len(np.unique(small[..., :3].reshape(-1, 3), axis=0)) < 64:
            return path  # already a graphic (flag, icon, transparent PNG): nothing to cut out
        cut = remove(src)
        before = (np.asarray(src)[..., 3] > 16).mean()
        after = (np.asarray(cut)[..., 3] > 16).mean()
        if before and after / before < 0.15:  # flags, logos, icons: rembg eats them, keep the full picture
            log.debug("background removal dropped %s (kept %.0f%%)", path.name, 100 * after / before)
            return path
        out = path.with_name(path.stem + "_cut.png")
        cut.save(out)
        return out
    except Exception as exc:
        log.debug("background removal skipped for %s: %s", path.name, exc)
        return path


def _wants_cut(cfg, vm: dict, ext: str) -> bool:
    """Cut out icons and objects (PNG/SVG, or style: sprite). Photos stay whole: they become the
    background behind the speaker (style: backdrop)."""
    if not cfg.at("assets.remove_background") or vm.get("style") == "backdrop":
        return False
    return vm.get("style") == "sprite" or ext.lower() == ".png"


def _download(url: str, dest: Path) -> Path:
    if not dest.exists() or dest.stat().st_size == 0:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def _real_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


def contact_sheet(reel_id: str, rows: list[tuple[str, list[dict], int]], out: Path) -> Path | None:
    """One picture per reel: every photo moment with its numbered candidates, size and licence."""
    from PIL import Image, ImageDraw
    from .gfx import font_for

    TW, TH, PAD = 480, 270, 12
    cols = max([len(c) for _, c, _ in rows] + [1])
    if not rows:
        return None
    sheet = Image.new("RGB", (PAD + cols * (TW + PAD), PAD + len(rows) * (TH + 70 + PAD)), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    f, fs = font_for("Ag", 26), font_for("Ag", 18, weight=500)
    for r, (title, cands, chosen) in enumerate(rows):
        y = PAD + r * (TH + 70 + PAD)
        d.text((PAD, y), title, font=f, fill=(255, 212, 0))
        for c, h in enumerate(cands):
            x = PAD + c * (TW + PAD)
            try:
                im = Image.open(h["original"]).convert("RGB")
                im.thumbnail((TW, TH))
                sheet.paste(im, (x + (TW - im.width) // 2, y + 34 + (TH - im.height) // 2))
            except Exception:
                d.text((x + 10, y + 120), "could not open", font=fs, fill=(200, 80, 80))
            col = (60, 220, 120) if c + 1 == chosen else (230, 230, 230)
            if c + 1 == chosen:
                d.rectangle([x - 3, y + 31, x + TW + 3, y + 34 + TH + 3], outline=col, width=4)
            d.text((x + 8, y + 40), f"{c + 1}", font=f, fill=col, stroke_width=3, stroke_fill=(0, 0, 0))
            d.text((x, y + 38 + TH), f"{h.get('width', 0)}x{h.get('height', 0)}  {h.get('license', '')[:22]}  "
                                     f"{h.get('source', '')}", font=fs, fill=(200, 200, 200))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def run_stage(cfg, only: str | None = None, force: bool = False) -> dict:
    """For each photo moment: gather big, commercially usable candidates, keep the chosen one
    (`"pick": n` in the plan, default 1) and draw a numbered contact sheet per reel in assets/sheets/."""
    plan = planlib.approved(cfg)
    adir = cfg.path("paths.assets") / "images"
    man_path = cfg.path("paths.assets") / "manifest.json"
    manifest = read_json(man_path) if man_path.exists() else {}
    min_w = int(cfg.at("assets.min_width", 2400))
    n_keep = int(cfg.at("assets.candidates", 5))
    for reel in planlib.selected(plan, only):
        entry = manifest.setdefault(reel["id"], {})
        sheet_rows = []
        for k, vm in enumerate(reel.get("visual_moments", [])):
            if vm.get("type") not in ("image", "broll"):
                continue
            key = str(k)
            user = sorted((adir / reel["id"]).glob(f"{k}.*"))
            if user:
                w, h = _real_size(user[0])
                entry[key] = {"file": str(_cutout(user[0])) if _wants_cut(cfg, vm, user[0].suffix) else str(user[0]),
                              "original": str(user[0]), "source": "you", "license": "your file", "phrase": vm["phrase"],
                              "width": w, "height": h}
                continue
            query = vm.get("query") or vm["phrase"]
            backdrop = vm.get("style") == "backdrop"
            old = entry.get(key, {})
            if (not force and old.get("query") == query and old.get("candidates")
                    and all(Path(c["original"]).exists() for c in old["candidates"])):
                cands = old["candidates"]
            else:
                hits = []
                if vm.get("commons"):
                    try:
                        hits += _commons_file(vm["commons"])
                    except Exception as exc:
                        log.warn("Could not fetch %s from Wikimedia: %s", vm["commons"], exc)
                elif vm.get("url"):
                    hits.append({"url": vm["url"], "page": vm.get("page", vm["url"]), "license": vm.get("license", "check"),
                                 "credit": vm.get("credit", ""), "source": "picked by Claude"})
                for name in cfg.at("assets.sources", ["wikimedia", "openverse"]):
                    try:
                        found = SOURCES[name](query)
                    except Exception as exc:
                        log.warn("%s search failed for '%s': %s", name, query, exc)
                        continue
                    hits += rank(found, min_w, backdrop)
                picked_first = hits[:1] if (vm.get("commons") or vm.get("url")) else []
                rest = [h for h in hits if h not in picked_first]
                cands = []
                for h in picked_first + rest:
                    if len(cands) >= n_keep:
                        break
                    ext = ".png" if h["url"].lower().split("?")[0].endswith(".png") else ".jpg"
                    dest = adir / reel["id"] / f"web_{k}_{len(cands) + 1}{ext}"
                    if dest.exists():
                        dest.unlink()
                    try:
                        _download(h["url"], dest)
                    except Exception as exc:
                        log.warn("download failed (%s): %s", h["url"][:80], exc)
                        continue
                    w, hh = _real_size(dest)
                    if not w:
                        continue
                    cands.append({**h, "original": str(dest), "width": w, "height": hh})
            if not cands:
                log.warn("%s: no image found for '%s'. A motion-graphic fallback will be used.", reel["id"], query)
                entry[key] = {"file": None, "phrase": vm["phrase"], "query": query, "fallback": "kinetic text"}
                continue
            pick = min(max(int(vm.get("pick", 1)), 1), len(cands))
            chosen = cands[pick - 1]
            src = Path(chosen["original"])
            file = _cutout(src) if _wants_cut(cfg, vm, src.suffix) else src
            risky = any(re.search(rf"\b{re.escape(w)}\b", query.lower()) for w in RISKY) \
                or chosen.get("trademark") or vm.get("trademark")
            small = (chosen.get("width") or 0) < min_w and backdrop
            entry[key] = {**chosen, "file": str(file), "phrase": vm["phrase"], "query": query, "pick": pick,
                          "candidates": cands,
                          "warning": "; ".join(x for x in (
                              "May be copyrighted (logo/news/film/person). Check before posting." if risky else "",
                              f"Only {chosen.get('width')} px wide (below {min_w})." if small else "") if x)}
            sheet_rows.append((f"moment {k}: {vm.get('label', vm['phrase'])}  (query: {query})", cands, pick))
            log.info("%s: '%s' -> #%d %dx%d %s (%s)%s", reel["id"], query, pick, chosen.get("width", 0),
                     chosen.get("height", 0), chosen["source"], chosen["license"],
                     "  ⚠ " + entry[key]["warning"] if entry[key]["warning"] else "")
        sheet = contact_sheet(reel["id"], sheet_rows, cfg.path("paths.assets") / "sheets" / f"{reel['id']}.jpg")
        if sheet:
            log.info("%s: candidate sheet %s (set \"pick\": n on a moment to change the photo)", reel["id"], sheet)
    write_json(man_path, manifest)
    return manifest
