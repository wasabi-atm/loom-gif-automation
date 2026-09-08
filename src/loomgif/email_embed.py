"""Email HTML for the hosted GIF.

Produces two things:

* ``snippet`` — the exact tag to paste into the Instantly sequence body via
  Code View. It references ``{{Gif url}}``, so it is written once for the whole
  campaign rather than per prospect.
* ``preview`` — a standalone HTML file for eyeballing the result locally.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import REPO_ROOT, RenderConfig
from .instantly import VAR_GIF, VAR_LINK

log = logging.getLogger(__name__)

TEMPLATE_DIR = REPO_ROOT / "templates"

DEFAULT_ALT = "Your homepage, with a few notes from me"


def default_width() -> int:
    """Display width for the <img>, taken from the render config.

    These must agree. A hosted GIF narrower than the tag's width gets upscaled
    by the client and looks soft; wider and the extra pixels are paid for and
    thrown away.
    """
    return RenderConfig().gif_width

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def snippet(
    gif_url: str = "{{" + VAR_GIF + "}}",
    link_url: str = "",
    alt: str = DEFAULT_ALT,
    width: Optional[int] = None,
) -> str:
    """Render the sequence-body tag.

    Defaults to the campaign-wide form using the ``{{Gif url}}`` variable. Pass a
    literal URL only when building a one-off email outside Instantly.
    """
    if not alt or not alt.strip():
        raise ValueError("Alt text is required — clients block images by default and the reader sees an empty box.")
    width = width or default_width()
    template = _env.get_template("email_embed.html.j2")
    return template.render(gif_url=gif_url, link_url=link_url, alt=alt, width=width).strip()


def campaign_snippet(with_link: bool = False, alt: str = DEFAULT_ALT, width: Optional[int] = None) -> str:
    """The snippet as it goes into Instantly, variables and all.

    `with_link` wraps the image in an <a> pointing at {{Loom link}} — only use it
    when a real Loom exists for every prospect in the file.
    """
    return snippet(
        gif_url="{{" + VAR_GIF + "}}",
        link_url=("{{" + VAR_LINK + "}}") if with_link else "",
        alt=alt,
        width=width,
    )


def preview(
    gif_url: str,
    dest: Path,
    company: str = "",
    link_url: str = "",
    alt: str = DEFAULT_ALT,
    width: Optional[int] = None,
    body_before: Optional[str] = None,
    body_after: Optional[str] = None,
) -> Path:
    """Write a browser-openable preview of the embed with a real GIF URL."""
    width = width or default_width()
    embed = snippet(gif_url=gif_url, link_url=link_url, alt=alt, width=width)
    html = _env.get_template("email_preview.html.j2").render(
        embed=embed,
        company=company,
        width=width,
        body_before=body_before,
        body_after=body_after,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    log.info("Wrote preview %s", dest)
    return dest
