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

import ctypes
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageTk
from pynput.keyboard import Key, KeyCode, Listener

from .keys import MODIFIER_KEYS, build_combo, keys_to_label, modifier_preview
from .models import COLOR_PALETTE, Shortcut

# Win32 helpers for flicker-free widget rebuilds. LockWindowUpdate suspends
# drawing to the given HWND (and its descendants); passing 0 unlocks and
# forces a single repaint of the accumulated invalidated region.
_GA_ROOT = 2
if sys.platform == "win32":
    _LockWindowUpdate = ctypes.windll.user32.LockWindowUpdate
    _GetAncestor = ctypes.windll.user32.GetAncestor
else:
    _LockWindowUpdate = None
    _GetAncestor = None

# ---------------------------------------------------------------------------
# Mutable working copy of a shortcut used during editing
# ---------------------------------------------------------------------------


@dataclass
class _ShortcutItem:
    """Transient editing state for one row in the settings dialog."""

    keys: tuple[Key | str, ...]
    label: str
    color_idx: int
    btn: tk.Label | None = field(default=None, repr=False)
    swatch_labels: list[tk.Label] = field(default_factory=list, repr=False)
    swatch_images: list[ImageTk.PhotoImage] = field(default_factory=list, repr=False)


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

    _SWATCH_PX = 22  # colour-swatch diameter in pixels
    _SWATCH_SS = 4   # supersampling factor for smooth circles

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
        self._swatch_images: list[ImageTk.PhotoImage] = []  # prevent GC

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

    def _make_delete_icon(self, color: str) -> ImageTk.PhotoImage:
        """Render an antialiased x icon at the same height as swatches."""
        s = self._SWATCH_PX
        ss = self._SWATCH_SS
        big = s * ss
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Draw an X with thick rounded lines
        margin = int(big * 0.25)
        w = max(ss * 2, 4)
        draw.line((margin, margin, big - margin, big - margin), fill=color, width=w)
        draw.line((margin, big - margin, big - margin, margin), fill=color, width=w)
        img = img.resize((s, s), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _make_swatch(self, color: str, selected: bool) -> ImageTk.PhotoImage:
        """Render an antialiased circle swatch, optionally with a selection ring."""
        s = self._SWATCH_PX
        ss = self._SWATCH_SS
        big = s * ss
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if selected:
            draw.ellipse((0, 0, big - 1, big - 1), fill="white")
        inset = ss * 2
        draw.ellipse(
            (inset, inset, big - inset - 1, big - inset - 1), fill=color,
        )
        img = img.resize((s, s), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

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
        """Rebuild shortcut rows without flicker.

        Past flicker had two causes: the list frame briefly shrinking
        (triggering a Toplevel auto-resize that exposed the desktop), and
        the list area painting empty between destroying old rows and
        packing new ones.

        Fix: pin the frame's size with ``pack_propagate(False)`` so it
        cannot shrink, and use Win32 ``LockWindowUpdate`` on the Toplevel
        HWND to suspend all drawing while children are destroyed and
        rebuilt. When the lock is released, Windows repaints the entire
        invalidated area in a single frame, so the user never sees an
        in-between state.
        """
        frame = self._list_frame

        # Measure current rendered size so we can pin the frame during the
        # rebuild. winfo_width/height are only valid after the frame has
        # been laid out at least once — on the very first build they'll
        # be 1, in which case we skip pinning.
        frame.update_idletasks()
        cur_w = frame.winfo_width()
        cur_h = frame.winfo_height()
        pinned = cur_w > 1 and cur_h > 1
        if pinned:
            frame.configure(width=cur_w, height=cur_h)
            frame.pack_propagate(False)

        # Suspend drawing on the whole Toplevel (the ancestor HWND, not
        # tk's inner widget HWND). Only one window system-wide may hold a
        # draw lock at a time, so the try/finally is important.
        locked_hwnd = 0
        if _LockWindowUpdate is not None:
            inner_hwnd = frame.winfo_id()
            root_hwnd = _GetAncestor(inner_hwnd, _GA_ROOT)
            if root_hwnd and _LockWindowUpdate(root_hwnd):
                locked_hwnd = root_hwnd

        try:
            for child in frame.winfo_children():
                child.destroy()
            self._swatch_images = []

            if not self._items:
                tk.Label(
                    frame,
                    text="No shortcuts \u2014 click + Add Shortcut",
                    font=self._FONT, bg=self._BG, fg=self._DIM,
                ).pack(pady=20)
            else:
                for idx in range(len(self._items)):
                    self._build_row(idx)

            # Flush tk's pending geometry work while the window is still
            # locked so the eventual repaint has the final layout.
            frame.update_idletasks()
        finally:
            if locked_hwnd:
                _LockWindowUpdate(0)

        if pinned:
            frame.pack_propagate(True)

    def _build_row(self, idx: int) -> None:
        """Render one shortcut row: arrow buttons, key button, colour swatches, delete."""
        item = self._items[idx]

        card = tk.Frame(
            self._list_frame, bg=self._CARD,
            highlightbackground=self._BORDER, highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 6))

        inner = tk.Frame(card, bg=self._CARD)
        inner.pack(fill="x", padx=10, pady=6)

        # Reorder arrows (side-by-side so they don't add row height)
        can_up = idx > 0
        can_down = idx < len(self._items) - 1
        arrow_font = ("Segoe UI", 7)
        up_lbl = tk.Label(
            inner, text="\u25B2", font=arrow_font,
            bg=self._CARD, fg=self._DIM if can_up else "#444",
            cursor="hand2" if can_up else "arrow",
        )
        up_lbl.pack(side="left", padx=(0, 1))
        if can_up:
            up_lbl.bind("<Button-1>", lambda _, i=idx: self._move(i, -1))
            self._hoverable(up_lbl, self._CARD, self._BTN)
        down_lbl = tk.Label(
            inner, text="\u25BC", font=arrow_font,
            bg=self._CARD, fg=self._DIM if can_down else "#444",
            cursor="hand2" if can_down else "arrow",
        )
        down_lbl.pack(side="left", padx=(0, 6))
        if can_down:
            down_lbl.bind("<Button-1>", lambda _, i=idx: self._move(i, 1))
            self._hoverable(down_lbl, self._CARD, self._BTN)

        # Key-combo button — a Label styled as a button (tk.Button creates a
        # native Win32 BUTTON control which flashes with the system theme
        # colour for one frame during rebuild, whereas Label paints directly
        # with our bg from the first frame).
        btn = tk.Label(
            inner, text=item.label, font=self._FONT_B,
            bg=self._BTN, fg=self._FG,
            padx=12, pady=4, cursor="hand2", width=14, anchor="w",
            bd=0,
        )
        btn.pack(side="left")
        btn.bind("<Button-1>", lambda _, i=idx: self._capture_start(i))
        self._hoverable(btn, self._BTN, self._BTN_HV)
        item.btn = btn

        # Delete icon (PIL-rendered × at same height as swatches for alignment)
        is_sole = len(self._items) == 1
        del_color = "#555" if is_sole else self._DIM
        del_img = self._make_delete_icon(del_color)
        self._swatch_images.append(del_img)
        del_lbl = tk.Label(
            inner, image=del_img, bg=self._CARD, bd=0,
            cursor="arrow" if is_sole else "hand2",
        )
        del_lbl.pack(side="right", padx=(4, 0), pady=(1, 0))
        if not is_sole:
            del_img_hover = self._make_delete_icon("#EF4444")
            self._swatch_images.append(del_img_hover)
            del_lbl.bind("<Button-1>", lambda _, i=idx: self._remove(i))
            del_lbl.bind("<Enter>", lambda _, w=del_lbl, hi=del_img_hover: w.configure(image=hi))
            del_lbl.bind("<Leave>", lambda _, w=del_lbl, ni=del_img: w.configure(image=ni))

        # Colour swatches (PIL-rendered for smooth antialiased circles)
        swatch_frame = tk.Frame(inner, bg=self._CARD)
        swatch_frame.pack(side="right", padx=(12, 8), pady=(1, 0))
        item.swatch_labels = []
        item.swatch_images = []
        for ci, (color, _, _) in enumerate(COLOR_PALETTE):
            photo = self._make_swatch(color, selected=(ci == item.color_idx))
            self._swatch_images.append(photo)
            item.swatch_images.append(photo)
            lbl = tk.Label(
                swatch_frame, image=photo, bg=self._CARD,
                cursor="hand2", bd=0,
            )
            lbl.bind("<Button-1>", lambda _, i=idx, c=ci: self._pick_color(i, c))
            lbl.pack(side="left", padx=1)
            item.swatch_labels.append(lbl)

    # -- Reorder -------------------------------------------------------------

    def _move(self, idx: int, direction: int) -> None:
        """Swap shortcut at *idx* with its neighbour in *direction* (-1/+1)."""
        target = idx + direction
        if target < 0 or target >= len(self._items):
            return
        self._capture_cancel()
        self._items[idx], self._items[target] = self._items[target], self._items[idx]
        self._refresh_rows()

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
        """Change the colour of shortcut *idx*, updating swatches in-place."""
        item = self._items[idx]
        item.color_idx = color_idx
        # Update swatch images without rebuilding
        for ci, (color, _, _) in enumerate(COLOR_PALETTE):
            photo = self._make_swatch(color, selected=(ci == color_idx))
            item.swatch_images[ci] = photo
            self._swatch_images.append(photo)  # prevent GC
            item.swatch_labels[ci].configure(image=photo)

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
