"""Record docs/demo.webp, the animation in the README: a run through the web UI.

    uv run --group e2e python docs/make_demo.py [--chromium /path/to/chrome]

The photos in docs/demo-photos (CC0, see CREDITS.md) are first faded the way colour film fades,
a power curve per channel with a lifted black and a lowered white, so the demo is the same
every time and needs nobody's own pictures.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PHOTOS = ROOT / "docs" / "demo-photos"
WIDTH, HEIGHT = 1280, 1000
OUT = (960, 600)
BACKGROUND = (242, 241, 235)
# Red and magenta gain, cyan and yellow fade: the usual look of old colour prints and film.
CAST = (.7, 1.3, 1.5)


def faded(path: Path) -> Image.Image:
    pixels = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255
    return Image.fromarray(((.10 + pixels ** np.array(CAST) * .75) * 255 + .5).astype(np.uint8))


def make_photos(folder: Path) -> None:
    # Two rolls, so the suggestion makes two groups: one suits FADE, the other GWG.
    for roll, prefix in (("1978 mountains", "mountains"), ("1981 coast", "coast")):
        (folder / roll).mkdir(parents=True)
        for number, path in enumerate(sorted(PHOTOS.glob(f"{prefix}-*.jpg")), 1):
            faded(path).save(folder / roll / f"IMG_{number:04d}.jpg", quality=92)


def frame(page, frames: list, duration: int, *selectors: str) -> None:
    """One frame: the viewport, or just the box around `selectors`, fitted onto the output size."""
    page.wait_for_timeout(300)
    clip = None
    if selectors:
        boxes = [page.locator(selector).first.bounding_box() for selector in selectors]
        left, top = min(box["x"] for box in boxes) - 12, min(box["y"] for box in boxes) - 12
        right = max(box["x"] + box["width"] for box in boxes) + 12
        bottom = max(box["y"] + box["height"] for box in boxes) + 12
        clip = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    shot = Image.open(io.BytesIO(page.screenshot(clip=clip))).convert("RGB")
    shot.thumbnail(OUT, Image.LANCZOS)
    canvas = Image.new("RGB", OUT, BACKGROUND)
    canvas.paste(shot, ((OUT[0] - shot.width) // 2, (OUT[1] - shot.height) // 2))
    frames.append((canvas, duration))


# Before and after: the cards of the group and the large preview of the selected image.
COMPARE = (".view-toggles", ".correction-grid", "#detail-canvas")


def show_correction(page) -> None:
    page.evaluate("document.querySelector('.correction-main').scrollIntoView({block: 'start'})")
    page.wait_for_function("[...document.querySelectorAll('.clip-badge')].every(b => b.textContent.includes('clips'))", timeout=60000)
    page.wait_for_function("document.querySelector('[data-delta-e-original]').textContent.includes('.')", timeout=60000)


def record(url: str, photos: Path, chromium: str | None) -> list:
    frames: list = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, locale="en-GB")
        page.goto(url)
        page.fill("#source-path", str(photos))
        page.check("input[name=source_recursive]")
        page.fill("#destination-path", str(photos.parent / "corrected"))
        frame(page, frames, 2000)
        page.click("#job-form > button")
        page.wait_for_url("**/jobs/*")
        page.wait_for_function("[...document.querySelectorAll('.thumbnail-open img')].every(i => i.complete && i.naturalWidth)")
        frame(page, frames, 2200)
        # The mountains with FADE, the default: faded original, then corrected, twice.
        show_correction(page)
        for _ in range(2):
            page.check("#show-original")
            frame(page, frames, 1200, *COMPARE)
            page.uncheck("#show-original")
            frame(page, frames, 1800, *COMPARE)
        page.click("form[action$='/approve'] button")
        page.wait_for_url("**/jobs/*")
        # The coast: FADE leaves a yellow cast here, GWG balances it.
        show_correction(page)
        page.check("#show-original")
        frame(page, frames, 1200, *COMPARE)
        page.uncheck("#show-original")
        frame(page, frames, 1500, ".method-options", *COMPARE)
        page.check("input[name=method][value=gwg]")
        page.wait_for_url("**saved=method**")
        show_correction(page)
        frame(page, frames, 2200, ".method-options", *COMPARE)
        page.click("form[action$='/approve'] button")
        page.wait_for_url("**saved=all**")
        page.click("#export button")
        page.wait_for_function("document.querySelector('#export-status').textContent.includes('finished')", timeout=120000)
        frame(page, frames, 3000, "#export")
        browser.close()
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chromium", default=os.environ.get("UNFADE_E2E_CHROMIUM"), help="an existing Chromium to drive")
    parser.add_argument("--out", default=str(ROOT / "docs" / "demo.webp"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        photos = Path(temp) / "photos"
        make_photos(photos)
        port = 8765
        env = {**os.environ, "UNFADE_DATA_DIR": str(Path(temp) / "data"), "UNFADE_ROOTS": temp, "PYTHONPATH": str(ROOT)}
        env.pop("IMMICH_URL", None)
        server = subprocess.Popen([sys.executable, "-m", "unfade", "serve", "--port", str(port)], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    httpx2.get(f"http://127.0.0.1:{port}", timeout=2)
                    break
                except httpx2.HTTPError:
                    time.sleep(.2)
            frames = record(f"http://127.0.0.1:{port}", photos, args.chromium)
        finally:
            server.terminate()
            server.wait(10)
    images = [image for image, _ in frames]
    images[0].save(args.out, save_all=True, append_images=images[1:], duration=[duration for _, duration in frames],
                   loop=0, quality=80, method=6)
    print(f"{args.out}: {len(images)} frames, {Path(args.out).stat().st_size // 1024} KiB")


if __name__ == "__main__":
    main()
