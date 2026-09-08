"""Face cam selection.

`FACECAM_PATH` may point at a single clip or at a directory of takes. With a
directory, each prospect gets one deterministically — the same prospect always
draws the same clip, so re-running a batch does not reshuffle media that has
already been emailed, while different prospects get different takes.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}


def takes(path: Path) -> List[Path]:
    """Every usable clip at `path`, whether it is a file or a directory."""
    path = Path(path).expanduser()
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES and p.is_file())
    return [path] if path.is_file() else []


def pick(path: Path, key: str) -> Optional[Path]:
    """Choose a clip for `key` (normally the prospect slug).

    Uses md5 rather than hash(), whose seed changes between processes and would
    hand the same prospect a different take on every run.
    """
    options = takes(path)
    if not options:
        return None
    if len(options) == 1:
        return options[0]
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    chosen = options[int(digest, 16) % len(options)]
    log.debug("Face cam for %s: %s (of %d takes)", key, chosen.name, len(options))
    return chosen
