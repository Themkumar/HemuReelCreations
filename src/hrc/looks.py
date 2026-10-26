"""Per-reel text looks. A reel picks one with "look" in reels_plan.json; the look's settings
(config.yaml `looks.<name>`) are laid over the main config for that reel only.

- classic: v0.7 landscape style: Noto Sans karaoke captions (yellow highlight), stacked Anton words
- glow:    v0.8 style: small white words + the key word big in a glowing yellow italic serif
- slab:    v0.8.2 reference style: one huge red slab word behind the speaker, one-word colour captions
"""
from __future__ import annotations

import copy

from .config import Config, _merge

DEFAULT_LOOKS = {
    "slab": {"subtitles": {"style": "punch", "punch": {"ref_style": True}},
             "gfx": {"hero_word": True, "fullpic_images": True}},
    "glow": {"subtitles": {"style": "punch", "punch": {"ref_style": False}},
             "gfx": {"hero_word": False, "fullpic_images": True}},
    "classic": {"subtitles": {"style": "classic", "fonts": {"en": "Noto Sans"}},
                "gfx": {"hero_word": False, "fullpic_images": False}},
}


def names(cfg) -> list[str]:
    return list({**DEFAULT_LOOKS, **(cfg.at("looks") or {})})


def for_reel(cfg, reel: dict):
    """The config to use for this reel (the main config when the reel has no look)."""
    look = reel.get("look")
    if not look:
        return cfg
    over = copy.deepcopy(DEFAULT_LOOKS.get(look, {}))
    _merge(over, copy.deepcopy((cfg.at("looks") or {}).get(look, {})))
    if not over:
        raise ValueError(f"{reel.get('id')}: unknown look '{look}' (choose from {', '.join(names(cfg))})")
    out = Config(_merge(copy.deepcopy(dict(cfg)), over), cfg.root)
    out.look = look
    return out
