#!/usr/bin/env python3
"""Recompute a corrected copy from an archived original and its recipe.

    python code/recompute.py originals/K001-0001.jpg                  # every copy listed for it
    python code/recompute.py originals/K001-0001.jpg --copy <UUID> --out copy.png
    python code/recompute.py originals/K001-0001.jpg --check K001-0001.jxl

The recipe comes from the original's JSON sidecar (<name>.json). It names the code that made the
copy; that code is loaded from code/unfade-<code id>/ in the archive, so the copy is recomputed
with exactly the arithmetic that made it. Needs Python 3 with numpy, pillow, pillow-heif and
imagecodecs (the versions are in that folder's uv.lock). The result is written as a lossless
16-bit PNG; --check compares its pixels with an existing copy instead.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

STRIP_ROWS = 512


def load_code(code_dir: Path):
    """Import unfade.color_methods, render and encode from one archived code snapshot."""
    for name in [module for module in sys.modules if module == "unfade" or module.startswith("unfade.")]:
        del sys.modules[name]
    sys.path.insert(0, str(code_dir))
    try:
        return (importlib.import_module("unfade.color_methods"), importlib.import_module("unfade.render"),
                importlib.import_module("unfade.encode"))
    finally:
        sys.path.remove(str(code_dir))


def recompute(original: Path, recipe: dict, code_dir: Path):
    """The copy's pixels: 16-bit RGB, upright, as the export computed them."""
    import numpy as np
    color_methods, render, _ = load_code(code_dir)
    pixels, _ = render.read_full(original)
    levels = 65536 if pixels.dtype == np.uint16 else 256
    fitted = {"table": color_methods.tables(recipe["curve"], levels), "saturation": recipe["curve"]["saturation"]}
    out = np.empty(pixels.shape, np.uint16)
    for top in range(0, pixels.shape[0], STRIP_ROWS):
        strip = color_methods.apply(pixels[top:top + STRIP_ROWS], fitted, recipe["adjustments"])
        out[top:top + STRIP_ROWS] = (strip * 65535 + 0.5).astype(np.uint16)
    return out


def decode_copy(path: Path):
    """A copy's pixels as 16-bit RGB (JPEG XL and PNG copies are 16-bit; a JPEG copy is 8-bit)."""
    import imagecodecs
    import numpy as np
    pixels = imagecodecs.imread(str(path))[..., :3]
    return pixels if pixels.dtype == np.uint16 else pixels.astype(np.uint16) * 257


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute a corrected copy from an archived original")
    parser.add_argument("original", type=Path)
    parser.add_argument("--copy", help="UUID of the copy (default: every copy listed for the original)")
    parser.add_argument("--out", type=Path, help="where to write the recomputed copy (16-bit PNG)")
    parser.add_argument("--check", type=Path, help="an existing copy to compare the recomputed pixels with")
    parser.add_argument("--code", type=Path, help="folder holding the code snapshots (default: next to this script)")
    args = parser.parse_args(argv)

    record = json.loads(args.original.with_name(args.original.name + ".json").read_text(encoding="utf-8"))
    copies = [copy for copy in record["copies"] if not args.copy or copy["uuid"] == args.copy]
    if not copies:
        print("No such copy in the original's record", file=sys.stderr)
        return 2
    if args.check and len(copies) > 1:
        print("Several copies are listed: name the one to check with --copy", file=sys.stderr)
        return 2
    code_root = args.code or Path(__file__).resolve().parent
    status = 0
    for copy in copies:
        recipe = copy["recipe"]
        code_dir = code_root / f"unfade-{recipe['code']['id']}"
        if not code_dir.is_dir():
            print(f"{copy['uuid']}: code {recipe['code']['id']} is not in {code_root}", file=sys.stderr)
            status = 1
            continue
        pixels = recompute(args.original, recipe, code_dir)
        if args.check:
            import numpy as np
            same = np.array_equal(pixels, decode_copy(args.check))
            print(f"{copy['uuid']}: {'same pixels' if same else 'DIFFERENT pixels'}")
            status = status or (0 if same else 1)
        elif args.out:
            _, _, encode = load_code(code_dir)
            out = args.out if len(copies) == 1 else args.out.with_name(f"{args.out.stem}-{copy['uuid']}{args.out.suffix}")
            out.write_bytes(encode.png(pixels, None, None, None))
            print(f"{copy['uuid']}: wrote {out}")
        else:
            print(f"{copy['uuid']}: recomputed {pixels.shape[1]}x{pixels.shape[0]} (use --out or --check)")
    return status


if __name__ == "__main__":
    sys.exit(main())
