"""Capture the Settings dialog and save as screenshot-settings.png.

Usage:
    uv run python take_screenshot.py

Opens the Settings dialog using the current saved config (shortcuts,
diameter, opacity), waits for it to render, captures the window, and
saves the result to screenshot-settings.png in the project root.

Fully automated — no need to have the app running.
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

# Make process DPI-aware before any window creation
ctypes.windll.user32.SetProcessDPIAware()

SETTINGS_TITLE = "Spot Key \u2014 Settings"
OUT_PATH = Path(__file__).parent / "screenshot-settings.png"


def find_window(title: str) -> int | None:
    """Find a visible window by exact title. Returns HWND or None."""
    user32 = ctypes.windll.user32
    result = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value == title:
                    result.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return result[0] if result else None


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Get the extended frame bounds (tight, no invisible shadow)."""
    rect = wt.RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS = 9
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
    )
    if hr == 0:
        return rect.left, rect.top, rect.right, rect.bottom
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def capture_and_exit(root: tk.Tk) -> None:
    """Find the Settings window, capture it, destroy the root, and exit."""
    hwnd = find_window(SETTINGS_TITLE)
    if hwnd is None:
        print("ERROR: Settings window not found after opening.")
        root.destroy()
        sys.exit(1)

    # Bring to front and let it fully paint
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    root.update()
    time.sleep(0.15)  # let DWM finish compositing

    left, top, right, bottom = get_window_rect(hwnd)
    print(f"Window rect: ({left}, {top}) - ({right}, {bottom})")
    print(f"Size: {right - left} x {bottom - top}")

    img = ImageGrab.grab(bbox=(left, top, right, bottom))
    img.save(OUT_PATH)
    print(f"Saved to {OUT_PATH} ({img.size[0]}x{img.size[1]})")

    root.destroy()


def main():
    from spot_key.models import Config
    from spot_key.persistence import load_state

    # Load current saved config
    state = load_state()
    shortcuts = state.shortcuts or Config().shortcuts
    diameter = state.diameter or Config().diameter
    opacity = state.opacity if state.opacity is not None else Config().opacity

    # Create a hidden root — the Settings dialog is a Toplevel on top of it
    root = tk.Tk()
    root.withdraw()

    # Import here so tk is already initialised
    from spot_key.settings import SettingsDialog

    # Open settings with real config (no-op callbacks)
    SettingsDialog(
        root,
        shortcuts=shortcuts,
        diameter=diameter,
        opacity=opacity,
        on_apply=None,
        on_preview_diameter=None,
        on_preview_opacity=None,
    )

    # Schedule capture after the dialog has rendered
    root.after(500, lambda: capture_and_exit(root))
    root.mainloop()


if __name__ == "__main__":
    main()
