"""Auto-exposure for the face cam.

Webcam clips vary a lot between takes — Sam's two reference clips measure 135
and 90 mean luma, a 45-point gap — so a fixed brightness bump would blow out one
and leave the other dark. Instead each clip is measured once and given the gamma
that lifts it to a target, which also means future takes need no tuning.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

# Measuring means decoding, so remember what we learned. A batch of 200
# prospects sharing two takes should measure exactly twice.
_CACHE: Dict[Tuple[str, float], float] = {}

# Never darken. A clip that is already bright is left alone; the point is to
# rescue dark ones, not to flatten everything to the same look.
GAMMA_MIN = 1.0
GAMMA_MAX = 1.75


def mean_luma(path: Path, sample_seconds: float = 2.5, sample_fps: int = 4) -> Optional[float]:
    """Average luma (0-255) of the square centre crop — the region actually used.

    Samples a handful of frames rather than decoding the whole clip.
    """
    key = (str(path), path.stat().st_mtime if path.exists() else 0.0)
    if key in _CACHE:
        return _CACHE[key]

    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-t", str(sample_seconds), "-i", str(path),
        "-vf", (
            "crop='min(iw,ih)':'min(iw,ih)',"
            f"fps={sample_fps},signalstats,"
            "metadata=print:key=lavfi.signalstats.YAVG:file=-"
        ),
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not measure %s: %s", path.name, exc)
        return None

    values = [float(m) for m in re.findall(r"YAVG=([0-9.]+)", proc.stdout + proc.stderr)]
    if not values:
        log.warning("No luma readings from %s — skipping auto-exposure", path.name)
        return None

    average = sum(values) / len(values)
    _CACHE[key] = average
    log.info("%s mean luma %.1f/255 (%d samples)", path.name, average, len(values))
    return average


def gamma_for(path: Path, target_luma: float) -> float:
    """Gamma that maps the clip's measured luma onto `target_luma`.

    ffmpeg's eq filter applies out = 255 * (in/255) ** (1/gamma), so solving for
    gamma gives log(measured/255) / log(target/255).
    """
    measured = mean_luma(path)
    if not measured or measured <= 1:
        return 1.0
    ratio_in = measured / 255.0
    ratio_out = max(min(target_luma, 250.0), 5.0) / 255.0
    gamma = math.log(ratio_in) / math.log(ratio_out)
    clamped = max(GAMMA_MIN, min(GAMMA_MAX, gamma))
    if clamped != GAMMA_MIN:
        log.info("Auto-exposure for %s: gamma %.2f (target luma %.0f)", path.name, clamped, target_luma)
    return clamped


def eq_filter(
    path: Path,
    auto: bool,
    target_luma: float,
    gamma: Optional[float],
    brightness: float,
    contrast: float,
    saturation: float,
) -> str:
    """Build the ffmpeg `eq` filter for the face cam, or '' if it is a no-op."""
    resolved = gamma if gamma is not None else (gamma_for(path, target_luma) if auto else 1.0)
    parts = []
    if abs(resolved - 1.0) > 0.01:
        parts.append(f"gamma={resolved:.3f}")
    if abs(brightness) > 0.001:
        parts.append(f"brightness={brightness:.3f}")
    if abs(contrast - 1.0) > 0.01:
        parts.append(f"contrast={contrast:.3f}")
    if abs(saturation - 1.0) > 0.01:
        parts.append(f"saturation={saturation:.3f}")
    return "eq=" + ":".join(parts) if parts else ""


def duration_of(path: Path) -> Optional[float]:
    """Clip duration in seconds, or None if ffprobe cannot say."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-print_format", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        value = json.loads(proc.stdout)["format"]["duration"]
        return float(value)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read duration of %s: %s", path.name, exc)
        return None
