"""ffmpeg composition: scrolling website screenshot + circular face cam bubble.

Layout mirrors a Loom recording — full-bleed page behind, talking head pinned
bottom-left in a bubble with a white ring and a soft shadow.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .config import RenderConfig
from .overlays import circle_mask, ring_overlay

log = logging.getLogger(__name__)

SHADOW_PAD = 18

_ENCODERS: Optional[set] = None


class FFmpegError(RuntimeError):
    pass


def ensure_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise FFmpegError(f"{binary} not found on PATH. Install it: brew install ffmpeg")


def _run(cmd: List[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"Command failed ({proc.returncode}):\n{' '.join(cmd)}\n\n{tail}")


def available_encoders() -> set:
    """Names of encoders this ffmpeg build actually has.

    Homebrew's default build ships VP9; slimmer builds do not, and we would
    rather skip the WebM than abort a whole batch over it.
    """
    global _ENCODERS
    if _ENCODERS is None:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
        names = set()
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6:
                names.add(parts[1])
        _ENCODERS = names
    return _ENCODERS


def pick_encoder(*candidates: str) -> Optional[str]:
    for name in candidates:
        if name in available_encoders():
            return name
    return None


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


@dataclass
class RenderResult:
    webm: Optional[Path]
    mp4: Optional[Path]
    poster: Path
    gif: Optional[Path] = None


def build_filtergraph(cfg: RenderConfig, with_navbar: bool = False) -> str:
    """Screenshot scrolls with an ease-in-out; the site's sticky nav (if we lifted
    one) stays pinned at the top; face cam is cropped square, made circular via
    alphamerge, then the ring is laid over the seam."""
    w, h, d = cfg.width, cfg.height, cfg.facecam_diameter
    margin = cfg.facecam_margin
    duration = max(cfg.duration, 0.1)

    bubble_x = margin
    bubble_y = h - d - margin
    ring_x = bubble_x - SHADOW_PAD
    ring_y = bubble_y - SHADOW_PAD

    # smoothstep(p) = p^2 * (3 - 2p), with p clamped to [0,1]
    p = f"min(t/{duration},1)"
    travel = f"(ih-oh)*{cfg.scroll_ratio}"
    scroll_y = f"'{travel}*({p}*{p}*(3-2*{p}))'"

    graph = (
        # Background: fit page to canvas width, guarantee it is at least canvas tall,
        # then crop a moving window down the page.
        f"[0:v]scale={w}:-2:flags=lanczos,setsar=1,"
        f"pad={w}:'max(ih,{h})':0:0:color=white[bgpad];"
        f"[bgpad]crop={w}:{h}:0:{scroll_y}[bg];"
        # Face cam: cover-crop to a square, resize to bubble, force constant fps.
        f"[1:v]fps={cfg.fps},scale={d}:{d}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={d}:{d},setsar=1,format=rgba[fcraw];"
        f"[fcraw][2:v]alphamerge[fc];"
        f"[bg][fc]overlay={bubble_x}:{bubble_y}:format=auto[withcam];"
        f"[withcam][3:v]overlay={ring_x}:{ring_y}:format=auto[withring];"
    )
    if with_navbar:
        # Pinned last so it sits above everything except nothing — a real sticky
        # header covers the page, but the face cam bubble is bottom-left anyway.
        graph += (
            f"[4:v]scale={w}:-1:flags=lanczos[nav];"
            f"[withring][nav]overlay=0:0:format=auto,format=yuv420p[v]"
        )
    else:
        graph += "[withring]format=yuv420p[v]"
    return graph


def render(
    screenshot: Path,
    facecam: Path,
    out_stem: Path,
    cfg: RenderConfig,
    make_mp4: bool = True,
    keep_audio: bool = True,
    navbar: Optional[Path] = None,
) -> RenderResult:
    """Render the composite to WebM (+ optional MP4) and pull a poster frame."""
    ensure_ffmpeg()
    if not screenshot.exists():
        raise FileNotFoundError(f"Screenshot not found: {screenshot}")
    if not facecam.exists():
        raise FileNotFoundError(
            f"Face cam footage not found: {facecam}. Drop your clip in assets/facecam/ "
            "or pass --facecam /path/to/clip.mp4"
        )

    out_stem.parent.mkdir(parents=True, exist_ok=True)
    cache = out_stem.parent / ".overlays"
    mask = circle_mask(cfg.facecam_diameter, cache / f"mask-{cfg.facecam_diameter}.png")
    ring = ring_overlay(
        cfg.facecam_diameter,
        cfg.facecam_ring_px,
        cfg.facecam_ring_color,
        cache / f"ring-{cfg.facecam_diameter}-{cfg.facecam_ring_px}-{cfg.facecam_ring_color.lstrip('#')}.png",
        shadow_px=SHADOW_PAD,
    )

    with_navbar = navbar is not None and navbar.exists()
    graph = build_filtergraph(cfg, with_navbar=with_navbar)
    audio = keep_audio and has_audio(facecam)

    def inputs() -> List[str]:
        return [
            "-loop", "1", "-framerate", str(cfg.fps), "-t", f"{cfg.duration}", "-i", str(screenshot),
            # Loop the clip if it is shorter than the target duration.
            "-stream_loop", "-1", "-ss", f"{cfg.facecam_start}", "-t", f"{cfg.duration}", "-i", str(facecam),
            "-i", str(mask),
            "-i", str(ring),
        ] + (["-i", str(navbar)] if with_navbar else [])

    def encode(dest: Path, video_args: List[str], audio_args: List[str]) -> Path:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs(),
               "-filter_complex", graph, "-map", "[v]"]
        cmd += (["-map", "1:a:0", *audio_args] if audio and audio_args else ["-an"])
        cmd += [*video_args, "-t", f"{cfg.duration}", str(dest)]
        _run(cmd)
        return dest

    webm: Optional[Path] = None
    vp9 = pick_encoder("libvpx-vp9", "libvpx")
    if vp9:
        video_args = ["-c:v", vp9, "-crf", "32", "-b:v", "0",
                      "-deadline", "good", "-cpu-used", "2", "-pix_fmt", "yuv420p"]
        if vp9 == "libvpx-vp9":
            video_args += ["-row-mt", "1"]
        opus = pick_encoder("libopus", "opus")
        webm = encode(out_stem.with_suffix(".webm"), video_args,
                      ["-c:a", opus, "-b:a", "96k"] if opus else [])
    else:
        log.warning(
            "This ffmpeg has no VP8/VP9 encoder, so no .webm was written. MP4 covers "
            "every use here; reinstall with `brew install ffmpeg` for WebM."
        )

    mp4: Optional[Path] = None
    if make_mp4 or webm is None:
        h264 = pick_encoder("libx264", "h264_videotoolbox")
        if h264 is None:
            raise FFmpegError("No H.264 or VP9 encoder available — cannot render. Reinstall ffmpeg.")
        aac = pick_encoder("libfdk_aac", "aac")
        video_args = ["-c:v", h264, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        video_args += (["-preset", "medium", "-crf", "21"] if h264 == "libx264" else ["-b:v", "3M"])
        mp4 = encode(out_stem.with_suffix(".mp4"), video_args,
                     ["-c:a", aac, "-b:a", "128k"] if aac else [])

    source = mp4 or webm
    poster = out_stem.with_name(out_stem.name + "-poster").with_suffix(".jpg")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
          "-ss", "0.4", "-i", str(source), "-frames:v", "1", "-q:v", "3", str(poster)])

    return RenderResult(webm=webm, mp4=mp4, poster=poster)


# --------------------------------------------------------------------------- #
# GIF                                                                          #
# --------------------------------------------------------------------------- #

# Outlook and Gmail both get unhappy with heavy GIFs, so we walk down this ladder
# of (width multiplier, fps multiplier, palette colours) until the file fits.
#
# Width is given up last and deliberately. The email tag declares width="600",
# so a narrower GIF gets upscaled by the client and looks soft — whereas a slow
# page scroll survives 8fps and a small palette almost unnoticed.
_GIF_LADDER = [
    (1.00, 1.00, 256),
    (1.00, 0.83, 192),
    (1.00, 0.66, 128),
    (1.00, 0.50, 96),
    (1.00, 0.50, 64),
    (0.85, 0.50, 64),
    (0.70, 0.50, 64),
]


def to_gif(source: Path, dest: Path, cfg: RenderConfig, max_bytes: Optional[int] = None) -> Path:
    """Two-pass palettegen/paletteuse, shrinking until under the size budget."""
    ensure_ffmpeg()
    budget = max_bytes if max_bytes is not None else cfg.gif_max_bytes
    dest.parent.mkdir(parents=True, exist_ok=True)

    last_size = None
    for width_mult, fps_mult, colors in _GIF_LADDER:
        width = max(int(cfg.gif_width * width_mult) // 2 * 2, 240)
        fps = max(int(round(cfg.gif_fps * fps_mult)), 6)

        with tempfile.TemporaryDirectory() as tmp:
            palette = Path(tmp) / "palette.png"
            common = f"fps={fps},scale={width}:-2:flags=lanczos"
            _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                  "-vf", f"{common},palettegen=max_colors={colors}:stats_mode=diff", str(palette)])
            _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-i", str(palette),
                  "-lavfi", f"{common}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                  "-loop", "0", str(dest)])

        last_size = dest.stat().st_size
        log.info("GIF %dpx @%dfps / %d colours -> %.2f MB", width, fps, colors, last_size / 1e6)
        if last_size <= budget:
            return dest

    log.warning(
        "GIF still %.2f MB after the full ladder (budget %.2f MB). Shorten --duration "
        "or lower GIF_WIDTH.", (last_size or 0) / 1e6, budget / 1e6
    )
    return dest
