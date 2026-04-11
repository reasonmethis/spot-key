"""Settings dialog for editing keyboard shortcuts.

Opens as a dark-themed ``tk.Toplevel`` with a list of shortcut rows.  Each
row has a key-capture button, colour swatches, and a delete button.

Key capture uses pynput's low-level keyboard hook (``WH_KEYBOARD_LL``)
instead of tkinter's ``<KeyPress>`` events.  This is necessary because:

1. Tkinter on Windows misreports certain Ctrl + extended-key combos
   (e.g. Ctrl+PageDown arrives as Ctrl+V).
2. Keys registered as global hotkeys by other apps (via ``RegisterHotKey``)
   are consumed before tkinter's event loop sees them.  Low-level hooks
   fire before hotkey processing, so they always see the real key.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pynput.keyboard import Key, KeyCode, Listener

from .keys import MODIFIER_KEYS, build_combo, keys_to_label, modifier_preview
from .models import COLOR_PALETTE, Shortcut

# ---------------------------------------------------------------------------
# Mutable working copy of a shortcut used during editing
# ---------------------------------------------------------------------------


@dataclass
class _ShortcutItem:
    """Transient editing state for one row in the settings dialog."""

    keys: tuple[Key | str, ...]
    label: str
    color_idx: int
    btn: tk.Button | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class SettingsDialog:
    """Modal dialog for adding, removing, and re-mapping shortcuts."""

    # -- Theme ---------------------------------------------------------------

    _BG         = "#1F2937"
    _CARD       = "#374151"
    _FG         = "#F3F4F6"
    _DIM        = "#9CA3AF"
    _BORDER     = "#4B5563"
    _BTN        = "#4B5563"
    _BTN_HV     = "#6B7280"
    _ACCENT     = "#2563EB"
    _ACCENT_HV  = "#1D4ED8"
    _CAPTURE_BG = "#7C3AED"

    _FONT       = ("Segoe UI", 10)
    _FONT_B     = ("Segoe UI", 10, "bold")
    _FONT_TITLE = ("Segoe UI", 13, "bold")

    _SWATCH_PX = 18  # colour-swatch diameter in pixels

    # -- Construction --------------------------------------------------------

    def __init__(
        self,
        parent: tk.Tk,
        shortcuts: tuple[Shortcut, ...],
        on_apply: Callable[[tuple[Shortcut, ...]], None],
    ) -> None:
        self._on_apply = on_apply
        self._items = [
            _ShortcutItem(
                keys=sc.keys,
                label=sc.label,
                color_idx=self._color_idx(sc.color),
            )
            for sc in shortcuts
        ]
        self._capturing: int | None = None
        self._held_mods: set[Key] = set()
        self._listener: Listener | None = None

        win = tk.Toplevel(parent)
        win.title("Spot Key \u2014 Settings")
        win.configure(bg=self._BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._win = win

        self._list_frame: tk.Frame  # assigned in _build_layout
        self._build_layout()

        # Centre on screen.
        win.update_idletasks()
        x = win.winfo_screenwidth() // 2 - win.winfo_width() // 2
        y = win.winfo_screenheight() // 2 - win.winfo_height() // 2
        win.geometry(f"+{x}+{y}")

        win.grab_set()
        win.focus_set()
        win.bind("<Escape>", self._on_escape)
        win.protocol("WM_DELETE_WINDOW", self._cancel)

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _color_idx(color: str) -> int:
        """Find the ``COLOR_PALETTE`` index matching *color*, defaulting to 0."""
        lo = color.lower()
        for i, (c, _, _) in enumerate(COLOR_PALETTE):
            if c.lower() == lo:
                return i
        return 0

    @staticmethod
    def _hoverable(widget: tk.Widget, normal: str, hover: str) -> None:
        """Bind mouse enter/leave to swap *widget*'s background colour."""
        widget.bind("<Enter>", lambda _: widget.configure(bg=hover))
        widget.bind("<Leave>", lambda _: widget.configure(bg=normal))

    # -- Layout --------------------------------------------------------------

    def _build_layout(self) -> None:
        pad = 16
        win = self._win

        tk.Label(
            win, text="Shortcuts", font=self._FONT_TITLE,
            bg=self._BG, fg=self._FG,
        ).pack(anchor="w", padx=pad, pady=(pad, 8))

        self._list_frame = tk.Frame(win, bg=self._BG)
        self._list_frame.pack(fill="x", padx=pad)
        self._refresh_rows()

        # "+ Add Shortcut" button
        add_frame = tk.Frame(win, bg=self._BG)
        add_frame.pack(fill="x", padx=pad, pady=(8, 0))
        add_btn = tk.Button(
            add_frame, text="+ Add Shortcut", font=self._FONT,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=12, pady=6, cursor="hand2", command=self._add,
        )
        add_btn.pack(anchor="w")
        self._hoverable(add_btn, self._BTN, self._BTN_HV)

        # Separator line
        tk.Frame(win, bg=self._BORDER, height=1).pack(
            fill="x", padx=pad, pady=(pad, 0),
        )

        # Apply / Cancel
        btn_frame = tk.Frame(win, bg=self._BG)
        btn_frame.pack(fill="x", padx=pad, pady=pad)

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", font=self._FONT,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=20, pady=8, cursor="hand2", command=self._cancel,
        )
        cancel_btn.pack(side="right", padx=(8, 0))
        self._hoverable(cancel_btn, self._BTN, self._BTN_HV)

        apply_btn = tk.Button(
            btn_frame, text="Apply", font=self._FONT_B,
            bg=self._ACCENT, fg="#FFF",
            activebackground=self._ACCENT_HV, activeforeground="#FFF",
            bd=0, padx=20, pady=8, cursor="hand2", command=self._apply,
        )
        apply_btn.pack(side="right")
        self._hoverable(apply_btn, self._ACCENT, self._ACCENT_HV)

    # -- Row rendering -------------------------------------------------------

    def _refresh_rows(self) -> None:
        """Tear down and rebuild every shortcut row."""
        for child in self._list_frame.winfo_children():
            child.destroy()

        if not self._items:
            tk.Label(
                self._list_frame,
                text="No shortcuts \u2014 click + Add Shortcut",
                font=self._FONT, bg=self._BG, fg=self._DIM,
            ).pack(pady=20)
            return

        for idx in range(len(self._items)):
            self._build_row(idx)

    def _build_row(self, idx: int) -> None:
        """Render one shortcut row: key button, colour swatches, delete."""
        item = self._items[idx]

        card = tk.Frame(
            self._list_frame, bg=self._CARD,
            highlightbackground=self._BORDER, highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 6))
        inner = tk.Frame(card, bg=self._CARD)
        inner.pack(fill="x", padx=10, pady=8)

        # Key-combo button — click to re-record
        btn = tk.Button(
            inner, text=item.label, font=self._FONT_B,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=12, pady=4, cursor="hand2", width=14, anchor="w",
            command=lambda i=idx: self._capture_start(i),
        )
        btn.pack(side="left")
        self._hoverable(btn, self._BTN, self._BTN_HV)
        item.btn = btn

        # Delete button (visually disabled when only one shortcut remains)
        is_sole = len(self._items) == 1
        del_fg = "#555" if is_sole else self._DIM
        tk.Button(
            inner, text="\u00d7", font=("Segoe UI", 14),
            bg=self._CARD, fg=del_fg,
            activebackground=self._CARD,
            activeforeground="#555" if is_sole else "#EF4444",
            bd=0, padx=4,
            cursor="arrow" if is_sole else "hand2",
            command=(lambda: None) if is_sole else (lambda i=idx: self._remove(i)),
        ).pack(side="right")

        # Colour swatches
        swatch_frame = tk.Frame(inner, bg=self._CARD)
        swatch_frame.pack(side="right", padx=(12, 8))
        s = self._SWATCH_PX
        for ci, (color, _, _) in enumerate(COLOR_PALETTE):
            canvas = tk.Canvas(
                swatch_frame, width=s, height=s, bg=self._CARD,
                highlightthickness=0, cursor="hand2",
            )
            if ci == item.color_idx:
                canvas.create_oval(1, 1, s - 1, s - 1, fill=color, outline="#FFF", width=2)
            else:
                canvas.create_oval(3, 3, s - 3, s - 3, fill=color, outline="")
            canvas.bind("<Button-1>", lambda _, i=idx, c=ci: self._pick_color(i, c))
            canvas.pack(side="left", padx=1)

    # -- Key capture (pynput low-level hook) ---------------------------------

    def _capture_start(self, idx: int) -> None:
        """Enter key-capture mode for the shortcut at *idx*."""
        self._capture_cancel()
        self._capturing = idx
        self._held_mods = set()

        btn = self._items[idx].btn
        assert btn is not None
        btn.configure(text="Press keys\u2026", bg=self._CAPTURE_BG)
        btn.bind("<Enter>", lambda _: None)   # suppress hover while capturing
        btn.bind("<Leave>", lambda _: None)

        self._listener = Listener(
            on_press=self._on_hook_press,
            on_release=self._on_hook_release,
        )
        self._listener.start()

    def _on_hook_press(self, key: Key | KeyCode) -> bool | None:
        """Low-level key-down callback (runs on the listener thread)."""
        if key in MODIFIER_KEYS:
            self._held_mods.add(key)
            self._win.after(0, self._update_mod_preview)
            return None  # keep listening

        if key == Key.esc:
            self._win.after(0, self._capture_cancel)
            return False  # stop listener

        # Non-modifier → build the combo and accept it.
        combo = build_combo(self._held_mods, key)
        if combo:
            self._win.after(0, self._finish_capture, combo)
        return False  # stop listener

    def _on_hook_release(self, key: Key | KeyCode) -> bool | None:
        """Low-level key-up callback (runs on the listener thread)."""
        if self._capturing is None:
            return False
        self._held_mods.discard(key)
        self._win.after(0, self._update_mod_preview)
        return None

    def _update_mod_preview(self) -> None:
        """Refresh the capture button to reflect currently-held modifiers."""
        if self._capturing is None:
            return
        btn = self._items[self._capturing].btn
        assert btn is not None
        btn.configure(text=modifier_preview(self._held_mods))

    def _finish_capture(self, keys: tuple[Key | str, ...]) -> None:
        """Accept *keys* as the new shortcut and exit capture mode."""
        if self._capturing is None:
            return
        item = self._items[self._capturing]
        item.keys = keys
        item.label = keys_to_label(keys)
        assert item.btn is not None
        item.btn.configure(text=item.label, bg=self._BTN)
        self._hoverable(item.btn, self._BTN, self._BTN_HV)
        self._capturing = None
        self._held_mods = set()

    def _capture_cancel(self) -> None:
        """Exit capture mode without changing the shortcut."""
        if self._capturing is None:
            return
        if self._listener is not None and self._listener.is_alive():
            self._listener.stop()
        item = self._items[self._capturing]
        assert item.btn is not None
        item.btn.configure(text=item.label, bg=self._BTN)
        self._hoverable(item.btn, self._BTN, self._BTN_HV)
        self._capturing = None
        self._held_mods = set()

    # -- List mutations ------------------------------------------------------

    def _pick_color(self, idx: int, color_idx: int) -> None:
        """Change the colour of shortcut *idx* and redraw."""
        self._items[idx].color_idx = color_idx
        self._refresh_rows()

    def _add(self) -> None:
        """Append a new shortcut and immediately start capturing its key combo."""
        used = {item.color_idx for item in self._items}
        color_idx = next(
            (i for i in range(len(COLOR_PALETTE)) if i not in used), 0,
        )
        self._items.append(_ShortcutItem(
            keys=(Key.enter,), label="Enter", color_idx=color_idx,
        ))
        self._refresh_rows()
        self._capture_start(len(self._items) - 1)

    def _remove(self, idx: int) -> None:
        """Remove shortcut *idx* (no-op if it's the last one)."""
        if len(self._items) <= 1:
            return
        self._capture_cancel()
        self._items.pop(idx)
        self._refresh_rows()

    # -- Dialog actions ------------------------------------------------------

    def _apply(self) -> None:
        """Build ``Shortcut`` objects from the edited items and invoke the callback."""
        self._capture_cancel()
        shortcuts = tuple(
            Shortcut(
                label=item.label,
                keys=item.keys,
                color=COLOR_PALETTE[item.color_idx][0],
                hover_color=COLOR_PALETTE[item.color_idx][1],
            )
            for item in self._items
        )
        self._on_apply(shortcuts)
        self._win.destroy()

    def _cancel(self) -> None:
        """Close without applying changes."""
        self._capture_cancel()
        self._win.destroy()

    def _on_escape(self, _event: tk.Event[Any]) -> None:
        """Escape cancels capture if active, otherwise closes the dialog."""
        if self._capturing is not None:
            self._capture_cancel()
        else:
            self._cancel()
