"""Core application: the floating pie-chart overlay."""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import replace
from typing import Any

from PIL import Image, ImageDraw
from pynput.keyboard import Controller, Key

from .models import Config, Shortcut, SUPERSAMPLE
from .persistence import load_shortcuts, save_shortcuts
from .settings import SettingsDialog
from .tray import TrayIcon
from .win32 import make_layered, update_layered_window


class SpotKey:
    """Frameless, always-on-top pie chart that sends a keystroke on hover.

    The widget floats over all windows and renders via Win32
    ``UpdateLayeredWindow`` for true per-pixel alpha transparency.  Each
    pie slice maps to a keyboard shortcut; hovering over a slice for
    :pyattr:`Config.shortcut_hover_ms` triggers the keystroke and
    highlights the slice for visual confirmation.

    The top-left hamburger button can be clicked to open a context menu
    (Settings / Quit) or dragged to reposition the widget.
    """

    _DRAG_THRESHOLD_PX = 5

    def __init__(
        self,
        cfg: Config | None = None,
        keyboard: Controller | None = None,
    ) -> None:
        self.cfg = cfg or Config()
        self.keyboard = keyboard or Controller()

        # Shortcut hover state -------------------------------------------------
        self._active_index: int | None = None    # slice currently highlighted
        self._pending_index: int | None = None   # slice waiting for dwell timer
        self._shortcut_timer: str | None = None  # tkinter ``after`` id

        # Menu-button / drag state ---------------------------------------------
        self._in_menu_zone = False
        self._menu_zone_hover = False
        self._dragging = False
        self._click_started_in_menu = False
        self._drag_origin = (0, 0)
        self._click_origin = (0, 0)

        self._hidden = False

        # Build the UI ---------------------------------------------------------
        self.root = self._build_window()
        self.canvas = self._build_canvas()
        self._menu = self._build_context_menu()
        self._render_pie()
        self._bind_events()

        # Tray icon — runs on a background thread, marshals back via after().
        self._tray = TrayIcon(
            on_toggle=lambda: self.root.after(0, self._toggle_visibility),
            on_show=lambda: self.root.after(0, self._show),
            on_hide=lambda: self.root.after(0, self._hide),
            on_settings=lambda: self.root.after(0, self._open_settings),
            on_quit=lambda: self.root.after(0, self._quit),
        )
        self._tray.start()

    # ── Window construction ─────────────────────────────────────────────────

    def _build_window(self) -> tk.Tk:
        """Create a frameless, topmost, layered window sized to the pie."""
        root = tk.Tk()
        root.title("Spot Key")
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        d = self.cfg.diameter
        root.geometry(f"{d}x{d}")
        x = root.winfo_screenwidth() - d - 40
        y = root.winfo_screenheight() // 2 - d // 2
        root.geometry(f"+{x}+{y}")

        root.update_idletasks()
        make_layered(root.winfo_id())

        return root

    def _build_canvas(self) -> tk.Canvas:
        """Create the canvas that receives mouse events."""
        d = self.cfg.diameter
        canvas = tk.Canvas(self.root, width=d, height=d, highlightthickness=0)
        canvas.pack()
        return canvas

    def _build_context_menu(self) -> tk.Menu:
        """Create the right-click / hamburger context menu."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Settings...", command=self._open_settings)
        menu.add_command(label="Hide to tray", command=self._hide)
        menu.add_separator()
        menu.add_command(label="Quit", command=self._quit)
        return menu

    # ── Rendering ───────────────────────────────────────────────────────────

    def _render_pie(self, highlight: int | None = None) -> None:
        """Render the pie chart as RGBA and push it to the layered window.

        Args:
            highlight: Index of the slice to draw in its ``hover_color``,
                       or ``None`` to draw all slices at rest.
        """
        d = self.cfg.diameter
        ss = SUPERSAMPLE
        hi = d * ss
        img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Pie slices — numbered clockwise from 12 o'clock.
        n = len(self.cfg.shortcuts)
        extent = 360 / n
        pad = 2 * ss
        bbox = (pad, pad, hi - pad, hi - pad)

        for i, sc in enumerate(self.cfg.shortcuts):
            start = 90 - i * extent
            color = sc.hover_color if i == highlight else sc.color
            draw.pieslice(
                bbox, start=-start, end=-(start - extent),
                fill=color, outline=self.cfg.outline_color, width=2 * ss,
            )

        self._draw_menu_button(draw, ss)

        # Downsample with LANCZOS for smooth antialiased edges.
        img = img.resize((d, d), Image.LANCZOS)
        update_layered_window(self.root.winfo_id(), img)

    def _draw_menu_button(self, draw: ImageDraw.ImageDraw, ss: int) -> None:
        """Draw the hamburger-icon button at the top-left of the pie."""
        cz = self.cfg.menu_zone_size * ss
        margin = ss
        color = (
            self.cfg.menu_zone_hover_color
            if self._menu_zone_hover
            else self.cfg.menu_zone_color
        )

        draw.rounded_rectangle(
            (margin, margin, cz, cz),
            radius=4 * ss, fill=color,
            outline=self.cfg.outline_color, width=ss,
        )

        # Three horizontal lines (hamburger icon).
        line_w = 2 * ss
        cx_start = 7 * ss
        cx_end = cz - 6 * ss
        cy_mid = (margin + cz) // 2
        gap = 5 * ss
        for y_off in (-gap, 0, gap):
            draw.line(
                (cx_start, cy_mid + y_off, cx_end, cy_mid + y_off),
                fill="#FFFFFF", width=line_w,
            )

    # ── Event binding ───────────────────────────────────────────────────────

    def _bind_events(self) -> None:
        """Wire up all mouse events on the canvas."""
        c = self.canvas
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", self._on_leave)
        c.bind("<Button-1>", self._on_button_down)
        c.bind("<B1-Motion>", self._on_button_motion)
        c.bind("<ButtonRelease-1>", self._on_button_up)

    # ── Hit detection ───────────────────────────────────────────────────────

    def _is_in_menu_zone(self, x: int, y: int) -> bool:
        """True if ``(x, y)`` falls inside the hamburger-button rectangle."""
        cz = self.cfg.menu_zone_size
        return x <= cz and y <= cz

    def _index_at(self, x: int, y: int) -> int | None:
        """Return the pie-slice index under ``(x, y)``, or ``None`` if outside.

        Slices are numbered clockwise starting from 12 o'clock.
        """
        r = self.cfg.diameter / 2
        dx, dy = x - r, r - y  # translate to y-up coords centred on the pie
        if dx * dx + dy * dy > r * r:
            return None
        angle = math.degrees(math.atan2(dy, dx))   # 0° = right, 90° = up
        clockwise = (90 - angle) % 360
        extent = 360 / len(self.cfg.shortcuts)
        return int(clockwise // extent)

    # ── Menu zone ───────────────────────────────────────────────────────────

    def _enter_menu_zone(self) -> None:
        if self._in_menu_zone:
            return
        self._in_menu_zone = True
        self._menu_zone_hover = True
        self._cancel_shortcut_timer()
        self._active_index = None
        self._render_pie()

    def _leave_menu_zone(self) -> None:
        self._in_menu_zone = False
        self._menu_zone_hover = False
        self._render_pie()

    def _show_context_menu(self) -> None:
        """Pop up the context menu anchored below the hamburger button."""
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self._menu.tk_popup(x, y + self.cfg.menu_zone_size)

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.cfg.shortcuts, self._apply_settings)

    def _apply_settings(self, shortcuts: tuple[Shortcut, ...]) -> None:
        """Replace shortcuts, re-render the pie, and persist to disk."""
        self.cfg = replace(self.cfg, shortcuts=shortcuts)
        self._cancel_shortcut_timer()
        self._active_index = None
        self._pending_index = None
        self._render_pie()
        save_shortcuts(shortcuts)

    def _hide(self) -> None:
        """Hide the overlay. The tray icon remains the way back."""
        if self._hidden:
            return
        self._cancel_shortcut_timer()
        self._active_index = None
        self._pending_index = None
        self.root.withdraw()
        self._hidden = True

    def _show(self) -> None:
        """Restore the overlay and re-assert topmost on top of other windows."""
        if not self._hidden:
            return
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._hidden = False

    def _toggle_visibility(self) -> None:
        if self._hidden:
            self._show()
        else:
            self._hide()

    def _quit(self) -> None:
        self._tray.stop()
        self.root.destroy()

    # ── Hover / shortcut triggering ─────────────────────────────────────────

    def _cancel_shortcut_timer(self) -> None:
        if self._shortcut_timer is not None:
            self.root.after_cancel(self._shortcut_timer)
            self._shortcut_timer = None

    def _on_motion(self, event: tk.Event[Any]) -> None:
        """Track which slice the cursor is over and manage dwell timers."""
        if self._dragging:
            return

        # Menu zone takes priority.
        if self._is_in_menu_zone(event.x, event.y):
            self._enter_menu_zone()
            return
        if self._in_menu_zone:
            self._leave_menu_zone()

        idx = self._index_at(event.x, event.y)

        # Already tracking this slice — nothing to do.
        if idx == self._active_index or idx == self._pending_index:
            return

        self._cancel_shortcut_timer()

        # Clear previous highlight.
        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

        # Start dwell timer for the new slice.
        self._pending_index = idx
        if idx is not None:
            self._shortcut_timer = self.root.after(
                self.cfg.shortcut_hover_ms, self._fire_shortcut, idx,
            )

    def _fire_shortcut(self, idx: int) -> None:
        """Dwell timer callback: send the keystroke and highlight the slice."""
        self._shortcut_timer = None
        if self._pending_index != idx:
            return
        self._active_index = idx
        self._pending_index = None
        self._render_pie(highlight=idx)
        self._send_keys(self.cfg.shortcuts[idx].keys)

    def _on_leave(self, _event: tk.Event[Any]) -> None:
        """Cursor left the widget — cancel everything."""
        self._cancel_shortcut_timer()
        self._pending_index = None
        if self._in_menu_zone:
            self._leave_menu_zone()
        if self._active_index is not None:
            self._active_index = None
            self._render_pie()

    # ── Click / drag ────────────────────────────────────────────────────────

    def _on_button_down(self, event: tk.Event[Any]) -> None:
        self._dragging = False
        self._click_started_in_menu = self._is_in_menu_zone(event.x, event.y)
        self._click_origin = (event.x_root, event.y_root)
        self._drag_origin = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_button_motion(self, event: tk.Event[Any]) -> None:
        """Distinguish a drag from a click using a pixel threshold."""
        if not self._click_started_in_menu:
            return
        dx = event.x_root - self._click_origin[0]
        dy = event.y_root - self._click_origin[1]
        if not self._dragging and (
            abs(dx) > self._DRAG_THRESHOLD_PX
            or abs(dy) > self._DRAG_THRESHOLD_PX
        ):
            self._dragging = True
        if self._dragging:
            ox, oy = self._drag_origin
            self.root.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def _on_button_up(self, event: tk.Event[Any]) -> None:
        if self._dragging:
            self._dragging = False
            return
        if self._click_started_in_menu:
            self._show_context_menu()

    # ── Key sending ─────────────────────────────────────────────────────────

    def _send_keys(self, keys: tuple[Key | str, ...]) -> None:
        """Press each key in order, then release in reverse (LIFO)."""
        for k in keys:
            self.keyboard.press(k)
        for k in reversed(keys):
            self.keyboard.release(k)

    # ── Main loop ───────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the tkinter event loop (blocks until the window is closed)."""
        self.root.mainloop()


def main() -> None:
    """Entry point: load persisted shortcuts (if any) and launch the overlay."""
    saved = load_shortcuts()
    cfg = Config(shortcuts=saved) if saved else Config()
    SpotKey(cfg).run()
