"""Where images come from and where corrected copies go: a local folder or Immich.

A source is a local folder (optionally with its subfolders), or an Immich folder, tag or album.
A destination is a local folder, or Immich, where the uploads get a tag and/or join an album.
Immich chooses the storage path of an upload itself, so an "Immich folder" destination is a local
folder that an Immich external library watches.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from unfade import immich
from unfade.i18n import t

register_heif_opener(thumbnails=False)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp"}
SOURCE_KINDS = ("local", "immich-folder", "immich-tag", "immich-album")
DESTINATION_KINDS = ("local", "immich")
FORMATS = ("jxl", "jpeg")
FORMAT_LABELS = {"jxl": "lossless 16-bit JPEG XL", "jpeg": "JPEG (small, lossy)"}
_downloads: dict[Path, threading.Lock] = {}
_downloads_lock = threading.Lock()


def data_directory() -> Path:
    return Path(os.environ.get("UNFADE_DATA_DIR", "./data/unfade")).resolve()


def allowed_roots() -> list[Path]:
    """Local folders the tool may read and write; the web UI has no login, so it stays inside these."""
    configured = os.environ.get("UNFADE_ROOTS", "")
    roots = [Path(item).expanduser().resolve() for item in configured.split(os.pathsep) if item.strip()]
    return roots or [Path.home().resolve()]


def checked_folder(value: str, create: bool = False) -> Path:
    if not value or not value.strip():
        raise ValueError(t("Choose a folder"))
    path = Path(value.strip()).expanduser().resolve()
    if not any(path == root or root in path.parents for root in allowed_roots()):
        raise ValueError(t("The folder is outside the allowed roots: {roots}", roots=', '.join(map(str, allowed_roots()))))
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(t("Folder not found: {path}", path=path))
    return path


def browse(value: str) -> dict:
    """One level of the local folder tree for the folder picker."""
    roots = allowed_roots()
    path = checked_folder(value) if value else roots[0]
    dirs = sorted((child.name for child in path.iterdir() if child.is_dir() and not child.name.startswith(".")), key=str.lower)
    images = sum(1 for child in path.iterdir() if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES)
    inside = any(root in path.parents for root in roots)
    return {"path": str(path), "parent": str(path.parent) if inside else None, "dirs": dirs, "images": images,
            "roots": [str(root) for root in roots]}


def source_label(source: dict) -> str:
    kind = source["kind"]
    if kind == "local":
        return t("Folder {path} with subfolders" if source.get("recursive") else "Folder {path}", path=source["path"])
    if kind == "immich-folder":
        return t("Immich folder {path} with subfolders" if source.get("recursive") else "Immich folder {path}", path=source["path"])
    if kind == "immich-tag":
        return t("Immich tag {name}", name=source["name"])
    return t("Immich album {name}", name=source["name"])


def destination_label(destination: dict) -> str:
    form = t(FORMAT_LABELS[destination.get("format", "jxl")])
    if destination["kind"] == "local":
        return t("Folder {path}", path=destination["path"]) + f" · {form}"
    parts = [t("tag {name}", name=destination["tag"]) if destination.get("tag") else "",
             t("album {name}", name=destination["album_name"]) if destination.get("album_id") else "",
             t("replaces the original") if destination.get("replace_original") else "",
             t("stacked with the original") if destination.get("stack") else "",
             t("the original's albums, tags and details") if destination.get("copy_details") else "",
             t("earlier copy to the trash") if destination.get("replace_previous") and not destination.get("replace_original") else ""]
    return "Immich, " + ", ".join(filter(None, parts)) + f" · {form}"


def validate_source(source: dict) -> dict:
    kind = source.get("kind")
    if kind not in SOURCE_KINDS:
        raise ValueError(t("Choose a source"))
    recursive = bool(source.get("recursive"))
    if kind == "local":
        return {"kind": kind, "path": str(checked_folder(source.get("path", ""))), "recursive": recursive}
    client = immich.client()
    if kind == "immich-folder":
        path = (source.get("path") or "").strip()
        if path not in client.folders():
            raise ValueError(t("Immich folder not found"))
        return {"kind": kind, "path": path, "recursive": recursive}
    if kind == "immich-tag":
        tag = next((tag for tag in client.tags() if tag["id"] == source.get("id") or tag["value"] == source.get("name")), None)
        if not tag:
            raise ValueError(t("Immich tag not found"))
        return {"kind": kind, "id": tag["id"], "name": tag["value"]}
    albums = [album for album in client.albums() if album["id"] == source.get("id") or album["albumName"] == source.get("name")]
    if len(albums) != 1:
        raise ValueError(t("Immich album not found") if not albums else t("Several albums have that name"))
    return {"kind": kind, "id": albums[0]["id"], "name": albums[0]["albumName"]}


def validate_destination(destination: dict, source: dict) -> dict:
    kind = destination.get("kind")
    if kind not in DESTINATION_KINDS:
        raise ValueError(t("Choose a destination"))
    output_format = destination.get("format") or "jxl"
    if output_format not in FORMATS:
        raise ValueError(t("Unknown file format"))
    if kind == "local":
        path = checked_folder(destination.get("path", ""), create=True)
        suffix = (destination.get("suffix") or "").strip()
        if any(char in suffix for char in "/\\") or len(suffix) > 40:
            raise ValueError(t("Invalid file name suffix"))
        if source["kind"] == "local" and not suffix:
            origin = Path(source["path"])
            if path == origin or (source.get("recursive") and origin in path.parents):
                raise ValueError(t("The destination is in the source folder: give a file name suffix so the originals are not replaced"))
        return {"kind": kind, "path": str(path), "suffix": suffix, "keep_folders": bool(destination.get("keep_folders")), "format": output_format}
    client = immich.client()
    tag = (destination.get("tag") or "").strip()
    album_id = (destination.get("album_id") or "").strip()
    album_name = ""
    if album_id:
        album = next((album for album in client.albums() if album["id"] == album_id), None)
        if not album:
            raise ValueError(t("Immich album not found"))
        album_name = album["albumName"]
    # Only an Immich original can share a stack, albums, tags and Immich-side details with its copy,
    # or be replaced by it.
    from_immich = source["kind"].startswith("immich-")
    replace_original = bool(destination.get("replace_original")) and from_immich
    stack = bool(destination.get("stack")) and from_immich and not replace_original
    copy_details = (bool(destination.get("copy_details")) or replace_original) and from_immich
    if not tag and not album_id and not copy_details:
        raise ValueError(t("Give the Immich destination a tag or an album, so the corrected images can be found"))
    return {"kind": kind, "tag": tag, "album_id": album_id, "album_name": album_name, "format": output_format,
            "stack": stack, "copy_details": copy_details, "replace_original": replace_original,
            "replace_previous": bool(destination.get("replace_previous")) or replace_original}


def list_items(source: dict, trashed: bool = False) -> list[dict]:
    """The images of a source, in a stable order. `key` names the image inside the source.

    Immich items say whether they are unfade copies (`copy_of`: the original's asset id) and,
    with `trashed` (tag and album sources), include images in Immich's trash."""
    if source["kind"] == "local":
        root = Path(source["path"])
        files = root.rglob("*") if source.get("recursive") else root.iterdir()
        paths = sorted((path for path in files if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and not path.name.startswith(".")),
                       key=lambda path: str(path.relative_to(root)).lower())
        return [{"key": str(path.relative_to(root)), "name": str(path.relative_to(root))} for path in paths]
    client = immich.client()
    if source["kind"] == "immich-folder":
        assets = client.folder_assets(source["path"], source.get("recursive", False))
    elif source["kind"] == "immich-tag":
        assets = client.tag_assets(source["id"], trashed)
    else:
        assets = client.album_assets(source["id"], trashed)
    items = []
    for asset in assets:
        copy_of = _copy_of(client, asset["id"])
        items.append({"key": asset["id"], "name": asset["originalFileName"], "folder": str(Path(asset.get("originalPath") or "").parent),
                      "checksum": asset.get("checksum", ""), "created": asset.get("fileCreatedAt") or asset.get("localDateTime"),
                      "unfade_copy": bool(copy_of), "copy_of": copy_of, "trashed": bool(asset.get("isTrashed"))})
    return items


def _copy_of(client: immich.Immich, asset_id: str) -> str | None:
    """The original's asset id when this asset is a unfade copy. A copy carries the original's
    tags and albums, so it turns up in the same sources; its Immich metadata (Immich v2.5+)
    tells it apart so it is not corrected a second time."""
    try:
        source = (client.metadata(asset_id, "unfade") or {}).get("source") or {}
    except immich.ImmichError:
        return None
    return source.get("immich_asset") or ("unknown" if source else None)


def local_file(source: dict, item: dict) -> Path:
    """A local path to the item's original: the file itself, or a cached download from Immich."""
    if source["kind"] == "local":
        root = Path(source["path"])
        path = (root / item["key"]).resolve()
        if root not in path.parents or not path.is_file():
            raise LookupError(t("Source image not found: {name}", name=item['name']))
        return path
    digest = hashlib.sha256(f"{item['key']}:{item.get('checksum', '')}".encode()).hexdigest()[:16]
    cache = data_directory() / "immich-originals"
    target = cache / f"{item['key']}-{digest}{Path(item['name']).suffix.lower()}"
    # A card asks for its corrected and original previews at once; download each file only once.
    with _downloads_lock:
        lock = _downloads.setdefault(target, threading.Lock())
    with lock:
        if not target.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            immich.client().download(item["key"], partial, item.get("checksum", ""))
            partial.replace(target)
    return target


def grouping_image(source: dict, item: dict) -> Image.Image:
    """A small image for proposing groups; from Immich its thumbnail, so nothing large is downloaded."""
    if source["kind"] == "local":
        image = Image.open(local_file(source, item))
        image.draft("RGB", (240, 240))
        return image
    return Image.open(io.BytesIO(immich.client().thumbnail(item["key"])))


def _local_thumbnail(source: Path, cache_directory: Path, max_size: int = 400) -> Path:
    """A cached JPEG thumbnail; the source image is never modified."""
    stat = source.stat()
    cache_key = hashlib.sha256(f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{max_size}".encode()).hexdigest()
    target = cache_directory / f"{cache_key}.jpg"
    if target.is_file():
        return target
    cache_directory.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            image.save(target, "JPEG", quality=82, optimize=True)
    except (OSError, ValueError, SyntaxError) as error:
        raise ValueError(t("Could not create a preview of the image")) from error
    return target


def thumbnail(source: dict, item: dict) -> Path:
    """A cached small JPEG or Immich thumbnail for the group board."""
    cache = data_directory() / "thumbnails"
    if source["kind"] == "local":
        return _local_thumbnail(local_file(source, item), cache)
    target = cache / f"immich-{item['key']}-{hashlib.sha256(item.get('checksum', '').encode()).hexdigest()[:12]}"
    if not target.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + f".{threading.get_ident()}.part")
        partial.write_bytes(immich.client().thumbnail(item["key"]))
        partial.replace(target)
    return target


def output_stem(item: dict, destination: dict, taken: set[str]) -> str:
    """The corrected copy's relative file name without suffix (the format decides it at export).
    A clash gets the start of the item key's hash appended."""
    name = Path(item["name"] if destination.get("keep_folders") else Path(item["name"]).name)
    candidate = f"{name.with_suffix('')}{destination.get('suffix', '')}"
    if candidate.lower() in taken:
        candidate = f"{candidate}-{hashlib.sha256(item['key'].encode()).hexdigest()[:8]}"
    taken.add(candidate.lower())
    return candidate
