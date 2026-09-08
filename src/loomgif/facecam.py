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
    """Every usable clip at `path`, whether it is a file or a directory.

    Directories are searched recursively, so takes can be filed into subfolders
    by shoot date without dropping out of the rotation. Anything that is not a
    video — a README, a .gitkeep, a stray export note — is ignored, so new clips
    join simply by being added to the folder.
    """
    path = Path(path).expanduser()
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES)
    return [path] if path.is_file() else []


def pick(path: Path, key: str) -> Optional[Path]:
    """Choose a clip for `key` (normally the prospect slug).

    Uses rendezvous hashing — score every clip against the key, take the highest
    — rather than `index = hash(key) % len(clips)`. Modulo reshuffles the whole
    list when a clip is added: with two takes going to three, most prospects
    would be reassigned. Rendezvous moves only the share that genuinely belongs
    to the new clip and leaves everyone else where they were, so adding a take
    before a follow-up send does not silently change what a prospect sees.

    md5 rather than hash(), whose seed changes between processes and would hand
    the same prospect a different take on every run.
    """
    options = takes(path)
    if not options:
        return None
    if len(options) == 1:
        return options[0]

    def score(clip: Path) -> str:
        return hashlib.md5(f"{key}\x00{clip.name}".encode("utf-8")).hexdigest()

    chosen = max(options, key=score)
    log.debug("Face cam for %s: %s (of %d takes)", key, chosen.name, len(options))
    return chosen
