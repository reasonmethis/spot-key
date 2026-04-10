"""Spot Key — a floating pie-chart button that triggers shortcuts on hover."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import struct
import sys
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageTk
from pynput.keyboard import Controller, Key

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
    close_zone_size: int = 28
    close_zone_color: str = "#6B7280"
    close_zone_warn_color: str = "#EF4444"
    shortcut_hover_ms: int = 330  # ms hover before a shortcut fires
    close_auto_quit_ms: int = 5000


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

    # BLENDFUNCTION packed as a DWORD: (BlendOp, BlendFlags, SourceConstantAlpha, AlphaFormat)
    blend = struct.pack("BBBB", AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    pt_src = struct.pack("ii", 0, 0)
    size = struct.pack("ii", w, h)

    user32.UpdateLayeredWindow(
        hwnd, hdc_screen,
        None,                   # pptDst — keep current position
        size,                   # psize
        hdc_mem,                # hdcSrc
        pt_src,                 # pptSrc
        0,                      # crKey (unused)
        blend,                  # pblend
        ULW_ALPHA,              # dwFlags
    )

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)


class SpotKey:
    """Frameless, always-on-top pie chart that sends a keystroke per segment on hover."""

    def __init__(self, cfg: Config = Config(), keyboard: Controller | None = None) -> None:
        self.cfg = cfg
        self.keyboard = keyboard or Controller()
        self._active_index: int | None = None   # currently highlighted (fired)
        self._pending_index: int | None = None  # waiting for hover timer
        self._drag_origin: tuple[int, int] = (0, 0)
        self._shortcut_timer: str | None = None

        # Close-zone state
        self._in_close_zone = False
        self._close_zone_armed = False
        self._close_auto_quit_timer: str | None = None

        self.root = self._build_window()
        self.canvas = self._build_canvas()
        self._photo: ImageTk.PhotoImage | None = None  # prevent GC
        self._canvas_image: int | None = None
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

        return root

    def _build_canvas(self) -> tk.Canvas:
        d = self.cfg.diameter
        canvas = tk.Canvas(
            self.root, width=d, height=d, highlightthickness=0,
        )
        canvas.pack()
        return canvas

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

        # Draw close zone in top-left corner
        cz = self.cfg.close_zone_size * ss
        cz_color = self.cfg.close_zone_warn_color if self._close_zone_armed else self.cfg.close_zone_color
        margin = 1 * ss
        draw.rounded_rectangle(
            (margin, margin, cz, cz),
            radius=4 * ss, fill=cz_color, outline=self.cfg.outline_color, width=1 * ss,
        )
        # Draw "×" in the close zone
        x_margin = 6 * ss
        x_size = cz - x_margin * 2 + margin
        draw.line(
            (x_margin, x_margin, x_margin + x_size, x_margin + x_size),
            fill="#FFFFFF", width=2 * ss,
        )
        draw.line(
            (x_margin + x_size, x_margin, x_margin, x_margin + x_size),
            fill="#FFFFFF", width=2 * ss,
        )

        # Downsample with LANCZOS for smooth antialiased edges
        img = img.resize((d, d), Image.LANCZOS)

        # Push the RGBA image to the layered window — true per-pixel alpha,
        # no fringe on any background.
        hwnd = self.root.winfo_id()
        _update_layered_window(hwnd, img)

    def _bind_events(self) -> None:
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_drag_start)
        self.canvas.bind("<B3-Motion>", self._on_drag_motion)

    # -- Hit detection -------------------------------------------------------

    def _is_in_close_zone(self, x: int, y: int) -> bool:
        cz = self.cfg.close_zone_size
        return x <= cz and y <= cz

    def _index_at(self, x: int, y: int) -> int | None:
        """Return the slice index under (x, y), or None if outside the circle."""
        r = self.cfg.diameter / 2
        cx, cy = r, r
        dx, dy = x - cx, cy - y  # y-up for atan2
        if dx * dx + dy * dy > r * r:
            return None
        angle = math.degrees(math.atan2(dy, dx))  # 0°=right, 90°=up
        # Normalise so 0° = 12-o'clock, increasing clockwise
        clock = (90 - angle) % 360
        extent = 360 / len(self.cfg.shortcuts)
        return int(clock // extent)

    # -- Close zone ----------------------------------------------------------

    def _enter_close_zone(self) -> None:
        if self._in_close_zone:
            return
        self._in_close_zone = True
        self._close_zone_armed = True
        self._cancel_shortcut_timer()
        if self._active_index is not None:
            self._active_index = None
        self._render_pie()
        self._close_auto_quit_timer = self.root.after(
            self.cfg.close_auto_quit_ms, self._quit,
        )

    def _leave_close_zone(self) -> None:
        self._in_close_zone = False
        self._close_zone_armed = False
        if self._close_auto_quit_timer is not None:
            self.root.after_cancel(self._close_auto_quit_timer)
            self._close_auto_quit_timer = None
        self._render_pie()

    def _quit(self) -> None:
        self.root.destroy()

    # -- Hover / shortcut ----------------------------------------------------

    def _cancel_shortcut_timer(self) -> None:
        if self._shortcut_timer is not None:
            self.root.after_cancel(self._shortcut_timer)
            self._shortcut_timer = None

    def _on_motion(self, event: tk.Event[Any]) -> None:
        if self._is_in_close_zone(event.x, event.y):
            self._enter_close_zone()
            return

        if self._in_close_zone:
            self._leave_close_zone()

        idx = self._index_at(event.x, event.y)
        if idx == self._active_index or idx == self._pending_index:
            return

        self._cancel_shortcut_timer()

        # Reset highlight from previous slice
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
        if self._in_close_zone:
            self._leave_close_zone()
        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

    def _on_click(self, event: tk.Event[Any]) -> None:
        if self._close_zone_armed and self._is_in_close_zone(event.x, event.y):
            self._quit()

    def _send_keys(self, keys: tuple[Key | str, ...]) -> None:
        for k in keys:
            self.keyboard.press(k)
        for k in reversed(keys):
            self.keyboard.release(k)

    # -- Drag ----------------------------------------------------------------

    def _on_drag_start(self, event: tk.Event[Any]) -> None:
        self._drag_origin = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_drag_motion(self, event: tk.Event[Any]) -> None:
        dx, dy = self._drag_origin
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _on_quit(self, _event: tk.Event[Any]) -> None:
        self._quit()

    # -- Run -----------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    SpotKey().run()


if __name__ == "__main__":
    main()
