"""An archive of originals: the files byte for byte, their metadata as files, and how every
corrected copy was made.

    archive/
      originals/<name>            the original, as Immich (or the source folder) stores it
      originals/<name>.xmp        XMP sidecar: description, tags, rating, date, place, people,
                                  albums and the copies with their recipes
      originals/<name>.json       the complete record, including the raw Immich asset
      code/unfade-<code id>/    the correction code that made a copy, file for file
      code/recompute.py           original + recipe -> the copy's pixels
      index.html, index/          offline overview by tag, album and date
      manifest.json, SHA256SUMS, README.md

Names are fixed when an original is first archived: an Immich source is flat (the original file
name), a local source keeps its subfolders, and a name taken by different content gets the start
of the file's SHA-256 appended. Later runs only add originals and refresh the derived files
(sidecars, records, index); an archived original that no longer matches its digest stops the run.
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

from unfade import encode, fingerprint, immich, jobs, locations, render, version

FORMAT = "unfade-archive/1"
NAMESPACE = "https://github.com/ssalonen/unfade/ns/archive/1.0/"
THUMB_SIZE = 240


class ArchiveError(Exception):
    """The archive and its manifest disagree; nothing is overwritten."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(content)
    os.replace(partial, path)


def _json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


class Archive:
    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "manifest.json"
        if self.manifest_path.is_file():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest.get("format") != FORMAT:
                raise ArchiveError(f"{root} ei ole unfade-arkisto ({self.manifest.get('format')})")
        elif root.exists() and any(root.iterdir()):
            raise ArchiveError(f"{root} ei ole tyhjä eikä unfade-arkisto")
        else:
            self.manifest = {"format": FORMAT, "created": _now(), "originals": []}
        self.by_sha = {entry["sha256"]: entry for entry in self.manifest["originals"]}

    def name_for(self, wanted: str, sha256: str) -> str:
        """A fixed archive name: the wanted one, or with the digest's start when taken by other content."""
        if sha256 in self.by_sha:
            return self.by_sha[sha256]["name"]
        taken = {entry["name"].lower() for entry in self.manifest["originals"]}
        if wanted.lower() not in taken:
            return wanted
        path = Path(wanted)
        return str(path.with_name(f"{path.stem}-{sha256[:8]}{path.suffix}"))

    def add_original(self, source_file: Path, wanted: str, sha256: str) -> tuple[dict, bool]:
        """Copy an original in (once) and return its manifest entry and whether it was new."""
        entry = self.by_sha.get(sha256)
        if entry:
            stored = self.root / "originals" / entry["name"]
            if not stored.is_file() or _sha256(stored) != sha256:
                raise ArchiveError(f"Arkiston tiedosto {stored} puuttuu tai on muuttunut; arkistoa ei päivitetty")
            return entry, False
        name = self.name_for(wanted, sha256)
        target = self.root / "originals" / name
        if target.exists():
            # Left by a run that stopped before saving the manifest: keep it if it is this original.
            if _sha256(target) != sha256:
                raise ArchiveError(f"{target} on olemassa mutta puuttuu luettelosta eikä vastaa alkuperäistä; arkistoa ei päivitetty")
            entry = {"name": name, "sha256": sha256, "archived": _now()}
            self.manifest["originals"].append(entry)
            self.by_sha[sha256] = entry
            return entry, True
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(source_file, partial)
        if _sha256(partial) != sha256:
            partial.unlink()
            raise ArchiveError(f"{name}: kopio arkistoon ei vastaa lähdettä")
        os.replace(partial, target)
        entry = {"name": name, "sha256": sha256, "archived": _now()}
        self.manifest["originals"].append(entry)
        self.by_sha[sha256] = entry
        return entry, True

    def save_manifest(self) -> None:
        self.manifest["updated"] = _now()
        self.manifest["originals"].sort(key=lambda entry: entry["name"].lower())
        _write(self.manifest_path, _json(self.manifest))


