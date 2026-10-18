"""Lossless 16-bit output with the original's metadata and the correction recipe.

- JPEG XL, lossless and 16 bits per channel, for sRGB sources (or sources without a profile).
  imagecodecs writes the codestream; EXIF and XMP go into the container's `Exif` and `xml ` boxes.
- PNG, 16 bits per channel, for sources with another colour profile: JPEG XL through imagecodecs
  can only declare sRGB-like colour spaces, while PNG carries the ICC profile itself.
- JPEG, 8 bits at quality 95, only when the destination asks for small files.

Every format gets an XMP packet with the recipe (see recipe_xmp), merged into the source's own XMP.
"""
from __future__ import annotations

import io
import json
import re
import struct
import zlib
from xml.sax.saxutils import escape, unescape

import imagecodecs
import numpy as np
from PIL import Image, ImageCms

NAMESPACE = "https://github.com/ssalonen/unfade/ns/recipe/1.0/"
RECIPE_VERSION = 1


def is_srgb(icc: bytes | None) -> bool:
    if not icc:
        return True
    try:
        description = ImageCms.ImageCmsProfile(io.BytesIO(icc)).profile.profile_description or ""
    except (OSError, ImageCms.PyCMSError):
        return False
    return "srgb" in description.lower()


def suffix_for(output_format: str, icc: bytes | None) -> str:
    if output_format == "jpeg":
        return ".jpg"
    return ".jxl" if is_srgb(icc) else ".png"


def recipe_xmp(recipe: dict, source_xmp: bytes | None = None) -> bytes:
    """An XMP packet with the recipe, as an extra rdf:Description inside the source's own XMP.

    The copy gets its own xmpMM:DocumentID (a UUID). The recipe names the source (file, size,
    SHA-256 and SHA-1 of the file, SHA-256 of its decoded pixels, perceptual hashes, EXIF
    orientation, Immich asset, UUID and DocumentID) and holds the fitted curve parameters and adjustments, so the
    copy can be recomputed from the original, or dropped. The file digest changes with any
    metadata edit; the pixel digest only when the pixels do.
    """
    origin = recipe["source"]
    source_id = f"uuid:{origin['uuid']}" if origin.get("uuid") else origin.get("document_id") or f"sha256:{origin['sha256']}"
    fields = {
        "RecipeVersion": str(RECIPE_VERSION),
        "Method": recipe["method"],
        "Curve": json.dumps(recipe["curve"], separators=(",", ":")),
        "Adjustments": json.dumps(recipe["adjustments"], separators=(",", ":")),
        "SourceName": origin["name"],
        "SourcePath": origin.get("path", ""),
        "SourceSHA256": origin["sha256"],
        "SourceBytes": str(origin.get("size", "")),
        "SourcePixelSHA256": origin.get("pixel_sha256", ""),
        "SourcePixelFormat": origin.get("pixel_format", ""),
        "SourceOrientation": str(origin.get("orientation", "")),
        "SourceSHA1": origin.get("sha1", ""),
        "SourcePHash": origin.get("phash", ""),
        "SourceDHash": origin.get("dhash", ""),
        "SourceImmichAsset": origin.get("immich_asset", ""),
        "SourceUUID": origin.get("uuid", ""),
        "SourceDocumentID": origin.get("document_id", ""),
        "PHash": recipe.get("phash", ""),
        "DHash": recipe.get("dhash", ""),
        "CodeVersion": (recipe.get("code") or {}).get("id", ""),
        "CodeCommit": (recipe.get("code") or {}).get("commit", "") + ("+uncommitted" if (recipe.get("code") or {}).get("dirty") else ""),
    }
    properties = "".join(f"<unfade:{key}>{escape(value)}</unfade:{key}>" for key, value in fields.items() if value)
    description = (
        f'<rdf:Description rdf:about="" xmlns:unfade="{NAMESPACE}" '
        'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#">'
        f"{properties}<xmpMM:DocumentID>uuid:{recipe['uuid']}</xmpMM:DocumentID><xmpMM:InstanceID>uuid:{recipe['uuid']}</xmpMM:InstanceID>"
        f"<xmpMM:DerivedFrom rdf:parseType=\"Resource\"><stRef:filePath>{escape(origin.get('path') or origin['name'])}</stRef:filePath>"
        f"<stRef:documentID>{escape(source_id)}</stRef:documentID></xmpMM:DerivedFrom></rdf:Description>"
    )
    text = source_xmp.decode("utf-8", "replace").strip("\x00") if source_xmp else ""
    if "</rdf:RDF>" in text:
        # A source that is itself a unfade copy: its old recipe and DerivedFrom give way to the new.
        text = re.sub(r"<rdf:Description[^>]*xmlns:unfade=.*?</rdf:Description>", "", text, flags=re.S)
        # The copy is a new document: the source's identity moves into DerivedFrom and SourceDocumentID.
        text = re.sub(r"<xmpMM:(DocumentID|InstanceID|OriginalDocumentID)>.*?</xmpMM:\1>|<xmpMM:DerivedFrom\b.*?(?:/>|</xmpMM:DerivedFrom>)", "", text, flags=re.S)
        text = re.sub(r'\s+xmpMM:(DocumentID|InstanceID|OriginalDocumentID)="[^"]*"', "", text)
        packet = text.replace("</rdf:RDF>", description + "</rdf:RDF>", 1)
    else:
        packet = (
            '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'{description}</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
        )
    return packet.encode("utf-8")


