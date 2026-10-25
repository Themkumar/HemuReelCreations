"""Plain-English logging to the console and to cache/hrc.log."""
from __future__ import annotations

import logging
from pathlib import Path

_LOG = logging.getLogger("hrc")


def setup(cache_dir: Path, verbose: bool = False) -> logging.Logger:
    if _LOG.handlers:
        return _LOG
    _LOG.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    _LOG.addHandler(console)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fileh = logging.FileHandler(cache_dir / "hrc.log", encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOG.addHandler(fileh)
    return _LOG


def get() -> logging.Logger:
    return _LOG


def info(msg: str, *a):
    _LOG.info(msg, *a)


def warn(msg: str, *a):
    _LOG.warning("WARNING: " + msg, *a)


def debug(msg: str, *a):
    _LOG.debug(msg, *a)
