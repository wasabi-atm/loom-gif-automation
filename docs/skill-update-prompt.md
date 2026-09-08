# Prompt: wire `loom-gif-automation` into `/mta-instantly-campaign`

Paste the block below into a session that has the `mta-instantly-campaign`
skill available. It asks for a reviewable patch rather than an in-place edit,
because the skill lives in an app-managed directory and may be synced.

---

Update the `mta-instantly-campaign` skill so it points at the GIF pipeline that
now exists, instead of describing one abstractly.

Read `references/instantly-csv.md` first. Its "If the campaign uses personalised
GIFs" section already sets the contract — column names, the split, the ordering,
the four settings. Do not restate those rules; they are correct. The gap is that
the section says "The GIF pipeline runs before the CSV is written" without ever
saying what the pipeline is, so a run of this skill has no way to produce the
hosted URLs it then demands.

Show me a diff for review. Do not edit the skill files in place.

## What to add

The pipeline is `https://github.com/wasabi-atm/loom-gif-automation`. It renders
a Loom-style clip per prospect — their homepage scrolling behind a squircle face
cam bottom-left — converts it to an email-safe GIF, hosts it on ImageKit and
hands back a CSV that merges into the campaign file.

Add a short subsection under the existing GIF section covering:

**The two commands, in the order the spec already mandates.**

```bash
loomgif batch --input prospects.csv          # renders, hosts, writes output/manifest.csv
loomgif merge --campaign <campaign.csv> --manifest output/manifest.csv
```

`merge` outputs `<name>-gif.csv` and `<name>-nogif.csv`. It performs the
GIF/no-GIF split this spec already requires, so the skill should hand that job
to it rather than managing it by hand. Note that `merge` drops any media column
that is empty across every no-GIF row — including this spec's own `Gif url`
tracking column, which would otherwise carry through empty and render the broken
image icon the spec warns about.

**The columns it writes.** `Gif url` plus `Gif small`, `Video url`, `Webm url`,
`Poster url`, `Loom link`. All capital-first and 20 characters or fewer, per the
existing rules. Only columns with values are written, so a run that hosts just
the GIF adds one column, not six.

**The tag is unchanged.** The `<img src="{{Gif url}}" width="600" ...>` example
already in the spec is still correct — `loomgif snippet` prints exactly that,
and the render width and tag width are kept in sync in code.

**Pre-launch checking is now partly automated.** Add to the four-settings
warning that `loomgif preflight --campaign <id>` verifies two of them
("Send emails as text-only" and "Send first email as text-only") against the
campaign object, and flags an image landing on step 1. The remaining two —
Delivery Optimization and Advanced Deliverability's "Always send first email as
text-only" — are workspace-level and still need a person. Keep them on the
manual list.

**Hosted URLs carry a `?v=` content hash.** Explain in one line that this is a
CDN cache key, so the URL for a given prospect changes only when their creative
actually changes. It matters here because it means a re-render before send is
safe, and an already-sent email keeps rendering what was sent.

## What to change, not just add

- The GIF is **5 seconds**, bound to the length of the face cam take. If any
  guidance in the skill assumes an arbitrary clip length, align it.
- `references/pipeline.md` step 7 ends at the Instantly CSV. If the GIF pipeline
  belongs in the numbered pipeline as its own step before the export, add it
  there too and renumber, so the ordering rule is enforced by the pipeline
  rather than only mentioned in the CSV reference.

## Constraints

- Keep the skill's existing voice and formatting. It is terse and rule-shaped;
  match that.
- Do not duplicate the repo's README into the skill. Link, state the commands,
  and state only what changes a decision the skill makes.
- Do not weaken any existing rule. Everything above is additive except the clip
  length.
