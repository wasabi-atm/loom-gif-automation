"""End-to-end orchestration for one prospect, and the batch loop over a CSV.

Order matters and is fixed by the campaign skill:
screenshot -> composite -> GIF -> host on ImageKit -> collect URLs -> CSV.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import compose, email_embed, facecam as facecam_mod, screenshot
from .config import Settings
from .imagekit_client import ImageKitUploader, Upload
from .instantly import (
    VAR_GIF,
    VAR_GIF_SMALL,
    VAR_LINK,
    VAR_POSTER,
    VAR_VIDEO,
    VAR_VIDEO_WEBM,
)

log = logging.getLogger(__name__)

#: Width of the optional `Gif small` variant, for mobile-heavy lists.
SMALL_VARIANT_WIDTH = 400



@dataclass
class Prospect:
    email: str
    website: str
    first_name: str = ""
    company: str = ""
    facecam: str = ""
    link_url: str = ""

    @property
    def slug(self) -> str:
        return screenshot.slug_for(self.website)


@dataclass
class ProspectResult:
    prospect: Prospect
    status: str = "pending"
    note: str = ""
    urls: Dict[str, str] = field(default_factory=dict)
    local: Dict[str, Path] = field(default_factory=dict)

    def as_manifest_row(self) -> Dict[str, str]:
        row = {
            "Email": self.prospect.email,
            "Website": self.prospect.website,
            "Status": self.status,
            "Note": self.note,
        }
        row.update(self.urls)
        return row


def read_prospects(path: Path) -> List[Prospect]:
    """Read a prospect CSV. Accepts the campaign CSV's own column names."""
    aliases = {
        "email": "email",
        "website": "website",
        "url": "website",
        "domain": "website",
        "first name": "first_name",
        "first_name": "first_name",
        "firstname": "first_name",
        "company name": "company",
        "company": "company",
        "facecam": "facecam",
        "loom link": "link_url",
        "link": "link_url",
    }
    prospects: List[Prospect] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for index, raw in enumerate(csv.DictReader(handle), start=2):
            mapped: Dict[str, str] = {}
            for key, value in raw.items():
                if key is None:
                    continue
                field_name = aliases.get(key.strip().lower())
                if field_name and value:
                    mapped[field_name] = value.strip()
            if not mapped.get("email"):
                log.warning("Row %d has no email — skipped", index)
                continue
            if not mapped.get("website"):
                log.warning("Row %d (%s) has no website — skipped", index, mapped["email"])
                continue
            prospects.append(Prospect(**mapped))
    log.info("Loaded %d prospects from %s", len(prospects), path)
    return prospects


def run_one(
    prospect: Prospect,
    settings: Settings,
    uploader: Optional[ImageKitUploader] = None,
    upload: bool = True,
    make_mp4: bool = True,
    make_gif: bool = True,
    force: bool = False,
) -> ProspectResult:
    """Screenshot, composite, GIF and host the media for a single prospect."""
    result = ProspectResult(prospect=prospect)
    cfg = settings.render
    work = settings.output_dir / prospect.slug
    work.mkdir(parents=True, exist_ok=True)

    try:
        shot = work / "screenshot.png"
        navbar = work / "navbar.png"
        if force or not shot.exists():
            capture = screenshot.capture(prospect.website, shot, cfg)
            navbar = capture.navbar
        else:
            log.info("Reusing cached screenshot %s", shot)
            navbar = navbar if navbar.exists() else None
        result.local["screenshot"] = shot
        if navbar:
            result.local["navbar"] = navbar

        source = Path(prospect.facecam).expanduser() if prospect.facecam else settings.facecam_path
        facecam = facecam_mod.pick(source, prospect.slug)
        if facecam is None:
            raise FileNotFoundError(
                f"No face cam clip found at {source}. Drop one in assets/facecam/ "
                "or pass --facecam /path/to/clip.mp4 (a directory works too)."
            )
        render = compose.render(shot, facecam, work / prospect.slug, cfg, make_mp4=make_mp4, navbar=navbar)
        result.local["poster"] = render.poster
        for label, path in (("webm", render.webm), ("mp4", render.mp4)):
            if path:
                result.local[label] = path

        if make_gif:
            gif = compose.to_gif(render.mp4 or render.webm, work / f"{prospect.slug}.gif", cfg)
            result.local["gif"] = gif

        if upload:
            uploader = uploader or ImageKitUploader(settings.imagekit)
            result.urls = _upload_all(uploader, result.local, prospect, settings)
            result.urls[VAR_LINK] = _click_url(prospect, settings)
            result.status = "ok"
        else:
            result.status = "rendered"
            result.note = "local only, not uploaded"

    except Exception as exc:  # noqa: BLE001 — one bad prospect must not kill the batch
        result.status = "failed"
        result.note = f"{type(exc).__name__}: {exc}"
        log.error("Failed for %s (%s): %s", prospect.email, prospect.website, exc)

    return result


def _upload_all(
    uploader: ImageKitUploader,
    local: Dict[str, Path],
    prospect: Prospect,
    settings: Settings,
) -> Dict[str, str]:
    folder = settings.imagekit.folder
    tags = ["outreach", "loom-gif", prospect.slug]
    urls: Dict[str, str] = {}

    def push(key: str, column: str, suffix: str) -> Optional[Upload]:
        path = local.get(key)
        if not path or not path.exists():
            return None
        uploaded = uploader.upload(
            path,
            file_name=f"{prospect.slug}{suffix}",
            folder=folder,
            tags=tags,
            unique=False,
            overwrite=True,
        )
        urls[column] = uploaded.versioned_url
        return uploaded

    cfg = settings.render
    gif = push("gif", VAR_GIF, ".gif") if cfg.hosts("gif") else None
    if cfg.hosts("mp4"):
        push("mp4", VAR_VIDEO, ".mp4")
    if cfg.hosts("webm"):
        push("webm", VAR_VIDEO_WEBM, ".webm")
    if cfg.hosts("poster"):
        push("poster", VAR_POSTER, "-poster.jpg")

    # A narrower variant for mobile-heavy lists, served by ImageKit rather than
    # re-encoded. Only worth a second variable when the file is actually wider.
    if gif and cfg.gif_width > SMALL_VARIANT_WIDTH:
        urls[VAR_GIF_SMALL] = uploader.transform(gif.versioned_url, f"w-{SMALL_VARIANT_WIDTH}")
    return urls


def _click_url(prospect: Prospect, settings: Settings) -> str:
    if prospect.link_url:
        return prospect.link_url
    template = settings.click_through_url or ""
    return (
        template.replace("{{company}}", prospect.company or "")
        .replace("{{slug}}", prospect.slug)
        .replace("{{email}}", prospect.email)
    )


def run_batch(
    prospects: List[Prospect],
    settings: Settings,
    upload: bool = True,
    make_mp4: bool = True,
    make_gif: bool = True,
    force: bool = False,
) -> List[ProspectResult]:
    uploader = ImageKitUploader(settings.imagekit) if upload else None
    results: List[ProspectResult] = []
    total = len(prospects)
    for index, prospect in enumerate(prospects, start=1):
        log.info("[%d/%d] %s (%s)", index, total, prospect.email, prospect.website)
        results.append(
            run_one(
                prospect,
                settings,
                uploader=uploader,
                upload=upload,
                make_mp4=make_mp4,
                make_gif=make_gif,
                force=force,
            )
        )
    return results
