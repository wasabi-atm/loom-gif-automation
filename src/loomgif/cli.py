"""Command line entry point.

    loomgif render   --website acme.com --email a@acme.com   # one prospect
    loomgif batch    --input examples/prospects.csv          # a whole list
    loomgif merge    --campaign campaign.csv --manifest output/manifest.csv
    loomgif snippet                                          # sequence-body HTML
    loomgif doctor                                           # check the setup
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import csv_merge, email_embed
from .config import ConfigError, Settings, load_settings
from .instantly import MEDIA_COLUMNS, VAR_GIF, InstantlyClient
from .pipeline import Prospect, ProspectResult, read_prospects, run_batch, run_one


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    render = settings.render
    for attr, value in (
        ("duration", getattr(args, "duration", None)),
        ("canvas_width", getattr(args, "width", None)),
        ("canvas_height", getattr(args, "height", None)),
        ("fps", getattr(args, "fps", None)),
        ("gif_width", getattr(args, "gif_width", None)),
        ("gif_fps", getattr(args, "gif_fps", None)),
        ("facecam_start", getattr(args, "facecam_start", None)),
    ):
        if value is not None:
            setattr(render, attr, value)
    if getattr(args, "max_mb", None):
        render.gif_max_bytes = int(args.max_mb * 1_000_000)
    if getattr(args, "output", None):
        settings.output_dir = Path(args.output).expanduser().resolve()
        settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _report(results: List[ProspectResult], settings: Settings, push: bool, campaign: Optional[str]) -> int:
    manifest = csv_merge.write_manifest([r.as_manifest_row() for r in results], settings.output_dir / "manifest.csv")

    ok = [r for r in results if r.status in ("ok", "rendered")]
    failed = [r for r in results if r.status == "failed"]

    if push:
        _push_to_instantly(ok, settings, campaign)

    print("\n" + "=" * 62)
    print(f"Done: {len(ok)}/{len(results)} succeeded")
    print(f"Manifest: {manifest}")
    if failed:
        print(f"\n{len(failed)} failed — these must NOT get the GIF version of the campaign:")
        for result in failed:
            print(f"  - {result.prospect.email:<38} {result.note}")
    print("\nNext: loomgif merge --campaign <campaign.csv> --manifest %s" % manifest)
    print("=" * 62)
    return 1 if failed and not ok else 0


def _push_to_instantly(results: List[ProspectResult], settings: Settings, campaign: Optional[str]) -> None:
    try:
        client = InstantlyClient(settings.instantly)
    except ConfigError as exc:
        print(f"\nSkipping Instantly push: {exc}", file=sys.stderr)
        return
    for result in results:
        urls = result.urls
        if not urls.get(VAR_GIF):
            continue
        try:
            client.push_media(
                email=result.prospect.email,
                gif_url=urls.get(VAR_GIF, ""),
                gif_small_url=urls.get("Gif small", ""),
                video_url=urls.get("Video url", ""),
                webm_url=urls.get("Webm url", ""),
                poster_url=urls.get("Poster url", ""),
                link_url=urls.get("Loom link", ""),
                campaign=campaign,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  Instantly push failed for {result.prospect.email}: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# commands                                                                     #
# --------------------------------------------------------------------------- #

def cmd_render(args: argparse.Namespace) -> int:
    settings = _apply_overrides(load_settings(args.facecam), args)
    prospect = Prospect(
        email=args.email or "preview@example.com",
        website=args.website,
        first_name=args.first_name or "",
        company=args.company or "",
        link_url=args.link or "",
    )
    result = run_one(
        prospect,
        settings,
        upload=not args.no_upload,
        make_mp4=not args.no_mp4,
        make_gif=not args.no_gif,
        force=args.force,
    )
    if result.status == "failed":
        print(f"Failed: {result.note}", file=sys.stderr)
        return 1

    for label, path in result.local.items():
        if path:
            print(f"  {label:<11} {path}")
    for column, url in result.urls.items():
        print(f"  {column:<11} {url}")

    gif_url = result.urls.get(VAR_GIF)
    if gif_url and not args.no_preview:
        dest = settings.output_dir / prospect.slug / "preview.html"
        email_embed.preview(gif_url, dest, company=prospect.company, alt=args.alt)
        print(f"\n  preview     {dest}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    settings = _apply_overrides(load_settings(args.facecam), args)
    prospects = read_prospects(Path(args.input))
    if args.limit:
        prospects = prospects[: args.limit]
    if not prospects:
        print("No usable rows in the input CSV.", file=sys.stderr)
        return 1
    results = run_batch(
        prospects,
        settings,
        upload=not args.no_upload,
        make_mp4=not args.no_mp4,
        make_gif=not args.no_gif,
        force=args.force,
    )
    return _report(results, settings, push=args.push, campaign=args.campaign)


def cmd_merge(args: argparse.Namespace) -> int:
    report = csv_merge.merge(
        campaign_csv=Path(args.campaign),
        manifest_csv=Path(args.manifest),
        out_dir=Path(args.output or "output"),
        columns=args.columns.split(",") if args.columns else MEDIA_COLUMNS,
    )
    print(report.summary())
    if report.nogif_path:
        print(
            "\nThe no-GIF file must run as a separate campaign — an empty "
            "'Gif url' renders a broken image icon."
        )
    return 0


def cmd_snippet(args: argparse.Namespace) -> int:
    print(email_embed.campaign_snippet(with_link=args.link, alt=args.alt, width=args.gif_width or 600))
    print(
        "\n# Paste into the Instantly sequence body via Code View, not by dragging an image.\n"
        "# Put it on step 2 or 3 — step 1 is often forced to text-only.\n"
        "# Check all four image-stripping settings before launch (see README)."
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print("Dependencies")
    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        print(f"  {'OK ' if found else 'MISSING'}  {binary}  {found or 'brew install ffmpeg'}")
        ok &= bool(found)
    for module, hint in (
        ("playwright", "pip install playwright && playwright install chromium"),
        ("PIL", "pip install Pillow"),
        ("jinja2", "pip install Jinja2"),
        ("requests", "pip install requests"),
        ("imagekitio", "pip install imagekitio  (optional — REST fallback exists)"),
    ):
        try:
            __import__(module)
            print(f"  OK       {module}")
        except ImportError:
            print(f"  MISSING  {module}  {hint}")
            ok &= module == "imagekitio"

    settings = load_settings(args.facecam)
    print("\nCredentials")
    for label, cfg in (("ImageKit", settings.imagekit), ("Instantly", settings.instantly)):
        try:
            cfg.require()
            print(f"  OK       {label}")
        except ConfigError as exc:
            print(f"  MISSING  {label}: {exc}")
            ok &= label != "ImageKit"

    print("\nAssets")
    facecam = settings.facecam_path
    print(f"  {'OK ' if facecam.exists() else 'MISSING'}  face cam  {facecam}")
    if not facecam.exists():
        print("           Drop a clip there, or pass --facecam /path/to/clip.mp4")

    print("\n" + ("Ready." if ok else "Fix the MISSING items above."))
    return 0 if ok else 1


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loomgif", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_render_opts(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--facecam", help="Override the face cam clip for this run")
        sub.add_argument("--facecam-start", type=float, help="Seconds into the clip to start from")
        sub.add_argument("--duration", type=float, help="Clip length in seconds (default 6)")
        sub.add_argument("--width", type=int, help="Canvas width (default 1280)")
        sub.add_argument("--height", type=int, help="Canvas height (default 720)")
        sub.add_argument("--fps", type=int, help="Video frame rate (default 24)")
        sub.add_argument("--gif-width", type=int, help="GIF width in px (default 600)")
        sub.add_argument("--gif-fps", type=int, help="GIF frame rate (default 12)")
        sub.add_argument("--max-mb", type=float, help="GIF size budget in MB (default 1.8)")
        sub.add_argument("--output", help="Output directory (default ./output)")
        sub.add_argument("--no-upload", action="store_true", help="Render locally, skip ImageKit")
        sub.add_argument("--no-gif", action="store_true")
        sub.add_argument("--no-mp4", action="store_true")
        sub.add_argument("--force", action="store_true", help="Re-screenshot even if one is cached")

    render = subparsers.add_parser("render", help="Build media for a single prospect")
    render.add_argument("--website", required=True, help="Prospect homepage, e.g. acme.com")
    render.add_argument("--email", help="Prospect email (used for naming and the manifest)")
    render.add_argument("--first-name")
    render.add_argument("--company")
    render.add_argument("--link", help="Click-through URL if a real Loom exists")
    render.add_argument("--alt", default=email_embed.DEFAULT_ALT, help="Image alt text (required in the email)")
    render.add_argument("--no-preview", action="store_true")
    add_render_opts(render)
    render.set_defaults(func=cmd_render)

    batch = subparsers.add_parser("batch", help="Build media for every row in a CSV")
    batch.add_argument("--input", required=True, help="CSV with Email and Website columns")
    batch.add_argument("--limit", type=int, help="Only process the first N rows (handy for a test run)")
    batch.add_argument("--push", action="store_true",
                       help="Also write the URLs onto Instantly leads via the API")
    batch.add_argument("--campaign", help="Instantly campaign id to scope --push to")
    add_render_opts(batch)
    batch.set_defaults(func=cmd_batch)

    merge = subparsers.add_parser("merge", help="Join hosted URLs onto a campaign CSV")
    merge.add_argument("--campaign", required=True, help="Campaign CSV from /mta-instantly-campaign")
    merge.add_argument("--manifest", required=True, help="manifest.csv produced by batch")
    merge.add_argument("--output", help="Output directory (default ./output)")
    merge.add_argument("--columns", help="Comma-separated columns to merge (default all media columns)")
    merge.set_defaults(func=cmd_merge)

    snippet = subparsers.add_parser("snippet", help="Print the sequence-body HTML")
    snippet.add_argument("--link", action="store_true", help="Wrap in <a> pointing at {{Loom link}}")
    snippet.add_argument("--alt", default=email_embed.DEFAULT_ALT)
    snippet.add_argument("--gif-width", type=int, default=600)
    snippet.set_defaults(func=cmd_snippet)

    doctor = subparsers.add_parser("doctor", help="Check dependencies, credentials and assets")
    doctor.add_argument("--facecam")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
