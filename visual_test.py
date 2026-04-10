"""Visual test: render the pie on a white background, crop, and save for inspection."""

from __future__ import annotations

import sys
import ctypes
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

from PIL import ImageGrab

from spot_key import SpotKey, Config

if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

OUTPUT_DIR = Path(__file__).parent
PADDING = 40  # space around the circle


def run_visual_test() -> None:
    cfg = Config()
    d = cfg.diameter
    win_size = d + PADDING * 2

    app = SpotKey(cfg=cfg, keyboard=MagicMock())

    sx = app.root.winfo_screenwidth() // 2 - win_size // 2
    sy = app.root.winfo_screenheight() // 2 - win_size // 2

    backgrounds = [("white", "white"), ("dark", "#1E1E1E")]
    backdrops: list[tk.Toplevel] = []

    for i, (name, color) in enumerate(backgrounds):
        bx = sx + i * (win_size + 20)
        bd = tk.Toplevel(app.root)
        bd.overrideredirect(True)
        bd.attributes("-topmost", True)
        bd.geometry(f"{win_size}x{win_size}+{bx}+{sy}")
        bd.configure(bg=color)
        backdrops.append(bd)

    # Place the pie on the white backdrop (first one)
    app.root.geometry(f"+{sx + PADDING}+{sy + PADDING}")
    app.root.update()
    for bd in backdrops:
        bd.lower(app.root)
    app.root.update()

    def capture_white() -> None:
        bx = sx
        img = ImageGrab.grab(bbox=(bx, sy, bx + win_size, sy + win_size))
        out = OUTPUT_DIR / "visual_test_white.png"
        img.save(out)
        print(f"Saved {out}")

        # Now move pie to dark backdrop
        bx_dark = sx + win_size + 20
        app.root.geometry(f"+{bx_dark + PADDING}+{sy + PADDING}")
        app.root.update()
        app.root.after(300, capture_dark)

    def capture_dark() -> None:
        bx = sx + win_size + 20
        img = ImageGrab.grab(bbox=(bx, sy, bx + win_size, sy + win_size))
        out = OUTPUT_DIR / "visual_test_dark.png"
        img.save(out)
        print(f"Saved {out}")
        for bd in backdrops:
            bd.destroy()
        app.root.destroy()

    app.root.after(500, capture_white)
    app.root.mainloop()



if __name__ == "__main__":
    run_visual_test()
