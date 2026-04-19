"""Helpers for converting between pynput key objects and human-readable labels."""

from __future__ import annotations

from pynput.keyboard import Key, KeyCode

from .models import Action, KeyComboAction, MouseClickAction, SleepAction

# ---------------------------------------------------------------------------
# Modifier classification
# ---------------------------------------------------------------------------

MODIFIER_KEYS: frozenset[Key] = frozenset({
    Key.ctrl_l, Key.ctrl_r,
    Key.shift, Key.shift_l, Key.shift_r,
    Key.alt_l, Key.alt_r, Key.alt_gr,
    Key.cmd, Key.cmd_l, Key.cmd_r,
})

# Groups used for display: each group collapses left/right variants into one
# canonical label.
_MOD_GROUPS: tuple[tuple[frozenset[Key], str], ...] = (
    (frozenset({Key.ctrl_l, Key.ctrl_r}), "Ctrl"),
    (frozenset({Key.shift, Key.shift_l, Key.shift_r}), "Shift"),
    (frozenset({Key.alt_l, Key.alt_r, Key.alt_gr}), "Alt"),
)

# ---------------------------------------------------------------------------
# Pretty labels for non-modifier special keys
# ---------------------------------------------------------------------------

_KEY_LABELS: dict[str, str] = {
    "enter": "Enter", "esc": "Esc", "space": "Space", "tab": "Tab",
    "backspace": "Backspace", "delete": "Delete", "insert": "Insert",
    "home": "Home", "end": "End",
    "page_up": "Page Up", "page_down": "Page Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "caps_lock": "Caps Lock", "num_lock": "Num Lock",
    "print_screen": "Print Screen", "scroll_lock": "Scroll Lock",
    "pause": "Pause", "menu": "Menu",
}
for _i in range(1, 21):
    _KEY_LABELS[f"f{_i}"] = f"F{_i}"

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def keys_to_label(keys: tuple[Key | str, ...]) -> str:
    """Convert a pynput key tuple to a human-readable label like ``'Ctrl+Q'``."""
    parts: list[str] = []
    for k in keys:
        if isinstance(k, Key):
            name = k.name
            if name.startswith("ctrl"):
                parts.append("Ctrl")
            elif name.startswith("alt"):
                parts.append("Alt")
            elif name.startswith("shift"):
                parts.append("Shift")
            elif name in ("cmd", "cmd_l", "cmd_r"):
                parts.append("Win")
            elif name in _KEY_LABELS:
                parts.append(_KEY_LABELS[name])
            else:
                parts.append(name.replace("_", " ").title())
        else:
            parts.append(k.upper())
    return "+".join(parts)


def modifier_preview(held: set[Key]) -> str:
    """Return a live preview like ``'Ctrl+Shift+…'`` for currently-held modifiers."""
    parts = [label for group, label in _MOD_GROUPS if held & group]
    return "+".join(parts) + "+\u2026" if parts else "Press keys\u2026"


def build_combo(
    held_mods: set[Key], key: Key | KeyCode,
) -> tuple[Key | str, ...]:
    """Combine currently-held modifiers with *key* into a normalised key tuple.

    Modifier variants (e.g. ``Key.ctrl_r``) are collapsed to their left-hand
    canonical form.  Character keys are lowercased.  Returns an empty tuple
    if *key* cannot be identified.
    """
    keys: list[Key | str] = []
    if held_mods & {Key.ctrl_l, Key.ctrl_r}:
        keys.append(Key.ctrl_l)
    if held_mods & {Key.shift, Key.shift_l, Key.shift_r}:
        keys.append(Key.shift_l)
    if held_mods & {Key.alt_l, Key.alt_r, Key.alt_gr}:
        keys.append(Key.alt_l)

    if isinstance(key, Key):
        keys.append(key)
    elif isinstance(key, KeyCode):
        if key.char and key.char.isprintable():
            keys.append(key.char.lower())
        elif key.vk is not None and chr(key.vk).isalnum():
            # When Ctrl is held, char may be a control code — fall back to vk.
            keys.append(chr(key.vk).lower())
        else:
            return ()

    return tuple(keys)


# ---------------------------------------------------------------------------
# Action labels
# ---------------------------------------------------------------------------


def _format_seconds(s: float) -> str:
    """Format *s* seconds without trailing zeros (``0.5`` not ``0.50``)."""
    if s == int(s):
        return f"{int(s)}s"
    return f"{s:g}s"


def action_label(action: Action) -> str:
    """Return a short human-readable label for a single action."""
    if isinstance(action, KeyComboAction):
        return keys_to_label(action.keys) or "\u2014"
    if isinstance(action, SleepAction):
        return f"Sleep {_format_seconds(action.seconds)}"
    if isinstance(action, MouseClickAction):
        return f"Click ({action.x}, {action.y})"
    raise TypeError(f"Unknown action type: {type(action).__name__}")


def actions_label(actions: tuple[Action, ...]) -> str:
    """Return a joined summary label for an action sequence."""
    if not actions:
        return "(empty)"
    return " \u2192 ".join(action_label(a) for a in actions)
