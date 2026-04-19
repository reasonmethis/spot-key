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
# Actions — the atomic operations that make up a shortcut sequence
# ---------------------------------------------------------------------------

SUPERSAMPLE = 4  # Render at Nx resolution, downsample for smooth edges.


@dataclass(frozen=True)
class KeyComboAction:
    """Press a set of keys in order, then release them in reverse."""

    keys: tuple[Key | str, ...]


@dataclass(frozen=True)
class SleepAction:
    """Pause the sequence for a fixed number of seconds."""

    seconds: float


@dataclass(frozen=True)
class MouseClickAction:
    """Move the mouse to an absolute screen coordinate and left-click."""

    x: int
    y: int


Action = KeyComboAction | SleepAction | MouseClickAction


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Shortcut:
    """A single pie-chart segment: its action sequence and display colours.

    Attributes:
        label:       Human-readable summary shown in the UI (e.g. ``"Ctrl+Q"``
                     or ``"Ctrl+C \u2192 Sleep 0.5s \u2192 Ctrl+V"``).
        actions:     Ordered sequence of actions to execute when the slice
                     fires. May be a single key combo (the common case) or
                     any mix of key combos, sleeps, and mouse clicks.
        color:       Hex colour for the slice at rest.
        hover_color: Hex colour shown when the shortcut fires.
    """

    label: str
    actions: tuple[Action, ...]
    color: str
    hover_color: str


def _kc(*keys: Key | str) -> KeyComboAction:
    return KeyComboAction(keys=tuple(keys))


_DEFAULT_SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut(
        "hey",
        (_kc("h"), _kc("e"), _kc("y")),
        "#4A90D9", "#2563EB",
    ),
    Shortcut(
        "Alt+Tab",
        (_kc(Key.alt_l, Key.tab),),
        "#10B981", "#059669",
    ),
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
