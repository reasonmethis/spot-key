"""Tests for Spot Key."""

from __future__ import annotations

from unittest.mock import MagicMock, call
import pytest
from pynput.keyboard import Key

from spot_key import SpotKey, Config, Shortcut


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def app(cfg):
    """SpotKey with a mocked keyboard controller."""
    kb = MagicMock()
    instance = SpotKey(cfg=cfg, keyboard=kb)
    instance.root.update_idletasks()
    yield instance
    instance.root.destroy()


def _event(**kwargs: object) -> MagicMock:
    e = MagicMock()
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


class TestIndexAt:
    """_index_at maps pixel coordinates to pie-slice indices."""

    def test_top_center_is_first_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid, 5) == 0

    def test_right_of_center_is_second_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid + 20, mid + 15) == 1

    def test_left_of_center_is_third_slice(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid - 30, mid) == 2

    def test_outside_circle_returns_none(self, app):
        assert app._index_at(0, 0) is None

    def test_exact_center_returns_index(self, app):
        mid = app.cfg.diameter // 2
        assert app._index_at(mid, mid) is not None


class TestShortcutTrigger:
    def test_hover_fires_correct_shortcut(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))  # top center → slice 0
        sc = cfg.shortcuts[0]
        for k in sc.keys:
            app.keyboard.press.assert_any_call(k)
            app.keyboard.release.assert_any_call(k)

    def test_no_retrigger_in_same_slice(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app.keyboard.reset_mock()
        app._on_motion(_event(x=mid, y=6))
        app.keyboard.press.assert_not_called()

    def test_moving_to_different_slice_fires(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))                # slice 0
        app.keyboard.reset_mock()
        app._on_motion(_event(x=mid + 20, y=mid + 15))    # slice 1
        sc = cfg.shortcuts[1]
        for k in sc.keys:
            app.keyboard.press.assert_any_call(k)

    def test_leave_and_reenter_retriggers(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app.keyboard.reset_mock()
        app._on_leave(_event())
        app._on_motion(_event(x=mid, y=5))
        sc = cfg.shortcuts[0]
        for k in sc.keys:
            app.keyboard.press.assert_any_call(k)

    def test_key_press_and_release_order(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))  # slice 0: Ctrl+Q
        sc = cfg.shortcuts[0]
        assert app.keyboard.press.call_args_list == [call(k) for k in sc.keys]
        assert app.keyboard.release.call_args_list == [call(k) for k in reversed(sc.keys)]


class TestVisualFeedback:
    def test_hover_highlights_slice(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        fill = app.canvas.itemcget(app.slices[0], "fill")
        assert fill == cfg.shortcuts[0].hover_color

    def test_leave_resets_slice(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))
        app._on_leave(_event())
        fill = app.canvas.itemcget(app.slices[0], "fill")
        assert fill == cfg.shortcuts[0].color

    def test_moving_slices_resets_previous(self, app, cfg):
        mid = cfg.diameter // 2
        app._on_motion(_event(x=mid, y=5))              # highlight slice 0
        app._on_motion(_event(x=mid + 20, y=mid + 15))  # move to slice 1
        fill_0 = app.canvas.itemcget(app.slices[0], "fill")
        fill_1 = app.canvas.itemcget(app.slices[1], "fill")
        assert fill_0 == cfg.shortcuts[0].color
        assert fill_1 == cfg.shortcuts[1].hover_color


class TestPieConstruction:
    def test_slice_count_matches_shortcuts(self, app, cfg):
        assert len(app.slices) == len(cfg.shortcuts)

    def test_single_shortcut_draws_full_circle(self):
        one = Config(shortcuts=(
            Shortcut("Test", (Key.enter,), "#AAA", "#BBB"),
        ))
        kb = MagicMock()
        app = SpotKey(cfg=one, keyboard=kb)
        app.root.update_idletasks()
        assert len(app.slices) == 1
        mid = one.diameter // 2
        assert app._index_at(mid, 5) == 0
        assert app._index_at(mid, one.diameter - 5) == 0
        app.root.destroy()


class TestDragging:
    def test_drag_repositions_window(self, app):
        app.root.geometry("+200+200")
        app.root.update_idletasks()
        app._on_drag_start(_event(x_root=220, y_root=220))
        app._on_drag_motion(_event(x_root=270, y_root=250))
        app.root.update_idletasks()
        assert app.root.winfo_x() == 250
        assert app.root.winfo_y() == 230


class TestWindowProperties:
    def test_topmost(self, app):
        assert app.root.attributes("-topmost")

    def test_no_title_bar(self, app):
        assert app.root.overrideredirect()

    def test_canvas_size(self, app, cfg):
        assert app.canvas.winfo_reqwidth() == cfg.diameter
        assert app.canvas.winfo_reqheight() == cfg.diameter
