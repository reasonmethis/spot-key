"""Spot Key — a floating pie-chart button that triggers shortcuts on hover."""

from __future__ import annotations

import ctypes
import math
import sys
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw, ImageTk
from pynput.keyboard import Controller, Key

# Enable per-monitor DPI awareness so Windows doesn't bitmap-scale us.
if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[union-attr]

SUPERSAMPLE = 4  # draw at Nx resolution, downsample for smooth edges


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
    diameter: int = 80
    outline_color: str = "#374151"
    transparent_color: str = "#010101"


class SpotKey:
    """Frameless, always-on-top pie chart that sends a keystroke per segment on hover."""

    def __init__(self, cfg: Config = Config(), keyboard: Controller | None = None) -> None:
        self.cfg = cfg
        self.keyboard = keyboard or Controller()
        self._active_index: int | None = None
        self._drag_origin: tuple[int, int] = (0, 0)

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
        root.attributes("-transparentcolor", self.cfg.transparent_color)
        root.configure(bg=self.cfg.transparent_color)

        d = self.cfg.diameter
        root.geometry(f"{d}x{d}")
        x = root.winfo_screenwidth() - d - 40
        y = root.winfo_screenheight() // 2 - d // 2
        root.geometry(f"+{x}+{y}")
        return root

    def _build_canvas(self) -> tk.Canvas:
        d = self.cfg.diameter
        canvas = tk.Canvas(
            self.root, width=d, height=d,
            bg=self.cfg.transparent_color, highlightthickness=0,
        )
        canvas.pack()
        return canvas

    # -- Pillow-based pie rendering ------------------------------------------

    def _render_pie(self, highlight: int | None = None) -> None:
        """Render the pie chart with Pillow (supersampled for antialiasing)."""
        d = self.cfg.diameter
        hi = d * SUPERSAMPLE
        img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        n = len(self.cfg.shortcuts)
        extent = 360 / n
        pad = 2 * SUPERSAMPLE
        bbox = (pad, pad, hi - pad, hi - pad)

        for i, sc in enumerate(self.cfg.shortcuts):
            start = 90 - i * extent
            color = sc.hover_color if i == highlight else sc.color
            draw.pieslice(bbox, start=-start, end=-(start - extent),
                          fill=color, outline=self.cfg.outline_color, width=2 * SUPERSAMPLE)

        # Downsample with LANCZOS for smooth edges
        img = img.resize((d, d), Image.LANCZOS)

        # Composite onto a background matching the transparent color so tkinter
        # can punch through the non-pie area.
        bg = Image.new("RGBA", (d, d), self.cfg.transparent_color)
        bg.paste(img, (0, 0), img)
        final = bg.convert("RGB")

        self._photo = ImageTk.PhotoImage(final)
        if self._canvas_image is None:
            self._canvas_image = self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
        else:
            self.canvas.itemconfig(self._canvas_image, image=self._photo)

    def _bind_events(self) -> None:
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-3>", self._on_drag_start)
        self.canvas.bind("<B3-Motion>", self._on_drag_motion)
        self.canvas.bind("<Double-Button-3>", self._on_quit)

    # -- Hit detection -------------------------------------------------------

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

    # -- Hover / shortcut ----------------------------------------------------

    def _on_motion(self, event: tk.Event[Any]) -> None:
        idx = self._index_at(event.x, event.y)
        if idx == self._active_index:
            return

        self._active_index = idx
        self._render_pie(highlight=idx)

        if idx is not None:
            self._send_keys(self.cfg.shortcuts[idx].keys)

    def _on_leave(self, _event: tk.Event[Any]) -> None:
        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

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
        self.root.destroy()

    # -- Run -----------------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    SpotKey().run()


if __name__ == "__main__":
    main()
