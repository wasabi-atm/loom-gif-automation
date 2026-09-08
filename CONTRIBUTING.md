# Working on this repo

```bash
make setup      # venv + deps + Chromium + .env
make test       # unit tests (no network, no ffmpeg, no credentials needed)
make doctor     # verify binaries, credentials and footage
```

## Ground rules

- **Never commit `.env` or a private key.** CI fails the build if either appears.
  `.env.example` carries placeholders only.
- **Instantly column names are load-bearing.** Every one must start with a
  capital and be 20 characters or fewer, or Instantly maps it to nothing and the
  email sends with an empty variable. `csv_merge.validate_columns` enforces this
  and `tests/test_pipeline.py` guards `MEDIA_COLUMNS`.
- **Keep the tests offline.** They must run without ffmpeg, a browser or keys, so
  CI stays fast and the suite works on a plane.

## Changing the composition

`compose.build_filtergraph` returns the whole ffmpeg graph as a string, so the
quickest way to iterate is to print it and run it by hand:

```bash
python -c "from loomgif.compose import build_filtergraph; from loomgif.config import RenderConfig; print(build_filtergraph(RenderConfig()))"
```

To eyeball motion without waiting on a real site, render against a cached
screenshot — `output/<slug>/screenshot.png` is reused unless you pass `--force`.

A quick contact sheet of three frames:

```bash
ffmpeg -i out.mp4 -vf "select='eq(n\,2)+eq(n\,71)+eq(n\,141)',tile=1x3" -frames:v 1 sheet.png
```
