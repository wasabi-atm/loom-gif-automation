"""Capture a prospect's homepage / landing page as a tall full-page PNG.

The PNG becomes the scrolling background of the fake screen-recording, so we want
the whole page, at retina scale, with cookie banners out of the way.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import RenderConfig

from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Capture:
    """A captured page, plus the sticky navigation lifted out as its own layer."""

    page: Path
    navbar: Optional[Path] = None
    navbar_height: int = 0  # device pixels, matching the page image's scale

# Consent overlays ruin the shot. We click the obvious accept/reject controls, then
# nuke anything left that is still glued to the viewport.
_CONSENT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-reject-all-handler",
    "button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "button[aria-label*='accept' i]",
    "button[aria-label*='reject' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "[data-testid='uc-accept-all-button']",
]

_STRIP_OVERLAYS_JS = """
() => {
  // Remove only what would actually smear across a full-page screenshot:
  // consent bars, chat bubbles, newsletter popups, bottom-anchored bars and
  // full-screen modals. Anything outside the current viewport is left alone —
  // scroll-driven sticky *sections* further down the page are real content,
  // and deleting them guts the middle of the capture.
  const vh = window.innerHeight;
  const vw = window.innerWidth;
  const junk = /cookie|consent|gdpr|privacy-banner|newsletter|subscribe|intercom|drift|crisp|zendesk|hubspot-messages|livechat|chat-widget|popup|modal-overlay/i;
  const doomed = [];

  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;

    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    // Not currently overlaying the viewport -> not an overlay.
    if (r.top > vh || r.bottom < 0) continue;

    const name = (el.id || '') + ' ' + (el.getAttribute('class') || '');
    const isJunk = junk.test(name);
    const isBottomBar = r.bottom > vh - 8 && r.height < vh * 0.4;
    const isFullScreen = r.height > vh * 0.8 && r.width > vw * 0.8 && parseInt(s.zIndex || 0, 10) > 100;

    if (isJunk || isBottomBar || isFullScreen) doomed.push(el);
  }
  doomed.forEach((el) => el.remove());

  document.documentElement.style.scrollBehavior = 'auto';
  const style = document.createElement('style');
  style.textContent = '*,*::before,*::after{animation-play-state:paused!important;transition:none!important}';
  document.head.appendChild(style);
}
"""

# Find the site's sticky/fixed top navigation and report whether it is solid
# enough to pin as its own layer.
_FIND_NAV_JS = """
() => {
  const vw = window.innerWidth;
  let best = null;
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (r.top > 8 || r.height < 28 || r.height > 200) continue;
    if (r.width < vw * 0.6) continue;
    // Prefer the outermost bar, which is the one carrying the background.
    if (!best || r.height > best.rect.height) best = { el, rect: r, style: s };
  }
  if (!best) return null;

  const bg = best.style.backgroundColor || '';
  const alpha = (bg.match(/rgba?\(([^)]+)\)/) || [])[1];
  const parts = alpha ? alpha.split(',').map((v) => parseFloat(v)) : [];
  const opaque = (parts.length < 4 || parts[3] > 0.75) && bg !== 'transparent';
  const blurred = (best.style.backdropFilter || best.style.webkitBackdropFilter || 'none') !== 'none';

  window.__loomgifNav = best.el;
  return { height: Math.round(best.rect.height), solid: Boolean(opaque || blurred) };
}
"""

_HIDE_NAV_JS = "() => { if (window.__loomgifNav) window.__loomgifNav.style.visibility = 'hidden'; }"

_LAZY_LOAD_JS = """
async () => {
  // Scroll to the bottom in steps so lazy images actually load, then return to top.
  const step = Math.round(window.innerHeight * 0.8);
  for (let y = 0; y < document.body.scrollHeight; y += step) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 120));
  }
  window.scrollTo(0, 0);
  await new Promise((r) => setTimeout(r, 350));
}
"""


def normalise_url(raw: str) -> str:
    """Accept 'acme.com', 'www.acme.com/pricing', 'https://acme.com' alike."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Empty website URL")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    return raw


def slug_for(url: str) -> str:
    host = urlparse(normalise_url(url)).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "prospect"


def capture(
    url: str,
    dest: Path,
    cfg: RenderConfig,
    timeout_ms: int = 45_000,
    full_page: bool = True,
    wait_extra_ms: int = 1_200,
    attempts: int = 3,
) -> "Capture":
    """Screenshot `url` to `dest`. Raises if every attempt fails.

    Headless Chromium occasionally dies outright on heavy pages, which would
    otherwise cost a whole batch, so each capture gets a few goes.
    """
    last: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return _capture_once(url, dest, cfg, timeout_ms, full_page, wait_extra_ms)
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Capture attempt %d/%d failed for %s: %s", attempt, attempts, url, exc)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Screenshot failed after {attempts} attempts for {url}: {last}")