def _local_job_copies() -> dict[str, list[dict]]:
    """Copies this machine's unfade jobs exported, by original (Immich asset id or local path)."""
    found: dict[str, list[dict]] = {}
    for job in jobs.list_jobs():
        for item in job["items"]:
            record = item.get("exported")
            if not record or not record.get("recipe"):
                continue
            key = item["key"] if job["source"]["kind"] != "local" else str((Path(job["source"]["path"]) / item["key"]).resolve())
            recipe = record["recipe"]
            found.setdefault(key, []).append({
                "uuid": recipe["uuid"], "immich_asset": record["target"] if job["destination"]["kind"] == "immich" else None,
                "file": None if job["destination"]["kind"] == "immich" else record["target"], "name": record.get("name"),
                "sha256": record.get("sha256"), "created": record.get("at"), "method": recipe["method"],
                "recipe": {key: recipe.get(key) for key in ("method", "curve", "adjustments", "code")}})
    return found


def _immich_copies(client: immich.Immich, original_id: str, asset: dict, candidates: set[str]) -> list[dict]:
    """Copies in Immich: the original's stack members and other known assets whose unfade
    metadata names this original. The recipe comes from that metadata or, for copies made before
    it was stored there, from the copy's own XMP."""
    ids = set(candidates)
    if asset.get("stack"):
        ids |= {member["id"] for member in client.stack_assets(asset["stack"]["id"])}
    ids.discard(original_id)
    copies = []
    for copy_id in sorted(ids):
        try:
            meta = client.metadata(copy_id, jobs.METADATA_KEY) or {}
        except immich.ImmichError:
            continue
        if (meta.get("source") or {}).get("immich_asset") != original_id:
            continue
        copy = client.asset(copy_id)
        recipe, sha256 = meta.get("recipe"), meta.get("sha256")
        if not recipe or not sha256:
            content = client.download_bytes(copy_id)
            sha256 = hashlib.sha256(content).hexdigest()
            found = encode.read_recipe(_embedded_xmp(content) or b"")
            recipe = {"method": found.get("Method"), "curve": found.get("Curve"), "adjustments": found.get("Adjustments"),
                      "code": {"id": found.get("CodeVersion", ""), "commit": found.get("CodeCommit", "")}}
        copies.append({"uuid": meta.get("uuid"), "immich_asset": copy_id, "file": None, "name": copy.get("originalFileName"),
                       "sha256": sha256, "created": copy.get("fileCreatedAt"), "method": recipe.get("method"),
                       "trashed": bool(copy.get("isTrashed")), "recipe": recipe})
    return copies


def _embedded_xmp(content: bytes) -> bytes | None:
    """The XMP of a JPEG XL container, PNG or JPEG copy."""
    if content[4:8] == b"JXL " or content[:4] == b"\x00\x00\x00\x0c":
        position = 0
        while position + 8 <= len(content):
            size = int.from_bytes(content[position:position + 4], "big")
            if content[position + 4:position + 8] == b"xml ":
                return content[position + 8:position + size]
            if size < 8:
                break
            position += size
        return None
    with Image.open(io.BytesIO(content)) as image:
        found = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
    return found.encode() if isinstance(found, str) else found


def _gps(value: float, positive: str, negative: str) -> str:
    degrees = abs(value)
    return f"{int(degrees)},{(degrees - int(degrees)) * 60:.6f}{positive if value >= 0 else negative}"


