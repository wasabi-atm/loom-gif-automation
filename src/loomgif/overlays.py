"""The face cam's alpha mask.

ffmpeg's `alphamerge` takes a greyscale image as the alpha channel, so the
bubble's shape is decided entirely here. Masks are cached on disk and reused
across every prospect in a batch.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

# Draw at 4x then downsample — cheap, reliable anti-aliasing on the curve.
_SS = 4

# A squircle is a superellipse, |x|^n + |y|^n = 1. n=2 is a plain circle; the
# familiar iOS-style corner sits around 4-5, flat enough along the edges to read
# as a rounded square while never showing a corner.
SQUIRCLE_EXPONENT = 4.5


def _superellipse(size: int, exponent: float) -> Image.Image:
    """White superellipse on black, solved per row rather than per pixel."""
    mask = Image.new("L", (size, size), 0)
    pixels = bytearray(size * size)
    half = size / 2.0

    for y in range(size):
        # Normalised distance from centre on the vertical axis.
        v = abs((y + 0.5) - half) / half
        if v >= 1.0:
            continue
        # Solve |u|^n = 1 - |v|^n for the row's half-width.
        span = (1.0 - v ** exponent) ** (1.0 / exponent)
        left = int(round(half - span * half))
        right = int(round(half + span * half))
        if right > left:
            start = y * size
            pixels[start + max(left, 0) : start + min(right, size)] = b"\xff" * (
                min(right, size) - max(left, 0)
            )

    mask.frombytes(bytes(pixels))
    return mask


def facecam_mask(size: int, dest: Path, shape: str = "squircle") -> Path:
    """Alpha mask for the face cam bubble: 'squircle' (default) or 'circle'."""
    if dest.exists():
        return dest

    exponent = 2.0 if shape == "circle" else SQUIRCLE_EXPONENT
    big = _superellipse(size * _SS, exponent)
    mask = big.resize((size, size), Image.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    mask.save(dest)
    return dest


def shadow_overlay(
    size: int,
    dest: Path,
    shape: str = "squircle",
    opacity: float = 0.30,
    blur_px: int = 22,
    offset_px: int = 8,
) -> "tuple[Path, int]":
    """Soft drop shadow to sit behind the bubble, Screen Studio style.

    Wide blur, low opacity, a small downward offset — the shadow should read as
    depth rather than as an outline. Returns the file and the padding used, so
    the caller knows where to place it relative to the bubble.

    The bubble is opaque and drawn on top, so only the halo is ever visible.
    """
    pad = blur_px * 2 + offset_px
    canvas = size + pad * 2

    shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    silhouette = _superellipse(size, 2.0 if shape == "circle" else SQUIRCLE_EXPONENT)
    black = Image.new("RGBA", (size, size), (0, 0, 0, int(max(0.0, min(opacity, 1.0)) * 255)))
    shadow.paste(black, (pad, pad + offset_px), silhouette)

    if blur_px > 0:
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur_px))

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shadow.save(dest)
    return dest, pad
