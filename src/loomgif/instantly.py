"""Instantly.ai V2 connector.

The pipeline's job here is narrow: after media is hosted on ImageKit, write the
URLs onto each lead as **custom variables** so the campaign body can reference
them as {{gif_url}}, {{video_url}}, {{poster_url}} and {{loom_link}}.

Instantly registers custom-variable keys on the campaign automatically the first
time a lead carries them, so no manual column setup is needed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from .config import InstantlyConfig

log = logging.getLogger(__name__)

# Column / variable names, dictated by references/instantly-csv.md in the
# mta-instantly-campaign skill: every name starts with a capital letter and is
# 20 characters or fewer, or Instantly's mapping fails silently at upload.
# `Gif url` is the canonical one — the sequence body uses {{Gif url}}.
VAR_GIF = "Gif url"
VAR_GIF_SMALL = "Gif small"
VAR_VIDEO = "Video url"
VAR_VIDEO_WEBM = "Webm url"
VAR_POSTER = "Poster url"
VAR_LINK = "Loom link"

#: Order used when these are appended to a campaign CSV.
MEDIA_COLUMNS = [
    VAR_GIF,
    VAR_GIF_SMALL,
    VAR_VIDEO,
    VAR_VIDEO_WEBM,
    VAR_POSTER,
    VAR_LINK,
]


class InstantlyError(RuntimeError):
    pass


@dataclass
class Lead:
    id: str
    email: str
    campaign: Optional[str] = None


class InstantlyClient:
    def __init__(self, cfg: InstantlyConfig, timeout: int = 30):
        self.cfg = cfg.require()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.cfg.api_key}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.cfg.api_base}/{path.lstrip('/')}"
        for attempt in range(4):
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                backoff = 2 ** attempt
                log.warning("Instantly %s %s -> %s, retrying in %ss", method, path, response.status_code, backoff)
                time.sleep(backoff)
                continue
            if response.status_code >= 400:
                raise InstantlyError(f"{method} {path} failed [{response.status_code}]: {response.text[:400]}")
            return response.json() if response.content else {}
        raise InstantlyError(f"{method} {path} kept failing after retries")

    # ------------------------------------------------------------------ #

    def find_lead(self, email: str, campaign: Optional[str] = None) -> Optional[Lead]:
        """Look a lead up by email address, optionally scoped to one campaign."""
        body: Dict[str, object] = {"contacts": [email], "limit": 1}
        if campaign:
            body["campaign"] = campaign
        payload = self._request("POST", "/leads/list", json=body)
        items = payload.get("items") or payload.get("data") or []
        if not items:
            return None
        item = items[0]
        return Lead(id=item.get("id", ""), email=item.get("email", email), campaign=item.get("campaign"))

    def list_leads(self, campaign: str, limit: int = 100) -> List[Lead]:
        """Page through every lead on a campaign."""
        leads: List[Lead] = []
        cursor: Optional[str] = None
        while True:
            body: Dict[str, object] = {"campaign": campaign, "limit": limit}
            if cursor:
                body["starting_after"] = cursor
            payload = self._request("POST", "/leads/list", json=body)
            items = payload.get("items") or payload.get("data") or []
            for item in items:
                leads.append(Lead(id=item.get("id", ""), email=item.get("email", ""), campaign=campaign))
            cursor = payload.get("next_starting_after") or payload.get("starting_after")
            if not cursor or not items:
                return leads

    def set_variables(self, lead_id: str, variables: Dict[str, str]) -> dict:
        """Merge custom variables onto a lead (PATCH is a merge, not a replace)."""
        clean = {key: value for key, value in variables.items() if value}
        if not clean:
            return {}
        return self._request("PATCH", f"/leads/{lead_id}", json={"custom_variables": clean})

    def push_media(
        self,
        email: str,
        gif_url: str,
        video_url: str = "",
        webm_url: str = "",
        poster_url: str = "",
        link_url: str = "",
        campaign: Optional[str] = None,
        gif_small_url: str = "",
    ) -> Optional[str]:
        """Find the lead by email and attach the hosted media URLs.

        Returns the lead id, or None when the lead is not in the workspace yet.
        """
        lead = self.find_lead(email, campaign=campaign)
        if lead is None:
            log.warning("No Instantly lead found for %s — skipping push", email)
            return None
        self.set_variables(
            lead.id,
            {
                VAR_GIF: gif_url,
                VAR_GIF_SMALL: gif_small_url,
                VAR_VIDEO: video_url,
                VAR_VIDEO_WEBM: webm_url,
                VAR_POSTER: poster_url,
                VAR_LINK: link_url,
            },
        )
        log.info("Pushed media vars to Instantly lead %s (%s)", lead.id, email)
        return lead.id
