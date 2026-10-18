"""Automatic fade correction methods and the per-image adjustments on top of them.

Every method is fitted on one image at a time and reduces to a lookup table per channel plus an
optional saturation factor, so applying it is cheap. Statistics come from the inner part of the
frame, so frames, black scanner borders and captions do not steer the correction.
See docs/methods.md for the methods and their sources.
"""
from __future__ import annotations

import numpy as np

from unfade import fade_model

METHODS = ("fade", "gwg", "pcs")
DEFAULT_METHOD = "fade"
METHOD_LABELS = {
    "fade": ("FADE", "Väriaineiden haalistumismalli. Vähiten leikkautumista."),
    "gwg": ("GWG", "Venytys ja harmaatasapaino. Poistaa minkä tahansa värivirheen."),
    "pcs": ("PCS", "Pelkkä kanavakohtainen venytys. Säilyttää alkuperäisen sävyn."),
}

ADJUSTMENT_LIMITS = {"strength": (0, 100), "black": (-20, 20), "white": (-20, 20), "temperature": (-20, 20)}
DEFAULT_ADJUSTMENTS = {"strength": 100, "black": 0, "white": 0, "temperature": 0}

def _percentiles(sample: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    region = fade_model._inner(sample).reshape(-1, 3)
    return np.percentile(region, 0.5, axis=0), np.percentile(region, 99.5, axis=0)


def _stretch(values: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    return np.clip((values - low) / np.maximum(high - low, 1e-3), 0, 1)


def fit(method: str, sample: np.ndarray) -> dict:
    """Fit one method on a small float RGB copy (0..1) of an image.

    Returns the 8-bit lookup table, the saturation factor and `curve`, the fitted parameters as
    plain JSON values, from which tables() rebuilds the curves at any bit depth.
    """
    if method == "fade":
        params = fade_model.fit(sample)
        curve = {"kind": "fade", **params}
    elif method in {"gwg", "pcs"}:
        low, high = _percentiles(sample)
        exponent = np.ones(3)
        if method == "gwg":
            # Bring the channel averages together with one gamma per channel after the stretch.
            means = np.clip(fade_model._inner(_stretch(sample, low, high)).reshape(-1, 3).mean(0), 1e-3, 1 - 1e-3)
            exponent = np.log(means.mean()) / np.log(means)
        curve = {"kind": method, "low": low.tolist(), "high": high.tolist(), "exponent": exponent.tolist(), "saturation": 1.0}
    else:
        raise ValueError("Tuntematon korjausmenetelmä")
    return {"table": tables(curve, 256), "saturation": curve["saturation"], "curve": curve}


def tables(curve: dict, levels: int) -> np.ndarray:
    """The fitted curves as a (levels, 3) table of floats 0..1; 65536 levels for 16-bit input."""
    if curve["kind"] == "fade":
        return fade_model.lookup_tables(curve, levels)
    ramp = np.linspace(0, 1, levels)[:, None]
    return _stretch(ramp, np.array(curve["low"]), np.array(curve["high"])) ** np.array(curve["exponent"])


def validate_adjustments(values: dict) -> dict:
    checked = {}
    for name, (minimum, maximum) in ADJUSTMENT_LIMITS.items():
        try:
            value = int(values[name])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Virheellinen säätö") from error
        if not minimum <= value <= maximum:
            raise ValueError("Säätö on sallitun alueen ulkopuolella")
        checked[name] = value
    return checked


def apply(image: np.ndarray, fitted: dict, adjustments: dict | None = None) -> np.ndarray:
    """Correct an 8-bit RGB array; returns float RGB 0..1.

    A 16-bit array works the same way with a 65536-level table in `fitted["table"]`.

    The same arithmetic runs in the browser preview (static/correct.js): the method's
    curves and saturation, then levels per channel, warm/cool gains, and finally the strength,
    which mixes the result with the original pixel.
    """
    a = {**DEFAULT_ADJUSTMENTS, **(adjustments or {})}
    table = fitted["table"].astype(np.float32)
    out = np.empty(image.shape, np.float32)
    for channel in range(3):
        np.take(table[:, channel], image[..., channel], out=out[..., channel])
    k = np.float32(fitted["saturation"])
    if k != 1:
        value = out.max(-1)
        value *= 1 - k
        out *= k
        out += value[..., None]
        # A boost above 1 pushes the weakest channel below 0. Clip here, as fade_model.apply() does,
        # so the adjustments work on the same 0..1 image the browser gets as its 8-bit preview.
        np.clip(out, 0, 1, out=out)
    low = a["black"] / 255
    high = 1 - a["white"] / 255
    if low or high != 1:
        out -= low
        out /= high - low
    if a["temperature"]:
        out[..., 0] *= 1 + a["temperature"] / 100
        out[..., 2] *= 1 - a["temperature"] / 100
    np.clip(out, 0, 1, out=out)
    if a["strength"] != 100:
        original = image.astype(np.float32) / (len(table) - 1)
        out = original + (a["strength"] / 100) * (out - original)
    return out


_XYZ = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
_WHITE = _XYZ.sum(axis=1)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """CIE Lab (D65) of float sRGB 0..1."""
    xyz = fade_model._linear(np.clip(rgb, 0, 1)) @ _XYZ.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def mean_delta_e(a: np.ndarray, b: np.ndarray) -> float:
    """Mean CIE76 ΔE between two Lab images."""
    return float(np.sqrt(((a - b) ** 2).sum(-1)).mean())
