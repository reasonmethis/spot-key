# Catching a one-frame flicker

## The bug

When you reordered the shortcuts in the Spot Key settings dialog, the list
flashed for an instant. Something brief and weird happened, then it settled.
You saw it, but it was too fast to really *look* at.

That flash is what we call **flicker**. The tricky part is that it only lasts
a single frame. Your screen refreshes sixty times a second, so one frame is
about sixteen milliseconds. You notice it, but it is way too fast to study.

The question was: how do you write an automated test that catches something
that only shows up for one frame out of sixty in a second?

An earlier attempt at this test had confidently reported *"no flicker
detected"* — while the user watched the flicker happen with their own eyes.
So the bar was not just "write a test" but "write a test you can actually
trust".

## Step 1: find a fast enough camera

First problem: how do you even take screenshots fast enough?

Most Python screenshot libraries cap out around sixty frames per second on
Windows. That sounds like plenty, except for one cruel detail. Windows'
desktop compositor also refreshes at sixty frames per second. So if you take
screenshots at the same rate the screen updates, you and the screen are
*synchronised*. Whether you catch a one-frame flicker depends on whether
your sampling clock happens to line up with the flicker's clock. It might
catch it, it might miss it. Coin flip.

That is like trying to photograph a hummingbird's wing-beat with a camera
that can only take one picture per wing-beat. You need to be faster than
the thing you are trying to catch.

So the test skips the nice high-level screenshot libraries and talks
directly to the low-level Windows drawing system (GDI) through `ctypes`.
Each capture goes like this:

```
GetDC(0)                 # get a handle to the whole screen
CreateCompatibleDC       # make a memory canvas
CreateCompatibleBitmap   # and a bitmap to draw into
BitBlt                   # blit screen → bitmap
GetDIBits                # bitmap → raw BGRA bytes
→ numpy array
```

That is fast enough that a single-frame flash reliably lands in at least
one captured image, even on a cheap laptop.

## Step 2: three kinds of pictures

To recognise a flicker, the test needs to know what *normal* looks like.
So it takes three kinds of pictures.

1. **Before.** One calm snapshot of the dialog, taken on the main thread,
   after the window has been sitting still for half a second. Guaranteed
   quiet. Guaranteed pre-rebuild.
2. **During.** A background thread spinning in a tight loop, taking
   pictures as fast as it can, while the reorder operation runs. This is
   the messy middle — the stream of frames that might contain the flicker.
3. **After.** Another calm snapshot on the main thread, once the capture
   thread has stopped and the window has settled again. Guaranteed quiet.
   Guaranteed post-rebuild.

Notice what this buys us. The *before* and *after* frames are both taken
from the same thread that controls the widgets, and they are taken only
when the dialog is definitively at rest. So we cannot accidentally capture
them mid-rebuild. The only frames that sample mid-rebuild are the
background-thread ones, which is precisely what we want.

An earlier version of the test used the first captured frame as "before"
and the last captured frame as "after". That was racy: depending on
thread-scheduling luck, "before" sometimes caught the state after the
reorder and "after" sometimes caught the state before. The baseline-vs-final
pixel difference drifted between 0.00, 1.61, and 27.34 for the *same*
operation across runs. The tidy separation fixes that.

## Step 3: the clever bit — "different from both"

Here is the key insight, and it is the thing that makes the whole approach
work. Listen carefully.

**A flicker, by definition, is a frame that does not match the *before*
state AND does not match the *after* state.**

Think about it:

- If a frame matches *before*, it is just a pre-rebuild frame. Fine.
- If a frame matches *after*, it is just a post-rebuild frame. Fine.
- If a frame matches **neither**, it is showing something that appeared
  briefly, was not supposed to be there, and disappeared. That is flicker.

So for each picture in the messy middle, the test computes two numbers:

```python
d_base  = mean(|frame - baseline|)
d_final = mean(|frame - final|)
```

Just the average absolute difference across all pixels and all colour
channels — one scalar per frame, cheap and easy with numpy.

Then:

```python
if d_base > THRESHOLD and d_final > THRESHOLD:
    flag as flicker
```

The threshold (3.0 on a 0–255 scale) is high enough to ignore capture noise
and anti-aliasing jitter, but low enough to catch any real visible
transient. Before the fix, flicker frames produced diffs of ~120+. That is
a mountain above the threshold.