def document_id(xmp: bytes | None) -> str:
    """The xmpMM:DocumentID of an XMP packet, in element or attribute form, or ""."""
    match = re.search(r'xmpMM:DocumentID(?:="([^"]*)"|>([^<]*)<)', xmp.decode("utf-8", "replace")) if xmp else None
    return unescape(match[1] or match[2]).strip() if match else ""


def read_recipe(xmp: bytes) -> dict:
    """The recipe fields of an XMP packet written by recipe_xmp(), with its DocumentID."""
    text = xmp.decode("utf-8", "replace")
    found = dict(re.findall(r"<unfade:(\w+)>(.*?)</unfade:\1>", text, flags=re.S))
    found["DocumentID"] = document_id(xmp)
    found = {key: unescape(value) for key, value in found.items()}
    for key in ("Curve", "Adjustments"):
        if key in found:
            found[key] = json.loads(found[key])
    return found


def _tiff_exif(exif: bytes | None) -> bytes | None:
    if not exif:
        return None
    return exif[6:] if exif.startswith(b"Exif\x00\x00") else exif


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def jxl(pixels: np.ndarray, exif: bytes | None, xmp: bytes | None) -> bytes:
    """Lossless JPEG XL in a container, with `Exif` and `xml ` boxes before the codestream."""
    # Effort 3: higher efforts took several times longer on film grain for under 2 % smaller files.
    stream = imagecodecs.jpegxl_encode(pixels, lossless=True, effort=3, usecontainer=True)
    extra = b""
    tiff = _tiff_exif(exif)
    if tiff:
        extra += _box(b"Exif", b"\x00\x00\x00\x00" + tiff)
    if xmp:
        extra += _box(b"xml ", xmp)
    position = 0
    while position < len(stream):
        size, kind = struct.unpack(">I4s", stream[position:position + 8])
        if kind in (b"jxlc", b"jxlp"):
            return stream[:position] + extra + stream[position:]
        if size < 8:
            break
        position += size
    raise ValueError("JPEG XL -säiliötä ei voitu lukea")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))


def png(pixels: np.ndarray, icc: bytes | None, exif: bytes | None, xmp: bytes | None) -> bytes:
    """Lossless PNG with iCCP, eXIf and XMP (iTXt) chunks right after the header."""
    stream = imagecodecs.png_encode(pixels)
    extra = b""
    if icc:
        extra += _chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc))
    tiff = _tiff_exif(exif)
    if tiff:
        extra += _chunk(b"eXIf", tiff)
    if xmp:
        extra += _chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp)
    header_end = 8 + 8 + 13 + 4  # signature + IHDR
    return stream[:header_end] + extra + stream[header_end:]


def jpeg(pixels: np.ndarray, icc: bytes | None, exif: bytes | None, xmp: bytes | None) -> bytes:
    if pixels.dtype == np.uint16:
        pixels = ((pixels.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    options = {"exif": exif} if exif else {}
    if icc:
        options["icc_profile"] = icc
    if xmp:
        options["xmp"] = xmp
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, "JPEG", quality=95, subsampling=0, **options)
    return buffer.getvalue()