def _capture_once(
    url: str,
    dest: Path,
    cfg: RenderConfig,
    timeout_ms: int,
    full_page: bool,
    wait_extra_ms: int,
) -> "Capture":
    from playwright.sync_api import TimeoutError as PWTimeout  # noqa: WPS433
    from playwright.sync_api import sync_playwright

    url = normalise_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--hide-scrollbars", "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            device_scale_factor=cfg.device_scale_factor,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PWTimeout:
                log.debug("networkidle never settled for %s — continuing", url)

            _dismiss_consent(page)
            page.evaluate(_LAZY_LOAD_JS)

            navbar, navbar_height = _lift_navbar(page, dest, cfg)

            page.evaluate(_STRIP_OVERLAYS_JS)
            page.wait_for_timeout(wait_extra_ms)

            _shoot(page, dest, cfg, full_page)
            return Capture(page=dest, navbar=navbar, navbar_height=navbar_height)
        finally:
            for closeable in (context, browser):
                try:
                    closeable.close()
                except Exception:  # noqa: BLE001 — the browser may already be gone
                    pass


def _dismiss_consent(page, per_selector_timeout: int = 900) -> None:
    for selector in _CONSENT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=per_selector_timeout):
                locator.click(timeout=per_selector_timeout, force=True)
                page.wait_for_timeout(250)
                return
        except Exception:  # noqa: BLE001 — any failure here is non-fatal by design
            continue


def _lift_navbar(page, dest: Path, cfg: RenderConfig):
    """Capture the sticky nav as its own layer and hide it on the page.

    A real screen recording keeps the site's sticky header pinned while the body
    scrolls underneath. Baking it into the scrolling image instead makes it slide
    away after a second, which is the first thing that reads as fake.

    Only solid headers are lifted: pinning a transparent one would freeze a band
    of hero content over the moving page.
    """
    if not cfg.pin_sticky_nav:
        return None, 0
    try:
        info = page.evaluate(_FIND_NAV_JS)
    except Exception as exc:  # noqa: BLE001
        log.debug("Nav detection failed: %s", exc)
        return None, 0

    if not info:
        log.info("No sticky navigation found — the page's own header scrolls with the body")
        return None, 0
    if not info.get("solid"):
        log.info("Sticky nav is transparent — leaving it in the page rather than pinning it")
        return None, 0

    height = int(info["height"])
    # Scroll first so the header settles into its scrolled state, which is what
    # the viewer sees for all but the opening moment.
    page.evaluate("() => window.scrollTo(0, Math.min(900, document.body.scrollHeight))")
    page.wait_for_timeout(700)

    navbar = dest.with_name("navbar.png")
    page.screenshot(
        path=str(navbar),
        clip={"x": 0, "y": 0, "width": cfg.viewport_width, "height": height},
        animations="disabled",
        scale="device",
    )

    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(400)
    page.evaluate(_HIDE_NAV_JS)

    device_height = height * cfg.device_scale_factor
    log.info("Pinned sticky nav (%dpx tall) as its own layer", height)
    return navbar, device_height


def _shoot(page, dest: Path, cfg: RenderConfig, full_page: bool) -> None:
    """Capture, clipped to `max_capture_height` and retried at 1x if Chromium
    chokes on the image size."""
    height = page.evaluate("() => document.body.scrollHeight") or cfg.viewport_height
    height = max(int(min(height, cfg.max_capture_height)), cfg.viewport_height)
    # `clip` only escapes the viewport when full_page is also set — without it
    # Playwright silently returns just the first screenful.
    clip = {"x": 0, "y": 0, "width": cfg.viewport_width, "height": height}
    kwargs = {"clip": clip, "full_page": True} if full_page else {}
    try:
        page.screenshot(path=str(dest), animations="disabled", scale="device", **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("Capture failed at %dx scale (%s) — retrying at 1x", cfg.device_scale_factor, exc)
        page.screenshot(path=str(dest), animations="disabled", scale="css", **kwargs)


def capture_or_none(url: str, dest: Path, cfg: RenderConfig) -> Optional["Capture"]:
    """Batch-friendly wrapper: log and skip instead of blowing up the whole run."""
    try:
        return capture(url, dest, cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("Screenshot failed for %s: %s", url, exc)
        return None
