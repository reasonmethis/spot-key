"""Persist user-adjustable state to ``spot_key_config.json``.

When running from source the file lives next to the package (project
root), is human-readable, and is gitignored. When installed to a
read-only location (e.g. Program Files via the installer) it falls
back to ``%APPDATA%/Spot Key/spot_key_config.json``.

Backwards compatibility
-----------------------
Older config files stored each shortcut as a flat ``"keys"`` list — one
key combo per shortcut. Such shortcuts are still loaded and silently
upgraded to a single-action sequence containing that combo. Fields that
are missing entirely (``diameter``, ``position``, ``shortcuts``) fall
back to the application defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pynput.keyboard import Key

from .models import (
    Action,
    KeyComboAction,
    MouseClickAction,
    Shortcut,
    SleepAction,
)

_CONFIG_NAME = "spot_key_config.json"


def _resolve_config_path() -> Path:
    """Pick the config file location.

    If ``pyproject.toml`` exists next to the package we're in a source
    checkout — store the config in the project root (gitignored).
    Otherwise we're an installed copy (Nuitka exe, pipx, etc.) — use
    ``%APPDATA%/Spot Key/``.
    """
    project_root = Path(__file__).resolve().parent.parent
    if (project_root / "pyproject.toml").exists():
        return project_root / _CONFIG_NAME
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "Spot Key"
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata / _CONFIG_NAME


_CONFIG_PATH = _resolve_config_path()


@dataclass(frozen=True)
class SavedState:
    """Everything the app persists between runs.

    Any field may be ``None`` if the user has not customised that setting
    yet (or if we are loading an older, narrower config file).
    """

    shortcuts: tuple[Shortcut, ...] | None = None
    diameter: int | None = None
    opacity: float | None = None
    position: tuple[int, int] | None = None


# ── Key (de)serialisation ───────────────────────────────────────────────────


def _serialise_key(k: Key | str) -> str:
    return f"Key.{k.name}" if isinstance(k, Key) else k


def _deserialise_key(raw: str) -> Key | str:
    return getattr(Key, raw[4:]) if raw.startswith("Key.") else raw


# ── Action (de)serialisation ────────────────────────────────────────────────


def _serialise_action(action: Action) -> dict[str, Any]:
    if isinstance(action, KeyComboAction):
        return {
            "type": "key",
            "keys": [_serialise_key(k) for k in action.keys],
        }
    if isinstance(action, SleepAction):
        return {"type": "sleep", "seconds": action.seconds}
    if isinstance(action, MouseClickAction):
        return {"type": "click", "x": action.x, "y": action.y}
    raise TypeError(f"Unknown action type: {type(action).__name__}")


def _deserialise_action(raw: dict[str, Any]) -> Action:
    kind = raw.get("type")
    if kind == "key":
        return KeyComboAction(
            keys=tuple(_deserialise_key(k) for k in raw["keys"]),
        )
    if kind == "sleep":
        return SleepAction(seconds=float(raw["seconds"]))
    if kind == "click":
        return MouseClickAction(x=int(raw["x"]), y=int(raw["y"]))
    raise ValueError(f"Unknown action kind: {kind!r}")


def _deserialise_shortcut(item: dict[str, Any]) -> Shortcut:
    """Parse one shortcut, accepting the new ``actions`` format or the legacy
    flat ``keys`` format (interpreted as a single key combo)."""
    if "actions" in item:
        actions: tuple[Action, ...] = tuple(
            _deserialise_action(a) for a in item["actions"]
        )
    else:
        actions = (
            KeyComboAction(
                keys=tuple(_deserialise_key(k) for k in item["keys"]),
            ),
        )
    return Shortcut(
        label=item["label"],
        actions=actions,
        color=item["color"],
        hover_color=item["hover_color"],
    )


# ── Top-level save / load ───────────────────────────────────────────────────


def save_state(state: SavedState) -> None:
    """Write *state* to the config file, replacing any existing contents."""
    data: dict[str, object] = {}
    if state.shortcuts is not None:
        data["shortcuts"] = [
            {
                "label": sc.label,
                "actions": [_serialise_action(a) for a in sc.actions],
                "color": sc.color,
                "hover_color": sc.hover_color,
            }
            for sc in state.shortcuts
        ]
    if state.diameter is not None:
        data["diameter"] = state.diameter
    if state.opacity is not None:
        data["opacity"] = state.opacity
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
                _deserialise_shortcut(item) for item in raw["shortcuts"]
            )
        except (KeyError, AttributeError, TypeError, ValueError):
            shortcuts = None

    diameter = raw.get("diameter") if isinstance(raw.get("diameter"), int) else None

    opacity_raw = raw.get("opacity")
    opacity = float(opacity_raw) if isinstance(opacity_raw, (int, float)) else None

    position: tuple[int, int] | None = None
    pos_raw = raw.get("position")
    if isinstance(pos_raw, dict) and isinstance(pos_raw.get("x"), int) \
            and isinstance(pos_raw.get("y"), int):
        position = (pos_raw["x"], pos_raw["y"])

    return SavedState(shortcuts=shortcuts, diameter=diameter, opacity=opacity, position=position)
