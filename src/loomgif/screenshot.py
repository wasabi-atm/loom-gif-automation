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

log = logging.getLogger(__name__)

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

_STRIP_FIXED_JS = """
() => {
  // Remove sticky/fixed chrome that would smear across a full-page screenshot.
  const kill = [];
  for (const el of document.querySelectorAll('body *')) {
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    const coversLot = r.height > window.innerHeight * 0.5 || r.width > window.innerWidth * 0.9;
    const looksLikeConsent = /cookie|consent|gdpr|banner|modal|overlay|popup|newsletter|chat|intercom|drift/i
      .test((el.id || '') + ' ' + (el.className || ''));
    // Keep a normal top nav (it reads as authentic); drop overlays and bottom bars.
    const isTopNav = r.top <= 4 && r.height < 140;
    if (isTopNav && !looksLikeConsent) continue;
    if (looksLikeConsent || coversLot || r.top > window.innerHeight * 0.5) kill.push(el);
  }
  kill.forEach((el) => el.remove());
  document.documentElement.style.scrollBehavior = 'auto';
  // Freeze animations so the capture is deterministic.
  const style = document.createElement('style');
  style.textContent = '*,*::before,*::after{animation-play-state:paused!important;transition:none!important}';
  document.head.appendChild(style);
}
"""

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
) -> Path:
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
) -> Path:
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
            page.evaluate(_STRIP_FIXED_JS)
            page.wait_for_timeout(wait_extra_ms)

            _shoot(page, dest, cfg, full_page)
        finally:
            for closeable in (context, browser):
                try:
                    closeable.close()
                except Exception:  # noqa: BLE001 — the browser may already be gone
                    pass

    log.info("Captured %s -> %s", url, dest)
    return dest


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


def capture_or_none(url: str, dest: Path, cfg: RenderConfig) -> Optional[Path]:
    """Batch-friendly wrapper: log and skip instead of blowing up the whole run."""
    try:
        return capture(url, dest, cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("Screenshot failed for %s: %s", url, exc)
        return None
