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
| `loomgif shot` | Capture a prospect page and stop — the screenshot stage alone |
| `loomgif preflight` | Check a campaign for the settings that strip the GIF |
| `loomgif doctor` | Check dependencies, credentials and footage |

Run any of them with `--help` for the full flag list. The useful render flags:

```bash
--duration 5          # override; by default it matches the face cam clip
--facecam-start 2.5   # skip a slate at the head of the footage
--facecam path.mp4    # a specific take, or a directory to rotate between
--no-pin-nav          # let the site header scroll away instead of pinning it
--gif-width 600       # GIF width; the email <img> tag follows it automatically
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

Two of the four are exposed on the campaign object, so `preflight` checks them
rather than trusting them. It also reports which step carries the image:

```bash
loomgif preflight --campaign <campaign-id>
```

```
  OK    'Send emails as text-only' is off
  OK    'Send first email as text-only' is off
  FAIL  A step references {{Gif url}}  (no step does — the GIF will never render)
```

The other two are workspace-level and still need a human; `preflight` says so
rather than implying it checked them.

That Instantly ships two separate settings for forcing step 1 to plain text is a
hint. **Put the GIF on step 2 or 3**, where the plain-text feel matters less —
`preflight` flags it if the image lands on step 1.

### Variable names with spaces

`{{Gif url}}` has a space in it, which looks wrong but is not: the live
workspace already runs `{{NEW A 1}}` and `{{SUBJECT EMAIL 1}}` in sent
campaigns. The capital-first, 20-character rules are what actually matter.

### Optional: push straight to the leads

Instead of the CSV round trip you can write the URLs onto existing Instantly
leads as custom variables. Instantly registers new keys on the campaign
automatically.

```bash
loomgif batch --input prospects.csv --push --campaign <campaign-id>
```

Needs `INSTANTLY_API_KEY` in `.env`. The CSV path stays the default because it
matches how the campaign skill already hands work over.

## Screenshots from elsewhere

The screenshot is the only stage that needs a browser. Everything after it —
compositing, GIF, hosting, CSV — is ffmpeg and Python. So a capture can come
from outside, which is what makes the pipeline runnable in environments where
Chromium is unavailable:

```bash
# a saved PNG, an S3 object, an Apify key-value-store record — anything
loomgif render --website acme.com --screenshot https://.../screenshot.png
loomgif render --website acme.com --screenshot ./acme.png
```

A `Screenshot` column in the batch CSV does the same per row.

The trade-off is the sticky navigation. Lifting it needs the live DOM, so a
supplied image keeps whatever header it was captured with and that header
scrolls away with the page instead of staying pinned. The log says so when it
happens.

Supplied images are trimmed to the same aspect cap as our own captures, since a
15,000px page scrolled inside five seconds is an unreadable blur.

To drive just the capture stage with this repo's own browser instead of an
external service:

```bash
loomgif shot --website acme.com --out acme.png
```

## Redirects

`jaama.co.uk` redirects to `jaama.com`. Left alone, every run burns a redirect
and files the prospect under a domain they do not use, so the cache key and the
output folder are both wrong. Redirects are resolved before capture and the slug
follows the destination. `--no-follow-redirects` opts out.

## Input CSV

Only `Email` and `Website` are required. Column names are matched
case-insensitively and the campaign CSV's own headers work as-is.

```csv
Email,First name,Company name,Website
sam@obrizum.com,Sam,Obrizum,https://obrizum.com
```

Optional columns: `Facecam` (path to a different clip for that prospect),
`Screenshot` (a path or URL to a capture taken elsewhere) and `Loom link`
(a real click-through URL).

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
2. The face cam is cover-cropped to a square, resized to the bubble, and shaped
   with `alphamerge` against a Pillow-generated anti-aliased mask.
3. If the site has a solid sticky header, it is composited **pinned at the top**.

Masks are generated once at 4x and cached in `output/.overlays/`.

The bubble is a **squircle** — a superellipse, `|x|^n + |y|^n = 1` at n=4.5 —
with no ring and a soft drop shadow behind it, the way a screen recorder's own
bubble looks. `FACECAM_SHAPE=circle` switches back to a circle.

The shadow is wide and faint rather than tight and dark, so it reads as depth
instead of an outline, and it gives the bubble separation on white pages where a
bare shape would otherwise dissolve into the background. All three parameters are
fractions of the bubble size, so they scale with the canvas:

| Variable | Default | Effect |
|---|---|---|
| `FACECAM_SHADOW_OPACITY` | `0.30` | `0` turns the shadow off entirely |
| `FACECAM_SHADOW_BLUR_RATIO` | `0.085` | Softness; larger is more diffuse |
| `FACECAM_SHADOW_OFFSET_RATIO` | `0.035` | How far it sits below the bubble |

Geometry is env-tunable: `FACECAM_DIAMETER_RATIO` (0.32 of canvas height),
`FACECAM_MARGIN_RATIO`, `FACECAM_SHAPE`, `SCROLL_RATIO`.

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

### Length

By default the clip runs **exactly as long as the face cam take** (minus any
`--facecam-start` trim). Sam's clips are 5s, so the video and the GIF are 5.000s
— the video ends where he stops talking, and the GIF loops on a whole sentence
instead of cutting mid-word. `--duration` overrides it.

GIF frame delays are stored in centiseconds, so only frame rates that divide 100
exactly can hit a whole number of seconds. 12fps means an 8.33cs delay that
rounds to 8 and quietly runs the GIF 4% fast. Rates are therefore snapped to
25 / 20 / 10 / 5, and the encoder logs the duration it actually produced:

```
Duration bound to sam-facecam-1.mp4: 5.00s
GIF 600px @10fps / 256 colours -> 1.68 MB, 5.00s
```

### Face cam exposure

Webcam takes vary a lot, so a fixed brightness bump would blow out one clip and
leave another dark. Each clip is **measured once** (a few sampled frames, cached
per file) and given the gamma that lifts it to `FACECAM_TARGET_LUMA`. Bright
takes are left alone — the correction is clamped to never darken.

**The measurement reads the face region, not the whole frame.** This matters more
than it sounds. Sam's first clip sits in front of a bright window: the full frame
averages 129 while his face is 104. Measuring the whole frame reads the room, asks
for almost no correction, and leaves a backlit face dark — which is exactly what
happened before this was fixed. Measured on the centre box instead, two clips that
differ by 40 points overall both come in at 104, and both get the lift they need.

```
sam-facecam-1.mp4 face luma 103.8/255 -> gamma 1.69   (frame average: 129)
sam-facecam-2.mp4 face luma 103.6/255 -> gamma 1.70   (frame average:  88)
```

Gamma maps 0 to 0 and 255 to 255 by definition, so lifting cannot clip highlights
— a window behind the subject compresses rather than blowing out. It does flatten
contrast, which `FACECAM_CONTRAST` (1.16) puts back.

Set `FACECAM_GAMMA` to a number to skip the measurement, or
`FACECAM_AUTO_EXPOSURE=0` to turn it off. `FACECAM_BRIGHTNESS`,
`FACECAM_CONTRAST` and `FACECAM_SATURATION` are applied on top.

### GIF sizing

Email clients choke well before the nominal limits, so `to_gif` walks a ladder of
palette size, then frame rate, then width until the file fits `--max-mb`
(default 1.8MB), using two-pass `palettegen`/`paletteuse` with `stats_mode=diff`.
Width goes last, because the `<img>` tag declares `width="600"` and a narrower
GIF gets upscaled by the client and looks soft.

If it exhausts the ladder it warns rather than shipping something huge — shorten
`--duration`, which is the biggest lever by far.

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
re-running refreshes the media without breaking already-sent emails. Verified
against the live account: a repeat upload returns the same URL *and* the same
file id, so it genuinely overwrites rather than creating a second file.

The client adapts to whichever SDK generation is installed — 3.x/4.x take
`public_key` and `url_endpoint` alongside the private key, 5.x takes the private
key alone — and falls back to the documented REST endpoint if the SDK is absent
or errors. Both paths are tested against the live account and return identical
URLs.

**Hosted URLs carry a content hash**, e.g. `…/monday-com.gif?v=4e03f01f3b8f`.

Overwriting a file keeps its URL, but ImageKit's CDN goes on serving the
previously cached bytes — verified live, where the plain URL still returned a
stale GIF after a new one had been stored.

The key has to come from the file's contents. ImageKit's own `versionInfo.id`
does **not** change on overwrite (the name advances to "Version 5" while the id
stays put), so keying on it busts the cache exactly once and is stale from then
on. A content hash is stable when nothing changed — no needless URL churn — and
changes the moment the render does. It also pins each send to the creative that
existed when it went out: already-sent emails keep rendering what was actually
sent, and new sends pick up the change.

**Only the GIF is uploaded by default** (`UPLOAD_ASSETS=gif`), at 600px wide. The MP4, WebM and
poster are local masters; hosting them multiplies storage for files no prospect
opens. Set `UPLOAD_ASSETS=all` if you want them on the CDN too.

ImageKit also re-optimises on delivery: the 897KB stored GIF is served at 593KB.
