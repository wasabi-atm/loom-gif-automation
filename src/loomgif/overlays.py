"""The face cam's alpha mask.

ffmpeg's `alphamerge` takes a greyscale image as the alpha channel, so the
bubble's shape is decided entirely here. Masks are cached on disk and reused
across every prospect in a batch.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

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