def sidecar(record: dict) -> bytes:
    """An XMP sidecar readable by digiKam, darktable, Lightroom, exiftool and Immich (which reads
    TagsList, description, rating, date, place and, if face import is on, MWG face regions)."""
    meta = record.get("immich") or {}
    exif = (meta.get("asset") or {}).get("exifInfo") or {}
    parts = []
    if meta.get("uuid"):
        parts.append(f"<xmpMM:DocumentID>uuid:{meta['uuid']}</xmpMM:DocumentID>")
    if exif.get("description"):
        parts.append(f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{escape(exif["description"])}</rdf:li></rdf:Alt></dc:description>')
    tags = [tag["value"] for tag in (meta.get("asset") or {}).get("tags") or []]
    if tags:
        items = "".join(f"<rdf:li>{escape(tag)}</rdf:li>" for tag in tags)
        parts.append(f"<dc:subject><rdf:Bag>{items}</rdf:Bag></dc:subject><digiKam:TagsList><rdf:Seq>{items}</rdf:Seq></digiKam:TagsList>")
    if exif.get("rating") is not None:
        parts.append(f"<xmp:Rating>{int(exif['rating'])}</xmp:Rating>")
    if exif.get("dateTimeOriginal"):
        when = jobs._local_time(exif["dateTimeOriginal"], exif.get("timeZone"))
        parts.append(f"<exif:DateTimeOriginal>{when}</exif:DateTimeOriginal><photoshop:DateCreated>{when}</photoshop:DateCreated>")
    if exif.get("latitude") is not None and exif.get("longitude") is not None:
        parts.append(f"<exif:GPSLatitude>{_gps(exif['latitude'], 'N', 'S')}</exif:GPSLatitude>"
                     f"<exif:GPSLongitude>{_gps(exif['longitude'], 'E', 'W')}</exif:GPSLongitude>")
    faces = [face for face in meta.get("faces") or [] if face.get("person") and face["person"].get("name")]
    if faces:
        width, height = faces[0]["imageWidth"], faces[0]["imageHeight"]
        regions = "".join(
            '<rdf:li rdf:parseType="Resource"><mwg-rs:Type>Face</mwg-rs:Type>'
            f"<mwg-rs:Name>{escape(face['person']['name'])}</mwg-rs:Name>"
            '<mwg-rs:Area rdf:parseType="Resource"><stArea:unit>normalized</stArea:unit>'
            f"<stArea:x>{(face['boundingBoxX1'] + face['boundingBoxX2']) / 2 / face['imageWidth']:.6f}</stArea:x>"
            f"<stArea:y>{(face['boundingBoxY1'] + face['boundingBoxY2']) / 2 / face['imageHeight']:.6f}</stArea:y>"
            f"<stArea:w>{(face['boundingBoxX2'] - face['boundingBoxX1']) / face['imageWidth']:.6f}</stArea:w>"
            f"<stArea:h>{(face['boundingBoxY2'] - face['boundingBoxY1']) / face['imageHeight']:.6f}</stArea:h>"
            "</mwg-rs:Area></rdf:li>" for face in faces)
        parts.append('<mwg-rs:Regions rdf:parseType="Resource"><mwg-rs:AppliedToDimensions rdf:parseType="Resource">'
                     f"<stDim:w>{width}</stDim:w><stDim:h>{height}</stDim:h><stDim:unit>pixel</stDim:unit></mwg-rs:AppliedToDimensions>"
                     f"<mwg-rs:RegionList><rdf:Bag>{regions}</rdf:Bag></mwg-rs:RegionList></mwg-rs:Regions>")
    albums = [album["name"] for album in meta.get("albums") or []]
    if albums:
        parts.append("<ca:Albums><rdf:Bag>" + "".join(f"<rdf:li>{escape(name)}</rdf:li>" for name in albums) + "</rdf:Bag></ca:Albums>")
    for key, field in (("favorite", "Favorite"), ("archived", "Archived"), ("trashed", "Trashed"), ("shared", "SharedByLink")):
        if key in meta:
            parts.append(f"<ca:{field}>{'True' if meta[key] else 'False'}</ca:{field}>")
    for key, field in (("sha256", "SHA256"), ("sha1", "SHA1"), ("size", "Bytes"), ("pixel_sha256", "PixelSHA256"),
                       ("pixel_format", "PixelFormat"), ("phash", "PHash"), ("dhash", "DHash")):
        if record.get(key) not in (None, ""):
            parts.append(f"<ca:{field}>{escape(str(record[key]))}</ca:{field}>")
    ids = [source["asset_id"] for source in record.get("sources") or [] if source.get("asset_id")]
    if ids:
        parts.append("<ca:ImmichAssets><rdf:Bag>" + "".join(f"<rdf:li>{escape(asset_id)}</rdf:li>" for asset_id in ids) + "</rdf:Bag></ca:ImmichAssets>")
    if record.get("copies"):
        copies = "".join(
            '<rdf:li rdf:parseType="Resource">'
            + "".join(f"<ca:{field}>{escape(str(value))}</ca:{field}>" for field, value in (
                ("UUID", copy.get("uuid")), ("ImmichAsset", copy.get("immich_asset")), ("File", copy.get("file")),
                ("FileName", copy.get("name")), ("SHA256", copy.get("sha256")), ("Created", copy.get("created")),
                ("Method", copy.get("method")), ("CodeVersion", (copy.get("recipe") or {}).get("code", {}).get("id")),
                ("Recipe", json.dumps(copy.get("recipe"), separators=(",", ":"), ensure_ascii=False))) if value)
            + "</rdf:li>" for copy in record["copies"])
        parts.append(f"<ca:Copies><rdf:Bag>{copies}</rdf:Bag></ca:Copies>")
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:digiKam="http://www.digikam.org/ns/1.0/" '
        'xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/" xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#" '
        f'xmlns:stDim="http://ns.adobe.com/xap/1.0/sType/Dimensions#" xmlns:ca="{NAMESPACE}">'
        + "".join(parts) + "</rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end=\"w\"?>\n"
    ).encode("utf-8")


