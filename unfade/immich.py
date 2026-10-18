"""The few Immich API calls unfade needs: list folders, tags and albums, read originals, upload.

Written against the API of Immich v2 and v3. Search pages are followed by `nextCursor` (v3.2+)
or `nextPage` (older); uploads still send `deviceAssetId` and `deviceId`, which v2 requires and
v3 ignores.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from datetime import datetime, timezone

import httpx

from unfade.i18n import t

PAGE_SIZE = 1000
# Connection drops, timeouts and these answers are retried with growing pauses (0.5 s doubling,
# at most 30 s; with 8 attempts about a minute in all). Every request unfade makes can be
# repeated safely: an upload of the same bytes comes back as Immich's "duplicate" of the first.
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
RETRY_ATTEMPTS = int(os.environ.get("UNFADE_RETRY_ATTEMPTS", "8"))
RETRY_BASE = float(os.environ.get("UNFADE_RETRY_BASE", "0.5"))
RETRY_CAP = 30.0


class ImmichError(Exception):
    """Immich is not configured, cannot be reached or refused a request."""


class Immich:
    def __init__(self, url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        base = url.rstrip("/")
        if not base.endswith("/api"):
            base += "/api"
        self.client = httpx.Client(base_url=base, headers={"x-api-key": api_key, "Accept": "application/json"},
                                   # read: the longest silence between received chunks, not the whole transfer;
                                   # a stalled connection is given up and retried after it.
                                   timeout=httpx.Timeout(30, read=120), transport=transport)

    def _pause(self, attempt: int, response: httpx.Response | None = None) -> None:
        wait = min(RETRY_CAP, RETRY_BASE * 2 ** attempt)
        retry_after = response.headers.get("Retry-After", "") if response is not None else ""
        if retry_after.isdigit():
            wait = min(RETRY_CAP, max(wait, float(retry_after)))
        time.sleep(wait)

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        problem = ""
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.client.request(method, path, **kwargs)
            except httpx.TransportError as error:  # refused, reset, cut off mid-transfer, timed out
                problem = f"{type(error).__name__}: {error}"
                response = None
            else:
                if response.status_code not in RETRY_STATUSES:
                    if response.status_code >= 400:
                        raise ImmichError(t("Immich refused the request {request}: {status} {text}", request=f"{method} {path}", status=response.status_code, text=response.text[:200]))
                    return response
                problem = f"{response.status_code} {response.text[:200]}"
            if attempt + 1 < RETRY_ATTEMPTS:
                self._pause(attempt, response)
        raise ImmichError(t("Immich does not respond ({request}, {attempts} attempts): {problem}", request=f"{method} {path}", attempts=RETRY_ATTEMPTS, problem=problem))

    def me(self) -> dict:
        return self._call("GET", "/users/me").json()

    def tags(self) -> list[dict]:
        return sorted(self._call("GET", "/tags").json(), key=lambda tag: tag["value"].lower())

    def albums(self) -> list[dict]:
        return sorted(self._call("GET", "/albums").json(), key=lambda album: album["albumName"].lower())

    def folders(self) -> list[str]:
        return sorted(self._call("GET", "/view/folder/unique-paths").json())

    def faces(self, asset_id: str) -> list[dict]:
        """Faces on an asset, with bounding boxes and the person (name) each belongs to."""
        return self._call("GET", "/faces", params={"id": asset_id}).json()

    def shared_assets(self) -> set[str]:
        """Assets shared by a link of their own (album links are flagged on the album)."""
        return {asset["id"] for link in self._call("GET", "/shared-links").json() for asset in link.get("assets") or []}

    def stack_assets(self, stack_id: str) -> list[dict]:
        return self._call("GET", f"/stacks/{stack_id}").json().get("assets") or []

    def folder_assets(self, path: str, recursive: bool = False) -> list[dict]:
        paths = [path]
        if recursive:
            prefix = path.rstrip("/") + "/"
            paths += [folder for folder in self.folders() if folder.startswith(prefix)]
        assets = []
        for folder in paths:
            assets += self._call("GET", "/view/folder", params={"path": folder}).json()
        return _images(assets)

    def search(self, trashed: bool = False, **filters) -> list[dict]:
        """Images matching the filters; with `trashed`, also those in the trash (withDeleted,
        deprecated in v3.2 but still served)."""
        assets, paging = [], {"page": 1}
        extra = {"withDeleted": True} if trashed else {}
        while True:
            body = self._call("POST", "/search/metadata", json={**filters, **extra, "type": "IMAGE", "size": PAGE_SIZE, **paging}).json()["assets"]
            assets += body["items"]
            if body.get("nextCursor"):
                paging = {"cursor": body["nextCursor"]}
            elif body.get("nextPage"):
                paging = {"page": int(body["nextPage"])}
            else:
                return _images(assets, trashed)

    def tag_assets(self, tag_id: str, trashed: bool = False) -> list[dict]:
        return self.search(trashed, tagIds=[tag_id])

    def album_assets(self, album_id: str, trashed: bool = False) -> list[dict]:
        return self.search(trashed, albumIds=[album_id])

    def download(self, asset_id: str, target, checksum: str = "") -> None:
        """Stream one original file to `target` (a path), never into memory.

        A transfer that breaks off, comes up short of its Content-Length or does not match
        Immich's checksum (base64 SHA-1) is fetched again from the start.
        """
        problem = ""
        for attempt in range(RETRY_ATTEMPTS):
            response = None
            try:
                with self.client.stream("GET", f"/assets/{asset_id}/original") as response:
                    if response.status_code >= 400 and response.status_code not in RETRY_STATUSES:
                        raise ImmichError(t("Could not get original {asset}: {status}", asset=asset_id, status=response.status_code))
                    if response.status_code < 400:
                        digest, size = hashlib.sha1(), 0
                        with open(target, "wb") as stream:
                            for chunk in response.iter_bytes(1024 * 1024):
                                stream.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                        expected = response.headers.get("Content-Length", "")
                        if expected.isdigit() and int(expected) != size:
                            problem = f"lataus katkesi ({size} / {expected} tavua)"
                        elif checksum and base64.b64encode(digest.digest()).decode() != checksum:
                            problem = t("the downloaded file does not match Immich's checksum")
                        else:
                            return
                    else:
                        problem = f"{response.status_code}"
            except httpx.TransportError as error:
                problem = f"{type(error).__name__}: {error}"
            if attempt + 1 < RETRY_ATTEMPTS:
                self._pause(attempt, response)
        raise ImmichError(t("Could not download original {asset} ({attempts} attempts): {problem}", asset=asset_id, attempts=RETRY_ATTEMPTS, problem=problem))

    def thumbnail(self, asset_id: str) -> bytes:
        """Immich's small thumbnail, enough for grouping by brightness and colour."""
        return self._call("GET", f"/assets/{asset_id}/thumbnail", params={"size": "thumbnail"}).content

    def download_bytes(self, asset_id: str) -> bytes:
        return self._call("GET", f"/assets/{asset_id}/original").content

    def upload(self, filename: str, content: bytes, created: str | None = None) -> dict:
        """Upload one file; returns {"id", "status"}, where status is "created" or "duplicate"."""
        when = created or datetime.now(timezone.utc).isoformat()
        data = {
            "deviceAssetId": f"unfade-{filename}-{len(content)}",
            "deviceId": "unfade",
            "fileCreatedAt": when,
            "fileModifiedAt": datetime.now(timezone.utc).isoformat(),
            "isFavorite": "false",
        }
        return self._call("POST", "/assets", data=data, files={"assetData": (filename, content)}).json()

    def wait_for_metadata(self, asset_id: str, timeout: float = 60) -> bool:
        """Wait until Immich has read a new upload's metadata; True when it has.

        Tags added before that are lost: extraction creates the asset's metadata row and then
        replaces its tags with those in the file (Immich v3.3, MetadataService.applyTagList).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # The upload itself records the file size; the image size comes from extraction.
            if (self.asset(asset_id).get("exifInfo") or {}).get("exifImageWidth"):
                return True
            time.sleep(0.25)
        return False

    def ensure_tag(self, value: str) -> dict:
        """The tag with this full value (for example "Kuvat/Korjattu"), created if missing."""
        return self._call("PUT", "/tags", json={"tags": [value]}).json()[0]

    def add_tags(self, tag_ids: list[str], asset_ids: list[str]) -> None:
        """Give assets all these tags in one update. Separate calls race inside Immich: each
        queues a sidecar write that unlocks the tags it read, and the metadata re-read after it
        can then restore a stale tag list (v3.3, MetadataService.handleSidecarWrite)."""
        self._call("PUT", "/tags/assets", json={"tagIds": tag_ids, "assetIds": asset_ids})

    def stack(self, asset_ids: list[str]) -> dict:
        """Stack the assets with the first as the primary; a stack whose primary is listed is merged in."""
        return self._call("POST", "/stacks", json={"assetIds": asset_ids}).json()

    def copy_asset(self, source_id: str, target_id: str) -> None:
        """Immich's own hand-over from one asset to another: albums, shared links, stack, favourite.
        The sidecar is left out; the details are set explicitly through update_asset."""
        self._call("PUT", "/assets/copy", json={"sourceId": source_id, "targetId": target_id, "albums": True,
                                                "sharedLinks": True, "stack": True, "favorite": True, "sidecar": False})

    def remove_from_stack(self, stack_id: str, asset_id: str) -> None:
        self._call("DELETE", f"/stacks/{stack_id}/assets/{asset_id}")

    def trash(self, asset_ids: list[str]) -> None:
        """Move assets to Immich's trash (restorable until it is emptied), not a permanent delete."""
        self._call("DELETE", "/assets", json={"ids": asset_ids, "force": False})

    def asset(self, asset_id: str) -> dict:
        return self._call("GET", f"/assets/{asset_id}").json()

    def albums_with(self, asset_id: str) -> list[dict]:
        return self._call("GET", "/albums", params={"assetId": asset_id}).json()

    def update_asset(self, asset_id: str, fields: dict) -> None:
        """Set description, rating, favourite, date or location; Immich keeps them over the file's values."""
        self._call("PUT", f"/assets/{asset_id}", json=fields)

    def metadata(self, asset_id: str, key: str) -> dict | None:
        """One key of an asset's metadata (kept in Immich's database, not in the file), or None."""
        items = self._call("GET", f"/assets/{asset_id}/metadata").json()
        return next((item["value"] for item in items if item.get("key") == key), None)

    def set_metadata(self, asset_id: str, key: str, value: dict) -> None:
        """Store a key on an asset; any key name needs Immich v2.5 or newer."""
        self._call("PUT", f"/assets/{asset_id}/metadata", json={"items": [{"key": key, "value": value}]})

    def add_to_album(self, album_id: str, asset_ids: list[str]) -> None:
        self._call("PUT", f"/albums/{album_id}/assets", json={"ids": asset_ids})


def _images(assets: list[dict], trashed: bool = False) -> list[dict]:
    seen, images = set(), []
    for asset in assets:
        if asset.get("type") == "IMAGE" and (trashed or not asset.get("isTrashed")) and asset["id"] not in seen:
            seen.add(asset["id"])
            images.append(asset)
    # By file name: the storage path of an upload is random (upload/<user>/1f/7b/...).
    return sorted(images, key=lambda asset: ((asset.get("originalFileName") or "").lower(), asset.get("originalPath") or ""))


_transport: httpx.BaseTransport | None = None


def configured() -> bool:
    return bool(os.environ.get("IMMICH_URL") and os.environ.get("IMMICH_API_KEY"))


def client() -> Immich:
    if not configured():
        raise ImmichError(t("Immich is not configured: set IMMICH_URL and IMMICH_API_KEY"))
    return Immich(os.environ["IMMICH_URL"], os.environ["IMMICH_API_KEY"], _transport)
