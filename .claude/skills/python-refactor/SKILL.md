---
name: python-refactor
description: Quality pass over Python modules — docstrings, typing, noise removal, idiomatic simplification. Accumulates project-specific learnings over time.
---

# python-refactor

Apply a quality pass to one or more Python modules in this project. The goal
is code that is **easy to read, cheap to change, and hard to misuse** — not
code that is maximally abstracted.

## The checklist

Work through a module top-to-bottom. For each class/function:

### 1. Documentation
- Every public class has a docstring explaining **what it is** and **when to
  use it** (not a restatement of its name).
- Every public function/method has a one-line summary plus — if non-obvious —
  a short description of arguments and return value. Private helpers need a
  docstring only if the name doesn't carry the meaning.
- Module docstring at the top explains the module's one job.
- **Do not** document the obvious. `def _quit(self): self.root.destroy()` does
  not need a docstring.
- Docstrings describe behaviour, not implementation. "Rebuild the row list"
  not "calls destroy on each child then build_row for each item".

### 2. Typing
- All function signatures fully annotated, including `-> None`.
- Prefer modern syntax: `list[int]` not `List[int]`, `X | None` not
  `Optional[X]`, `from __future__ import annotations` at the top.
- Callables get `Callable[[arg_types], return]` with concrete types.
- `Any` is a smell — use it only at genuine interop boundaries.
- Private state annotated on the class body or in `__init__` (whichever is
  clearer for the reader).

### 3. Noise removal
Strip anything that doesn't earn its line:
- **Pointless variables.** `photo = ImageTk.PhotoImage(img); return photo`
  → `return ImageTk.PhotoImage(img)`.
- **Redundant guards.** `if x is not None: x = None` — assigning None is
  idempotent, drop the check (unless it gates an expensive side effect).
- **Dead branches.** Duplicated code in both arms of an if/else — hoist the
  common part out.
- **Comments that restate code.** Delete them. Keep only comments explaining
  *why* (hidden constraint, non-obvious invariant, workaround for a bug).
- **Unused imports, unused parameters, unused class attributes.**
- **Stale TODOs** and `# removed` markers.

### 4. Idiomatic simplification
- Replace manual loops with comprehensions where the intent is clearer.
- Use `next((x for x in seq if cond), default)` instead of a for/break.
- Unpack tuples in the signature: `for i, (a, b) in enumerate(pairs):`.
- Prefer `dict.get(k, default)` over `k in d and d[k] or default`.
- Replace `dataclasses.replace` for in-place mutation only when immutability
  actually matters — otherwise just mutate.
- Collapse multi-line `if/else` assignment into a ternary when it fits on
  one line readably.
- If two methods share 80% of their body, extract the shared bit.
- Don't over-abstract. Three similar lines is better than a four-line helper
  that's called three times.

### 5. Structure
- Methods grouped by concern, with section-comment banners
  (`# -- Section name ---------------------------`).
- Construction / public API / private helpers roughly in that order.
- Prefer one class per concern. If a class has grown past ~400 lines and the
  responsibilities are separable, split it.

## Process

1. **Read the full module first.** Do not start editing until you've seen
   the whole thing — context matters for the noise/docs decisions.
2. **Make one category of change at a time.** Easier to review, easier to
   revert if one change is wrong.
3. **Run the tests after each category.** `uv run pytest test_spot_key.py`.
4. **Reload the app after UI changes** (see the feedback memory — kill and
   restart `spot_key` after every change so the user can test).
5. **Commit each category separately** when the changes are substantial.
   One commit per module for small passes is also fine.
6. **Update the Learnings section below** with anything non-obvious you
   discovered or fixed, so future sessions can benefit.

## What NOT to do

- Do not rename public APIs without a reason.
- Do not introduce new dependencies as part of a refactor.
- Do not rewrite working code in a different style for style's sake.
- Do not add "defensive" checks at internal boundaries — trust your own
  code. Only validate at the edge (user input, file IO, external calls).
- Do not add backwards-compatibility shims for code nothing else calls.
- Do not refactor a file you were not asked to touch just because you
  noticed something there. Flag it instead.

## Examples & Learnings (append-only)

This section grows session-over-session. Each entry is a small, concrete
thing that was found and fixed (or a mistake to avoid next time). Keep
entries short — a one-line title, a two-to-four-line body, and if it helps,
a before/after snippet.

### settings.py: redundant ellipse draw in `_make_swatch`
The `selected` branch drew an outer white circle *and* an inset coloured
circle; the `else` branch drew only the inset circle. The inset draw was
duplicated across both arms. Hoist it out, keep the selection ring as the
only branch-specific code.

### settings.py: pointless `photo` temp variable
```python
photo = ImageTk.PhotoImage(img)
return photo
```
is just `return ImageTk.PhotoImage(img)`.

### app.py: redundant `if x is not None: x = None` guard
Setting an attribute to `None` is idempotent. The check only matters if it
gates an expensive side effect on the next line (like `_render_pie()`). If
it doesn't, drop the `if`.

### settings.py: platform guard producing useless None assignments
```python
if sys.platform == "win32":
    _LockWindowUpdate = ctypes.windll.user32.LockWindowUpdate
else:
    _user32 = None           # unused everywhere else
    _LockWindowUpdate = None
```
The `_user32 = None` line exists to mirror the win32 branch but nothing
reads it. Drop unused mirrors. Keep only the names the rest of the module
actually imports.

### Win32 widget gotcha: `tk.Button` flashes on Windows
Native `tk.Button` is a Win32 BUTTON control; it paints the system default
background for one frame on creation, ignoring any `bg=` you set. For any
button that gets rebuilt during the lifetime of a dialog, use
`tk.Label` + `bind("<Button-1>", ...)` instead. Behaviour is identical
for our purposes.

### Win32 widget gotcha: `LockWindowUpdate` must target the Toplevel HWND
`winfo_id()` on a `tk.Frame` returns the *inner* widget HWND, not the
Toplevel. `LockWindowUpdate` on the inner HWND does nothing visible.
Resolve the ancestor first:
```python
root_hwnd = ctypes.windll.user32.GetAncestor(inner_hwnd, GA_ROOT=2)
```

### Test hygiene: run `uv run pytest` after each category of changes
Don't batch a full refactor then test once at the end — you lose the
ability to bisect which change broke what. The ~30-second test run is
cheap; pay it per category.

### Tray integration: pystray menu callbacks run on the tray thread
`pystray.Icon.run()` blocks a worker thread and dispatches menu actions
from that thread. Never touch tk widgets directly from a menu callback —
marshal back to the tk main thread via `root.after(0, fn)`. Same rule
applies to any background thread in a tkinter app.

### Tray integration: dynamic `visible` lambdas need `update_menu()` on Windows
pystray's `MenuItem(..., visible=lambda: ...)` is only re-evaluated when
`Icon.update_menu()` is called — on the Windows backend the menu is a
cached Win32 HMENU, so without `update_menu()` the right-click shows
whatever state the menu was in when it was last rebuilt. Any state change
that affects a dynamic `visible` / `text` / `enabled` / `checked`
predicate must be followed by `update_menu()`, or the UI lies about
state. (Conditional Show/Hide tray entries hit this on first encounter.)
