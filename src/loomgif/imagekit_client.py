"""ImageKit uploads.

Prefers the official `imagekitio` SDK; falls back to the documented REST upload
endpoint so the pipeline keeps working across SDK major versions.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

import requests

from .config import ImageKitConfig

log = logging.getLogger(__name__)

UPLOAD_ENDPOINT = "https://upload.imagekit.io/api/v1/files/upload"


def content_key(path: Path, length: int = 12) -> str:
    """Short, stable digest of a file's bytes, used as the CDN cache key."""
    digest = hashlib.sha1()  # noqa: S324 — cache key, not a security boundary
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:length]


@dataclass
class Upload:
    url: str
    file_id: str
    name: str
    path: str
    version: str = ""

    @property
    def versioned_url(self) -> str:
        """URL carrying a hash of the file's contents as a cache key.

        Overwriting a file keeps its URL, but ImageKit's CDN goes on serving the
        previously cached bytes, so a re-run after a creative change would send
        prospects the old GIF.

        The key has to be derived from the content, not from ImageKit's response:
        an overwrite bumps `versionInfo.name` ("Version 5") while leaving
        `versionInfo.id` unchanged, so keying on the id busts the cache exactly
        once and then goes stale again.

        A content hash also means an unchanged render keeps its existing URL
        rather than churning, and each send is pinned to the creative that
        existed when it went out.
        """
        if not self.version:
            return self.url
        joiner = "&" if "?" in self.url else "?"
        return f"{self.url}{joiner}v={self.version}"


class ImageKitUploader:
    def __init__(self, cfg: ImageKitConfig):
        self.cfg = cfg.require()
        self._sdk = self._init_sdk()

    def _init_sdk(self):
        """Build the client for whichever SDK generation is installed.

        The constructor changed between majors: 3.x/4.x take public_key and
        url_endpoint alongside the private key, while 5.x takes the private key
        alone and derives the rest. Passing the wrong set raises TypeError, so
        the signature decides rather than a try/except ladder.
        """
        try:
            import inspect  # noqa: WPS433

            from imagekitio import ImageKit  # noqa: WPS433
        except ImportError:
            log.debug("imagekitio SDK not installed — using REST upload")
            return None

        try:
            accepted = inspect.signature(ImageKit.__init__).parameters
            kwargs = {"private_key": self.cfg.private_key}
            if "public_key" in accepted:
                kwargs["public_key"] = self.cfg.public_key
            if "url_endpoint" in accepted:
                kwargs["url_endpoint"] = self.cfg.url_endpoint
            return ImageKit(**kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("ImageKit SDK init failed (%s) — using REST upload", exc)
            return None

    # ------------------------------------------------------------------ #

    def upload(
        self,
        path: Path,
        file_name: Optional[str] = None,
        folder: Optional[str] = None,
        tags: Optional[List[str]] = None,
        unique: bool = False,
        overwrite: bool = True,
    ) -> Upload:
        """Upload a local file and return its public CDN URL.

        `unique=False` + `overwrite=True` keeps URLs stable per prospect, so
        re-running the pipeline refreshes media without breaking sent emails.
        """
        if not path.exists():
            raise FileNotFoundError(path)
        file_name = file_name or path.name
        folder = folder or self.cfg.folder
        tags = tags or []

        result = self._upload_sdk(path, file_name, folder, tags, unique, overwrite)
        if result is None:
            result = self._upload_rest(path, file_name, folder, tags, unique, overwrite)
        result.version = content_key(path)
        log.info("Uploaded %s -> %s", path.name, result.versioned_url)
        return result

    def _upload_sdk(self, path, file_name, folder, tags, unique, overwrite) -> Optional[Upload]:
        if self._sdk is None:
            return None
        try:
            files_api = getattr(self._sdk, "files", None)
            if files_api is not None and hasattr(files_api, "upload"):
                # imagekitio >= 5 style
                response = files_api.upload(
                    file=path,
                    file_name=file_name,
                    folder=folder,
                    tags=tags,
                    use_unique_file_name=unique,
                    overwrite_file=overwrite,
                )
            else:
                # Legacy imagekitio 3.x/4.x style
                from imagekitio.models.UploadFileRequestOptions import UploadFileRequestOptions  # noqa: WPS433

                with open(path, "rb") as handle:
                    response = self._sdk.upload_file(
                        file=handle,
                        file_name=file_name,
                        options=UploadFileRequestOptions(
                            folder=folder,
                            tags=tags,
                            use_unique_file_name=unique,
                            overwrite_file=overwrite,
                        ),
                    )
            return self._coerce(response)
        except Exception as exc:  # noqa: BLE001
            log.warning("SDK upload failed for %s (%s) — retrying over REST", path.name, exc)
            return None

    def _upload_rest(self, path, file_name, folder, tags, unique, overwrite) -> Upload:
        mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            response = requests.post(
                UPLOAD_ENDPOINT,
                auth=(self.cfg.private_key, ""),
                files={"file": (file_name, handle, mime)},
                data={
                    "fileName": file_name,
                    "folder": folder,
                    "useUniqueFileName": "true" if unique else "false",
                    "overwriteFile": "true" if overwrite else "false",
                    "tags": ",".join(tags) if tags else "",
                },
                timeout=300,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"ImageKit upload failed [{response.status_code}]: {response.text[:400]}")
        return self._coerce(response.json())

    @staticmethod
    def _coerce(response) -> Upload:
        def pick(*names):
            for name in names:
                if isinstance(response, dict) and response.get(name) is not None:
                    return response[name]
                value = getattr(response, name, None)
                if value is not None:
                    return value
            return None

        url = pick("url")
        if not url:
            raise RuntimeError(f"ImageKit response contained no URL: {response!r}")

        return Upload(
            url=url,
            file_id=pick("file_id", "fileId") or "",
            name=pick("name") or "",
            path=pick("file_path", "filePath") or "",
        )

    # ------------------------------------------------------------------ #

    def transform(self, url: str, tr: str) -> str:
        """Append an ImageKit transformation, e.g. tr='w-600,f-gif'.

        Handy for serving a smaller GIF or a static first frame without
        re-encoding locally: transform(gif_url, 'w-400').
        """
        if not tr:
            return url
        joiner = "&" if "?" in url else "?"
        return f"{url}{joiner}tr={quote(tr, safe='-,:_')}"
