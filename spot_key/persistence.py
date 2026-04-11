"""Save and load shortcut configuration to/from a JSON file.

Shortcuts are stored in ``spot_key_config.json`` in the project root.
The file is human-readable and gitignored.  Each shortcut records its
label, pynput key names (``"Key.ctrl_l"``, ``"q"``, etc.), and colours.
"""

from __future__ import annotations

import json
from pathlib import Path

from pynput.keyboard import Key

from .models import Shortcut

# Config lives next to the package directory (i.e. the project root).
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "spot_key_config.json"


def save_shortcuts(shortcuts: tuple[Shortcut, ...]) -> None:
    """Persist *shortcuts* to ``spot_key_config.json``."""
    data = [
        {
            "label": sc.label,
            "keys": [
                f"Key.{k.name}" if isinstance(k, Key) else k
                for k in sc.keys
            ],
            "color": sc.color,
            "hover_color": sc.hover_color,
        }
        for sc in shortcuts
    ]
    _CONFIG_PATH.write_text(
        json.dumps({"shortcuts": data}, indent=2),
        encoding="utf-8",
    )


def load_shortcuts() -> tuple[Shortcut, ...] | None:
    """Load shortcuts from disk, returning ``None`` if missing or corrupt."""
    if not _CONFIG_PATH.exists():
        return None
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        result: list[Shortcut] = []
        for item in raw["shortcuts"]:
            keys = tuple(
                getattr(Key, k[4:]) if k.startswith("Key.") else k
                for k in item["keys"]
            )
            result.append(Shortcut(
                label=item["label"],
                keys=keys,
                color=item["color"],
                hover_color=item["hover_color"],
            ))
        return tuple(result)
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return None
