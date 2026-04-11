"""Spot Key — a floating pie-chart button that triggers shortcuts on hover."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import math
import struct
import sys
import tkinter as tk
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageTk
from pynput.keyboard import Controller, Key, KeyCode, Listener

# Enable per-monitor DPI awareness so Windows doesn't bitmap-scale us.
if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[union-attr]

SUPERSAMPLE = 4  # draw at Nx resolution, downsample for smooth edges

# Win32 constants for layered windows (true per-pixel alpha).
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
ULW_ALPHA = 2
BI_RGB = 0
DIB_RGB_COLORS = 0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


@dataclass(frozen=True)
class Shortcut:
    """A single pie segment: its keystroke and colours."""

    label: str
    keys: tuple[Key | str, ...]
    color: str
    hover_color: str


@dataclass(frozen=True)
class Config:
    """All tunables in one place for future UI binding."""

    shortcuts: tuple[Shortcut, ...] = field(default_factory=lambda: (
        Shortcut("Ctrl+Q", (Key.ctrl_l, "q"), "#4A90D9", "#2563EB"),
        Shortcut("Ctrl+C", (Key.ctrl_l, "c"), "#10B981", "#059669"),
        Shortcut("Enter",  (Key.enter,),      "#F59E0B", "#D97706"),
    ))
    diameter: int = 160
    outline_color: str = "#374151"
    menu_zone_size: int = 28
    menu_zone_color: str = "#6B7280"
    menu_zone_hover_color: str = "#9CA3AF"
    shortcut_hover_ms: int = 330


# -- Colour palette and key-mapping helpers ----------------------------------

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

_KEYSYM_TO_PYNPUT: dict[str, Key] = {
    "Return": Key.enter, "Escape": Key.esc, "space": Key.space,
    "Tab": Key.tab, "BackSpace": Key.backspace, "Delete": Key.delete,
    "Up": Key.up, "Down": Key.down, "Left": Key.left, "Right": Key.right,
    "Home": Key.home, "End": Key.end,
    "Page_Up": Key.page_up, "Page_Down": Key.page_down,
    "Prior": Key.page_up, "Next": Key.page_down,  # Windows/X11 keysym aliases
    "Insert": Key.insert, "Caps_Lock": Key.caps_lock,
}
for _i in range(1, 21):
    _KEYSYM_TO_PYNPUT[f"F{_i}"] = getattr(Key, f"f{_i}")

_MODIFIER_KEYSYMS = frozenset({
    "Control_L", "Control_R", "Shift_L", "Shift_R",
    "Alt_L", "Alt_R", "Super_L", "Super_R",
})

# pynput Key objects that are modifiers (used by the low-level key capture).
_PYNPUT_MODIFIERS = frozenset({
    Key.ctrl_l, Key.ctrl_r,
    Key.shift, Key.shift_l, Key.shift_r,
    Key.alt_l, Key.alt_r, Key.alt_gr,
    Key.cmd, Key.cmd_l, Key.cmd_r,
})


def _keys_to_label(keys: tuple[Key | str, ...]) -> str:
    """Convert a pynput key tuple to a human-readable label like 'Ctrl+Q'."""
    parts: list[str] = []
    for k in keys:
        if isinstance(k, Key):
            n = k.name
            if n.startswith("ctrl"):
                parts.append("Ctrl")
            elif n.startswith("alt"):
                parts.append("Alt")
            elif n.startswith("shift"):
                parts.append("Shift")
            elif n in ("cmd", "cmd_l", "cmd_r"):
                parts.append("Win")
            else:
                pretty = n.replace("_", " ").title() if "_" in n else n.capitalize()
                parts.append(pretty)
        else:
            parts.append(k.upper())
    return "+".join(parts)


def _event_to_keys(event: tk.Event[Any]) -> tuple[Key | str, ...] | None:
    """Map a tkinter KeyPress *event* to a pynput key tuple, or ``None``."""
    keysym: str = event.keysym
    if keysym in _MODIFIER_KEYSYMS:
        return None

    keys: list[Key | str] = []
    if event.state & 0x4:
        keys.append(Key.ctrl_l)
    if event.state & 0x1:
        keys.append(Key.shift_l)
    if event.state & 0x20000:
        keys.append(Key.alt_l)

    if keysym in _KEYSYM_TO_PYNPUT:
        keys.append(_KEYSYM_TO_PYNPUT[keysym])
    elif len(keysym) == 1:
        keys.append(keysym.lower())
    elif event.char and len(event.char) == 1 and event.char.isprintable():
        keys.append(event.char.lower())
    else:
        return None

    return tuple(keys) if keys else None


# -- Config persistence ------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "spot_key_config.json"


def _save_shortcuts(shortcuts: tuple[Shortcut, ...]) -> None:
    """Write shortcuts to a JSON config file."""
    data = [
        {
            "label": sc.label,
            "keys": [f"Key.{k.name}" if isinstance(k, Key) else k for k in sc.keys],
            "color": sc.color,
            "hover_color": sc.hover_color,
        }
        for sc in shortcuts
    ]
    _CONFIG_PATH.write_text(json.dumps({"shortcuts": data}, indent=2), encoding="utf-8")


def _load_shortcuts() -> tuple[Shortcut, ...] | None:
    """Load shortcuts from the JSON config file, or return None."""
    if not _CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        shortcuts: list[Shortcut] = []
        for item in data["shortcuts"]:
            keys = tuple(
                getattr(Key, k[4:]) if k.startswith("Key.") else k
                for k in item["keys"]
            )
            shortcuts.append(Shortcut(
                label=item["label"], keys=keys,
                color=item["color"], hover_color=item["hover_color"],
            ))
        return tuple(shortcuts)
    except (json.JSONDecodeError, KeyError, AttributeError, TypeError):
        return None


# -- Settings dialog ---------------------------------------------------------

class SettingsDialog:
    """Modal dark-themed dialog for adding, removing, and editing shortcuts."""

    _BG = "#1F2937"
    _CARD = "#374151"
    _FG = "#F3F4F6"
    _DIM = "#9CA3AF"
    _BORDER = "#4B5563"
    _BTN = "#4B5563"
    _BTN_HV = "#6B7280"
    _ACCENT = "#2563EB"
    _ACCENT_HV = "#1D4ED8"
    _CAPTURE = "#7C3AED"
    _FONT = ("Segoe UI", 10)
    _FONT_B = ("Segoe UI", 10, "bold")
    _FONT_TITLE = ("Segoe UI", 13, "bold")
    _SWATCH = 18

    def __init__(
        self,
        parent: tk.Tk,
        shortcuts: tuple[Shortcut, ...],
        on_apply: Any,
    ) -> None:
        self._on_apply = on_apply
        self._items: list[dict[str, Any]] = [
            {"keys": sc.keys, "label": sc.label, "color_idx": self._color_idx(sc.color)}
            for sc in shortcuts
        ]
        self._capturing: int | None = None

        win = tk.Toplevel(parent)
        win.title("Spot Key \u2014 Settings")
        win.configure(bg=self._BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._win = win

        self._build(win)

        win.update_idletasks()
        x = win.winfo_screenwidth() // 2 - win.winfo_width() // 2
        y = win.winfo_screenheight() // 2 - win.winfo_height() // 2
        win.geometry(f"+{x}+{y}")

        win.grab_set()
        win.focus_set()
        win.bind("<Escape>", self._on_esc)
        win.protocol("WM_DELETE_WINDOW", self._cancel)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _color_idx(color: str) -> int:
        lo = color.lower()
        for i, (c, _, _) in enumerate(COLOR_PALETTE):
            if c.lower() == lo:
                return i
        return 0

    @staticmethod
    def _hoverable(w: tk.Widget, normal: str, hover: str) -> None:
        w.bind("<Enter>", lambda _: w.configure(bg=hover))
        w.bind("<Leave>", lambda _: w.configure(bg=normal))

    # -- layout --------------------------------------------------------------

    def _build(self, win: tk.Toplevel) -> None:
        pad = 16

        tk.Label(
            win, text="Shortcuts", font=self._FONT_TITLE,
            bg=self._BG, fg=self._FG,
        ).pack(anchor="w", padx=pad, pady=(pad, 8))

        self._list = tk.Frame(win, bg=self._BG)
        self._list.pack(fill="x", padx=pad)
        self._refresh()

        af = tk.Frame(win, bg=self._BG)
        af.pack(fill="x", padx=pad, pady=(8, 0))
        add = tk.Button(
            af, text="+ Add Shortcut", font=self._FONT,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=12, pady=6, cursor="hand2", command=self._add,
        )
        add.pack(anchor="w")
        self._hoverable(add, self._BTN, self._BTN_HV)

        tk.Frame(win, bg=self._BORDER, height=1).pack(
            fill="x", padx=pad, pady=(pad, 0),
        )

        bf = tk.Frame(win, bg=self._BG)
        bf.pack(fill="x", padx=pad, pady=pad)

        cancel = tk.Button(
            bf, text="Cancel", font=self._FONT,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=20, pady=8, cursor="hand2", command=self._cancel,
        )
        cancel.pack(side="right", padx=(8, 0))
        self._hoverable(cancel, self._BTN, self._BTN_HV)

        apply_ = tk.Button(
            bf, text="Apply", font=self._FONT_B,
            bg=self._ACCENT, fg="#FFF",
            activebackground=self._ACCENT_HV, activeforeground="#FFF",
            bd=0, padx=20, pady=8, cursor="hand2", command=self._apply,
        )
        apply_.pack(side="right")
        self._hoverable(apply_, self._ACCENT, self._ACCENT_HV)

    # -- shortcut rows -------------------------------------------------------

    def _refresh(self) -> None:
        for w in self._list.winfo_children():
            w.destroy()
        if not self._items:
            tk.Label(
                self._list, text="No shortcuts \u2014 click + Add Shortcut",
                font=self._FONT, bg=self._BG, fg=self._DIM,
            ).pack(pady=20)
            return
        for i in range(len(self._items)):
            self._row(i)

    def _row(self, idx: int) -> None:
        item = self._items[idx]
        card = tk.Frame(
            self._list, bg=self._CARD,
            highlightbackground=self._BORDER, highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 6))
        inner = tk.Frame(card, bg=self._CARD)
        inner.pack(fill="x", padx=10, pady=8)

        # Key-combo button (click to re-record)
        btn = tk.Button(
            inner, text=item["label"], font=self._FONT_B,
            bg=self._BTN, fg=self._FG,
            activebackground=self._BTN_HV, activeforeground=self._FG,
            bd=0, padx=12, pady=4, cursor="hand2", width=14, anchor="w",
            command=lambda i=idx: self._capture_start(i),
        )
        btn.pack(side="left")
        self._hoverable(btn, self._BTN, self._BTN_HV)
        item["_btn"] = btn

        # Delete button (disabled when only one shortcut remains)
        sole = len(self._items) == 1
        xfg = "#555" if sole else self._DIM
        tk.Button(
            inner, text="\u00d7", font=("Segoe UI", 14),
            bg=self._CARD, fg=xfg,
            activebackground=self._CARD,
            activeforeground="#555" if sole else "#EF4444",
            bd=0, padx=4, cursor="arrow" if sole else "hand2",
            command=(lambda: None) if sole else (lambda i=idx: self._remove(i)),
        ).pack(side="right")

        # Colour swatches
        sf = tk.Frame(inner, bg=self._CARD)
        sf.pack(side="right", padx=(12, 8))
        s = self._SWATCH
        for ci, (color, _, _) in enumerate(COLOR_PALETTE):
            c = tk.Canvas(sf, width=s, height=s, bg=self._CARD,
                          highlightthickness=0, cursor="hand2")
            if ci == item["color_idx"]:
                c.create_oval(1, 1, s - 1, s - 1, fill=color, outline="#FFF", width=2)
            else:
                c.create_oval(3, 3, s - 3, s - 3, fill=color, outline="")
            c.bind("<Button-1>", lambda _, i=idx, ci_=ci: self._pick_color(i, ci_))
            c.pack(side="left", padx=1)

    # -- key capture ---------------------------------------------------------
    #
    # We use pynput's low-level keyboard hook (WH_KEYBOARD_LL) instead of
    # tkinter key events.  This is necessary because:
    # 1. Tkinter misreports some Ctrl+<extended key> combos on Windows
    #    (e.g. Ctrl+PageDown arrives as Ctrl+V).
    # 2. Global hotkeys registered by other apps (via RegisterHotKey) swallow
    #    the key before tkinter ever sees it.  Low-level hooks fire first.

    def _capture_start(self, idx: int) -> None:
        self._capture_cancel()
        self._capturing = idx
        self._held_mods: set[Key] = set()
        btn = self._items[idx]["_btn"]
        btn.configure(text="Press keys\u2026", bg=self._CAPTURE)
        btn.bind("<Enter>", lambda _: None)
        btn.bind("<Leave>", lambda _: None)
        self._listener = Listener(
            on_press=self._on_hook_press,
            on_release=self._on_hook_release,
        )
        self._listener.start()

    def _on_hook_press(self, key: Key | KeyCode) -> bool | None:
        """Called on the listener thread for every key-down."""
        if key in _PYNPUT_MODIFIERS:
            self._held_mods.add(key)
            self._win.after(0, self._update_mod_preview)
            return  # keep listening

        if key == Key.esc:
            self._win.after(0, self._capture_cancel)
            return False  # stop listener

        # Non-modifier → build the combo and finalize.
        keys = self._build_capture_keys(key)
        if keys:
            self._win.after(0, self._finish_capture, keys)
        return False  # stop listener

    def _on_hook_release(self, key: Key | KeyCode) -> bool | None:
        """Called on the listener thread for every key-up."""
        if self._capturing is None:
            return False
        self._held_mods.discard(key)
        self._win.after(0, self._update_mod_preview)

    def _update_mod_preview(self) -> None:
        if self._capturing is None:
            return
        parts: list[str] = []
        if self._held_mods & {Key.ctrl_l, Key.ctrl_r}:
            parts.append("Ctrl")
        if self._held_mods & {Key.shift, Key.shift_l, Key.shift_r}:
            parts.append("Shift")
        if self._held_mods & {Key.alt_l, Key.alt_r, Key.alt_gr}:
            parts.append("Alt")
        text = "+".join(parts) + "+\u2026" if parts else "Press keys\u2026"
        self._items[self._capturing]["_btn"].configure(text=text)

    def _build_capture_keys(self, key: Key | KeyCode) -> tuple[Key | str, ...]:
        keys: list[Key | str] = []
        if self._held_mods & {Key.ctrl_l, Key.ctrl_r}:
            keys.append(Key.ctrl_l)
        if self._held_mods & {Key.shift, Key.shift_l, Key.shift_r}:
            keys.append(Key.shift_l)
        if self._held_mods & {Key.alt_l, Key.alt_r, Key.alt_gr}:
            keys.append(Key.alt_l)

        if isinstance(key, Key):
            keys.append(key)
        elif isinstance(key, KeyCode):
            if key.char and key.char.isprintable():
                keys.append(key.char.lower())
            elif key.vk is not None and chr(key.vk).isalnum():
                keys.append(chr(key.vk).lower())
            else:
                return ()

        return tuple(keys)

    def _finish_capture(self, keys: tuple[Key | str, ...]) -> None:
        if self._capturing is None:
            return
        item = self._items[self._capturing]
        item["keys"] = keys
        item["label"] = _keys_to_label(keys)
        btn = item["_btn"]
        btn.configure(text=item["label"], bg=self._BTN)
        self._hoverable(btn, self._BTN, self._BTN_HV)
        self._capturing = None
        self._held_mods = set()

    def _capture_cancel(self) -> None:
        if self._capturing is not None:
            if hasattr(self, "_listener") and self._listener.is_alive():
                self._listener.stop()
            item = self._items[self._capturing]
            btn = item["_btn"]
            btn.configure(text=item["label"], bg=self._BTN)
            self._hoverable(btn, self._BTN, self._BTN_HV)
            self._capturing = None
            self._held_mods = set()

    # -- actions -------------------------------------------------------------

    def _pick_color(self, idx: int, color_idx: int) -> None:
        self._items[idx]["color_idx"] = color_idx
        self._refresh()

    def _add(self) -> None:
        used = {it["color_idx"] for it in self._items}
        ci = next((i for i in range(len(COLOR_PALETTE)) if i not in used), 0)
        self._items.append({"keys": (Key.enter,), "label": "Enter", "color_idx": ci})
        self._refresh()
        self._capture_start(len(self._items) - 1)

    def _remove(self, idx: int) -> None:
        if len(self._items) <= 1:
            return
        self._capture_cancel()
        self._items.pop(idx)
        self._refresh()

    def _apply(self) -> None:
        self._capture_cancel()
        shortcuts: list[Shortcut] = []
        for item in self._items:
            c, h, _ = COLOR_PALETTE[item["color_idx"]]
            shortcuts.append(Shortcut(
                label=item["label"], keys=item["keys"], color=c, hover_color=h,
            ))
        self._on_apply(tuple(shortcuts))
        self._win.destroy()

    def _cancel(self) -> None:
        self._win.destroy()

    def _on_esc(self, _event: tk.Event[Any]) -> None:
        if self._capturing is not None:
            self._capture_cancel()
        else:
            self._cancel()


def _update_layered_window(hwnd: int, img: Image.Image) -> None:
    """Push an RGBA Pillow image onto a layered window with per-pixel alpha."""
    w, h = img.size

    # Convert to premultiplied-alpha BGRA (what Windows expects).
    arr = np.array(img)  # (H, W, 4) RGBA uint8
    alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
    rgb = arr[:, :, :3].astype(np.float32)
    premul = (rgb * alpha).clip(0, 255).astype(np.uint8)
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = premul[:, :, 2]  # B
    bgra[:, :, 1] = premul[:, :, 1]  # G
    bgra[:, :, 2] = premul[:, :, 0]  # R
    bgra[:, :, 3] = arr[:, :, 3]     # A
    # Flip vertically — DIB is bottom-up.
    bgra = bgra[::-1].copy()
    raw = bgra.tobytes()

    # BITMAPINFOHEADER (40 bytes)
    bmi = struct.pack(
        "IiiHHIIiiII",
        40, w, h, 1, 32, BI_RGB, len(raw), 0, 0, 0, 0,
    )

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

    ppv_bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_mem, bmi, DIB_RGB_COLORS, ctypes.byref(ppv_bits), None, 0,
    )
    gdi32.SelectObject(hdc_mem, hbmp)
    ctypes.memmove(ppv_bits, raw, len(raw))

    blend = struct.pack("BBBB", AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    pt_src = struct.pack("ii", 0, 0)
    size = struct.pack("ii", w, h)

    user32.UpdateLayeredWindow(
        hwnd, hdc_screen, None, size, hdc_mem, pt_src, 0, blend, ULW_ALPHA,
    )

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)


class SpotKey:
    """Frameless, always-on-top pie chart that sends a keystroke per segment on hover."""

    DRAG_THRESHOLD = 5  # px movement before a click becomes a drag

    def __init__(self, cfg: Config = Config(), keyboard: Controller | None = None) -> None:
        self.cfg = cfg
        self.keyboard = keyboard or Controller()
        self._active_index: int | None = None
        self._pending_index: int | None = None
        self._shortcut_timer: str | None = None

        # Menu zone state
        self._in_menu_zone = False
        self._menu_zone_hover = False
        self._dragging = False
        self._click_started_in_menu = False
        self._drag_origin: tuple[int, int] = (0, 0)
        self._click_origin: tuple[int, int] = (0, 0)

        self.root = self._build_window()
        self.canvas = self._build_canvas()
        self._menu = self._build_menu()
        self._render_pie()
        self._bind_events()

    # -- Construction --------------------------------------------------------

    def _build_window(self) -> tk.Tk:
        root = tk.Tk()
        root.title("Spot Key")
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        d = self.cfg.diameter
        root.geometry(f"{d}x{d}")
        x = root.winfo_screenwidth() - d - 40
        y = root.winfo_screenheight() // 2 - d // 2
        root.geometry(f"+{x}+{y}")

        # Make it a layered window for true per-pixel alpha.
        root.update_idletasks()
        hwnd = root.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)

        # Re-assert topmost after style change (SetWindowLongW can reset Z-order).
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

        return root

    def _build_canvas(self) -> tk.Canvas:
        d = self.cfg.diameter
        canvas = tk.Canvas(
            self.root, width=d, height=d, highlightthickness=0,
        )
        canvas.pack()
        return canvas

    def _build_menu(self) -> tk.Menu:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Settings...", command=self._open_settings)
        menu.add_separator()
        menu.add_command(label="Quit", command=self._quit)
        return menu

    # -- Pillow-based pie rendering ------------------------------------------

    def _render_pie(self, highlight: int | None = None) -> None:
        """Render the pie chart as RGBA and push to the layered window."""
        d = self.cfg.diameter
        ss = SUPERSAMPLE
        hi = d * ss
        img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        n = len(self.cfg.shortcuts)
        extent = 360 / n
        pad = 2 * ss
        bbox = (pad, pad, hi - pad, hi - pad)

        for i, sc in enumerate(self.cfg.shortcuts):
            start = 90 - i * extent
            color = sc.hover_color if i == highlight else sc.color
            draw.pieslice(bbox, start=-start, end=-(start - extent),
                          fill=color, outline=self.cfg.outline_color, width=2 * ss)

        # Draw menu button in top-left corner
        cz = self.cfg.menu_zone_size * ss
        if self._menu_zone_hover:
            btn_color = self.cfg.menu_zone_hover_color
        else:
            btn_color = self.cfg.menu_zone_color
        margin = 1 * ss
        draw.rounded_rectangle(
            (margin, margin, cz, cz),
            radius=4 * ss, fill=btn_color, outline=self.cfg.outline_color, width=1 * ss,
        )
        # Draw hamburger icon (three horizontal lines)
        line_w = 2 * ss
        line_color = "#FFFFFF"
        cx_start = 7 * ss
        cx_end = cz - 6 * ss
        cy_mid = (margin + cz) // 2
        gap = 5 * ss
        for y_off in (-gap, 0, gap):
            y = cy_mid + y_off
            draw.line((cx_start, y, cx_end, y), fill=line_color, width=line_w)

        # Downsample with LANCZOS for smooth antialiased edges
        img = img.resize((d, d), Image.LANCZOS)

        hwnd = self.root.winfo_id()
        _update_layered_window(hwnd, img)

    def _bind_events(self) -> None:
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_button_down)
        self.canvas.bind("<B1-Motion>", self._on_button_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_button_up)

    # -- Hit detection -------------------------------------------------------

    def _is_in_menu_zone(self, x: int, y: int) -> bool:
        cz = self.cfg.menu_zone_size
        return x <= cz and y <= cz

    def _index_at(self, x: int, y: int) -> int | None:
        """Return the slice index under (x, y), or None if outside the circle."""
        r = self.cfg.diameter / 2
        cx, cy = r, r
        dx, dy = x - cx, cy - y  # y-up for atan2
        if dx * dx + dy * dy > r * r:
            return None
        angle = math.degrees(math.atan2(dy, dx))  # 0°=right, 90°=up
        clock = (90 - angle) % 360
        extent = 360 / len(self.cfg.shortcuts)
        return int(clock // extent)

    # -- Menu zone -----------------------------------------------------------

    def _enter_menu_zone(self) -> None:
        if self._in_menu_zone:
            return
        self._in_menu_zone = True
        self._menu_zone_hover = True
        self._cancel_shortcut_timer()
        if self._active_index is not None:
            self._active_index = None
        self._render_pie()

    def _leave_menu_zone(self) -> None:
        self._in_menu_zone = False
        self._menu_zone_hover = False
        self._render_pie()

    def _show_menu(self) -> None:
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self._menu.tk_popup(x, y + self.cfg.menu_zone_size)

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.cfg.shortcuts, self._apply_settings)

    def _apply_settings(self, shortcuts: tuple[Shortcut, ...]) -> None:
        self.cfg = replace(self.cfg, shortcuts=shortcuts)
        self._cancel_shortcut_timer()
        self._active_index = None
        self._pending_index = None
        self._render_pie()
        _save_shortcuts(shortcuts)

    def _quit(self) -> None:
        self.root.destroy()

    # -- Hover / shortcut ----------------------------------------------------

    def _cancel_shortcut_timer(self) -> None:
        if self._shortcut_timer is not None:
            self.root.after_cancel(self._shortcut_timer)
            self._shortcut_timer = None

    def _on_motion(self, event: tk.Event[Any]) -> None:
        if self._dragging:
            return

        if self._is_in_menu_zone(event.x, event.y):
            self._enter_menu_zone()
            return

        if self._in_menu_zone:
            self._leave_menu_zone()

        idx = self._index_at(event.x, event.y)
        if idx == self._active_index or idx == self._pending_index:
            return

        self._cancel_shortcut_timer()

        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

        self._pending_index = idx
        if idx is not None:
            self._shortcut_timer = self.root.after(
                self.cfg.shortcut_hover_ms,
                self._fire_shortcut, idx,
            )

    def _fire_shortcut(self, idx: int) -> None:
        self._shortcut_timer = None
        if self._pending_index == idx:
            self._active_index = idx
            self._pending_index = None
            self._render_pie(highlight=idx)
            self._send_keys(self.cfg.shortcuts[idx].keys)

    def _on_leave(self, _event: tk.Event[Any]) -> None:
        self._cancel_shortcut_timer()
        self._pending_index = None
        if self._in_menu_zone:
            self._leave_menu_zone()
        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

    # -- Click / drag --------------------------------------------------------

    def _on_button_down(self, event: tk.Event[Any]) -> None:
        self._dragging = False
        self._click_started_in_menu = self._is_in_menu_zone(event.x, event.y)
        self._click_origin = (event.x_root, event.y_root)
        self._drag_origin = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_button_motion(self, event: tk.Event[Any]) -> None:
        if not self._click_started_in_menu:
            return
        dx = event.x_root - self._click_origin[0]
        dy = event.y_root - self._click_origin[1]
        if not self._dragging and (abs(dx) > self.DRAG_THRESHOLD or abs(dy) > self.DRAG_THRESHOLD):
            self._dragging = True
        if self._dragging:
            ox, oy = self._drag_origin
            self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def _on_button_up(self, event: tk.Event[Any]) -> None:
        if self._dragging:
            self._dragging = False
            return
        if self._click_started_in_menu:
            self._show_menu()

    def _send_keys(self, keys: tuple[Key | str, ...]) -> None:
        for k in keys:
            self.keyboard.press(k)
        for k in reversed(keys):
            self.keyboard.release(k)

    # -- Run -----------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    saved = _load_shortcuts()
    cfg = Config(shortcuts=saved) if saved else Config()
    SpotKey(cfg).run()


if __name__ == "__main__":
    main()