The beautiful thing about this is that **you never have to tell the test
what flicker looks like**. You only have to define what *normal* looks
like — the before and after — and the test catches anything else by
construction. You could fix bugs you have never seen before with this, and
it would find regressions you have never imagined.

## Step 4: the part that matters most — the self-check

Here is the question that made the previous test untrustworthy: *how do you
know your flicker detector actually works?*

If your detector is broken, "no flicker detected" is a lie that looks like
a pass. The previous test was telling that lie. Everybody believed it.
Nobody could explain why the user still saw the flash.

My test starts by poisoning itself on purpose.

Before it tests the real code, it runs a **fake** version of the rebuild
that deliberately creates flicker:

```python
def slow_refresh():
    destroy all row widgets
    win.update()           # force the empty state to paint
    sleep(0.05)            # hold the empty state for 50 ms
    rebuild the rows
    win.update()           # force the final state to paint
```

Fifty milliseconds is an eternity in screen-refresh time — roughly three
frames of empty list. Absolutely impossible to miss. If the test does not
detect that obvious, deliberately-planted flicker, then the test itself is
broken (wrong threshold? wrong capture region? thread timing drift?), and
it exits with an error and refuses to go any further.

**Only after** the test proves to itself that it can catch a known bad
fixture does it go on to test the real code.

Every run, every time, the detector is calibrated against a known positive.
No more unfalsifiable passes. If a future refactor accidentally dials the
threshold too high or breaks the capture, the self-check will fail *loudly*
on the next CI run, and you will notice before the real-code test ever
runs.

This is the part I want to emphasise: **a sensitivity check against a
known-bad fixture is the single most important part of any test that tries
to detect absence-of-a-bug.** Detectors that have never been shown what a
positive looks like cannot be trusted.

## Step 5: the saved evidence

When the test does find flicker, it does not just say *yes, there was
flicker*. It saves the **actual worst frame** it caught as a PNG, upscaled
three times with nearest-neighbour resampling so individual pixels are
visible.

That turned out to be the most valuable part. When I ran the test against
the real code, the saved images showed me **three completely different
failure modes**:

1. **A frame where the window had briefly shrunk**, exposing the desktop
   and the code editor behind it. The dialog was auto-resizing during the
   rebuild because its content frame temporarily had fewer children.
2. **A frame where the list area was totally empty.** The row widgets had
   been destroyed and the new ones had not yet been painted.
3. **A frame where the buttons were rendering as blank white rectangles.**
   On Windows, `tk.Button` is a native Win32 BUTTON control, and for one
   paint tick after creation it ignores the custom background colour you
   passed in and uses the system default.

Three totally different bugs, all happening during the same rebuild, all
contributing to what looked like one flash to the human eye.

**Without the saved images I almost certainly would have fixed one and
assumed I was done.** The test did not just say *there is a bug*; it handed
me three pieces of visual evidence I could look at and understand. Each one
suggested its own root cause, and each root cause got its own fix:

- Pin the content frame's dimensions before the rebuild so it cannot
  shrink.
- Suspend drawing on the whole dialog window during the rebuild using
  Win32 `LockWindowUpdate`, so the destroy-and-rebuild happens in a
  single atomic repaint.
- Replace `tk.Button` with `tk.Label` bound to `<Button-1>`, since `Label`
  paints with our chosen background from the first frame.

After all three fixes, the reorder operation reports **zero** transient
frames across repeated runs. The synthetic self-check still catches its
fifty-millisecond injected flicker every time, so we know the detector is
still alive.

## Takeaways

If you only remember three things from this post:

1. **Sample faster than the thing you are trying to catch.** Vsync-limited
   screenshot libraries are not enough for single-frame artefacts. Go one
   layer down.
2. **Define "flicker" as "different from both endpoints".** You never need
   to specify what the bug looks like. You only need to specify what
   *normal* looks like. Everything else becomes the negative space.
3. **Always run a known-bad fixture before your real test.** A detector
   that has never been shown a positive cannot be trusted. Calibrate every
   run. Fail loudly if your calibration fails.

And one bonus observation, because it surprised me: **save the evidence,
not just the verdict.** A test that says "yes there was a bug" helps you
pass or fail CI. A test that says "here is a picture of the bug, zoomed in"
helps you actually understand what went wrong. The second one is worth far
more than the first.
