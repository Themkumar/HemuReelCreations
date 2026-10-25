"""hrc command line. Run `hrc --help` or `hrc <command> --help`."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__, config, log


def cmd_doctor(cfg, a):
    from .util import is_apple_silicon, run

    ok = True
    def line(good, text):
        nonlocal ok
        ok &= bool(good) or text.startswith("(optional)")
        print(("  OK   " if good else "  --   ") + text)
    print(f"HemuReelCreations {__version__}  ({'Apple Silicon' if is_apple_silicon() else sys.platform})")
    ff = shutil.which("ffmpeg")
    line(ff, f"ffmpeg: {ff or 'missing (brew install ffmpeg)'}")
    if ff:
        filters = run(["ffmpeg", "-hide_banner", "-filters"]).stdout
        enc = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        line(" ass " in filters, "ffmpeg has libass (karaoke subtitles)")
        line("sidechaincompress" in filters, "ffmpeg has sidechaincompress (music ducking)")
        line("h264_videotoolbox" in enc, "(optional) VideoToolbox hardware encoder")
    for mod, why in [("numpy", "core"), ("cv2", "core"), ("PIL", "core"), ("yaml", "core"), ("requests", "core"),
                     ("torch", "(optional) matting on MPS"), ("whisperx", "(optional) best Telugu alignment"),
                     ("mlx_whisper", "(optional) fast Whisper on Apple Silicon"), ("mediapipe", "(optional) face + matte fallback"),
                     ("rembg", "(optional) image cut-outs"), ("anthropic", "(optional) API mode")]:
        try:
            m = __import__(mod)
            extra = ""
            if mod == "torch":
                extra = f", MPS={'yes' if m.backends.mps.is_available() else 'no'}"
            line(True, f"{mod}{extra}  [{why}]")
        except Exception:
            line(False, f"{why if why.startswith('(optional)') else ''} {mod} not installed".strip())
    try:
        from PIL import features
        line(features.check("raqm"), "Pillow can shape Telugu/Indic text for graphics (libraqm)  [brew install libraqm]")
    except Exception:
        pass
    import platform as _pf
    if _pf.system() == "Darwin":
        from . import matte as _m
        line(_m._vision_binary() is not None, "Apple Vision person cut-out (Swift helper)  [xcode-select --install]")
    fonts = cfg.path("paths.assets") / "fonts"
    for fam in ("NotoSansTelugu", "NotoSansDevanagari", "NotoSansTamil", "NotoSansKannada", "NotoSans"):
        line(any(fonts.glob(f"{fam}-*.ttf")), f"font {fam}")
    for key in ("input.video", "input.srt"):
        p = cfg.path(key)
        line(p.exists(), f"{key}: {p}")
    du = shutil.disk_usage(cfg.root)
    line(du.free > 20e9, f"free disk: {du.free / 1e9:.0f} GB (want 20+ GB)")
    return 0 if ok else 1


def cmd_ingest(cfg, a):
    from . import ingest
    ingest.run_stage(cfg, a.force)


def cmd_align(cfg, a):
    from . import align
    align.run_stage(cfg, a.force)


def cmd_select(cfg, a):
    from . import selection
    if a.action == "prepare":
        selection.prepare(cfg)
    else:
        return 1 if selection.check(cfg) else 0


def cmd_approve(cfg, a):
    from . import selection
    selection.approve(cfg)


def cmd_translate(cfg, a):
    from . import translate
    (translate.prepare if a.action == "prepare" else translate.build)(cfg, a.reel)


def cmd_assets(cfg, a):
    from . import assets
    assets.run_stage(cfg, a.reel, a.force)


def _render(cfg, a, preview):
    from . import export, plan as planlib, render
    from .util import read_json
    plan = planlib.approved(cfg)
    words = read_json(cfg.dir("cache") / "words.json")
    man = cfg.path("paths.assets") / "manifest.json"
    manifest = read_json(man) if man.exists() else {}
    formats = (a.formats.split(",") if a.formats else cfg.at("reel.formats", ["reel"]))
    for reel in planlib.selected(plan, a.reel):
        for fmt in formats:
            rdir = render.render_reel(cfg, reel, words, manifest, preview=preview, force=a.force, fmt=fmt)
            export.export_reel(cfg, reel, rdir, preview=preview, langs=(a.langs.split(",") if a.langs else None))
            if not preview and fmt == "reel" and cfg.at("export.resolve_pack", True):
                from . import resolve
                for lang in (a.resolve_langs.split(",") if getattr(a, "resolve_langs", None)
                             else cfg.at("export.resolve_langs", ["te"])):
                    resolve.build_pack(cfg, reel, rdir, lang)


def cmd_resolve(cfg, a):
    """Rebuild the layered Resolve pack from an existing render (after editing fonts or colours)."""
    from . import plan as planlib, resolve, render
    plan = planlib.approved(cfg)
    for reel in planlib.selected(plan, a.reel):
        rdir = cfg.dir("renders") / (reel["id"] + render.suffix("reel", False))
        if not (rdir / "render.json").exists():
            raise FileNotFoundError(f"{reel['id']} is not rendered yet. Run `hrc render --reel {reel['id']}` first.")
        for lang in (a.lang.split(",") if a.lang else cfg.at("export.resolve_langs", ["te"])):
            resolve.build_pack(cfg, reel, rdir, lang)


def cmd_render(cfg, a):
    _render(cfg, a, preview=False)


def cmd_preview(cfg, a):
    a.langs = a.langs or "te"
    _render(cfg, a, preview=True)


def cmd_qa(cfg, a):
    from . import qa
    qa.run_stage(cfg, a.reel, a.preview)


def cmd_sample(cfg, a):
    from . import sample
    sample.make_sample(cfg, a.start, a.minutes)


def cmd_demo_plan(cfg, a):
    from . import sample
    sample.demo_plan(cfg, a.length)


def main(argv=None):
    p = argparse.ArgumentParser(prog="hrc", description="HemuReelCreations: podcast -> reels with 5-language karaoke subtitles")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, reel=False, force=False):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if reel:
            sp.add_argument("--reel", help="only these reel ids, comma separated")
        if force:
            sp.add_argument("--force", action="store_true", help="ignore cache and redo")
        return sp

    add("doctor", cmd_doctor, "check ffmpeg, Python packages, fonts, GPU, inputs")
    add("ingest", cmd_ingest, "Stage A: parse SRT, extract audio, find shots and faces", force=True)
    add("align", cmd_align, "Stage B: word-level timing -> cache/words.json", force=True)
    sp = add("select", cmd_select, "Stage C: prepare transcript for reel picking, or check reels_plan.json")
    sp.add_argument("action", choices=["prepare", "check"])
    add("approve", cmd_approve, "approve reels_plan.json so rendering can start")
    sp = add("translate", cmd_translate, "Stage D: prepare chunks / build subtitle files", reel=True)
    sp.add_argument("action", choices=["prepare", "build"])
    add("assets", cmd_assets, "Stage E: find images for visual moments", reel=True, force=True)
    for name, fn, h in (("render", cmd_render, "Stages F-H: full-quality render and export"),
                        ("preview", cmd_preview, "fast 540x960 draft (Telugu subtitles by default)")):
        sp = add(name, fn, h, reel=True, force=True)
        sp.add_argument("--langs", help="comma separated, e.g. te,en")
        sp.add_argument("--resolve-langs", dest="resolve_langs", help="languages for the layered Resolve pack (default: export.resolve_langs)")
        sp.add_argument("--formats", help="reel,landscape,portrait (default: reel.formats in config.yaml; reel = landscape video in a 9:16 frame)")
    sp = add("resolve", cmd_resolve, "rebuild the layered DaVinci Resolve pack (every graphic, caption, sound on its own track)", reel=True)
    sp.add_argument("--lang", help="comma separated languages, e.g. te,en (default: export.resolve_langs)")
    sp = add("qa", cmd_qa, "write qa_report.md", reel=True)
    sp.add_argument("--preview", action="store_true")
    sp = add("sample", cmd_sample, "cut a short sample workspace into sample/")
    sp.add_argument("--start", default="00:10:00")
    sp.add_argument("--minutes", type=float, default=3)
    sp = add("demo-plan", cmd_demo_plan, "auto-pick one demo reel (for the sample test)")
    sp.add_argument("--length", type=float, default=45)

    a = p.parse_args(argv)
    cfg = config.load(a.config)
    log.setup(cfg.dir("cache"), a.verbose)
    try:
        return a.fn(cfg, a) or 0
    except (FileNotFoundError, PermissionError, KeyError, RuntimeError) as exc:
        log.get().error("Stopped: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
