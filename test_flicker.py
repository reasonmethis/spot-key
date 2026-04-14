"""Fast window frame capture via Win32 BitBlt to detect flicker during
settings-dialog row rebuilds.

This is a regression test for the flicker fixes in ``spot_key.settings``.
Captures ~60 frames per second of the dialog's window region via GDI
BitBlt (fast enough that a single-frame repaint artefact shows up in at
least one sample), and flags any frame that differs significantly from
both the stable baseline and the stable final state — those are genuine
transients visible to the user during rebuild.

Two-step sensitivity check:

1. First run injects a 50 ms sleep into ``_refresh_rows`` so the list
   frame is demonstrably empty for several capture ticks. This must be
   detected — if not, the test itself is broken and exits non-zero.
2. Second step runs each rebuild operation (move, remove, add) against
   the real ``_refresh_rows`` and reports whether any flicker remains.

Add/remove will always legitimately resize the dialog (the row count is
changing), so a few "transient" frames with small ``d_final`` values are
expected there — they are the settled final state captured before the
fixed region shrinks to match the new window size. Reorder (move) is
the canonical no-flicker case.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
import tkinter as tk

import numpy as np
from PIL import Image
from pynput.keyboard import Key

from spot_key.models import COLOR_PALETTE, KeyComboAction, Shortcut
from spot_key.settings import SettingsDialog, _ShortcutItem


# -- Win32 GDI bindings ------------------------------------------------------

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wt.DWORD * 3),
    ]


def capture_window(hwnd: int, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Capture a region of the screen via BitBlt."""
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)
    return arr[:, :, :3].copy()  # drop alpha


# -- Test harness ------------------------------------------------------------

# Threshold for "the frame meaningfully differs from a reference state".
# Both base and final thresholds must trip for a frame to count as a
# transient — this filters out GDI capture noise and small anti-aliasing
# variation while still catching the single-frame empty flashes that
# previously plagued _refresh_rows (flash diffs used to be ~120+).
TRANSIENT_THRESHOLD = 3.0


