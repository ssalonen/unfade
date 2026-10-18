"""Dye-fade model correction, implemented from Geoff Daniell's paper "Restoration of Faded Slides II"
(2016, PhotoRestore project) and its appendix. Written from the paper's description, not from the
GPL code; the author's pyrestore3.py (the standalone version of that paper) was used only as a
reference to compare results against.

The model: a faded channel is C' = (C)^a, so restoration is a power curve per channel,
f_c(v) = k_c * v^lambda_c. Parameters are fitted to the image's distinct colours sorted by
luminance, which an undamaged photo is assumed to spread evenly over the whole range.

Differences from the paper, chosen for speed and robustness:
- the appendix's octree palette is built from numpy histograms (same colours as pyrestore3 for
  95-100 % of the palette entries, about 10 ms);
- lambda is found by a vectorised grid search with sigma solved in closed form, not simplex;
- grey balance adjusts the red and blue gammas (endpoints stay fixed) with a coarse-to-fine grid
  that prefers the solution closest to no change, instead of a 6-parameter simplex;
- the overall scale is capped so that at most 0.5 % of the inner area clips;
- saturation is boosted like the author's HSV step (scale S, keep V), computed directly in RGB.
Statistics come from the inner 80 % of the frame so mounts, borders and captions do not count
(pyrestore3 uses the whole frame).
The result is three lookup tables plus one saturation factor, so previews stay fast.
"""
from __future__ import annotations

import numpy as np

LAMBDAS = np.geomspace(0.2, 6.0, 600)
GREY_WEIGHT = 50.0          # paper: weight g / (g + u*^2 + v*^2), "about 50 seems to work well"
UV_TARGET = np.array([2.0, 2.0])  # slightly warm instead of exactly neutral, as in the paper
MAX_SATURATION_BOOST = 1.5
PALETTE_FROM_INNER = True
MAX_CLIPPED = 0.005
D65_U, D65_V = 0.1978, 0.4683


