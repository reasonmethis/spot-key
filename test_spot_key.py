"""Tests for Spot Key."""

from unittest.mock import MagicMock, patch, call
import pytest

import spot_key
from spot_key import SpotKey, CIRCLE_COLOR, CIRCLE_HOVER_COLOR, SHORTCUT


@pytest.fixture
def app():
    """Create a SpotKey instance with a mocked keyboard controller."""
    with patch.object(spot_key, "Controller") as mock_ctrl_cls:
        mock_kb = MagicMock()
        mock_ctrl_cls.return_value = mock_kb
        instance = SpotKey()
        instance.root.update_idletasks()
        yield instance
        instance.root.destroy()


def _fake_event(**kwargs):
    """Create a fake tkinter event with arbitrary attributes."""
    event = MagicMock()
    for k, v in kwargs.items():
        setattr(event, k, v)
    return event


class TestShortcutTrigger:
    def test_hover_sends_shortcut(self, app):
        app._on_enter(_fake_event())

        modifier, key = SHORTCUT
        app.keyboard.press.assert_any_call(modifier)
        app.keyboard.press.assert_any_call(key)
        app.keyboard.release.assert_any_call(key)
        app.keyboard.release.assert_any_call(modifier)

    def test_hover_does_not_retrigger_without_leave(self, app):
        app._on_enter(_fake_event())
        app.keyboard.reset_mock()

        app._on_enter(_fake_event())
        app.keyboard.press.assert_not_called()

    def test_hover_retriggers_after_leave(self, app):
        app._on_enter(_fake_event())
        app.keyboard.reset_mock()

        app._on_leave(_fake_event())
        app._on_enter(_fake_event())

        modifier, key = SHORTCUT
        app.keyboard.press.assert_any_call(modifier)
        app.keyboard.press.assert_any_call(key)

    def test_key_order_is_correct(self, app):
        app._on_enter(_fake_event())

        modifier, key = SHORTCUT
        assert app.keyboard.press.call_args_list == [call(modifier), call(key)]
        assert app.keyboard.release.call_args_list == [call(key), call(modifier)]


class TestVisualFeedback:
    def test_circle_turns_red_on_hover(self, app):
        app._on_enter(_fake_event())
        fill = app.canvas.itemcget(app.circle, "fill")
        assert fill == CIRCLE_HOVER_COLOR

    def test_circle_resets_color_on_leave(self, app):
        app._on_enter(_fake_event())
        app._on_leave(_fake_event())
        fill = app.canvas.itemcget(app.circle, "fill")
        assert fill == CIRCLE_COLOR


class TestDragging:
    def test_drag_repositions_window(self, app):
        # Place window at a known position first
        app.root.geometry("+200+200")
        app.root.update_idletasks()

        app._on_drag_start(_fake_event(x_root=220, y_root=220))
        app._on_drag_motion(_fake_event(x_root=270, y_root=250))
        app.root.update_idletasks()

        assert app.root.winfo_x() == 250
        assert app.root.winfo_y() == 230


class TestWindowProperties:
    def test_window_is_topmost(self, app):
        assert app.root.attributes("-topmost")

    def test_window_has_no_title_bar(self, app):
        assert app.root.overrideredirect()

    def test_window_size(self, app):
        assert app.canvas.winfo_reqwidth() == spot_key.CIRCLE_DIAMETER
        assert app.canvas.winfo_reqheight() == spot_key.CIRCLE_DIAMETER
