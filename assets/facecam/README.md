# Face cam footage

Drop talking-head clips here. `FACECAM_PATH` defaults to this **directory**, so
every clip in it joins the rotation: each prospect is assigned one
deterministically from a hash of their domain, giving a list variety while a
re-run never swaps media that has already gone out.

Real files are gitignored — only this README and `.gitkeep` are tracked.

## What records well

- **Square or 4:3 framing.** The pipeline centre-crops to a square, so anything
  at the far left or right of frame gets cut. Head roughly centred, a little
  headroom.
- **1080p or better.** The bubble renders at ~280px on a 1200x675 canvas, so
  there is plenty of headroom for downscaling, and none for upscaling.
- **Plain, contrasting background.** The bubble sits over a busy website
  screenshot; a cluttered background turns to mush at GIF palette sizes.
- **Loopable.** Clips shorter than `--duration` are looped automatically, so a
  clip that starts and ends in a similar pose loops without a visible jump.
- **Motion in the first second.** Outlook shows only frame one of a GIF, and
  Gmail's preview is short — a wave or a smile early does the work.

## Multiple takes

Keep several and select per campaign:

```bash
loomgif batch --input list.csv --facecam assets/facecam/sam-facecam-1.mp4
```

Or per prospect, with a `Facecam` column in the input CSV holding a path.

Trim a slate or countdown off the front without re-editing:

```bash
loomgif render --website acme.com --facecam-start 2.5
```
