"""Fitting, previews and full-resolution output for one image.

The correction itself is unfade/color_methods.py: each image is fitted on its own with the chosen
method on a fixed-size 8-bit copy, and the per-image adjustments go on top. Output is computed from the original at its own bit depth (16-bit TIFF and PNG stay
16-bit) and stored with 16 bits per channel, so the correction adds no rounding steps of its own.
The original's EXIF (orientation reset, since pixels are turned upright), colour profile and XMP
are kept, and the recipe is added to the XMP; the original file is never written.
"""
from __future__ import annotations

import functools
import hashlib
import io
import itertools
import uuid
from pathlib import Path

import imagecodecs
import numpy as np
from PIL import Image
from pillow_heif import register_heif_opener

from unfade import color_methods, encode, fade_model, fingerprint
from unfade.version import code_version

register_heif_opener(thumbnails=False)

PREVIEW_SIZE = 900
FIT_SIZE = 700
DELTA_E_SIZE = 300
STRIP_ROWS = 512
ORIENTATION = 0x0112
XMP_TAG = 700
# Decoded with imagecodecs, which keeps 16 bits per channel where Pillow would drop to 8.
DEEP_SUFFIXES = {".tif", ".tiff", ".png"}


def version(path: Path) -> str:
    """Keys the caches to the file's state, so a replaced file is fitted again."""
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def upright(pixels: np.ndarray, orientation: int) -> np.ndarray:
    """Turn pixels as EXIF orientation 1..8 says, like PIL.ImageOps.exif_transpose."""
    turns = {2: lambda a: a[:, ::-1], 3: lambda a: a[::-1, ::-1], 4: lambda a: a[::-1],
             5: lambda a: a.swapaxes(0, 1), 6: lambda a: np.rot90(a, -1), 7: lambda a: a.swapaxes(0, 1)[::-1, ::-1],
             8: lambda a: np.rot90(a, 1)}
    return np.ascontiguousarray(turns[orientation](pixels)) if orientation in turns else pixels


def _rgb(pixels: np.ndarray) -> np.ndarray:
    if pixels.ndim == 2:
        pixels = pixels[..., None]
    if pixels.shape[-1] in (1, 2):
        pixels = np.repeat(pixels[..., :1], 3, axis=-1)
    pixels = pixels[..., :3]
    if pixels.dtype not in (np.uint8, np.uint16):
        raise ValueError(f"Kuvan bittisyvyyttä ei tueta: {pixels.dtype}")
    return pixels


def decode(path: Path) -> tuple[np.ndarray, int, dict]:
    """RGB pixels at their own depth (uint8 or uint16) as the decoder returns them, before EXIF
    orientation; the orientation; and EXIF (orientation reset to 1), ICC and XMP for the copy."""
    with Image.open(path) as image:
        exif = image.getexif()
        meta = {
            "icc": image.info.get("icc_profile"),
            "xmp": image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
            or (image.tag_v2.get(XMP_TAG) if hasattr(image, "tag_v2") else None),
        }
        pixels = None
        if path.suffix.lower() in DEEP_SUFFIXES:
            try:
                pixels = imagecodecs.imread(str(path))
            except Exception:  # imagecodecs raises codec-specific errors; Pillow below is the fallback
                pixels = None
        if pixels is None:
            pixels = np.asarray(image.convert("RGB"))
    orientation = exif.get(ORIENTATION, 1)
    if ORIENTATION in exif:
        exif[ORIENTATION] = 1
    meta["exif"] = exif.tobytes() if len(exif) else None
    if isinstance(meta["xmp"], str):
        meta["xmp"] = meta["xmp"].encode("utf-8")
    return _rgb(pixels), orientation, meta


def read_full(path: Path) -> tuple[np.ndarray, dict]:
    """The upright RGB pixels at their own depth (uint8 or uint16), with EXIF, ICC and XMP."""
    pixels, orientation, meta = decode(path)
    return upright(pixels, orientation), meta


def pixel_digest(pixels: np.ndarray) -> tuple[str, str]:
    """SHA-256 of the raw pixel bytes and their layout, e.g. "4000x2667 RGB 16-bit LE".

    The bytes are the decoded RGB samples row by row from the top, interleaved, 16-bit samples
    little-endian. Unlike the file's SHA-256 this stays the same when only metadata changes.
    """
    height, width = pixels.shape[:2]
    bits = 16 if pixels.dtype == np.uint16 else 8
    raw = np.ascontiguousarray(pixels.astype("<u2") if bits == 16 else pixels)
    return hashlib.sha256(raw.tobytes()).hexdigest(), f"{width}x{height} RGB {bits}-bit" + (" LE" if bits == 16 else "")


