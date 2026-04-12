"""System-tray icon for showing, hiding, and quitting the overlay.

The tray icon runs on a background thread (``pystray.Icon.run_detached``)
and dispatches menu actions from that thread. Callers must marshal any
tkinter interaction back to the main thread via ``root.after(0, fn)`` —
see ``SpotKey`` for how the callbacks are wired up.
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from .models import SUPERSAMPLE


def _make_icon_image(size: int = 64) -> Image.Image:
    """Render a miniature four-slice pie as the tray icon."""
    ss = SUPERSAMPLE
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = ("#2563EB", "#16A34A", "#DC2626", "#CA8A04")
    pad = 2 * ss
    bbox = (pad, pad, big - pad, big - pad)
    for i, color in enumerate(colors):
        draw.pieslice(
            bbox, start=-(90 - i * 90), end=-(90 - (i + 1) * 90),
            fill=color, outline="#FFFFFF", width=2 * ss,
        )
    return img.resize((size, size), Image.LANCZOS)


class TrayIcon:
    """Wraps a ``pystray.Icon`` with Show / Hide / Settings / Quit actions.

    All callbacks are invoked on the tray's background thread. The caller
    is responsible for marshalling back to the tk main loop.
    """

    def __init__(
        self,
        *,
        is_hidden: Callable[[], bool],
        on_toggle: Callable[[], None],
        on_show: Callable[[], None],
        on_hide: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        # A hidden "default" MenuItem makes left-clicks on the tray icon
        # invoke `on_toggle` without adding a redundant entry to the menu.
        # Show/Hide are mutually exclusive — pystray re-evaluates the
        # `visible` lambdas each time the menu pops up.
        menu = pystray.Menu(
            pystray.MenuItem(
                "Toggle", lambda _icon, _item: on_toggle(),
                default=True, visible=False,
            ),
            pystray.MenuItem(
                "Show", lambda _icon, _item: on_show(),
                visible=lambda _item: is_hidden(),
            ),
            pystray.MenuItem(
                "Hide", lambda _icon, _item: on_hide(),
                visible=lambda _item: not is_hidden(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings…", lambda _icon, _item: on_settings()),
            pystray.MenuItem("Quit", lambda _icon, _item: on_quit()),
        )
        self._icon = pystray.Icon(
            "spot_key", _make_icon_image(), "Spot Key", menu,
        )

    def start(self) -> None:
        """Start the tray icon on a detached background thread."""
        self._icon.run_detached()

    def stop(self) -> None:
        """Remove the tray icon."""
        self._icon.stop()

    def refresh(self) -> None:
        """Re-evaluate dynamic menu state (``visible`` / ``text`` callables).

        On Windows the tray menu is a cached Win32 HMENU — pystray will
        not pick up changes to ``visible`` lambdas until ``update_menu``
        is called explicitly. Call this after any state change that
        affects how the menu should render.
        """
        self._icon.update_menu()
