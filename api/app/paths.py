"""Filesystem anchors: config and prompts live next to the `app` package (also in the image)."""

from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = API_ROOT / "config"
PROMPTS_DIR = API_ROOT / "prompts"