def _immich_details(client: immich.Immich, asset_id: str, shared: set[str]) -> dict:
    asset = client.asset(asset_id)
    albums = client.albums_with(asset_id)
    try:
        faces = client.faces(asset_id)
    except immich.ImmichError:
        faces = []
    try:
        uuid = (client.metadata(asset_id, jobs.METADATA_KEY) or {}).get("uuid")
    except immich.ImmichError:
        uuid = None
    return {"asset": asset, "albums": [{"id": album["id"], "name": album["albumName"], "shared_link": bool(album.get("hasSharedLink"))} for album in albums],
            "faces": faces, "uuid": uuid, "favorite": bool(asset.get("isFavorite")), "trashed": bool(asset.get("isTrashed")),
            "archived": asset.get("visibility") == "archive", "shared": asset_id in shared or any(album.get("hasSharedLink") for album in albums)}


def _archive_items(archive: "Archive", source: dict, client, shared: set[str], copies_in_source: dict[str, set[str]],
                   local_copies: dict[str, list[dict]], originals: list[dict], counts: dict, codes: dict, progress) -> None:
    """Archive each original in turn; the manifest is saved every so often, so an interrupted run loses little."""
    root = archive.root
    for number, item in enumerate(originals, 1):
        path = locations.local_file(source, item)
        sha1 = fingerprint.file_sha1(path)
        if client and item.get("checksum") and base64.b64decode(item["checksum"]).hex() != sha1:
            # A cached download that does not match (e.g. from an older, unverified run): fetch again.
            path.unlink()
            path = locations.local_file(source, item)
            sha1 = fingerprint.file_sha1(path)
            if base64.b64decode(item["checksum"]).hex() != sha1:
                raise ArchiveError(f"{item['name']}: ladattu tiedosto ei vastaa Immichin tarkistussummaa")
        sha256 = _sha256(path)
        entry, added = archive.add_original(path, item["name"], sha256)
        counts["added" if added else "unchanged"] += 1
        record_path = root / "originals" / (entry["name"] + ".json")
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {"format": FORMAT, "archived": entry["archived"]}
        stored, orientation, _ = render.decode(path)
        pixel_sha256, pixel_format = render.pixel_digest(stored)
        upright = render.upright(stored, orientation)
        record.update(name=entry["name"], sha256=sha256, sha1=sha1, size=path.stat().st_size, pixel_sha256=pixel_sha256,
                      pixel_format=pixel_format, orientation=orientation, phash=fingerprint.phash(upright),
                      dhash=fingerprint.dhash(upright), updated=_now())
        sources = {json.dumps(found, sort_keys=True): found for found in record.get("sources", [])}
        if client:
            found = {"kind": "immich", "asset_id": item["key"], "file_name": item["name"], "original_path": str(Path(item["folder"]) / item["name"])}
            details = _immich_details(client, item["key"], shared)
            record["immich"] = details
            copies = _immich_copies(client, item["key"], details["asset"], copies_in_source.get(item["key"], set()))
            copies += local_copies.get(item["key"], [])
        else:
            found = {"kind": "local", "path": str(path)}
            copies = local_copies.get(str(path), [])
        sources[json.dumps(found, sort_keys=True)] = found
        record["sources"] = list(sources.values())
        known = {copy["uuid"]: copy for copy in record.get("copies", [])}
        for copy in copies:
            # Immich's view (trash state, name) wins over a local job record of the same copy.
            known[copy["uuid"]] = {**copy, **{key: value for key, value in known.get(copy["uuid"], {}).items() if copy.get(key) is None}}
        record["copies"] = sorted(known.values(), key=lambda copy: copy.get("created") or "")
        for copy in record["copies"]:
            code = (copy.get("recipe") or {}).get("code") or {}
            if code.get("id"):
                codes[code["id"]] = code
        _write(record_path, _json(record))
        _write(root / "originals" / (entry["name"] + ".xmp"), sidecar(record))
        _write(root / "index" / "thumbs" / (sha256[:16] + ".jpg"), _thumbnail(path))
        entry.update(sources=record["sources"], copies=len(record["copies"]))
        counts["originals"] += 1
        if progress:
            progress(number, len(originals), entry["name"], added)
        if number % 25 == 0:
            archive.save_manifest()

