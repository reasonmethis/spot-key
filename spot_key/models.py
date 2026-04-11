"""Data models and colour palette for Spot Key."""

from __future__ import annotations

from dataclasses import dataclass, field

from pynput.keyboard import Key

# ---------------------------------------------------------------------------
# Colour palette — (normal, hover, display-name) tuples used by the settings
# dialog colour picker and as defaults for new shortcuts.
# ---------------------------------------------------------------------------

COLOR_PALETTE: tuple[tuple[str, str, str], ...] = (
    ("#4A90D9", "#2563EB", "Blue"),
    ("#10B981", "#059669", "Green"),
    ("#F59E0B", "#D97706", "Amber"),
    ("#EF4444", "#DC2626", "Red"),
    ("#8B5CF6", "#7C3AED", "Purple"),
    ("#EC4899", "#DB2777", "Pink"),
    ("#06B6D4", "#0891B2", "Cyan"),
    ("#F97316", "#EA580C", "Orange"),
)

# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------

SUPERSAMPLE = 4  # Render at Nx resolution, downsample for smooth edges.


@dataclass(frozen=True)
class Shortcut:
    """A single pie-chart segment: its key combination and display colours.

    Attributes:
        label:       Human-readable name shown in the UI (e.g. ``"Ctrl+Q"``).
        keys:        Sequence of pynput ``Key`` enums and/or single-char
                     strings that are pressed in order and released in reverse.
        color:       Hex colour for the slice at rest.
        hover_color: Hex colour shown when the shortcut fires.
    """

    label: str
    keys: tuple[Key | str, ...]
    color: str
    hover_color: str


_DEFAULT_SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut("Ctrl+Q", (Key.ctrl_l, "q"), "#4A90D9", "#2563EB"),
    Shortcut("Ctrl+C", (Key.ctrl_l, "c"), "#10B981", "#059669"),
    Shortcut("Enter",  (Key.enter,),      "#F59E0B", "#D97706"),
)


@dataclass(frozen=True)
class Config:
    """All tunables in one place.

    Frozen so that updates go through ``dataclasses.replace()``, keeping
    the rest of the app from accidentally mutating shared state.
    """

    shortcuts: tuple[Shortcut, ...] = field(
        default_factory=lambda: _DEFAULT_SHORTCUTS,
    )
    diameter: int = 160
    outline_color: str = "#374151"
    menu_zone_size: int = 28
    menu_zone_color: str = "#6B7280"
    menu_zone_hover_color: str = "#9CA3AF"
    shortcut_hover_ms: int = 330