def run_flicker_test(label: str, delay_in_rebuild: float = 0.0,
                     operation: str = "move") -> bool:
    """Open a settings dialog, capture frames while triggering a rebuild.

    Returns True if any transient flicker was detected.
    """
    root = tk.Tk()
    root.withdraw()

    shortcuts = tuple(
        Shortcut(
            label=l,
            actions=(KeyComboAction(keys=k),),
            color=COLOR_PALETTE[i][0],
            hover_color=COLOR_PALETTE[i][1],
        )
        for i, (l, k) in enumerate([
            ("Ctrl+C", (Key.ctrl_l, "c")),
            ("Ctrl+V", (Key.ctrl_l, "v")),
            ("Ctrl+Z", (Key.ctrl_l, "z")),
            ("Alt+Tab", (Key.alt_l, Key.tab)),
        ])
    )

    dialog = SettingsDialog(
        root, shortcuts=shortcuts, diameter=160, on_apply=lambda *_: None,
    )
    dialog._win.protocol("WM_DELETE_WINDOW", root.destroy)

    # Optionally inject a sleep in _refresh_rows to simulate a visible
    # empty-frame flicker that the test MUST detect (the sensitivity
    # check). This monkey-patch bypasses all the real anti-flicker work
    # so the empty-list state is guaranteed to be painted.
    if delay_in_rebuild > 0:
        def slow_refresh():
            for child in dialog._list_frame.winfo_children():
                child.destroy()
            dialog._swatch_images = []
            dialog._win.update()  # force the empty state to repaint
            time.sleep(delay_in_rebuild)
            for idx in range(len(dialog._items)):
                dialog._build_row(idx)
            dialog._win.update()
        dialog._refresh_rows = slow_refresh

    frames: list[tuple[str, np.ndarray, float]] = []
    region: dict[str, int] = {}
    capture_active = threading.Event()
    stop_capture = threading.Event()

    def capture_loop():
        start = time.perf_counter()
        while not stop_capture.is_set():
            if capture_active.is_set():
                frame = capture_window(**region)
                frames.append((f"f{len(frames):03d}", frame,
                               time.perf_counter() - start))

    def run() -> bool:
        win = dialog._win
        win.update()
        time.sleep(0.5)  # let the window fully settle
        win.update()
        region["hwnd"] = win.winfo_id()
        region["x"] = win.winfo_rootx()
        region["y"] = win.winfo_rooty()
        region["w"] = win.winfo_width()
        region["h"] = win.winfo_height()

        baseline = capture_window(**region)

        t = threading.Thread(target=capture_loop, daemon=True)
        t.start()

        capture_active.set()
        time.sleep(0.02)  # a handful of pre-rebuild frames
        if operation == "move":
            dialog._move(0, 1)
        elif operation == "remove":
            dialog._remove(1)
        elif operation == "add":
            new = _ShortcutItem(
                actions=[KeyComboAction(keys=(Key.enter,))],
                color_idx=len(dialog._items) % 8,
            )
            dialog._items.append(new)
            dialog._refresh_rows()
        dialog._win.update()
        time.sleep(0.3)  # catch any delayed repaint transients
        capture_active.clear()
        stop_capture.set()
        t.join(timeout=0.5)

        dialog._win.update()
        time.sleep(0.1)
        dialog._win.update()
        final = capture_window(**region)

        d_final_vs_base = float(
            np.abs(final.astype(int) - baseline.astype(int)).mean()
        )
        transients = []
        for name, frame, ts in frames:
            d_base = float(np.abs(frame.astype(int) - baseline.astype(int)).mean())
            d_final = float(np.abs(frame.astype(int) - final.astype(int)).mean())
            if d_base > TRANSIENT_THRESHOLD and d_final > TRANSIENT_THRESHOLD:
                transients.append((name, d_base, d_final, frame, ts))

        print(f"\n=== {label} ===")
        print(f"  captured {len(frames)} frames, "
              f"final-vs-baseline diff: {d_final_vs_base:.2f}")
        print(f"  transient frames: {len(transients)}")
        if transients:
            worst = max(transients, key=lambda x: x[1])
            print(f"  worst: {worst[0]} @ {worst[4]*1000:.1f}ms "
                  f"base_diff={worst[1]:.1f} final_diff={worst[2]:.1f}")
            Image.fromarray(worst[3]).resize(
                (worst[3].shape[1]*3, worst[3].shape[0]*3), Image.NEAREST
            ).save(f"flicker_{label}_worst.png")
            return True
        return False

    result = [False]
    def finish():
        result[0] = run()
        root.after(100, root.destroy)

    root.after(500, finish)
    root.mainloop()
    return result[0]


if __name__ == "__main__":
    # STEP 1: Sensitivity check — injected 50 ms sleep MUST be detected.
    print("STEP 1: Validate test sensitivity with a 50 ms injected delay")
    if not run_flicker_test("synthetic", delay_in_rebuild=0.05):
        print("\n!!! Test insensitive — did not detect synthetic flicker !!!")
        sys.exit(1)
    print("  -> test sensitivity confirmed")

    # STEP 2: Real code, one pass per rebuild path.
    print("\nSTEP 2: Exercise the real _refresh_rows across operations")
    move_flicker = run_flicker_test("real_move", operation="move")
    remove_flicker = run_flicker_test("real_remove", operation="remove")
    add_flicker = run_flicker_test("real_add", operation="add")

    print("\n=== Summary ===")
    print(f"  move:   {'FLICKER' if move_flicker else 'clean'}")
    print(f"  remove: {'FLICKER' if remove_flicker else 'clean'} "
          "(small residuals expected — window resizes)")
    print(f"  add:    {'FLICKER' if add_flicker else 'clean'} "
          "(small residuals expected — window resizes)")

    # Only the reorder path must be perfectly flicker-free; add/remove
    # necessarily resize the window and the fixed capture region will
    # show small diffs where the window edge moves.
    sys.exit(0 if not move_flicker else 2)
