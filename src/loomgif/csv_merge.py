"""CSV handoff into /mta-instantly-campaign.

The skill's `references/instantly-csv.md` makes the CSV the integration point:
the GIF pipeline runs first, then its hosted URLs are merged into the campaign
CSV and the file is dragged into Instantly.

Two rules from that spec drive everything here:

* Column names must start with a capital and be <= 20 characters, or Instantly
  maps them silently to nothing.
* An empty ``Gif url`` renders a broken image icon, which is worse than no
  image — so prospects without media are split into their own no-GIF file.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .instantly import MEDIA_COLUMNS, VAR_GIF

log = logging.getLogger(__name__)

MAX_COLUMN_CHARS = 20
MAX_CUSTOM_VARS = 50


class CsvSpecError(ValueError):
    pass


@dataclass
class MergeReport:
    gif_path: Optional[Path]
    nogif_path: Optional[Path]
    matched: int
    unmatched: int
    total_rows: int
    warnings: List[str]

    def summary(self) -> str:
        lines = [
            f"Rows in:        {self.total_rows}",
            f"With media:     {self.matched}  -> {self.gif_path.name if self.gif_path else '—'}",
            f"Without media:  {self.unmatched}  -> {self.nogif_path.name if self.nogif_path else '—'}",
        ]
        lines += [f"WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


def validate_columns(columns: Sequence[str]) -> List[str]:
    """Check a header row against Instantly's upload rules. Returns warnings."""
    warnings: List[str] = []
    if not columns:
        raise CsvSpecError("CSV has no header row")
    if columns[0].strip().lower() != "email":
        raise CsvSpecError(f"'Email' must be the first column, found {columns[0]!r}")

    predefined = {
        "email", "first name", "last name", "job title", "company name",
        "personalization", "phone", "website", "location", "linkedin",
    }
    custom = 0
    for name in columns:
        if not name or not name.strip():
            raise CsvSpecError("Blank column name — Instantly rejects unnamed columns")
        if not name[0].isupper():
            warnings.append(f"Column {name!r} does not start with a capital letter — mapping will fail silently")
        if len(name) > MAX_COLUMN_CHARS:
            warnings.append(f"Column {name!r} is {len(name)} chars, over the {MAX_COLUMN_CHARS}-char limit")
        if name.strip().lower() not in predefined:
            custom += 1
    if custom > MAX_CUSTOM_VARS:
        warnings.append(f"{custom} custom variables, over Instantly's limit of {MAX_CUSTOM_VARS}")
    return warnings


def write_manifest(rows: List[Dict[str, str]], dest: Path) -> Path:
    """Standalone record of what the pipeline produced, one row per prospect."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    columns = ["Email", "Website", *MEDIA_COLUMNS, "Status", "Note"]
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    log.info("Wrote manifest %s (%d rows)", dest, len(rows))
    return dest


def upsert_manifest(row: Dict[str, str], dest: Path) -> Path:
    """Add or replace one prospect's row in the manifest, keyed on email.

    Lets a single `render` feed `merge` the same way a batch run does, and lets
    a re-render of one prospect update the manifest without rewriting the rest.
    """
    rows: List[Dict[str, str]] = []
    email = (row.get("Email") or "").strip().lower()
    if dest.exists():
        with dest.open(newline="", encoding="utf-8-sig") as handle:
            rows = [
                existing
                for existing in csv.DictReader(handle)
                if (existing.get("Email") or "").strip().lower() != email
            ]
    rows.append(row)
    return write_manifest(rows, dest)


def merge(
    campaign_csv: Path,
    manifest_csv: Path,
    out_dir: Path,
    email_column: str = "Email",
    columns: Optional[Sequence[str]] = None,
) -> MergeReport:
    """Join hosted media URLs onto a campaign CSV, split GIF vs no-GIF.

    Matching is on lowercased email. Output files are written next to each other
    in `out_dir` and are ready to drag into Instantly as-is.
    """
    columns = list(columns or MEDIA_COLUMNS)
    out_dir.mkdir(parents=True, exist_ok=True)

    media: Dict[str, Dict[str, str]] = {}
    with manifest_csv.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            email = (row.get("Email") or "").strip().lower()
            if email and (row.get(VAR_GIF) or "").strip():
                media[email] = row

    with campaign_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        original = list(reader.fieldnames or [])
        rows = list(reader)

    # Drop any media column that is empty for every matched row — it would only
    # eat into Instantly's 50-custom-variable budget.
    columns = [c for c in columns if any((row.get(c) or "").strip() for row in media.values())]

    warnings = validate_columns(original)
    header = original + [c for c in columns if c not in original]
    warnings += [w for w in validate_columns(header) if w not in warnings]

    with_media: List[Dict[str, str]] = []
    without_media: List[Dict[str, str]] = []
    for row in rows:
        email = (row.get(email_column) or "").strip().lower()
        found = media.get(email)
        if found:
            enriched = dict(row)
            for column in columns:
                enriched[column] = found.get(column, "")
            with_media.append(enriched)
        else:
            without_media.append(dict(row))

    stem = campaign_csv.stem
    gif_path = nogif_path = None

    if with_media:
        gif_path = out_dir / f"{stem}-gif.csv"
        _write(gif_path, header, with_media)
    if without_media:
        # An empty `Gif url` renders a broken image icon, which is worse than no
        # image. The campaign CSV carries `Gif url` as one of its own tracking
        # columns, so it is not enough to skip adding ours — any media column
        # that is empty for every row here has to be dropped outright.
        nogif_columns = [
            column
            for column in original
            if column not in MEDIA_COLUMNS
            or any((row.get(column) or "").strip() for row in without_media)
        ]
        nogif_path = out_dir / f"{stem}-nogif.csv"
        _write(nogif_path, nogif_columns, without_media)

    return MergeReport(
        gif_path=gif_path,
        nogif_path=nogif_path,
        matched=len(with_media),
        unmatched=len(without_media),
        total_rows=len(rows),
        warnings=warnings,
    )


def _write(dest: Path, header: Sequence[str], rows: List[Dict[str, str]]) -> None:
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    log.info("Wrote %s (%d rows)", dest, len(rows))
