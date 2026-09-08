# loom-gif-automation

Personalised Loom-style video and GIF generation for Motion The Agency cold outreach.

For every prospect the pipeline screenshots their homepage, composites a circular
face cam over it bottom-left, renders a short clip, converts it to an email-safe
GIF, hosts everything on ImageKit and hands back a CSV that merges straight into
an Instantly campaign.

Built to feed [`/mta-instantly-campaign`](#how-it-plugs-into-mta-instantly-campaign).

```
screenshot  ->  composite  ->  GIF  ->  ImageKit  ->  CSV  ->  Instantly
```

## Quick start

```bash
make setup                      # venv, deps, Chromium, .env
cp .env.example .env            # then fill in the ImageKit keys
loomgif doctor                  # checks binaries, credentials, footage
```

Drop your face cam clips in `assets/facecam/` (see
[`assets/facecam/README.md`](assets/facecam/README.md) for how to shoot it), then:

```bash
# one prospect, local only — the fastest way to eyeball the look
loomgif render --website obrizum.com --no-upload

# one prospect, hosted, with an email preview
loomgif render --website obrizum.com --email sam@obrizum.com --company Obrizum

# a whole list
loomgif batch --input examples/prospects.csv
```

## Commands

| Command | What it does |
|---|---|
| `loomgif render` | Build media for a single prospect |
| `loomgif batch` | Build media for every row of a CSV, write `output/manifest.csv` |
| `loomgif merge` | Join hosted URLs onto a campaign CSV, split GIF vs no-GIF |
| `loomgif snippet` | Print the HTML to paste into the Instantly sequence body |
| `loomgif doctor` | Check dependencies, credentials and footage |

Run any of them with `--help` for the full flag list. The useful render flags:

```bash
--duration 6          # clip length in seconds
--facecam-start 2.5   # skip a slate at the head of the footage
--facecam path.mp4    # a specific take, or a directory to rotate between
--no-pin-nav          # let the site header scroll away instead of pinning it
--gif-width 600       # email display width
--max-mb 1.2          # GIF size budget; the encoder ladders down to fit
--no-upload           # render locally, skip ImageKit
--force               # re-screenshot instead of reusing the cached PNG
```

## How it plugs into /mta-instantly-campaign

The campaign skill's `references/instantly-csv.md` sets the contract, and this
repo implements its side of it.

**Ordering.** The GIF pipeline runs *before* the CSV is written, because the CSV
needs the hosted URLs. Screenshot, composite, host, collect, export, drag and drop.

```bash
# 1. build and host the media for the surviving prospects
loomgif batch --input prospects.csv

# 2. merge the URLs into the campaign CSV the skill produced
loomgif merge --campaign campaign.csv --manifest output/manifest.csv
#    -> output/campaign-gif.csv     rows that have media
#    -> output/campaign-nogif.csv   rows that do not
```

**Two files, two campaigns.** An empty `Gif url` renders a broken image icon,
which is worse than no image, so any prospect whose screenshot failed is split
into `-nogif.csv` and runs as a separate campaign. `merge` does that split for
you and prints the counts.

**Column names.** Instantly maps columns silently to nothing unless every name
starts with a capital and is 20 characters or fewer. The merged columns are
`Gif url`, `Gif small`, `Video url`, `Webm url`, `Poster url`, `Loom link`.
`merge` validates the whole header and warns on anything that breaks the rules.

**The tag goes in the sequence body, never in the CSV.** Written once, via Code
View — Instantly warns that pasted images render broken.

```bash
loomgif snippet
```

```html
<img src="{{Gif url}}" width="600" alt="Your homepage, with a few notes from me" style="..." />
```

Alt text is required, not optional: most clients block images by default, and
without it the reader sees an empty box. **The email has to read completely with
images off.** The GIF is a bonus, never load-bearing.

No wrapping `<a>` unless a Loom actually exists to click through to — use
`loomgif snippet --link` only when every prospect in the file has one.

### Four settings that strip images

Check all of them before launching, or the GIF silently never sends:

- Delivery Optimization must be **disabled**
- "Send emails as text-only (no HTML)" strips images from every step
- "Send first email as text-only" strips images from step 1
- Advanced Deliverability → "Always send first email as text-only" must be **disabled**

That Instantly ships two separate settings for forcing step 1 to plain text is a
hint. **Put the GIF on step 2 or 3**, where the plain-text feel matters less.

### Optional: push straight to the leads

Instead of the CSV round trip you can write the URLs onto existing Instantly
leads as custom variables. Instantly registers new keys on the campaign
automatically.

```bash
loomgif batch --input prospects.csv --push --campaign <campaign-id>
```

Needs `INSTANTLY_API_KEY` in `.env`. The CSV path stays the default because it
matches how the campaign skill already hands work over.

## Input CSV

Only `Email` and `Website` are required. Column names are matched
case-insensitively and the campaign CSV's own headers work as-is.

```csv
Email,First name,Company name,Website
sam@obrizum.com,Sam,Obrizum,https://obrizum.com
```

Optional columns: `Facecam` (path to a different clip for that prospect) and
`Loom link` (a real click-through URL).

## Output

```
output/
  manifest.csv              one row per prospect: URLs + status
  obrizum-com/
    screenshot.png          full-page capture (cached; --force to refresh)
    navbar.png              the site's sticky header, lifted out (when solid)
    obrizum-com.mp4         H.264 master
    obrizum-com.webm        VP9 master (if your ffmpeg has libvpx)
    obrizum-com.gif         email-embeddable, size-capped
    obrizum-com-poster.jpg  first frame
    preview.html            open in a browser to check the embed
```

## How the composition works

`compose.build_filtergraph` is one ffmpeg graph:

1. The full-page screenshot is scaled to canvas width and padded to at least
   canvas height, then a **crop window walks down it** on a smoothstep ease, so
   the page reads as a real scroll rather than a linear pan.
2. The face cam is cover-cropped to a square, resized to the bubble, and made
   circular with `alphamerge` against a Pillow-generated anti-aliased mask.
3. A ring-and-shadow PNG is laid over the seam.
4. If the site has a solid sticky header, it is composited **pinned at the top**.

Masks are generated once at 4x and cached in `output/.overlays/`.

Geometry is env-tunable: `FACECAM_DIAMETER_RATIO` (0.32 of canvas height),
`FACECAM_MARGIN_RATIO`, `FACECAM_RING_PX`, `FACECAM_RING_COLOR`, `SCROLL_RATIO`.

### The site's navigation

A real screen recording keeps the site's sticky header pinned while the body
scrolls underneath. Baking it into the scrolling image instead makes it slide
away after a second, which is the first thing that reads as fake.

So the header is lifted into its own layer: captured after a short scroll (so it
takes its *scrolled* appearance, which is what the viewer sees for all but the
opening moment), hidden on the page, and composited back at `y=0`.

This only happens for **solid** headers. A transparent one is left in the page to
scroll away naturally — pinning it would freeze a band of hero content over the
moving page. The log says which path a site took:

```
Pinned sticky nav (72px tall) as its own layer          # monday.com
Sticky nav is transparent — leaving it in the page      # motiontheagency.com
```

`--no-pin-nav` (or `PIN_STICKY_NAV=0`) forces the header to scroll with the page.

Overlays that would smear across a full-page capture — consent bars, chat
bubbles, bottom-anchored bars, full-screen modals — are removed, but **only if
they currently overlay the viewport**. Scroll-driven sticky *sections* further
down the page are real content and are left alone.

### Face cam takes

`FACECAM_PATH` can be a single clip or a **directory of takes**. With a
directory, each prospect gets one deterministically from a hash of their domain,
so a list gets variety while a re-run never swaps media that has already been
emailed.

```bash
loomgif doctor        # lists the takes it can see
```

### GIF sizing

Email clients choke well before the nominal limits, so `to_gif` walks a ladder of
width / frame rate / palette size until the file fits `--max-mb` (default 1.8MB),
using two-pass `palettegen`/`paletteuse` with `stats_mode=diff`. It logs what it
settled on. If it exhausts the ladder it warns rather than shipping something
huge — shorten `--duration`, which is the biggest lever by far.

**Outlook 2007–2019 shows only frame one.** Frame one has to make sense on its
own, so record footage with a wave or a smile early.

## Requirements

- Python 3.9+
- ffmpeg with **libx264**; **libvpx** too if you want the `.webm`
  (`brew install ffmpeg` — the pipeline warns and skips WebM if it is missing,
  since MP4 covers every use here)
- Chromium via `playwright install chromium`

## Credentials

Everything is env-driven and `.env` is gitignored. **No key is ever committed.**

| Variable | Notes |
|---|---|
| `IMAGEKIT_URL_ENDPOINT` | `https://ik.imagekit.io/motiontheagency` |
| `IMAGEKIT_PUBLIC_KEY` | Safe to expose |
| `IMAGEKIT_PRIVATE_KEY` | **Server-side only.** Rotate it if it has ever been pasted into a chat, a ticket or a doc |
| `INSTANTLY_API_KEY` | Only needed for `--push` |

Uploads use `unique=False, overwrite=True`, so URLs stay stable per prospect and
re-running refreshes the media without breaking already-sent emails.
