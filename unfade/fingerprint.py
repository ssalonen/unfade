"""Identifiers for finding an original and its corrected copies later.

- SHA-1 of the file, as Immich keeps it (base64 there, hex here), so an unchanged original is one
  checksum search away in Immich.
- pHash and dHash of the whole upright image: 64-bit perceptual hashes compared by Hamming
  distance, which survive re-encoding, resizing, bit depth and moderate tone changes. They are
  computed as the imagehash library computes `phash` and `dhash` (Pillow "L" luma, Lanczos
  resize, same bit order and hex form), so other tools can check them.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hex(bits: np.ndarray) -> str:
    return f"{int(''.join('1' if bit else '0' for bit in bits.ravel()), 2):016x}"


def _dct_matrix(size: int) -> np.ndarray:
    n = np.arange(size)
    return np.cos(np.pi * (2 * n[None, :] + 1) * n[:, None] / (2 * size))


_DCT32 = _dct_matrix(32)


def _gray(pixels: np.ndarray) -> Image.Image:
    if pixels.dtype == np.uint16:
        pixels = ((pixels.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    return Image.fromarray(np.ascontiguousarray(pixels)).convert("L")


def phash(pixels: np.ndarray) -> str:
    """DCT hash: low 8×8 frequencies of a 32×32 luma copy against their median."""
    small = np.asarray(_gray(pixels).resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT32 @ small @ _DCT32.T)[:8, :8]
    return _hex(low > np.median(low))


def dhash(pixels: np.ndarray) -> str:
    """Gradient hash: is each pixel of a 9×8 luma copy brighter than its left neighbour."""
    small = np.asarray(_gray(pixels).resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    return _hex(small[:, 1:] > small[:, :-1])


def distance(a: str, b: str) -> int:
    """Hamming distance between two hex hashes of the same length."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")
