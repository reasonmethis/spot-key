# Drawing a smooth circle, without pixelation or edge halos

## The bug, or rather the standard

Spot Key is a floating pie chart that sits on your desktop. The whole thing
is a circle. So it had better *look* like a circle — not a circle with a
jagged staircase running around the rim, not a circle with a ghostly grey
halo where it meets the wallpaper. When you draw a small round thing on a
screen made of square pixels, both of those problems are the default, and
getting rid of them takes some care.

An early version of this used tkinter's built-in `Canvas.create_oval`. It
works, and the output is fine if you squint from three feet away. Up close,
it is a staircase. No anti-aliasing, no soft edges. The rim is made of
pixels that are either fully the fill colour or fully transparent, and the
transitions are abrupt. You can feel each pixel boundary.

What you want is **anti-aliased** edges: pixels right on the rim should be
*partially* the fill colour and partially whatever is behind them, in
proportion to how much of that pixel the circle actually covers. Then your
brain reads the transition as smooth.

## Step 1: draw the thing bigger than you need

The trick is called **supersampling**, and it is one of the oldest tricks
in computer graphics.

Here is the idea. Imagine the final circle is 200 pixels across. Instead
of drawing it directly at 200 pixels, where each pixel is a binary
in-or-out decision about the rim, you draw the same circle at 800 pixels
across — four times bigger — on a temporary canvas. At 800 pixels the
staircase is still there, but each step is now four pixels tall instead of
one pixel tall.

Then you shrink the 800-pixel image down to 200 pixels. And *how* you
shrink it is the thing that matters.

In Spot Key this is one constant (`SUPERSAMPLE = 4` in
`spot_key/models.py`) that multiplies the image dimensions. Every `x` and
`y` in the drawing code gets multiplied by the same factor, so the geometry
stays proportional — a 2-pixel line width becomes 8 pixels on the big
canvas, a 4-pixel corner radius becomes 16 pixels, and so on. When you
shrink back down, all of those measurements land on the pixel values you
actually wanted.

## Step 2: shrink it the right way

Now the important part: when you shrink an image, you have to combine
multiple source pixels into one destination pixel. There are a few ways to
do that, and they give very different results.

- **Nearest neighbour** — for each destination pixel, grab whichever
  source pixel happens to line up and throw the rest away. Fast, terrible
  for anti-aliasing. You basically get back the same staircase, just
  smaller.
- **Bilinear** — blend the four closest source pixels with weights. Better,
  but still a bit soft and a bit blurry.
- **LANCZOS** — named after a mathematician, uses a windowed sinc function
  to blend a larger neighbourhood of source pixels with carefully chosen
  weights. It is the gold standard for image downscaling. It produces the
  sharpest result that is still properly anti-aliased.

The code in `_render_pie()` does exactly this:

```python
img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))   # big transparent canvas
draw = ImageDraw.Draw(img)
# ... draw the pie slices, the hamburger button, the outline ...
img = img.resize((d, d), Image.LANCZOS)           # shrink to real size
```

Every pixel on the final rim ends up as a weighted average of roughly
sixteen pixels from the big canvas. Where the big canvas had a rim pixel
(fully coloured) next to a background pixel (fully transparent), the
destination pixel becomes a partial blend. That is what anti-aliasing is.

Crank `SUPERSAMPLE` up from 4 to 8 and you get even smoother edges, at the
cost of drawing a canvas four times larger in memory and taking roughly
four times longer to render. Four is the sweet spot where the pie looks
perfectly round to the eye and the render cost is still trivial.

## Step 3: a properly transparent canvas, not a "background colour"

Here is the subtle trap. Anti-aliasing only works if the pixels you are
*blending against* are the right ones.

If you draw a coloured circle on a white canvas and downsample, the rim
pixels end up as a blend between the fill colour and white. When you then
put that image on a dark desktop, the rim still has a faint *white halo*
around it, because those rim pixels already contain white. Nothing you do
later can remove it. The blend was baked in at downsample time.

The fix is to draw the pie on a **fully transparent** canvas — not a white
one, not a grey one, genuinely alpha-zero. That is why `_render_pie()`
creates its image with `Image.new("RGBA", (hi, hi), (0, 0, 0, 0))`. The
fourth zero is the alpha channel. Every pixel starts out invisible, and
only the pixels you explicitly draw get any colour or opacity.

