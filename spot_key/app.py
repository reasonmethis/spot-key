"""Core application: the floating pie-chart overlay."""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from dataclasses import replace
from typing import Any

from PIL import Image, ImageDraw
from pynput.keyboard import Controller
from pynput.mouse import Button, Controller as MouseController

from .models import (
    Action,
    Config,
    KeyComboAction,
    MouseClickAction,
    Shortcut,
    SleepAction,
    SUPERSAMPLE,
)
from .persistence import SavedState, load_state, save_state
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
        initial_position: tuple[int, int] | None = None,
    ) -> None:
        self.cfg = cfg or Config()
        self.keyboard = keyboard or Controller()
        self.mouse = MouseController()
        self._initial_position = initial_position

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
        self._resize_center: tuple[float, float] | None = None

        # Build the UI ---------------------------------------------------------
        self.root = self._build_window()
        self.canvas = self._build_canvas()
        self._build_context_menu()
        self._menu_open = False
        self._render_pie()
        self._bind_events()

        # Tray icon — runs on a background thread, marshals back via after().
        self._tray = TrayIcon(
            is_hidden=lambda: self._hidden,
            on_toggle=lambda: self.root.after(0, self._toggle_visibility),
            on_show=lambda: self.root.after(0, self._show),
            on_hide=lambda: self.root.after(0, self._hide),
            on_settings=lambda: self.root.after(0, self._open_settings),
            on_quit=lambda: self.root.after(0, self._quit),
        )
        self._tray.start()

        # Re-assert topmost periodically: Tk on Windows sometimes drops the
        # always-on-top flag on overrideredirect windows after another app
        # briefly owns the foreground (fullscreen apps, UAC prompts, etc.).
        self._topmost_timer: str | None = None
        self._schedule_topmost_refresh()

    _TOPMOST_REFRESH_MS = 1500

    def _schedule_topmost_refresh(self) -> None:
        """Re-apply -topmost to defeat sticky z-order regressions on Windows.

        Skip while the context menu is mapped — otherwise the tick
        raises the pie over its own popup (separate HWND).
        """
        if not self._hidden and not self._menu_open:
            try:
                self.root.attributes("-topmost", True)
            except tk.TclError:
                return
        self._topmost_timer = self.root.after(
            self._TOPMOST_REFRESH_MS, self._schedule_topmost_refresh,
        )

    # ── Window construction ─────────────────────────────────────────────────

    def _build_window(self) -> tk.Tk:
        """Create a frameless, topmost, layered window sized to the pie."""
        root = tk.Tk()
        root.title("Spot Key")
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        d = self.cfg.diameter
        root.geometry(f"{d}x{d}")
        x, y = self._resolve_initial_position(root, d)
        root.geometry(f"+{x}+{y}")

        root.update_idletasks()
        make_layered(root.winfo_id())

        return root

    def _resolve_initial_position(self, root: tk.Tk, d: int) -> tuple[int, int]:
        """Return the on-screen position to open at.

        Uses the persisted position when it is still within the current
        screen bounds; otherwise falls back to the right-edge default so
        a saved position from a disconnected monitor does not strand the
        overlay off-screen.
        """
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        if self._initial_position is not None:
            x, y = self._initial_position
            if 0 <= x <= sw - d and 0 <= y <= sh - d:
                return x, y
        return sw - d - 40, sh // 2 - d // 2

    def _build_canvas(self) -> tk.Canvas:
        """Create the canvas that receives mouse events."""
        d = self.cfg.diameter
        canvas = tk.Canvas(self.root, width=d, height=d, highlightthickness=0)
        canvas.pack()
        return canvas

    # ── Dark popup menu ──────────────────────────────────────────────────

    _MENU_BG = "#1F2937"
    _MENU_FG = "#F3F4F6"
    _MENU_HV = "#374151"
    _MENU_SEP = "#4B5563"
    _MENU_FONT = ("Segoe UI", 10)
    _MENU_PAD_X = 20
    _MENU_PAD_Y = 6

    def _build_context_menu(self) -> None:
        """Prepare the popup menu items (the Toplevel is created on demand)."""
        self._menu_popup: tk.Toplevel | None = None
        self._menu_items = [
            ("Settings...", self._open_settings),
            ("Hide to tray", self._hide),
            None,  # separator
            ("Quit", self._quit),
        ]

    def _show_popup_menu(self, x: int, y: int) -> None:
        """Create and show the dark-themed popup menu at (x, y)."""
        self._dismiss_popup_menu()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=self._MENU_SEP)  # border color
        self._menu_popup = popup

        inner = tk.Frame(popup, bg=self._MENU_BG)
        inner.pack(padx=1, pady=1)  # 1px border

        for item in self._menu_items:
            if item is None:
                tk.Frame(inner, bg=self._MENU_SEP, height=1).pack(
                    fill="x", padx=4, pady=2,
                )
                continue
            label, command = item
            lbl = tk.Label(
                inner, text=label, font=self._MENU_FONT,
                bg=self._MENU_BG, fg=self._MENU_FG,
                padx=self._MENU_PAD_X, pady=self._MENU_PAD_Y,
                anchor="w", cursor="hand2",
            )
            lbl.pack(fill="x")
            lbl.bind("<Enter>", lambda _e, w=lbl: w.configure(bg=self._MENU_HV))
            lbl.bind("<Leave>", lambda _e, w=lbl: w.configure(bg=self._MENU_BG))
            lbl.bind("<Button-1>", lambda _e, cmd=command: (
                self._dismiss_popup_menu(), cmd(),
            ))

        popup.update_idletasks()
        popup.geometry(f"+{x}+{y}")

        self._menu_open = True
        # Dismiss on click anywhere outside.
        popup.bind("<FocusOut>", lambda _e: self._dismiss_popup_menu())
        popup.focus_set()

    def _dismiss_popup_menu(self) -> None:
        """Close the popup menu if open."""
        if self._menu_popup is not None:
            self._menu_popup.destroy()
            self._menu_popup = None
            self._menu_open = False

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
        c.bind("<Enter>", self._on_motion)
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
        """Pop up the dark-themed context menu below the hamburger button."""
        x = self.root.winfo_x()
        y = self.root.winfo_y() + self.cfg.menu_zone_size
        self._show_popup_menu(x, y)

    def _open_settings(self) -> None:
        # Freeze the current centre so slider preview resizes stay
        # anchored there instead of drifting from per-step rounding.
        # The anchor is snapped to integer pixels — combined with the
        # settings dialog quantising diameters to even numbers, this
        # keeps every preview geometry exact (no rounding jitter).
        old_d = self.root.winfo_width()
        self._resize_center = (
            float(self.root.winfo_x() + old_d // 2),
            float(self.root.winfo_y() + old_d // 2),
        )
        SettingsDialog(
            self.root,
            shortcuts=self.cfg.shortcuts,
            diameter=self.cfg.diameter,
            on_apply=self._apply_settings,
            on_preview_diameter=self._preview_diameter,
        )

    def _preview_diameter(self, d: int) -> None:
        """Resize without persisting — driven by the settings size slider."""
        self.cfg = replace(self.cfg, diameter=d)
        self._apply_diameter(d)

    def _apply_settings(
        self, shortcuts: tuple[Shortcut, ...], diameter: int,
    ) -> None:
        """Replace shortcuts / diameter, re-render the pie, and persist."""
        self.cfg = replace(self.cfg, shortcuts=shortcuts, diameter=diameter)
        self._cancel_shortcut_timer()
        self._active_index = None
        self._pending_index = None
        self._apply_diameter(diameter)
        self._save_state()

    def _apply_diameter(self, d: int) -> None:
        """Resize the root window and canvas to a new diameter *d*.

        Keeps the pie's centre point fixed so the widget grows and
        shrinks symmetrically around wherever the user placed it.

        If an explicit ``_resize_center`` anchor is set (by
        ``_open_settings``), new geometry is computed against that
        float anchor. Computing from ``winfo_x() + winfo_width()/2``
        each step truncates half a pixel per odd-delta move, so
        repeated slider drags drift visibly off-centre. Anchoring to
        the centre captured when the dialog opened makes successive
        previews idempotent — move the slider anywhere, come back to
        the original size, and the pie is in the original spot.
        """
        if self._resize_center is None:
            old_d = self.root.winfo_width()
            cx = self.root.winfo_x() + old_d / 2
            cy = self.root.winfo_y() + old_d / 2
        else:
            cx, cy = self._resize_center
        x = round(cx - d / 2)
        y = round(cy - d / 2)
        # Order matters: resize the canvas and redraw the pie image
        # BEFORE reshaping / moving the root window. Otherwise Tk may
        # paint the window at the new geometry with the old-sized pie
        # still in the canvas, producing a visible one-frame glitch
        # where the top-left snaps first and the pie catches up a
        # frame later.
        self.canvas.configure(width=d, height=d)
        self._render_pie()
        self.root.geometry(f"{d}x{d}+{x}+{y}")

    def _hide(self) -> None:
        """Hide the overlay. The tray icon remains the way back."""
        if self._hidden:
            return
        self._cancel_shortcut_timer()
        self._active_index = None
        self._pending_index = None
        self.root.withdraw()
        self._hidden = True
        self._tray.refresh()

    def _show(self) -> None:
        """Restore the overlay and re-assert topmost on top of other windows."""
        if not self._hidden:
            return
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self._hidden = False
        self._tray.refresh()

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
        """Dwell timer callback: run the action sequence and highlight the slice."""
        self._shortcut_timer = None
        if self._pending_index != idx:
            return
        self._active_index = idx
        self._pending_index = None
        self._render_pie(highlight=idx)
        self._run_actions(self.cfg.shortcuts[idx].actions)

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
            self._save_state()
            return
        if self._click_started_in_menu:
            self._show_context_menu()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist shortcuts, diameter, and current window position."""
        save_state(SavedState(
            shortcuts=self.cfg.shortcuts,
            diameter=self.cfg.diameter,
            position=(self.root.winfo_x(), self.root.winfo_y()),
        ))

    # ── Action execution ────────────────────────────────────────────────────

    def _run_actions(self, actions: tuple[Action, ...]) -> None:
        """Execute *actions* in order on a background thread.

        A background thread is essential because sleep actions would
        otherwise freeze the tkinter main loop (and with it the overlay,
        its tray icon, and all mouse handling). pynput's keyboard and
        mouse controllers are thread-safe, so firing input events off
        the UI thread is fine.
        """
        threading.Thread(
            target=self._run_actions_sync, args=(actions,), daemon=True,
        ).start()

    def _run_actions_sync(self, actions: tuple[Action, ...]) -> None:
        """Synchronously execute an action sequence. Runs on a worker thread."""
        for action in actions:
            if isinstance(action, KeyComboAction):
                for k in action.keys:
                    self.keyboard.press(k)
                for k in reversed(action.keys):
                    self.keyboard.release(k)
            elif isinstance(action, SleepAction):
                time.sleep(action.seconds)
            elif isinstance(action, MouseClickAction):
                self.mouse.position = (action.x, action.y)
                self.mouse.click(Button.left)

    # ── Main loop ───────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the tkinter event loop (blocks until the window is closed)."""
        self.root.mainloop()


def main() -> None:
    """Entry point: load persisted state and launch the overlay."""
    state = load_state()
    cfg = Config()
    if state.shortcuts is not None:
        cfg = replace(cfg, shortcuts=state.shortcuts)
    if state.diameter is not None:
        cfg = replace(cfg, diameter=state.diameter)
    SpotKey(cfg, initial_position=state.position).run()
