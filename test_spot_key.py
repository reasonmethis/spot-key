"""Tests for Spot Key."""

from unittest.mock import MagicMock, call
import pytest

from spot_key import SpotKey, Config


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


class TestShortcutTrigger:
    def test_hover_sends_shortcut(self, app, cfg):
        app._on_enter(_event())
        app.keyboard.press.assert_any_call(cfg.modifier)
        app.keyboard.press.assert_any_call(cfg.key)
        app.keyboard.release.assert_any_call(cfg.key)
        app.keyboard.release.assert_any_call(cfg.modifier)

    def test_no_retrigger_without_leave(self, app):
        app._on_enter(_event())
        app.keyboard.reset_mock()
        app._on_enter(_event())
        app.keyboard.press.assert_not_called()

    def test_retriggers_after_leave(self, app, cfg):
        app._on_enter(_event())
        app.keyboard.reset_mock()
        app._on_leave(_event())
        app._on_enter(_event())
        app.keyboard.press.assert_any_call(cfg.modifier)
        app.keyboard.press.assert_any_call(cfg.key)

    def test_key_order(self, app, cfg):
        app._on_enter(_event())
        assert app.keyboard.press.call_args_list == [call(cfg.modifier), call(cfg.key)]
        assert app.keyboard.release.call_args_list == [call(cfg.key), call(cfg.modifier)]


class TestVisualFeedback:
    def test_hover_changes_color(self, app, cfg):
        app._on_enter(_event())
        assert app.canvas.itemcget(app.circle, "fill") == cfg.hover_color

    def test_leave_resets_color(self, app, cfg):
        app._on_enter(_event())
        app._on_leave(_event())
        assert app.canvas.itemcget(app.circle, "fill") == cfg.color


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