def _inner(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    return a[h // 10: h * 9 // 10, w // 10: w * 9 // 10]


def _linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _luv(rgb: np.ndarray) -> np.ndarray:
    lin = _linear(np.clip(rgb, 0, None))
    x = lin @ np.array([0.4124, 0.3576, 0.1805])
    y = lin @ np.array([0.2126, 0.7152, 0.0722])
    z = lin @ np.array([0.0193, 0.1192, 0.9505])
    d = np.maximum(x + 15 * y + 3 * z, 1e-9)
    L = np.where(y > 0.008856, 116 * np.cbrt(y) - 16, 903.3 * y)
    u = 13 * L * (4 * x / d - D65_U)
    v = 13 * L * (9 * y / d - D65_V)
    return np.stack([L, u, v], -1)


def octree_palette(pixels: np.ndarray, target: int = 256, depth: int = 6, rounds: int = 200) -> np.ndarray:
    """Representative colours as in the paper's appendix: an octree whose boxes are split where
    pixel count / 2^level is largest and merged back where it is smallest.

    Returns box centres (0..1) of the boxes that keep pixels. The histogram counting, which is the
    slow part in pure Python, is done with numpy; the tree itself has at most a few hundred nodes.
    """
    rgb = pixels.reshape(-1, 3).astype(np.int64) >> (8 - depth)
    code = np.zeros(len(rgb), np.int64)
    for bit in range(depth - 1, -1, -1):
        for channel in range(3):
            code = (code << 1) | ((rgb[:, channel] >> bit) & 1)
    counts = [np.bincount(code >> (3 * (depth - level)), minlength=8 ** level) for level in range(depth + 1)]

    nodes = {(2, c): int(n) for c, n in enumerate(counts[2]) if n}  # (level, code) -> own pixel count
    children: dict[tuple[int, int], set[int]] = {}

    def colours_in_use():
        return sum(1 for n in nodes.values() if n > 0)

    def fit_measure():
        return sum(n / 2 ** level for (level, _), n in nodes.items())

    last = float("inf")
    for _ in range(rounds):
        if colours_in_use() < target:
            candidates = [(n >> key[0], key) for key, n in nodes.items() if n > 0 and key[0] < depth]
            if not candidates or max(candidates)[0] == 0:
                break
            _, (level, c) = max(candidates)
            own = nodes[(level, c)]
            existing = children.setdefault((level, c), set())
            for j in range(8):
                child = c * 8 + j
                n = int(counts[level + 1][child])
                if j in existing or n <= own / 8:
                    continue
                nodes[(level + 1, child)] = nodes.get((level + 1, child), 0) + n
                existing.add(j)
                nodes[(level, c)] -= n
        measure = fit_measure()
        if colours_in_use() >= target:
            candidates = [(n >> key[0], key) for key, n in nodes.items() if n > 0 and key[0] > 0]
            _, (level, c) = min(candidates)
            parent = (level - 1, c >> 3)
            nodes[parent] = nodes.get(parent, 0) + nodes[(level, c)]
            nodes[(level, c)] = 0
            if colours_in_use() == target:
                if measure >= last:
                    break
                last = measure

    palette = []
    for (level, c), n in nodes.items():
        if n <= 0:
            continue
        value = [0, 0, 0]
        for bit in range(level):
            for channel in range(3):
                value[channel] = (value[channel] << 1) | ((c >> (3 * (level - 1 - bit) + 2 - channel)) & 1)
        palette.append([(v << (8 - level)) | (0x80 >> level) for v in value])
    return np.array(palette, dtype=np.float64) / 255


def distinct_colours(sample: np.ndarray, count: int = 256, inner: bool = True) -> np.ndarray:
    """A 256-colour palette of the image, ordered by luminance (the paper's 'colour index')."""
    region = (np.clip(_inner(sample) if inner else sample, 0, 1) * 255 + 0.5).astype(np.uint8)
    palette = octree_palette(region)
    palette = palette[np.argsort(_linear(palette) @ np.array([0.2126, 0.7152, 0.0722]))]
    positions = np.linspace(0, len(palette) - 1, count)
    return np.stack([np.interp(positions, np.arange(len(palette)), palette[:, c]) for c in range(3)], 1)


def _fit_channel(values: np.ndarray, ideal: np.ndarray) -> tuple[float, float]:
    powered = np.clip(values, 1e-6, 1)[None, :] ** LAMBDAS[:, None]
    sigma = (powered @ ideal) / np.maximum((powered * powered).sum(1), 1e-12)
    error = ((sigma[:, None] * powered - ideal) ** 2).sum(1)
    best = int(np.argmin(error))
    return float(LAMBDAS[best]), float(sigma[best])


def fit(sample: np.ndarray, gamma: float = 1.0) -> dict:
    """Fit restoration parameters on a small float RGB image (values 0..1)."""
    colours = distinct_colours(sample, inner=PALETTE_FROM_INNER)
    index = np.linspace(0, 1, len(colours))
    ideal = index ** gamma
    lambdas, sigmas, highs = [], [], []
    for c in range(3):
        lam, sig = _fit_channel(colours[:, c], ideal)
        # Paper: C_hi is halfway between the channel maximum and a straight-line extrapolation,
        # so a few nearly dye-less bright colours do not dictate the scale.
        slope, intercept = np.polyfit(index, colours[:, c], 1)
        c_hi = np.clip(0.5 * (colours[:, c].max() + slope + intercept), 1e-3, 1)
        lambdas.append(lam)
        sigmas.append(sig)
        highs.append(sig * c_hi ** lam)
    lambdas, sigmas = np.array(lambdas), np.array(sigmas)
    gains = sigmas / max(highs)

    # Grey balance: red and blue gammas move the weighted average u*, v* of near-grey colours.
    # The weighted average has several solutions, so search a grid and prefer the one closest to
    # no change, as the author's simplex search starting from 1 does.
    def balanced(colour_values, g):
        restored = np.clip(gains * np.clip(colour_values, 0, 1) ** lambdas, 0, 1)
        return restored ** np.array([g[0], 1.0, g[1]])

    base = np.clip(gains * colours ** lambdas, 0, 1)

    def best_on(red_values, blue_values):
        g_red, g_blue = (m.ravel() for m in np.meshgrid(red_values, blue_values, indexing="ij"))
        trial = np.stack([base[None, :, 0] ** g_red[:, None],
                          np.broadcast_to(base[None, :, 1], (g_red.size, len(base))),
                          base[None, :, 2] ** g_blue[:, None]], -1)
        luv = _luv(trial)
        weight = GREY_WEIGHT / (GREY_WEIGHT + luv[..., 1] ** 2 + luv[..., 2] ** 2)
        mean_uv = (luv[..., 1:] * weight[..., None]).sum(1) / weight.sum(1)[:, None]
        cost = ((mean_uv - UV_TARGET) ** 2).sum(1) + 0.5 * (np.log(g_red) ** 2 + np.log(g_blue) ** 2)
        best = int(np.argmin(cost))
        return np.array([g_red[best], g_blue[best]])

    # Coarse grid over 0.5..2, then a fine grid around the best point.
    g = best_on(np.geomspace(0.5, 2.0, 15), np.geomspace(0.5, 2.0, 15))
    step = np.geomspace(0.5, 2.0, 15)[1] / 0.5
    g = best_on(g[0] * np.geomspace(1 / step, step, 9), g[1] * np.geomspace(1 / step, step, 9))
    exponents = np.array([g[0], 1.0, g[1]])

    # Cap the scale so that only a small share of the picture burns out.
    scale = 1.0
    raw = np.clip(gains * np.clip(_inner(sample).reshape(-1, 3), 0, 1) ** lambdas, 0, None) ** exponents
    over = np.quantile(raw.max(1), 1 - MAX_CLIPPED)
    if over > 1:
        scale = 1 / over

    # Author's rule: raise HSV saturation so that mean + 2 std reaches 150/255, at most x1.5.
    restored = np.clip(raw * scale, 0, 1)
    value = restored.max(1)
    hsv_saturation = 255 * (value - restored.min(1)) / np.maximum(value, 1e-6)
    high = hsv_saturation.mean() + 2 * hsv_saturation.std()
    boost = float(np.clip(150 / max(high, 1e-3), 1.0, MAX_SATURATION_BOOST))
    return {"lambda": lambdas.tolist(), "gain": (gains * scale ** (1 / exponents)).tolist(),
            "gamma": exponents.tolist(), "saturation": boost}


def lookup_tables(params: dict, levels: int = 256) -> np.ndarray:
    """Per-channel curves as a (levels, 3) table of floats 0..1."""
    v = np.linspace(0, 1, levels)[:, None]
    gains, lambdas, exponents = (np.array(params[k]) for k in ("gain", "lambda", "gamma"))
    return np.clip((gains * v ** lambdas) ** exponents, 0, 1)


def apply(image: np.ndarray, params: dict) -> np.ndarray:
    """Apply to an 8-bit RGB array; returns float RGB 0..1."""
    table = lookup_tables(params).astype(np.float32)
    out = np.empty(image.shape, np.float32)
    for c in range(3):
        np.take(table[:, c], image[..., c], out=out[..., c])
    k = np.float32(params["saturation"])
    if k == 1:
        return out
    # HSV saturation times k with V kept: out = V - k * (V - out), written in place.
    value = out.max(-1)
    value *= 1 - k
    out *= k
    out += value[..., None]
    return np.clip(out, 0, 1, out=out)


def correct(image: np.ndarray, sample_size: int = 300) -> tuple[np.ndarray, dict]:
    """Fit on a small copy and apply to `image` (8-bit RGB array)."""
    h, w = image.shape[:2]
    step = max(1, int(max(h, w) / sample_size))
    params = fit(image[::step, ::step].astype(np.float64) / 255)
    return apply(image, params), params