def run(source: dict, target: str, progress=None) -> dict:
    """Archive a source's originals into `target`; returns counts and warnings."""
    source = locations.validate_source(source)
    root = locations.checked_folder(target, create=True)
    if source["kind"] == "local" and (Path(source["path"]) == root or Path(source["path"]) in root.parents or root in Path(source["path"]).parents):
        raise ArchiveError("Arkisto ja lähde eivät voi olla sisäkkäin")
    archive = Archive(root)
    items = locations.list_items(source, trashed=True)
    client = immich.client() if source["kind"] != "local" else None
    shared = client.shared_assets() if client else set()
    copies_in_source: dict[str, set[str]] = {}
    for item in items:
        if item.get("copy_of"):
            copies_in_source.setdefault(item["copy_of"], set()).add(item["key"])
    local_copies = _local_job_copies()
    counts = {"added": 0, "unchanged": 0, "originals": 0}
    warnings: list[str] = []
    codes: dict[str, dict] = {}
    originals = [item for item in items if not item.get("unfade_copy")]
    try:
        _archive_items(archive, source, client, shared, copies_in_source, local_copies, originals, counts, codes, progress)
    finally:
        # Whatever got in stays listed, so a run cut short by a dropped connection resumes cleanly.
        archive.save_manifest()
    for code in codes.values():
        if not version.snapshot(code, root / "code" / f"unfade-{code['id']}"):
            warnings.append(f"Koodiversiota {code['id']} (commit {code.get('commit') or '?'}) ei saatu arkistoon")
    _write(root / "code" / "recompute.py", (version.ROOT / "unfade" / "recompute.py").read_bytes())
    _write(root / "README.md", README.encode("utf-8"))
    _write(root / "index.html", _index(root, archive))
    _write_sums(root)
    return {**counts, "warnings": warnings, "codes": sorted(codes)}


