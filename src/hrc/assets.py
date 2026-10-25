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


def _openverse(q):
    r = requests.get("https://api.openverse.org/v1/images/", params={"q": q, "page_size": 5, "license_type": "all-cc"},
                     headers=UA, timeout=20)
    r.raise_for_status()
    return [{"url": x["url"], "page": x.get("foreign_landing_url"), "license": f"{x.get('license')} {x.get('license_version', '')}".strip(),
             "credit": x.get("creator") or "", "source": "openverse"} for x in r.json().get("results", [])]


def _wikimedia(q):
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"filetype:bitmap {q}",
        "gsrnamespace": 6, "gsrlimit": 5, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1200},
        headers=UA, timeout=20)
    r.raise_for_status()
    out = []
    for p in (r.json().get("query", {}).get("pages", {}) or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        out.append({"url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                    "license": meta.get("LicenseShortName", {}).get("value", "see page"),
                    "credit": meta.get("Artist", {}).get("value", ""), "source": "wikimedia"})
    return out


def _pexels(q):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.pexels.com/v1/search", params={"query": q, "per_page": 5},
                     headers={"Authorization": key}, timeout=20)
    r.raise_for_status()
    return [{"url": x["src"]["large"], "page": x["url"], "license": "Pexels License", "credit": x["photographer"],
             "source": "pexels"} for x in r.json().get("photos", [])]


def _pixabay(q):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/", params={"key": key, "q": q, "per_page": 5, "safesearch": "true"}, timeout=20)
    r.raise_for_status()
    return [{"url": x["largeImageURL"], "page": x["pageURL"], "license": "Pixabay Content License", "credit": x["user"],
             "source": "pixabay"} for x in r.json().get("hits", [])]


def _commons_file(name: str):
    """A specific Wikimedia Commons file picked by Claude (e.g. 'File:Italy location map.svg'), as a 900px PNG."""
    title = name if name.startswith("File:") else f"File:{name}"
    r = requests.get("https://commons.wikimedia.org/w/api.php", params={
        "action": "query", "format": "json", "titles": title, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 900}, headers=UA, timeout=20)
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    ii = (page.get("imageinfo") or [{}])[0]
    meta = ii.get("extmetadata", {})
    if not ii:
        return []
    return [{"url": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
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


def run_stage(cfg, only: str | None = None, force: bool = False) -> dict:
    plan = planlib.approved(cfg)
    adir = cfg.path("paths.assets") / "images"
    man_path = cfg.path("paths.assets") / "manifest.json"
    manifest = read_json(man_path) if man_path.exists() else {}
    for reel in planlib.selected(plan, only):
        entry = manifest.setdefault(reel["id"], {})
        for k, vm in enumerate(reel.get("visual_moments", [])):
            if vm.get("type") not in ("image", "broll"):
                continue
            key = str(k)
            user = sorted((adir / reel["id"]).glob(f"{k}.*"))
            if user:
                entry[key] = {"file": str(_cutout(user[0])) if cfg.at("assets.remove_background") else str(user[0]),
                              "source": "you", "license": "your file", "phrase": vm["phrase"]}
                continue
            if key in entry and Path(entry[key].get("file", "")).exists() and not force:
                continue
            query = vm.get("query") or vm["phrase"]
            found = None
            if vm.get("commons"):
                try:
                    found = (_commons_file(vm["commons"]) or [None])[0]
                except Exception as exc:
                    log.warn("Could not fetch %s from Wikimedia: %s", vm["commons"], exc)
            elif vm.get("url"):
                found = {"url": vm["url"], "page": vm.get("page", vm["url"]), "license": vm.get("license", "check"),
                         "credit": vm.get("credit", ""), "source": "picked by Claude"}
            for name in ([] if found else cfg.at("assets.sources", ["openverse", "wikimedia"])):
                try:
                    hits = [h for h in SOURCES[name](query) if h.get("url")]
                except Exception as exc:
                    log.warn("%s search failed for '%s': %s", name, query, exc)
                    continue
                if hits:
                    found = hits[0]
                    break
            if not found:
                log.warn("%s: no image found for '%s'. A motion-graphic fallback will be used.", reel["id"], query)
                entry[key] = {"file": None, "phrase": vm["phrase"], "query": query, "fallback": "kinetic text"}
                continue
            ext = ".png" if found["url"].lower().endswith(".png") else ".jpg"
            dest = adir / reel["id"] / f"web_{k}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(requests.get(found["url"], headers=UA, timeout=60).content)
            file = _cutout(dest) if cfg.at("assets.remove_background") else dest
            risky = any(w in query.lower() for w in RISKY) or found.get("trademark") or vm.get("trademark")
            entry[key] = {**found, "file": str(file), "phrase": vm["phrase"], "query": query,
                          "warning": "May be copyrighted (logo/news/film/person). Check before posting." if risky else ""}
            log.info("%s: '%s' -> %s (%s, %s)%s", reel["id"], query, found["source"], found["license"],
                     found.get("page", ""), "  ⚠ check copyright" if risky else "")
    write_json(man_path, manifest)
    return manifest
