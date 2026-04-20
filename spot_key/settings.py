"""Settings dialog for editing keyboard shortcuts.

Opens as a dark-themed ``tk.Toplevel`` with a list of shortcut rows.  Each
row has a button (showing a summary of the action sequence), colour
swatches, and a delete button. Clicking the summary button opens a
sub-dialog where the action sequence can be edited — adding key combos,
sleeps, and mouse clicks, and reordering or removing them.

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

from .keys import (
    MODIFIER_KEYS,
    action_label,
    actions_label,
    build_combo,
    modifier_preview,
)
from .models import (
    Action,
    COLOR_PALETTE,
    KeyComboAction,
    MouseClickAction,
    Shortcut,
    SleepAction,
)

# Win32 helpers for flicker-free widget rebuilds. LockWindowUpdate suspends
# drawing to the given HWND (and its descendants); passing 0 unlocks and
# forces a single repaint of the accumulated invalidated region.
_GA_ROOT = 2
if sys.platform == "win32":
    _LockWindowUpdate = ctypes.windll.user32.LockWindowUpdate
    _GetAncestor = ctypes.windll.user32.GetAncestor
    _dwmapi = ctypes.windll.dwmapi
    _DWMWA_TRANSITIONS_FORCEDISABLED = 3
else:
    _LockWindowUpdate = None
    _GetAncestor = None
    _dwmapi = None


def _disable_dwm_animation(win: tk.Toplevel) -> None:
    """Disable the DWM fade-in animation on a Toplevel window."""
    if _dwmapi is None:
        return
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        val = ctypes.c_int(1)
        _dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_TRANSITIONS_FORCEDISABLED,
            ctypes.byref(val), ctypes.sizeof(val),
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Mutable working copy of a shortcut used during editing
# ---------------------------------------------------------------------------


@dataclass
class _ShortcutItem:
    """Transient editing state for one row in the settings dialog."""

    actions: list[Action]
    color_idx: int
    btn: tk.Label | None = field(default=None, repr=False)
    swatch_labels: list[tk.Label] = field(default_factory=list, repr=False)
    swatch_images: list[ImageTk.PhotoImage] = field(default_factory=list, repr=False)

    @property
    def label(self) -> str:
        """Human-readable summary of this shortcut's action sequence."""
        return actions_label(tuple(self.actions))


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
    _SLIDER_TRACK_H = 4
    _SLIDER_HANDLE_R = 8

    # -- Construction --------------------------------------------------------

    # Diameters are quantised to even numbers. Odd diameters force the
    # app's resize anchor to round half-pixels, which makes the pie
    # visibly jitter by one pixel as the slider is dragged.
    _MIN_DIAMETER = 40
    _MAX_DIAMETER = 600
    _DIAMETER_STEP = 2

    def __init__(
        self,
        parent: tk.Tk,
        *,
        shortcuts: tuple[Shortcut, ...],
        diameter: int,
        opacity: float = 1.0,
        on_apply: Callable[[tuple[Shortcut, ...], int, float], None] | None = None,
        on_preview_diameter: Callable[[int], None] | None = None,
        on_preview_opacity: Callable[[float], None] | None = None,
    ) -> None:
        self._on_apply = on_apply
        self._on_preview_diameter = on_preview_diameter
        self._on_preview_opacity = on_preview_opacity
        self._diameter = diameter
        self._original_diameter = diameter
        self._opacity = opacity
        self._original_opacity = opacity
        self._items = [
            _ShortcutItem(
                actions=list(sc.actions),
                color_idx=self._color_idx(sc.color),
            )
            for sc in shortcuts
        ]
        self._swatch_images: list[ImageTk.PhotoImage] = []  # prevent GC

        win = tk.Toplevel(parent)
        win.title("Spot Key \u2014 Settings")
        win.configure(bg=self._BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        # Start fully transparent so the window is mapped and widgets
        # render, but nothing is visible. After everything is laid out
        # and painted we snap to opaque — no content-building flicker.
        win.withdraw()
        self._win = win

        self._list_frame: tk.Frame  # assigned in _build_layout
        self._build_layout()

        win.update_idletasks()

        # Centre on the primary monitor.
        try:
            import ctypes
            sw = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
            sh = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        except Exception:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
        win.deiconify()
        _disable_dwm_animation(win)
        win.update_idletasks()
        x = sw // 2 - win.winfo_width() // 2
        y = sh // 2 - win.winfo_height() // 2
        win.geometry(f"+{x}+{y}")
        win.update_idletasks()

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

        # Size slider -----------------------------------------------------
        # Custom canvas slider rather than tk.Scale: the native Win32 Scale
        # control ignores most styling, so on a dark theme it renders as a
        # nearly-invisible smear.
        tk.Label(
            win, text="Size", font=self._FONT_TITLE,
            bg=self._BG, fg=self._FG,
        ).pack(anchor="w", padx=pad, pady=(pad, 4))
        size_frame = tk.Frame(win, bg=self._BG)
        size_frame.pack(fill="x", padx=pad, pady=(0, 8))
        self._size_value = tk.Label(
            size_frame, text=f"{self._diameter} px", font=self._FONT,
            bg=self._BG, fg=self._DIM, width=7, anchor="e",
        )
        self._size_value.pack(side="right")
        self._build_size_slider(size_frame)

        # Opacity slider
        tk.Label(
            win, text="Opacity", font=self._FONT_TITLE,
            bg=self._BG, fg=self._FG,
        ).pack(anchor="w", padx=pad, pady=(pad, 4))
        opacity_frame = tk.Frame(win, bg=self._BG)
        opacity_frame.pack(fill="x", padx=pad, pady=(0, 8))
        self._opacity_value = tk.Label(
            opacity_frame, text=f"{round(self._opacity * 100)}%", font=self._FONT,
            bg=self._BG, fg=self._DIM, width=7, anchor="e",
        )
        self._opacity_value.pack(side="right")
        self._build_opacity_slider(opacity_frame)

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

    def _build_size_slider(self, parent: tk.Frame) -> None:
        """Build a dark-theme-friendly horizontal slider on a Canvas.

        Renders a flat track, a filled portion up to the current value,
        and a round draggable handle. The canvas stretches to fill the
        parent frame so the slider always spans the available width;
        ``_slider_length`` is recomputed on every ``<Configure>``.
        Click or drag anywhere in the canvas to jump the handle.
        """
        r = self._SLIDER_HANDLE_R
        pad = r + 2
        h = 2 * r + 4

        canvas = tk.Canvas(
            parent, height=h, bg=self._BG,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        canvas.pack(side="left", fill="x", expand=True, padx=(0, 8))

        cy = h // 2
        self._slider_track = canvas.create_rectangle(
            0, 0, 0, 0, fill=self._CARD, outline="",
        )
        self._slider_fill = canvas.create_rectangle(
            0, 0, 0, 0, fill=self._ACCENT, outline="",
        )
        self._slider_handle = canvas.create_oval(
            0, 0, 0, 0, fill=self._FG, outline=self._DIM, width=1,
        )
        self._slider_canvas = canvas
        self._slider_pad = pad
        self._slider_cy = cy
        self._slider_length = 1  # filled in on first <Configure>

        canvas.bind("<Configure>", self._on_slider_resize)
        canvas.bind("<Button-1>", self._slider_drag)
        canvas.bind("<B1-Motion>", self._slider_drag)

    def _on_slider_resize(self, event: tk.Event[Any]) -> None:
        """Recompute the slider's usable length when its canvas resizes."""
        self._slider_length = max(1, event.width - 2 * self._slider_pad)
        cy = self._slider_cy
        th = self._SLIDER_TRACK_H // 2
        self._slider_canvas.coords(
            self._slider_track,
            self._slider_pad, cy - th,
            self._slider_pad + self._slider_length, cy + th,
        )
        self._redraw_slider()

    def _redraw_slider(self) -> None:
        """Reposition the slider's fill rectangle and handle circle."""
        frac = (
            (self._diameter - self._MIN_DIAMETER)
            / (self._MAX_DIAMETER - self._MIN_DIAMETER)
        )
        x = self._slider_pad + frac * self._slider_length
        cy = self._slider_cy
        th = self._SLIDER_TRACK_H // 2
        r = self._SLIDER_HANDLE_R
        self._slider_canvas.coords(
            self._slider_fill,
            self._slider_pad, cy - th, x, cy + th,
        )
        self._slider_canvas.coords(
            self._slider_handle, x - r, cy - r, x + r, cy + r,
        )

    def _slider_drag(self, event: tk.Event[Any]) -> None:
        """Handle click/drag on the size slider canvas."""
        frac = (event.x - self._slider_pad) / self._slider_length
        frac = max(0.0, min(1.0, frac))
        span = self._MAX_DIAMETER - self._MIN_DIAMETER
        raw = self._MIN_DIAMETER + frac * span
        step = self._DIAMETER_STEP
        new = round(raw / step) * step
        if new == self._diameter:
            return
        self._diameter = new
        self._size_value.configure(text=f"{self._diameter} px")
        self._redraw_slider()
        if self._on_preview_diameter is not None:
            self._on_preview_diameter(new)

    # -- Opacity slider ------------------------------------------------------

    _MIN_OPACITY = 0.05
    _MAX_OPACITY = 1.0

    def _build_opacity_slider(self, parent: tk.Frame) -> None:
        """Build the opacity slider — same style as the size slider."""
        r = self._SLIDER_HANDLE_R
        pad = r + 2
        h = 2 * r + 4

        canvas = tk.Canvas(
            parent, height=h, bg=self._BG,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        canvas.pack(side="left", fill="x", expand=True, padx=(0, 8))

        cy = h // 2
        self._op_slider_track = canvas.create_rectangle(
            0, 0, 0, 0, fill=self._CARD, outline="",
        )
        self._op_slider_fill = canvas.create_rectangle(
            0, 0, 0, 0, fill=self._ACCENT, outline="",
        )
        self._op_slider_handle = canvas.create_oval(
            0, 0, 0, 0, fill=self._FG, outline=self._DIM, width=1,
        )
        self._op_slider_canvas = canvas
        self._op_slider_pad = pad
        self._op_slider_cy = cy
        self._op_slider_length = 1

        canvas.bind("<Configure>", self._on_op_slider_resize)
        canvas.bind("<Button-1>", self._op_slider_drag)
        canvas.bind("<B1-Motion>", self._op_slider_drag)

    def _on_op_slider_resize(self, event: tk.Event[Any]) -> None:
        self._op_slider_length = max(1, event.width - 2 * self._op_slider_pad)
        cy = self._op_slider_cy
        th = self._SLIDER_TRACK_H // 2
        self._op_slider_canvas.coords(
            self._op_slider_track,
            self._op_slider_pad, cy - th,
            self._op_slider_pad + self._op_slider_length, cy + th,
        )
        self._redraw_op_slider()

    def _redraw_op_slider(self) -> None:
        frac = (
            (self._opacity - self._MIN_OPACITY)
            / (self._MAX_OPACITY - self._MIN_OPACITY)
        )
        x = self._op_slider_pad + frac * self._op_slider_length
        cy = self._op_slider_cy
        th = self._SLIDER_TRACK_H // 2
        r = self._SLIDER_HANDLE_R
        self._op_slider_canvas.coords(
            self._op_slider_fill,
            self._op_slider_pad, cy - th, x, cy + th,
        )
        self._op_slider_canvas.coords(
            self._op_slider_handle, x - r, cy - r, x + r, cy + r,
        )

    def _op_slider_drag(self, event: tk.Event[Any]) -> None:
        frac = (event.x - self._op_slider_pad) / self._op_slider_length
        frac = max(0.0, min(1.0, frac))
        new = self._MIN_OPACITY + frac * (self._MAX_OPACITY - self._MIN_OPACITY)
        new = round(new, 2)
        new = max(self._MIN_OPACITY, min(self._MAX_OPACITY, new))
        if new == self._opacity:
            return
        self._opacity = new
        self._opacity_value.configure(text=f"{round(self._opacity * 100)}%")
        self._redraw_op_slider()
        if self._on_preview_opacity is not None:
            self._on_preview_opacity(new)

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
            padx=12, pady=4, cursor="hand2", width=22, anchor="w",
            bd=0,
        )
        btn.pack(side="left")
        btn.bind("<Button-1>", lambda _, i=idx: self._edit_actions(i))
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
        self._items[idx], self._items[target] = self._items[target], self._items[idx]
        self._refresh_rows()

    # -- Action editing ------------------------------------------------------

    def _edit_actions(self, idx: int) -> None:
        """Open the action-sequence sub-dialog for shortcut *idx*."""
        item = self._items[idx]
        ActionSequenceDialog(
            parent=self._win,
            actions=list(item.actions),
            on_apply=lambda new_actions: self._replace_actions(idx, new_actions),
        )

    def _replace_actions(self, idx: int, actions: list[Action]) -> None:
        """Commit an edited action sequence back to the shortcut at *idx*."""
        self._items[idx].actions = actions
        item = self._items[idx]
        if item.btn is not None:
            item.btn.configure(text=item.label)

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
        """Open the action editor for a brand-new shortcut.

        The shortcut is only appended if the user confirms with OK, so
        cancelling leaves the list unchanged. The editor opens with an
        empty action sequence — users fill it in from scratch using the
        Add Key Combo / Sleep / Mouse Click buttons.
        """
        used = {item.color_idx for item in self._items}
        color_idx = next(
            (i for i in range(len(COLOR_PALETTE)) if i not in used), 0,
        )
        ActionSequenceDialog(
            parent=self._win,
            actions=[],
            on_apply=lambda new_actions: self._append_new_shortcut(
                color_idx, new_actions,
            ),
        )

    def _append_new_shortcut(
        self, color_idx: int, actions: list[Action],
    ) -> None:
        """Commit a newly-edited action sequence as a new shortcut row."""
        self._items.append(_ShortcutItem(
            actions=actions, color_idx=color_idx,
        ))
        self._refresh_rows()

    def _remove(self, idx: int) -> None:
        """Remove shortcut *idx* (no-op if it's the last one)."""
        if len(self._items) <= 1:
            return
        self._items.pop(idx)
        self._refresh_rows()

    # -- Dialog actions ------------------------------------------------------

    def _apply(self) -> None:
        """Build ``Shortcut`` objects from the edited items and invoke the callback."""
        shortcuts = tuple(
            Shortcut(
                label=item.label,
                actions=tuple(item.actions),
                color=COLOR_PALETTE[item.color_idx][0],
                hover_color=COLOR_PALETTE[item.color_idx][1],
            )
            for item in self._items
        )
        self._on_apply(shortcuts, self._diameter, self._opacity)
        self._win.destroy()

    def _cancel(self) -> None:
        """Close without applying changes. Restore original diameter/opacity."""
        if (
            self._on_preview_diameter is not None
            and self._diameter != self._original_diameter
        ):
            self._on_preview_diameter(self._original_diameter)
        if (
            self._on_preview_opacity is not None
            and self._opacity != self._original_opacity
        ):
            self._on_preview_opacity(self._original_opacity)
        self._win.destroy()

    def _on_escape(self, _event: tk.Event[Any]) -> None:
        """Escape closes the dialog without applying changes."""
        self._cancel()


# ---------------------------------------------------------------------------
# Action sequence sub-dialog
# ---------------------------------------------------------------------------


class ActionSequenceDialog:
    """Modal sub-dialog for editing a single shortcut's action sequence.

    Shows an ordered list of actions (key combos, sleeps, mouse clicks)
    that can be reordered or removed, plus an "Add" row with three
    buttons. Clicking an Add button inserts a new action of that type
    and — for key combos — immediately enters key-capture mode using the
    same low-level pynput hook that the main settings dialog used to use.
    """

    _BG         = SettingsDialog._BG
    _CARD       = SettingsDialog._CARD
    _FG         = SettingsDialog._FG
    _DIM        = SettingsDialog._DIM
    _BORDER     = SettingsDialog._BORDER
    _BTN        = SettingsDialog._BTN
    _BTN_HV     = SettingsDialog._BTN_HV
    _ACCENT     = SettingsDialog._ACCENT
    _ACCENT_HV  = SettingsDialog._ACCENT_HV
    _CAPTURE_BG = SettingsDialog._CAPTURE_BG

    _FONT       = SettingsDialog._FONT
    _FONT_B     = SettingsDialog._FONT_B
    _FONT_TITLE = SettingsDialog._FONT_TITLE

    def __init__(
        self,
        *,
        parent: tk.Toplevel,
        actions: list[Action],
        on_apply: Callable[[list[Action]], None],
    ) -> None:
        self._actions = list(actions)
        self._on_apply = on_apply

        self._capturing = False
        self._capture_btn: tk.Label | None = None
        self._held_mods: set[Key] = set()
        self._listener: Listener | None = None

        win = tk.Toplevel(parent)
        win.title("Edit Actions")
        win.configure(bg=self._BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.withdraw()
        self._win = win

        self._list_frame: tk.Frame  # assigned in _build_layout
        self._build_layout()

        win.update_idletasks()

        # Centre over the parent settings dialog.
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + pw // 2 - win.winfo_width() // 2
        y = py + ph // 2 - win.winfo_height() // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

        # Map the window invisibly, disable DWM fade, then reveal.
        win.attributes("-alpha", 0.0)
        win.deiconify()
        win.update_idletasks()
        _disable_dwm_animation(win)
        win.attributes("-alpha", 1.0)

        win.transient(parent)
        win.grab_set()
        win.focus_set()
        win.bind("<Escape>", self._on_escape)
        win.protocol("WM_DELETE_WINDOW", self._cancel)

    # -- Layout --------------------------------------------------------------

    def _build_layout(self) -> None:
        pad = 14
        win = self._win

        tk.Label(
            win, text="Actions", font=self._FONT_TITLE,
            bg=self._BG, fg=self._FG,
        ).pack(anchor="w", padx=pad, pady=(pad, 6))

        self._list_frame = tk.Frame(win, bg=self._BG)
        self._list_frame.pack(fill="x", padx=pad)
        self._refresh_list()

        # "Add …" row
        tk.Label(
            win, text="Add action", font=self._FONT,
            bg=self._BG, fg=self._DIM,
        ).pack(anchor="w", padx=pad, pady=(12, 4))
        add_frame = tk.Frame(win, bg=self._BG)
        add_frame.pack(fill="x", padx=pad)

        for label, handler in (
            ("Key Combo", self._add_key_combo),
            ("Sleep", self._add_sleep),
            ("Mouse Click", self._add_mouse_click),
        ):
            btn = tk.Button(
                add_frame, text=label, font=self._FONT,
                bg=self._BTN, fg=self._FG,
                activebackground=self._BTN_HV, activeforeground=self._FG,
                bd=0, padx=12, pady=6, cursor="hand2", command=handler,
            )
            btn.pack(side="left", padx=(0, 6))
            SettingsDialog._hoverable(btn, self._BTN, self._BTN_HV)

        # Separator
        tk.Frame(win, bg=self._BORDER, height=1).pack(
            fill="x", padx=pad, pady=(pad, 0),
        )

        btn_frame = tk.Frame(win, bg=self._BG)
        btn_frame.pack(fill="x", padx=pad, pady=pad)

        cancel_btn = tk.Button(
            btn_frame, text="Cancel", font=self._FONT,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=20, pady=8, cursor="hand2", command=self._cancel,
        )
        cancel_btn.pack(side="right", padx=(8, 0))
        SettingsDialog._hoverable(cancel_btn, self._BTN, self._BTN_HV)

        ok_btn = tk.Button(
            btn_frame, text="OK", font=self._FONT_B,
            bg=self._ACCENT, fg="#FFF",
            activebackground=self._ACCENT_HV, activeforeground="#FFF",
            bd=0, padx=20, pady=8, cursor="hand2", command=self._apply,
        )
        ok_btn.pack(side="right")
        SettingsDialog._hoverable(ok_btn, self._ACCENT, self._ACCENT_HV)

    # -- Action list rendering -----------------------------------------------

    def _refresh_list(self) -> None:
        """Rebuild the action-row list from ``self._actions``."""
        for child in self._list_frame.winfo_children():
            child.destroy()

        if not self._actions:
            tk.Label(
                self._list_frame,
                text="No actions \u2014 use the buttons below to add one.",
                font=self._FONT, bg=self._BG, fg=self._DIM,
            ).pack(pady=10)
            return

        for idx in range(len(self._actions)):
            self._build_action_row(idx)

    def _build_action_row(self, idx: int) -> None:
        """Render one row for the action at index *idx*."""
        action = self._actions[idx]
        card = tk.Frame(
            self._list_frame, bg=self._CARD,
            highlightbackground=self._BORDER, highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 4))

        inner = tk.Frame(card, bg=self._CARD)
        inner.pack(fill="x", padx=10, pady=6)

        # Reorder arrows
        can_up = idx > 0
        can_down = idx < len(self._actions) - 1
        arrow_font = ("Segoe UI", 7)
        up_lbl = tk.Label(
            inner, text="\u25B2", font=arrow_font,
            bg=self._CARD, fg=self._DIM if can_up else "#444",
            cursor="hand2" if can_up else "arrow",
        )
        up_lbl.pack(side="left", padx=(0, 1))
        if can_up:
            up_lbl.bind("<Button-1>", lambda _, i=idx: self._move(i, -1))
        down_lbl = tk.Label(
            inner, text="\u25BC", font=arrow_font,
            bg=self._CARD, fg=self._DIM if can_down else "#444",
            cursor="hand2" if can_down else "arrow",
        )
        down_lbl.pack(side="left", padx=(0, 8))
        if can_down:
            down_lbl.bind("<Button-1>", lambda _, i=idx: self._move(i, 1))

        # Action label (clickable only if it's a key combo — opens capture)
        is_key = isinstance(action, KeyComboAction)
        is_sleep = isinstance(action, SleepAction)
        is_click = isinstance(action, MouseClickAction)

        if is_key:
            lbl = tk.Label(
                inner, text=action_label(action), font=self._FONT_B,
                bg=self._BTN, fg=self._FG,
                padx=10, pady=4, cursor="hand2", anchor="w", bd=0,
            )
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Button-1>", lambda _, i=idx: self._capture_start(i))
            SettingsDialog._hoverable(lbl, self._BTN, self._BTN_HV)
        elif is_sleep:
            assert isinstance(action, SleepAction)
            tk.Label(
                inner, text="Sleep", font=self._FONT,
                bg=self._CARD, fg=self._FG,
            ).pack(side="left")
            var = tk.StringVar(value=f"{action.seconds:g}")
            entry = tk.Entry(
                inner, textvariable=var, font=self._FONT, width=8,
                bg=self._BG, fg=self._FG, insertbackground=self._FG,
                bd=0, relief="flat", highlightthickness=1,
                highlightbackground=self._BORDER, highlightcolor=self._ACCENT,
            )
            entry.pack(side="left", padx=(8, 4))
            tk.Label(
                inner, text="seconds", font=self._FONT,
                bg=self._CARD, fg=self._DIM,
            ).pack(side="left")
            var.trace_add(
                "write",
                lambda *_, i=idx, v=var: self._update_sleep(i, v.get()),
            )
        elif is_click:
            assert isinstance(action, MouseClickAction)
            tk.Label(
                inner, text="Click at", font=self._FONT,
                bg=self._CARD, fg=self._FG,
            ).pack(side="left")
            x_var = tk.StringVar(value=str(action.x))
            y_var = tk.StringVar(value=str(action.y))
            for var, placeholder in ((x_var, "x"), (y_var, "y")):
                tk.Label(
                    inner, text=placeholder, font=self._FONT,
                    bg=self._CARD, fg=self._DIM,
                ).pack(side="left", padx=(8, 2))
                entry = tk.Entry(
                    inner, textvariable=var, font=self._FONT, width=6,
                    bg=self._BG, fg=self._FG, insertbackground=self._FG,
                    bd=0, relief="flat", highlightthickness=1,
                    highlightbackground=self._BORDER,
                    highlightcolor=self._ACCENT,
                )
                entry.pack(side="left")
            x_var.trace_add(
                "write",
                lambda *_, i=idx, xv=x_var, yv=y_var:
                self._update_click(i, xv.get(), yv.get()),
            )
            y_var.trace_add(
                "write",
                lambda *_, i=idx, xv=x_var, yv=y_var:
                self._update_click(i, xv.get(), yv.get()),
            )

        # Remove button
        rm_lbl = tk.Label(
            inner, text="\u2715", font=self._FONT_B,
            bg=self._CARD, fg=self._DIM, cursor="hand2",
            padx=6,
        )
        rm_lbl.pack(side="right")
        rm_lbl.bind("<Button-1>", lambda _, i=idx: self._remove(i))
        rm_lbl.bind(
            "<Enter>", lambda _, w=rm_lbl: w.configure(fg="#EF4444"),
        )
        rm_lbl.bind(
            "<Leave>", lambda _, w=rm_lbl: w.configure(fg=self._DIM),
        )

    # -- Row mutations -------------------------------------------------------

    def _move(self, idx: int, direction: int) -> None:
        target = idx + direction
        if target < 0 or target >= len(self._actions):
            return
        self._capture_cancel()
        self._actions[idx], self._actions[target] = (
            self._actions[target], self._actions[idx],
        )
        self._refresh_list()

    def _remove(self, idx: int) -> None:
        self._capture_cancel()
        self._actions.pop(idx)
        self._refresh_list()

    def _update_sleep(self, idx: int, text: str) -> None:
        try:
            seconds = float(text)
        except ValueError:
            return
        if seconds < 0:
            return
        self._actions[idx] = SleepAction(seconds=seconds)

    def _update_click(self, idx: int, x_text: str, y_text: str) -> None:
        try:
            x = int(x_text)
            y = int(y_text)
        except ValueError:
            return
        self._actions[idx] = MouseClickAction(x=x, y=y)

    # -- Add handlers --------------------------------------------------------

    def _add_key_combo(self) -> None:
        self._capture_cancel()
        self._actions.append(KeyComboAction(keys=(Key.enter,)))
        self._refresh_list()
        self._capture_start(len(self._actions) - 1)

    def _add_sleep(self) -> None:
        self._capture_cancel()
        self._actions.append(SleepAction(seconds=0.5))
        self._refresh_list()

    def _add_mouse_click(self) -> None:
        self._capture_cancel()
        self._actions.append(MouseClickAction(x=0, y=0))
        self._refresh_list()

    # -- Key capture (shared with the main dialog's original logic) ----------

    def _capture_start(self, idx: int) -> None:
        """Enter key-capture mode for the key-combo action at *idx*."""
        self._capture_cancel()
        action = self._actions[idx]
        if not isinstance(action, KeyComboAction):
            return
        self._capturing_idx = idx
        self._capturing = True
        self._held_mods = set()

        # Find the row label we just rendered and flip it to capture mode.
        row = self._list_frame.winfo_children()[idx]
        inner = row.winfo_children()[0]
        target: tk.Label | None = None
        for child in inner.winfo_children():
            if isinstance(child, tk.Label) and child.cget("bg") == self._BTN:
                target = child
                break
        if target is None:
            self._capturing = False
            return
        self._capture_btn = target
        target.configure(text="Press keys\u2026", bg=self._CAPTURE_BG)

        self._listener = Listener(
            on_press=self._on_hook_press,
            on_release=self._on_hook_release,
        )
        self._listener.start()

    def _on_hook_press(self, key: Key | KeyCode) -> bool | None:
        if key in MODIFIER_KEYS:
            self._held_mods.add(key)
            self._win.after(0, self._update_mod_preview)
            return None

        if key == Key.esc:
            self._win.after(0, self._capture_cancel)
            return False

        combo = build_combo(self._held_mods, key)
        if combo:
            self._win.after(0, self._finish_capture, combo)
        return False

    def _on_hook_release(self, key: Key | KeyCode) -> bool | None:
        if not self._capturing:
            return False
        self._held_mods.discard(key)
        self._win.after(0, self._update_mod_preview)
        return None

    def _update_mod_preview(self) -> None:
        if not self._capturing or self._capture_btn is None:
            return
        self._capture_btn.configure(text=modifier_preview(self._held_mods))

    def _finish_capture(self, keys: tuple[Key | str, ...]) -> None:
        if not self._capturing:
            return
        idx = self._capturing_idx
        self._actions[idx] = KeyComboAction(keys=keys)
        self._capturing = False
        self._capture_btn = None
        self._held_mods = set()
        self._refresh_list()

    def _capture_cancel(self) -> None:
        if not self._capturing:
            return
        if self._listener is not None and self._listener.is_alive():
            self._listener.stop()
        self._capturing = False
        self._capture_btn = None
        self._held_mods = set()
        self._refresh_list()

    # -- Dialog actions ------------------------------------------------------

    def _apply(self) -> None:
        self._capture_cancel()
        self._on_apply(list(self._actions))
        self._win.destroy()

    def _cancel(self) -> None:
        self._capture_cancel()
        self._win.destroy()

    def _on_escape(self, _event: tk.Event[Any]) -> None:
        if self._capturing:
            self._capture_cancel()
        else:
            self._cancel()
