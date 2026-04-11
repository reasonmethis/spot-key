"""Win32 layered-window helpers for true per-pixel alpha transparency.

Windows' ``UpdateLayeredWindow`` API composites an RGBA bitmap onto the
desktop with proper alpha blending — no colour-key hacks, no edge fringing.

This module handles:
- Per-monitor DPI awareness (must be set before any window is created).
- The Pillow RGBA → pre-multiplied BGRA → DIBSection pipeline that
  ``UpdateLayeredWindow`` requires.
"""

from __future__ import annotations

import ctypes
import struct
import sys

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# DPI awareness — must run before any tkinter or Win32 window is created.
# Per-monitor-v2 (value 2) prevents Windows from bitmap-scaling the overlay.
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

GWL_EXSTYLE    = -20
WS_EX_LAYERED  = 0x0008_0000
HWND_TOPMOST   = -1
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010

_AC_SRC_OVER    = 0
_AC_SRC_ALPHA   = 1
_ULW_ALPHA      = 2
_BI_RGB         = 0
_DIB_RGB_COLORS = 0

_user32 = ctypes.windll.user32
_gdi32  = ctypes.windll.gdi32

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def make_layered(hwnd: int) -> None:
    """Add ``WS_EX_LAYERED`` to *hwnd* and re-assert ``TOPMOST`` z-order.

    Changing the extended window style with ``SetWindowLongW`` can silently
    reset the window's z-order, so we immediately call ``SetWindowPos`` to
    put it back on top.
    """
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
    _user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


def update_layered_window(hwnd: int, img: Image.Image) -> None:
    """Push an RGBA Pillow image onto a layered window with per-pixel alpha.

    The image is converted to pre-multiplied-alpha BGRA (the format Windows
    expects), packed into a bottom-up DIBSection, and handed to
    ``UpdateLayeredWindow``.  All GDI resources are freed before returning.
    """
    w, h = img.size

    # RGBA → pre-multiplied BGRA.
    arr = np.array(img)                           # (H, W, 4) RGBA uint8
    alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
    rgb   = arr[:, :, :3].astype(np.float32)
    premul = (rgb * alpha).clip(0, 255).astype(np.uint8)

    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = premul[:, :, 2]              # B
    bgra[:, :, 1] = premul[:, :, 1]              # G
    bgra[:, :, 2] = premul[:, :, 0]              # R
    bgra[:, :, 3] = arr[:, :, 3]                 # A (unchanged)
    bgra = bgra[::-1].copy()                     # flip — DIB is bottom-up
    raw  = bgra.tobytes()

    # BITMAPINFOHEADER (40 bytes).
    bmi = struct.pack(
        "IiiHHIIiiII",
        40, w, h, 1, 32, _BI_RGB, len(raw), 0, 0, 0, 0,
    )

    hdc_screen = _user32.GetDC(0)
    hdc_mem    = _gdi32.CreateCompatibleDC(hdc_screen)

    ppv_bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(
        hdc_mem, bmi, _DIB_RGB_COLORS, ctypes.byref(ppv_bits), None, 0,
    )
    _gdi32.SelectObject(hdc_mem, hbmp)
    ctypes.memmove(ppv_bits, raw, len(raw))

    blend  = struct.pack("BBBB", _AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
    pt_src = struct.pack("ii", 0, 0)
    size   = struct.pack("ii", w, h)

    _user32.UpdateLayeredWindow(
        hwnd, hdc_screen, None, size, hdc_mem, pt_src, 0, blend, _ULW_ALPHA,
    )

    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(0, hdc_screen)
