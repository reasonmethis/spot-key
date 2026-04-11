# Spot Key

A floating pie-chart widget for Windows that triggers keyboard shortcuts on hover.

![Spot Key floating over the desktop](screenshot.png)

Each slice of the pie represents a different keyboard shortcut. Hover over a slice for 330ms to trigger it — the slice highlights at the exact moment the keystroke fires, giving clear visual confirmation.

## Features

- **Pie-chart overlay** — configurable shortcuts, one per slice, with distinct colors
- **Hover to trigger** — deliberate 330ms dwell time prevents accidental activation
- **Smooth rendering** — Pillow supersampling with Win32 layered windows for true per-pixel alpha transparency (no edge fringe on any background)
- **Always on top** — stays visible over all windows
- **Draggable** — grab the menu button (top-left) to reposition
- **DPI aware** — crisp at any display scaling

## Requirements

- Windows 10/11
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Getting started

```
git clone https://github.com/reasonmethis/spot-key.git
cd spot-key
uv sync
uv run python -m spot_key
```

## Default shortcuts

| Slice | Color | Shortcut |
|-------|-------|----------|
| Top | Blue | Ctrl+Q |
| Bottom-right | Green | Ctrl+C |
| Bottom-left | Amber | Enter |

Shortcuts can be customised via the Settings dialog (hamburger menu → Settings).

## Controls

- **Hover a slice** — triggers the shortcut after 330ms
- **Menu button** (top-left hamburger icon) — click to open menu, drag to reposition

## Running tests

```
uv run pytest -v
```

## License

MIT
