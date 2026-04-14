"""Persist user-adjustable state to ``spot_key_config.json``.

The file lives in the project root next to the package, is human-readable,
and is gitignored. It stores the keyboard shortcuts, the current window
diameter, and the last-known window position so the overlay re-appears
exactly where the user left it.

Old config files that only contain ``shortcuts`` are still accepted — the
loader treats missing fields as "use the default".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pynput.keyboard import Key

from .models import Shortcut

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "spot_key_config.json"


@dataclass(frozen=True)
class SavedState:
    """Everything the app persists between runs.

    Any field may be ``None`` if the user has not customised that setting
    yet (or if we are loading an older, narrower config file).
    """

    shortcuts: tuple[Shortcut, ...] | None = None
    diameter: int | None = None
    position: tuple[int, int] | None = None


def _serialise_key(k: Key | str) -> str:
    return f"Key.{k.name}" if isinstance(k, Key) else k


def _deserialise_key(raw: str) -> Key | str:
    return getattr(Key, raw[4:]) if raw.startswith("Key.") else raw


def save_state(state: SavedState) -> None:
    """Write *state* to the config file, replacing any existing contents."""
    data: dict[str, object] = {}
    if state.shortcuts is not None:
        data["shortcuts"] = [
            {
                "label": sc.label,
                "keys": [_serialise_key(k) for k in sc.keys],
                "color": sc.color,
                "hover_color": sc.hover_color,
            }
            for sc in state.shortcuts
        ]
    if state.diameter is not None:
        data["diameter"] = state.diameter
    if state.position is not None:
        data["position"] = {"x": state.position[0], "y": state.position[1]}
    _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_state() -> SavedState:
    """Return persisted state, or an empty ``SavedState`` if none exists."""
    if not _CONFIG_PATH.exists():
        return SavedState()
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return SavedState()

    shortcuts: tuple[Shortcut, ...] | None = None
    if isinstance(raw.get("shortcuts"), list):
        try:
            shortcuts = tuple(
                Shortcut(
                    label=item["label"],
                    keys=tuple(_deserialise_key(k) for k in item["keys"]),
                    color=item["color"],
                    hover_color=item["hover_color"],
                )
                for item in raw["shortcuts"]
            )
        except (KeyError, AttributeError, TypeError):
            shortcuts = None

    diameter = raw.get("diameter") if isinstance(raw.get("diameter"), int) else None

    position: tuple[int, int] | None = None
    pos_raw = raw.get("position")
    if isinstance(pos_raw, dict) and isinstance(pos_raw.get("x"), int) \
            and isinstance(pos_raw.get("y"), int):
        position = (pos_raw["x"], pos_raw["y"])

    return SavedState(shortcuts=shortcuts, diameter=diameter, position=position)