Now when you downsample, the rim pixels end up as a blend between the fill
colour and *transparency*. There is no halo colour baked in. The rim is a
set of semi-transparent pixels carrying only the fill colour at varying
strengths.

## Step 4: tell Windows to actually honour that alpha

This is the step tkinter will not do for you, and it is where most people
give up and end up with a halo anyway.

Tkinter's regular windows do not really support per-pixel alpha on the
desktop. You can get a window-wide transparency, and you can get a
chroma-key style "treat this one colour as see-through" mode, but neither
gives you a rim pixel that is 40% orange and 60% desktop. Windows itself
does support that, but only for something called a **layered window** —
and you have to talk to it directly.

So `spot_key/win32.py` has a little helper called `update_layered_window`
that takes the Pillow image and hands it to `UpdateLayeredWindow` through
`ctypes`. Three small things are going on inside that helper:

1. **Channel order swap.** Pillow stores pixels as RGBA (red, green, blue,
   alpha). Windows expects BGRA. So the channels get shuffled.
2. **Pre-multiplication.** `UpdateLayeredWindow` does not want colour and
   alpha values separately. It wants colours that have already been
   multiplied by their alpha — a 50% opaque pure red should arrive as
   (128, 0, 0, 128), not (255, 0, 0, 128). The helper does that
   multiplication with numpy, which is fast.
3. **Bottom-up flip.** Windows bitmaps are stored upside-down for historic
   reasons. One `[::-1]` slice fixes that.

Then the pixels get packed into a DIBSection (a Windows-native bitmap) and
handed off. From that point on, Windows itself does the compositing onto
whatever is behind the window, respecting every pixel's alpha value
individually. Drop the pie on a white background, the rim blends to white.
Drop it on a dark terminal, the rim blends to the terminal. Drop it on a
wallpaper photo, the rim blends to the wallpaper. No halo, no fringe, no
assumed backdrop.

## Step 5: a visual test that proves it

The tricky thing about halos is that they are easy to miss if you only
test on one background. A faint white halo on a white page is invisible.
It only shows up when you put the pie on a dark background.

So the project has a little script, `visual_test.py`, that does one
specific thing: it pops up two coloured backdrops side by side — one pure
white, one dark grey — moves the real pie over each in turn, and saves a
screenshot of each. Then you open `visual_test_white.png` and
`visual_test_dark.png` and look at the rims at high zoom.

If your supersampling is wrong, both images show a staircase.
If your canvas had a background colour baked in, one of the two images
shows a halo that the other one does not.
If your layered-window alpha is broken, the dark image looks fine but the
white image gets a dark outline.

You can catch all three bugs by staring at two PNGs. It is dumb and it
works.

## Bonus: the same trick for swatches and the delete icon

The settings dialog needs the same treatment. The colour swatches (tiny
filled circles) and the delete icons (tiny ×'s) in the shortcut list are
both rendered by PIL at 4× supersampling and downsampled with LANCZOS, via
the `_make_swatch` and `_make_delete_icon` helpers in
`spot_key/settings.py`.

There is also a secondary reason they are rendered as images rather than
drawn with text characters: **alignment**. A `×` character drawn by
tkinter's text renderer sits on a font baseline, which does not put the
glyph in the centre of its box. A filled circle drawn with a widget also
does not centre itself at the pixel you might expect. Rendering both as
same-sized PIL images means you control the pixel center exactly — the
`×` and the swatches sit at the same vertical position with sub-pixel
accuracy.

## Takeaways

Three things, and they will solve this particular problem anywhere you
meet it:

1. **Supersample, then downsample with LANCZOS.** Draw bigger than you
   need, shrink with a good filter. The cost is negligible at 4×. The
   quality difference is the difference between a staircase and a smooth
   curve.
2. **Start from fully transparent, not from a background colour.**
   Otherwise the background colour gets baked into the rim pixels at
   downsample time and you can never get rid of it.
3. **If you actually need per-pixel alpha on the desktop, go around
   tkinter.** Tkinter does not expose it. Windows does, via layered
   windows and `UpdateLayeredWindow`, and the helper to drive that from
   Python is about thirty lines of ctypes. Pre-multiply your alpha, swap
   RGBA to BGRA, flip bottom-up, hand off the bitmap, done.

And a bonus observation that applies to more than just circles: **test on
two contrasting backgrounds.** A single test background will hide half the
rendering bugs you can make. Rendering bugs love to hide in plain sight.
