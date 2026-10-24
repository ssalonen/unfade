"""The browser's live slider preview must compute the same adjustments as the server, apart from rounding.

The browser gets the server's 8-bit `?view=corrected` and `?view=original` previews and runs
adjustedPixels() from unfade/static/correct.js on them. Here that very function runs under Node
on the raw arrays the server would encode (JPEG error is left out on purpose) and is compared with
`?view=adjusted`, i.e. color_methods.apply() with the adjustments, rounded the same way.
"""
import base64
import itertools
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from unfade import color_methods

SCRIPT = Path(__file__).resolve().parents[1] / "unfade" / "static" / "correct.js"
NODE = shutil.which("node") or shutil.which("/opt/node22/bin/node")
RUNNER = """
const {adjustedPixels} = require(process.argv[1]);
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const image = (b64) => ({data: new Uint8ClampedArray(Buffer.from(b64, 'base64'))});
const corrected = image(input.corrected), original = image(input.original);
const results = input.adjustments.map(a => Buffer.from(adjustedPixels(corrected, original, a)).toString('base64'));
process.stdout.write(JSON.stringify(results));
"""

EXTREMES = [
    dict(zip(("strength", "black", "white", "temperature"), values))
    for values in itertools.product((0, 50, 100), (-20, 0, 20), (-20, 0, 20), (-20, 0, 20))
]


def adjustment_sets(seed):
    rng = np.random.default_rng(seed)
    random = [
        {"strength": int(rng.integers(0, 101)), **{name: int(rng.integers(-20, 21)) for name in ("black", "white", "temperature")}}
        for _ in range(30)
    ]
    return EXTREMES + random


def to_8bit(values):
    # As preview_bytes() does before encoding.
    return (values * 255 + 0.5).astype(np.uint8)


def rgba(image):
    return np.concatenate([image, np.full(image.shape[:2] + (1,), 255, np.uint8)], -1)


def browser(corrected, original, adjustments):
    payload = {
        "corrected": base64.b64encode(rgba(corrected).tobytes()).decode(),
        "original": base64.b64encode(rgba(original).tobytes()).decode(),
        "adjustments": adjustments,
    }
    assert NODE is not None, "Node.js is not installed"
    done = subprocess.run([NODE, "-e", RUNNER, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True, check=True)
    return [np.frombuffer(base64.b64decode(item), np.uint8).reshape(corrected.shape[:2] + (4,))[..., :3] for item in json.loads(done.stdout)]


def faded_gradient():
    """A low-contrast gradient with a magenta cast and two dull patches, like a faded photo.

    Dull enough that FADE boosts the saturation (about x1.33). Values the boost pushes below 0
    need a more saturated image; the random-table case with x1.5 covers those.
    """
    x = np.linspace(0, 1, 96)[None, :, None]
    y = np.linspace(0, 1, 64)[:, None, None]
    image = np.broadcast_to(0.25 + 0.45 * x * (0.7 + 0.3 * y) * np.array([1.0, 0.75, 0.9]), (64, 96, 3)).copy()
    image[20:40, 30:50] = [0.55, 0.50, 0.45]
    image[40:56, 60:80] = [0.40, 0.45, 0.50]
    return (image * 255 + 0.5).astype(np.uint8)


def cases():
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    identity = np.repeat(np.linspace(0, 1, 256)[:, None], 3, 1)
    random_table = np.sort(rng.random((256, 3)), axis=0)
    yield "identity", noise, {"table": identity, "saturation": 1.0}
    yield "random-table", noise, {"table": random_table, "saturation": 1.0}
    yield "random-table-saturation", noise, {"table": random_table, "saturation": 1.5}
    gradient = faded_gradient()
    for method in color_methods.METHODS:
        yield f"gradient-{method}", gradient, color_methods.fit(method, gradient.astype(np.float64) / 255)


def test_gradient_exercises_the_saturation_boost():
    gradient = faded_gradient()
    assert color_methods.fit("fade", gradient.astype(np.float64) / 255)["saturation"] > 1


# Locally the check is skipped without Node; in CI a missing Node must fail instead of hiding it.
@pytest.mark.skipif(NODE is None and not os.environ.get("CI"), reason="Node.js is not installed")
@pytest.mark.parametrize("name, original, fitted", list(cases()), ids=lambda value: value if isinstance(value, str) else "")
def test_browser_preview_matches_server_adjustments(name, original, fitted):
    # The browser mixes with the original preview just like apply() mixes with its input image,
    # so apply() on the original is the reference for both inputs the browser receives.
    corrected = to_8bit(color_methods.apply(original, fitted))
    adjustments = adjustment_sets(len(name))
    worst = 0
    for a, result in zip(adjustments, browser(corrected, original, adjustments), strict=True):
        expected = to_8bit(color_methods.apply(original, fitted, a))
        difference = int(np.abs(expected.astype(int) - result.astype(int)).max())
        worst = max(worst, difference)
        assert difference <= 1, f"{name} {a}: browser differs from server by {difference}/255"
    print(f"{name}: max difference {worst}/255 over {len(adjustments)} adjustment sets")
