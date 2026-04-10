"""Spot Key — a small floating circle that triggers a keystroke on hover."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Any

from pynput.keyboard import Controller, Key


@dataclass(frozen=True)
class Config:
    """All tunables in one place for future UI binding."""

    modifier: Key = Key.ctrl_l
    key: str = "q"
    diameter: int = 80
    color: str = "#4A90D9"
    hover_color: str = "#E84040"
    outline_color: str = "#2C5F9E"
    transparent_color: str = "#010101"


class SpotKey:
    """Frameless, always-on-top circle that sends a keystroke when the mouse enters."""

    def __init__(self, cfg: Config = Config(), keyboard: Controller | None = None) -> None:
        self.cfg = cfg
        self.keyboard = keyboard or Controller()
        self.triggered = False
        self._drag_origin: tuple[int, int] = (0, 0)

        self.root = self._build_window()
        self.canvas, self.circle = self._build_circle()
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

    def _build_circle(self) -> tuple[tk.Canvas, int]:
        d = self.cfg.diameter
        canvas = tk.Canvas(
            self.root, width=d, height=d,
            bg=self.cfg.transparent_color, highlightthickness=0,
        )
        canvas.pack()

        pad = 2
        circle = canvas.create_oval(
            pad, pad, d - pad, d - pad,
            fill=self.cfg.color, outline=self.cfg.outline_color, width=2,
        )
        return canvas, circle

    def _bind_events(self) -> None:
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-3>", self._on_drag_start)
        self.canvas.bind("<B3-Motion>", self._on_drag_motion)
        self.canvas.bind("<Double-Button-3>", self._on_quit)

    # -- Hover / shortcut ----------------------------------------------------

    def _on_enter(self, _event: tk.Event[Any]) -> None:
        if self.triggered:
            return
        self.triggered = True
        self.canvas.itemconfig(self.circle, fill=self.cfg.hover_color)
        self.keyboard.press(self.cfg.modifier)
        self.keyboard.press(self.cfg.key)
        self.keyboard.release(self.cfg.key)
        self.keyboard.release(self.cfg.modifier)

    def _on_leave(self, _event: tk.Event[Any]) -> None:
        self.triggered = False
        self.canvas.itemconfig(self.circle, fill=self.cfg.color)

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
