"""Visual outliers within a suggested group, and the file digest that keys cached fits."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from unfade.i18n import N_


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def visual_metrics(image: Image.Image) -> tuple[float, float]:
    """Brightness and channel spread of the inner part of a small, upright copy of an image."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((120, 120))
    width, height = image.size
    image = image.crop((width // 8, height // 8, width * 7 // 8, height * 7 // 8))
    red, green, blue = ImageStat.Stat(image).mean
    return (red + green + blue) / 3, max(red, green, blue) - min(red, green, blue)


def outlier_buckets[K](metrics: dict[K, tuple[float, float]], groups) -> dict[K, str]:
    """Within each group of at least five, label only strong brightness or colour outliers."""
    buckets = {}
    for members in groups:
        if len(members) < 5:
            continue
        lights = sorted(metrics[member][0] for member in members)
        casts = sorted(metrics[member][1] for member in members)
        median_light, median_cast = lights[len(lights) // 2], casts[len(casts) // 2]
        for member in members:
            light, cast = metrics[member]
            if light < median_light - 65:
                buckets[member] = N_("possibly dark · check")
            elif light > median_light + 65:
                buckets[member] = N_("possibly light · check")
            elif cast > median_cast + 45:
                buckets[member] = N_("unusual colours · check")
    return buckets
