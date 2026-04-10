"""
Spot Key — a small floating circle that triggers a keystroke on hover.
"""

import tkinter as tk
from pynput.keyboard import Controller, Key

# --- Configuration ---
SHORTCUT = (Key.ctrl_l, "q")  # Ctrl+Q
CIRCLE_DIAMETER = 80  # pixels (~quarter-sized)
CIRCLE_COLOR = "#4A90D9"
CIRCLE_HOVER_COLOR = "#E84040"
TRANSPARENT_COLOR = "#010101"  # color used for transparency mask


class SpotKey:
    def __init__(self):
        self.keyboard = Controller()
        self.triggered = False

        # --- Window setup ---
        self.root = tk.Tk()
        self.root.title("Spot Key")
        self.root.overrideredirect(True)  # no title bar
        self.root.attributes("-topmost", True)  # always on top
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.geometry(f"{CIRCLE_DIAMETER}x{CIRCLE_DIAMETER}")
        self.root.configure(bg=TRANSPARENT_COLOR)

        # Center on screen initially
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - CIRCLE_DIAMETER - 40
        y = sh // 2 - CIRCLE_DIAMETER // 2
        self.root.geometry(f"+{x}+{y}")

        # --- Canvas with circle ---
        self.canvas = tk.Canvas(
            self.root,
            width=CIRCLE_DIAMETER,
            height=CIRCLE_DIAMETER,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack()

        pad = 2
        self.circle = self.canvas.create_oval(
            pad, pad,
            CIRCLE_DIAMETER - pad, CIRCLE_DIAMETER - pad,
            fill=CIRCLE_COLOR,
            outline="#2C5F9E",
            width=2,
        )

        # --- Event bindings ---
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)

        # Right-click drag to reposition
        self._drag_data = {"x": 0, "y": 0}
        self.canvas.bind("<Button-3>", self._on_drag_start)
        self.canvas.bind("<B3-Motion>", self._on_drag_motion)

        # Double-right-click to quit
        self.canvas.bind("<Double-Button-3>", self._on_quit)

    def _on_enter(self, _event):
        if self.triggered:
            return
        self.triggered = True
        self.canvas.itemconfig(self.circle, fill=CIRCLE_HOVER_COLOR)
        modifier, key = SHORTCUT
        self.keyboard.press(modifier)
        self.keyboard.press(key)
        self.keyboard.release(key)
        self.keyboard.release(modifier)

    def _on_leave(self, _event):
        self.triggered = False
        self.canvas.itemconfig(self.circle, fill=CIRCLE_COLOR)

    # --- Dragging (right-click) ---
    def _on_drag_start(self, event):
        self._drag_data["x"] = event.x_root - self.root.winfo_x()
        self._drag_data["y"] = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event):
        x = event.x_root - self._drag_data["x"]
        y = event.y_root - self._drag_data["y"]
        self.root.geometry(f"+{x}+{y}")

    def _on_quit(self, _event):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    SpotKey().run()


if __name__ == "__main__":
    main()
