"""Load config.yaml and resolve paths relative to the project root."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml


class Config(dict):
    """A dict with dotted lookups: cfg.get_path("paths.cache")."""

    def __init__(self, data: dict, root: Path):
        super().__init__(data)
        self.root = root

    def at(self, dotted: str, default=None):
        node = self
        for key in dotted.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def path(self, dotted: str) -> Path:
        value = self.at(dotted)
        if value is None:
            raise KeyError(f"config is missing '{dotted}'")
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    def dir(self, name: str) -> Path:
        d = self.path(f"paths.{name}")
        d.mkdir(parents=True, exist_ok=True)
        return d


def load(config_file: str | Path = "config.yaml", overrides: dict | None = None) -> Config:
    config_file = Path(config_file).resolve()
    with open(config_file, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if overrides:
        data = _merge(copy.deepcopy(data), overrides)
    return Config(data, config_file.parent)


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base