@functools.lru_cache(maxsize=128)
def _load(path: str, file_version: str, size: int) -> np.ndarray:
    """A downscaled upright 8-bit copy for fitting and previews."""
    source = Path(path)
    if source.suffix.lower() in DEEP_SUFFIXES:
        pixels, _ = read_full(source)
        if pixels.dtype == np.uint16:
            pixels = ((pixels.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
        image = Image.fromarray(pixels)
    else:
        with Image.open(path) as original:
            original.draft("RGB", (size, size))
            orientation = original.getexif().get(ORIENTATION, 1)
            image = Image.fromarray(upright(np.asarray(original.convert("RGB")), orientation))
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    return np.asarray(image)


@functools.lru_cache(maxsize=512)
def _fit(path: str, file_version: str, method: str) -> dict:
    return color_methods.fit(method, _load(path, file_version, FIT_SIZE).astype(np.float64) / 255)


def fitted(path: Path, method: str) -> dict:
    return _fit(str(path), version(path), method)


def preview_bytes(path: Path, method: str, adjustments: dict | None, view: str) -> bytes:
    """A JPEG preview: the original, the method's result, or the result with the adjustments."""
    if view not in {"original", "corrected", "adjusted"}:
        raise ValueError("Tuntematon esikatselu")
    image = _load(str(path), version(path), PREVIEW_SIZE)
    if view == "original":
        result = image
    else:
        corrected = color_methods.apply(image, fitted(path, method), adjustments if view == "adjusted" else None)
        result = (corrected * 255 + 0.5).astype(np.uint8)
    output = io.BytesIO()
    Image.fromarray(result).save(output, "JPEG", quality=90)
    return output.getvalue()


@functools.lru_cache(maxsize=512)
def _delta_e(path: str, file_version: str) -> dict:
    image = fade_model._inner(_load(path, file_version, DELTA_E_SIZE))
    original = color_methods.rgb_to_lab(image / 255)
    labs = {method: color_methods.rgb_to_lab(color_methods.apply(image, _fit(path, file_version, method)).astype(np.float64))
            for method in color_methods.METHODS}
    return {
        "original": {method: color_methods.mean_delta_e(lab, original) for method, lab in labs.items()},
        "pairs": {f"{a}-{b}": color_methods.mean_delta_e(labs[a], labs[b]) for a, b in itertools.combinations(color_methods.METHODS, 2)},
    }


def delta_e(path: Path) -> dict:
    """Mean CIE76 ΔE over the inner 80 %: each method against the original and each pair of methods."""
    return _delta_e(str(path), version(path))


def corrected_file(path: Path, method: str, adjustments: dict | None, output_format: str, origin: dict,
                   copy_uuid: str | None = None) -> tuple[bytes, str, dict]:
    """The full-resolution corrected image: (file content, suffix, recipe).

    `origin` names the source for the recipe (name, path, sha256, sha1, size, optionally
    immich_asset and uuid); the pixel digest, orientation, perceptual hashes and any DocumentID of
    the source are added here, and the copy gets a new UUID (or `copy_uuid`) and its own perceptual
    hashes. The same inputs and UUID give the same bytes, which is what lets an interrupted upload
    be repeated without a duplicate in Immich.
    """
    params = fitted(path, method)
    stored, orientation, meta = decode(path)
    digest, layout = pixel_digest(stored)
    pixels = upright(stored, orientation)
    origin = {**origin, "pixel_sha256": digest, "pixel_format": layout, "orientation": orientation,
              "phash": fingerprint.phash(pixels), "dhash": fingerprint.dhash(pixels)}
    if not origin.get("document_id") and encode.document_id(meta["xmp"]):
        origin["document_id"] = encode.document_id(meta["xmp"])
    levels = 65536 if pixels.dtype == np.uint16 else 256
    deep = {**params, "table": color_methods.tables(params["curve"], levels)}
    out = np.empty(pixels.shape, np.uint16)
    # In strips, so a large scan does not need a float copy of the whole frame.
    for top in range(0, pixels.shape[0], STRIP_ROWS):
        strip = color_methods.apply(pixels[top:top + STRIP_ROWS], deep, adjustments)
        out[top:top + STRIP_ROWS] = (strip * 65535 + 0.5).astype(np.uint16)
    recipe = {"uuid": copy_uuid or str(uuid.uuid4()), "method": method, "curve": params["curve"], "code": code_version(),
              "adjustments": adjustments or dict(color_methods.DEFAULT_ADJUSTMENTS), "source": origin,
              "phash": fingerprint.phash(out), "dhash": fingerprint.dhash(out)}
    xmp = encode.recipe_xmp(recipe, meta["xmp"])
    suffix = encode.suffix_for(output_format, meta["icc"])
    if suffix == ".jxl":
        content = encode.jxl(out, meta["exif"], xmp)
    elif suffix == ".png":
        content = encode.png(out, meta["icc"], meta["exif"], xmp)
    else:
        content = encode.jpeg(out, meta["icc"], meta["exif"], xmp)
    return content, suffix, recipe
