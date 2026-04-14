"""Tests for Spot Key."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock, call, patch
import pytest
from pynput.keyboard import Key

from spot_key import SpotKey, Config, Shortcut
from spot_key.models import KeyComboAction


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def app(cfg):
    """SpotKey with a mocked keyboard controller."""
    kb = MagicMock()
    instance = SpotKey(cfg=cfg, keyboard=kb)
    # Run action sequences synchronously in tests so assertions can
    # inspect keyboard calls immediately after _fire_shortcut.
    instance._run_actions = instance._run_actions_sync
    instance.root.update_idletasks()
    yield instance
    try:
        instance.root.destroy()
    except tk.TclError:
        pass


def _combo_keys(sc: Shortcut) -> tuple:
    """Return the keys of a single-key-combo shortcut's first action."""
    assert isinstance(sc.actions[0], KeyComboAction)
    return sc.actions[0].keys


def _event(**kwargs: object) -> MagicMock:
    e = MagicMock()
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


class TestIndexAt:
    def test_top_center_is_first_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid, 5) == 0

    def test_right_of_center_is_second_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid + 20, mid + 15) == 1

    def test_left_of_center_is_third_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid - 60, mid) == 2

    def test_outside_circle_returns_none(self, app):
        assert app._index_at(0, 0) is None

    def test_exact_center_returns_index(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid, mid) is not None


class TestShortcutTrigger:
    def test_hover_does_not_fire_immediately(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app.keyboard.press.assert_not_called()

    def test_hover_fires_after_timer(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        for k in _combo_keys(cfg.shortcuts[0]):
            app.keyboard.press.assert_any_call(k)
            app.keyboard.release.assert_any_call(k)

    def test_no_retrigger_in_same_slice(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        app.keyboard.reset_mock()
        app._on_motion(_event(x=mid, y=6))
        app.keyboard.press.assert_not_called()

    def test_moving_to_different_slice_cancels_and_starts_new(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._on_motion(_event(x=mid + 20, y=mid + 15))
        app._fire_shortcut(1)
        for k in _combo_keys(cfg.shortcuts[1]):
            app.keyboard.press.assert_any_call(k)

    def test_leave_cancels_pending_shortcut(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._on_leave(_event())
        assert app._shortcut_timer is None

    def test_leave_and_reenter_retriggers(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        app.keyboard.reset_mock()
        app._on_leave(_event())
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        for k in _combo_keys(cfg.shortcuts[0]):
            app.keyboard.press.assert_any_call(k)

    def test_key_press_and_release_order(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        keys = _combo_keys(cfg.shortcuts[0])
        assert app.keyboard.press.call_args_list == [call(k) for k in keys]
        assert app.keyboard.release.call_args_list == [call(k) for k in reversed(keys)]


class TestVisualFeedback:
    def test_hover_does_not_highlight_immediately(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        assert app._active_index is None
        assert app._pending_index == 0

    def test_highlight_appears_on_fire(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._fire_shortcut(0)
        assert app._active_index == 0

    def test_leave_clears_both_indices(self, app):
        mid = app.cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._on_leave(_event())
        assert app._active_index is None
        assert app._pending_index is None

    def test_render_pie_highlights_on_fire(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        with patch.object(app, "_render_pie") as mock_render:
            app._fire_shortcut(0)
            mock_render.assert_called_once_with(highlight=0)


class TestMenuZone:
    def test_hover_enters_menu_zone(self, app):
        app._on_motion(_event(x=5, y=5))
        assert app._in_menu_zone is True
        assert app._menu_zone_hover is True

    def test_leaving_menu_zone_resets(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=5, y=5))
        app._on_motion(_event(x=mid, y=mid))
        assert app._in_menu_zone is False
        assert app._menu_zone_hover is False

    def test_leave_canvas_resets_menu_zone(self, app):
        app._on_motion(_event(x=5, y=5))
        app._on_leave(_event())
        assert app._in_menu_zone is False

    def test_menu_zone_does_not_fire_shortcut(self, app):
        app._on_motion(_event(x=5, y=5))
        app.keyboard.press.assert_not_called()

    def test_click_without_drag_shows_menu(self, app):
        app._on_motion(_event(x=5, y=5))
        app._on_button_down(_event(x=5, y=5, x_root=105, y_root=105))
        with patch.object(app._menu, "tk_popup") as mock_popup:
            app._on_button_up(_event(x=5, y=5))
            mock_popup.assert_called_once()

    def test_drag_does_not_show_menu(self, app):
        app._on_motion(_event(x=5, y=5))
        app._on_button_down(_event(x=5, y=5, x_root=105, y_root=105))
        # Move beyond drag threshold
        app._on_button_motion(_event(x=15, y=15, x_root=115, y_root=115))
        assert app._dragging is True
        with patch.object(app._menu, "tk_popup") as mock_popup:
            app._on_button_up(_event(x=15, y=15))
            mock_popup.assert_not_called()


class TestDragging:
    def test_drag_via_menu_zone(self, app):
        app.root.geometry("+200+200")
        app.root.update_idletasks()
        app._on_motion(_event(x=5, y=5))
        app._on_button_down(_event(x=5, y=5, x_root=205, y_root=205))
        app._on_button_motion(_event(x=55, y=35, x_root=255, y_root=235))
        app.root.update_idletasks()
        assert app._dragging is True


class TestPieConstruction:
    def test_single_shortcut_full_circle(self):
        one = Config(shortcuts=(
            Shortcut("Test", (KeyComboAction(keys=(Key.enter,)),), "#AAA", "#BBB"),
        ))
        kb = MagicMock()
        app = SpotKey(cfg=one, keyboard=kb)
        app.root.update_idletasks()
        mid = one.diameter // 2
        assert app._index_at(mid, 5) == 0
        assert app._index_at(mid, one.diameter - 5) == 0
        app.root.destroy()


class TestWindowProperties:
    def test_topmost(self, app):
        assert app.root.attributes("-topmost")

    def test_no_title_bar(self, app):
        assert app.root.overrideredirect()

    def test_canvas_size(self, app, cfg):
        assert app.canvas.winfo_reqwidth() == cfg.diameter
        assert app.canvas.winfo_reqheight() == cfg.diameter
