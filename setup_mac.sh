#!/usr/bin/env bash
# One-time setup on your Apple Silicon Mac. Run from the HemuReelCreations folder:  bash setup_mac.sh
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg; else echo "Install Homebrew (https://brew.sh) then: brew install ffmpeg"; exit 1; fi
fi
# libass for subtitles (the default Homebrew ffmpeg dropped it), libraqm so Telugu GFX text shapes correctly
if command -v brew >/dev/null; then
  ffmpeg -hide_banner -filters 2>/dev/null | grep -q " ass " || brew install ffmpeg-full || true
  brew list libraqm >/dev/null 2>&1 || brew install libraqm || true
fi
PY=$(command -v python3.11 || command -v python3.12 || command -v python3)
echo "Using $PY ($($PY --version))"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-mac.txt || {
  echo "Some optional extras failed to install. Installing the core only; run 'hrc doctor' to see what is missing."
  .venv/bin/pip install -q -r requirements.txt
}
.venv/bin/pip install -q -e .
.venv/bin/hrc doctor || true
echo "Done. Activate with: source .venv/bin/activate"