def _thumbnail(path: Path) -> bytes:
    image = Image.fromarray(render._load(str(path), render.version(path), THUMB_SIZE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def _write_sums(root: Path) -> None:
    lines = [f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
             for path in sorted(root.rglob("*")) if path.is_file() and path.name != "SHA256SUMS" and not path.name.endswith(".part")]
    _write(root / "SHA256SUMS", ("\n".join(lines) + "\n").encode("utf-8"))


def verify(target: str) -> list[str]:
    """Problems found: files whose SHA-256 no longer matches SHA256SUMS, or that are missing."""
    root = Path(target)
    problems = []
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        path = root / name
        if not path.is_file():
            problems.append(f"puuttuu: {name}")
        elif _sha256(path) != digest:
            problems.append(f"muuttunut: {name}")
    return problems


def _index(root: Path, archive: Archive) -> bytes:
    """A static overview: every original by tag, album and year, with links to its files."""
    records = []
    for entry in archive.manifest["originals"]:
        path = root / "originals" / (entry["name"] + ".json")
        if path.is_file():
            records.append(json.loads(path.read_text(encoding="utf-8")))
    by: dict[str, dict[str, list[dict]]] = {"Tunnisteet": {}, "Albumit": {}, "Vuodet": {}}
    for record in records:
        meta = record.get("immich") or {}
        asset = meta.get("asset") or {}
        for tag in asset.get("tags") or []:
            by["Tunnisteet"].setdefault(tag["value"], []).append(record)
        for album in meta.get("albums") or []:
            by["Albumit"].setdefault(album["name"], []).append(record)
        taken = (asset.get("exifInfo") or {}).get("dateTimeOriginal") or asset.get("localDateTime") or ""
        by["Vuodet"].setdefault(taken[:4] or "ei päiväystä", []).append(record)

    def card(record: dict) -> str:
        name = html.escape(record["name"])
        link = html.escape("originals/" + record["name"])
        copies = len(record.get("copies") or [])
        return (f'<figure><a href="{link}"><img src="index/thumbs/{record["sha256"][:16]}.jpg" alt="" loading="lazy"></a>'
                f'<figcaption><a href="{link}">{name}</a><br><a href="{link}.xmp">XMP</a> · <a href="{link}.json">JSON</a>'
                f'{f" · {copies} korjattua" if copies else ""}</figcaption></figure>')

    sections, buttons = [], []
    for title, groups in by.items():
        if not groups:
            continue
        view = title.lower()
        blocks = "".join(f'<h3>{html.escape(key)} <small>({len(group)})</small></h3><div class="grid">{"".join(card(record) for record in group)}</div>'
                         for key, group in sorted(groups.items(), key=lambda pair: pair[0].lower()))
        sections.append(f'<section id="{view}" data-view><h2>{title}</h2>{blocks}</section>')
        buttons.append(f'<button type="button" data-show="{view}">{title} ({len(groups)})</button>')
    sections.append(f'<section id="kaikki" data-view><h2>Kaikki ({len(records)})</h2><div class="grid">{"".join(card(record) for record in records)}</div></section>')
    buttons.append(f'<button type="button" data-show="kaikki">Kaikki ({len(records)})</button>')
    return (
        '<!doctype html><html lang="fi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Alkuperäisten arkisto</title><style>"
        ":root{font-family:system-ui,sans-serif;color:#20312a;background:#f2f1eb}body{margin:0 auto;max-width:1200px;padding:1rem}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.6rem}"
        "figure{margin:0;background:#fff;border:1px solid #d8ded4;border-radius:.5rem;padding:.4rem}"
        "figure img{display:block;width:100%;aspect-ratio:3/2;object-fit:cover;border-radius:.3rem;background:#ddd}"
        "figcaption{font-size:.8rem;margin-top:.3rem;word-break:break-all}a{color:#245d40}h3 small{color:#526759;font-weight:400}"
        "nav{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0}nav button{font:inherit;font-weight:700;min-height:44px;padding:.5rem 1rem;"
        "border:2px solid #245d40;border-radius:.4rem;background:#fff;color:#245d40;cursor:pointer}"
        "nav button[aria-pressed=true]{background:#245d40;color:#fff}"
        "</style></head><body><h1>Alkuperäisten arkisto</h1>"
        f"<p>{len(records)} alkuperäistä · päivitetty {html.escape(archive.manifest.get('updated', ''))} · "
        'ohje: <a href="README.md">README.md</a> · tarkistus: <code>sha256sum -c SHA256SUMS</code></p>'
        f'<nav aria-label="Näkymä">{"".join(buttons)}</nav>'
        + "".join(sections)
        # One view at a time; without JavaScript every section stays visible. The view is kept in
        # the address (#albumit), so a link or reload opens the same one.
        + "<script>(()=>{const views=[...document.querySelectorAll('[data-view]')],buttons=[...document.querySelectorAll('[data-show]')];"
          "const show=id=>{if(!views.some(v=>v.id===id))id=views[0].id;views.forEach(v=>v.hidden=v.id!==id);"
          "buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.show===id)))};"
          "buttons.forEach(b=>b.addEventListener('click',()=>{history.replaceState(null,'','#'+b.dataset.show);show(b.dataset.show)}));"
          "show(location.hash.slice(1))})()</script>"
        + "</body></html>\n"
    ).encode("utf-8")


README = """# Alkuperäisten arkisto (unfade)

Tämä hakemisto on unfadein tekemä arkisto alkuperäisistä kuvista. Se on tarkoitettu
säilymään ilman unfadeia, Immichiä tai tätä konetta.

## Sisältö

- `originals/<nimi>` – alkuperäinen kuva tavu tavulta sellaisena kuin Immich (tai lähdekansio)
  sen säilytti. Arkistoa ei koskaan muokkaa tätä tiedostoa.
- `originals/<nimi>.xmp` – XMP-sivutiedosto: kuvaus, tunnisteet (`dc:subject` ja
  `digiKam:TagsList`), arvio, päiväys aikavyöhykkeineen, sijainti, henkilöt kasvoalueineen
  (MWG-alueet), albumit, suosikki- ja roskakoritila sekä jokainen korjattu kopio ohjeineen.
  digiKam, darktable, Lightroom, exiftool ja Immich lukevat sen.
- `originals/<nimi>.json` – täydellinen tietue: tiivisteet (SHA-256, SHA-1 eli Immichin
  tarkistussumma, pikselitiiviste, pHash, dHash), lähteet (Immich-tunnus ja polku), Immichin
  koko kuvatieto sellaisenaan, albumit, kasvot ja korjatut kopiot.
- `code/unfade-<koodin tunnus>/` – se korjauskoodi, jolla kopio tehtiin, tiedostoittain.
  Tunnus on tiedostojen tiiviste, joten kansion sisältö on täsmälleen se koodi.
  `uv.lock` kertoo kirjastojen versiot.
- `code/recompute.py` – laskee kopion uudelleen alkuperäisestä ja ohjeesta.
- `index.html` – selattava yleiskuva tunnisteittain, albumeittain ja vuosittain.
- `manifest.json` – luettelo alkuperäisistä ja niiden pysyvistä nimistä.
- `SHA256SUMS` – jokaisen tiedoston tiiviste.

## Tarkistus

    sha256sum -c SHA256SUMS

## Kopion uudelleenlaskenta

Korjattu kopio ei ole arkistossa, mutta sen voi laskea uudelleen: ohje (menetelmä, sovitetut
käyräparametrit ja kuvan omat säädöt) on alkuperäisen JSON- ja XMP-tiedostossa, ja koodi on
hakemistossa `code/`.

    python3 code/recompute.py originals/<nimi> --out kopio.png
    python3 code/recompute.py originals/<nimi> --copy <UUID> --check <olemassa oleva kopio>

Tarvitaan Python 3 sekä numpy, pillow, pillow-heif ja imagecodecs. Tulos on häviötön
16-bittinen PNG, jonka pikselit ovat samat kuin alkuperäisessä kopiossa.

## Nimet

Nimet päätetään, kun alkuperäinen arkistoidaan ensimmäisen kerran, eivätkä ne muutu.
Immich-lähteen alkuperäiset ovat yhdessä tasaisessa hakemistossa alkuperäisellä nimellään;
paikallisen kansion alikansiot säilyvät. Jos sama nimi on jo toisella sisällöllä, nimeen
lisätään tiedoston SHA-256-tiivisteen alku. Tunnisteet, albumit ja päiväykset ovat
sivutiedostoissa, eivät hakemistorakenteessa.
"""
