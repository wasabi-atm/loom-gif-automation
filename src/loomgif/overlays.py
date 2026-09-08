"""Pillow-generated overlays: the circular alpha mask and the ring/shadow that
sits on the face-cam bubble. Both are cached on disk and reused across prospects."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Draw at 4x then downsample — cheap, reliable anti-aliasing on the circle edge.
_SS = 4


def _hex_to_rgb(value: str):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def circle_mask(diameter: int, dest: Path) -> Path:
    """White disc on black — used as the alpha channel via ffmpeg's alphamerge."""
    if dest.exists():
        return dest
    big = diameter * _SS
    img = Image.new("L", (big, big), 0)
    ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=255)
    img = img.resize((diameter, diameter), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def ring_overlay(diameter: int, ring_px: int, color: str, dest: Path, shadow_px: int = 18) -> Path:
    """Transparent PNG: soft drop shadow + a solid ring hugging the bubble edge.

    The canvas is padded by `shadow_px` on every side, so when compositing you
    place it at (bubble_x - shadow_px, bubble_y - shadow_px).
    """
    if dest.exists():
        return dest

    pad = shadow_px
    size = diameter + pad * 2
    big_size = size * _SS
    big_d = diameter * _SS
    big_pad = pad * _SS

    # Shadow: a filled disc, blurred, at low opacity, nudged down a touch.
    shadow = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        (big_pad, big_pad + big_pad // 3, big_pad + big_d, big_pad + big_d + big_pad // 3),
        fill=(0, 0, 0, 110),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=big_pad * 0.55))

    # Ring: an outline stroked on the bubble boundary.
    ring = Image.new("RGBA", (big_size, big_size), (0, 0, 0, 0))
    if ring_px > 0:
        stroke = ring_px * _SS
        ImageDraw.Draw(ring).ellipse(
            (
                big_pad + stroke // 2,
                big_pad + stroke // 2,
                big_pad + big_d - stroke // 2,
                big_pad + big_d - stroke // 2,
            ),
            outline=_hex_to_rgb(color) + (255,),
            width=stroke,
        )

    out = Image.alpha_composite(shadow, ring).resize((size, size), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest)
    return dest
