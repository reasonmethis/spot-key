"""Spot Key — a floating pie-chart widget that triggers keyboard shortcuts on hover.

Run with ``python -m spot_key`` or use the ``spot-key`` console entry point.
"""

from .app import SpotKey, main
from .models import COLOR_PALETTE, Config, Shortcut

__all__ = ["COLOR_PALETTE", "Config", "Shortcut", "SpotKey", "main"]
