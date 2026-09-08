"""Central configuration. Everything is env-driven so nothing secret is ever committed."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


class ConfigError(RuntimeError):
    """Raised when required credentials are missing."""


@dataclass
class ImageKitConfig:
    url_endpoint: str = field(default_factory=lambda: os.getenv("IMAGEKIT_URL_ENDPOINT", ""))
    public_key: str = field(default_factory=lambda: os.getenv("IMAGEKIT_PUBLIC_KEY", ""))
    private_key: str = field(default_factory=lambda: os.getenv("IMAGEKIT_PRIVATE_KEY", ""))
    folder: str = field(default_factory=lambda: os.getenv("IMAGEKIT_FOLDER", "/outreach/loom-gif"))

    def require(self) -> "ImageKitConfig":
        missing = [
            name
            for name, value in (
                ("IMAGEKIT_URL_ENDPOINT", self.url_endpoint),
                ("IMAGEKIT_PUBLIC_KEY", self.public_key),
                ("IMAGEKIT_PRIVATE_KEY", self.private_key),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing ImageKit credentials: %s. Copy .env.example to .env and fill them in."
                % ", ".join(missing)
            )
        return self


@dataclass
class InstantlyConfig:
    api_key: str = field(default_factory=lambda: os.getenv("INSTANTLY_API_KEY", ""))
    api_base: str = field(
        default_factory=lambda: os.getenv("INSTANTLY_API_BASE", "https://api.instantly.ai/api/v2").rstrip("/")
    )

    def require(self) -> "InstantlyConfig":
        if not self.api_key:
            raise ConfigError(
                "Missing INSTANTLY_API_KEY. Set it in .env, or run with --no-push and upload "
                "the generated CSV to Instantly by hand."
            )
        return self


@dataclass
class RenderConfig:
    """Composition geometry and timing. Mirrors the reference Loom frame:
    full-bleed website screenshot, circular face cam pinned bottom-left."""

    canvas_width: int = field(default_factory=lambda: _int("CANVAS_WIDTH", 1280))
    canvas_height: int = field(default_factory=lambda: _int("CANVAS_HEIGHT", 720))
    duration: float = field(default_factory=lambda: _float("DURATION", 6.0))
    fps: int = field(default_factory=lambda: _int("FPS", 24))

    # Face cam bubble, expressed as a fraction of canvas height so it scales cleanly.
    facecam_diameter_ratio: float = field(default_factory=lambda: _float("FACECAM_DIAMETER_RATIO", 0.32))
    facecam_margin_ratio: float = field(default_factory=lambda: _float("FACECAM_MARGIN_RATIO", 0.045))
    facecam_ring_px: int = field(default_factory=lambda: _int("FACECAM_RING_PX", 5))
    facecam_ring_color: str = field(default_factory=lambda: os.getenv("FACECAM_RING_COLOR", "#FFFFFF"))
    # Seconds into the source footage to start using (lets you skip a slate/countdown).
    facecam_start: float = field(default_factory=lambda: _float("FACECAM_START", 0.0))

    # How far down the captured page the scroll travels, as a fraction. Lower is
    # calmer, more readable, and compresses to a much smaller GIF.
    scroll_ratio: float = field(default_factory=lambda: _float("SCROLL_RATIO", 0.5))

    # Lift a solid sticky header into its own pinned layer, the way a real
    # screen recording keeps it fixed while the body scrolls underneath.
    pin_sticky_nav: bool = field(default_factory=lambda: os.getenv("PIN_STICKY_NAV", "1") not in ("0", "false", "False"))

    # Browser capture
    viewport_width: int = field(default_factory=lambda: _int("VIEWPORT_WIDTH", 1440))
    viewport_height: int = field(default_factory=lambda: _int("VIEWPORT_HEIGHT", 900))
    device_scale_factor: int = field(default_factory=lambda: _int("DEVICE_SCALE_FACTOR", 2))
    # Cap the capture in CSS pixels. Marketing pages run to 15,000px; asking
    # Chromium for that at 2x crashes the tab, and scrolling all of it in 8
    # seconds would be unreadable anyway. ~4 viewports is what a real Loom intro
    # actually shows.
    max_capture_height: int = field(default_factory=lambda: _int("MAX_CAPTURE_HEIGHT", 3600))

    # GIF budget. Email clients choke well before this, so we ladder down to fit.
    gif_max_bytes: int = field(default_factory=lambda: _int("GIF_MAX_BYTES", 1_800_000))
    gif_width: int = field(default_factory=lambda: _int("GIF_WIDTH", 600))
    gif_fps: int = field(default_factory=lambda: _int("GIF_FPS", 12))

    @property
    def width(self) -> int:
        """Canvas width, snapped even — H.264 rejects odd dimensions."""
        return self.canvas_width // 2 * 2

    @property
    def height(self) -> int:
        return self.canvas_height // 2 * 2

    @property
    def facecam_diameter(self) -> int:
        # Keep it even — libvpx/GIF encoders dislike odd dimensions.
        return int(self.height * self.facecam_diameter_ratio) // 2 * 2

    @property
    def facecam_margin(self) -> int:
        return int(self.height * self.facecam_margin_ratio)


@dataclass
class Settings:
    imagekit: ImageKitConfig = field(default_factory=ImageKitConfig)
    instantly: InstantlyConfig = field(default_factory=InstantlyConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    facecam_path: Path = field(
        default_factory=lambda: REPO_ROOT / os.getenv("FACECAM_PATH", "assets/facecam")
    )
    output_dir: Path = field(default_factory=lambda: REPO_ROOT / os.getenv("OUTPUT_DIR", "output"))
    click_through_url: str = field(
        default_factory=lambda: os.getenv("CLICK_THROUGH_URL", "https://motiontheagency.com/")
    )


def load_settings(facecam: Optional[str] = None) -> Settings:
    settings = Settings()
    if facecam:
        settings.facecam_path = Path(facecam).expanduser().resolve()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    return settings
