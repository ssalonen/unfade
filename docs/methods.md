# Correction methods

Every method is fitted on one image at a time on a fixed-size 8-bit copy. Statistics come from the inner 80 % of the frame, so frames, borders and captions do not steer the correction. A method reduces to a lookup table per channel plus an optional saturation factor. The fitted parameters are stored in the recipe, and the tables are rebuilt from them at 16 bits for export.

## FADE: dye-fade model (default)

Implemented from Geoff Daniell's paper *Restoration of Faded Slides II* (2016) and its appendix, published with the [PhotoRestore](https://github.com/stongey/PhotoRestore) project.

- **Model:** a faded channel is C' = C^a, so restoration is a power curve per channel, f(v) = k·v^λ.
- **Fitting:** the parameters are fitted to the image's distinct colours sorted by luminance, which an undamaged photo is assumed to spread evenly.
- **Grey balance** uses only near-grey colours.
- **Saturation** is then boosted.

This is an independent implementation written from the paper's description. PhotoRestore's GPL-3.0 code was not used. Its `pyrestore3.py` served only as a reference to compare results against. Differences from the paper, for speed and robustness:

- The palette is built from numpy histograms instead of an octree.
- λ comes from a vectorised grid search, with σ solved in closed form.
- Grey balance adjusts the red and blue gammas, with the endpoints fixed. It uses a coarse-to-fine grid that prefers the solution closest to no change.
- The overall scale is capped so that at most 0.5 % of the inner area clips.

The model goes back to accelerated-ageing measurements by Frey, Gschwind and Rosenthaler (1996), [CIC 4](https://library.imaging.org/cic/articles/4/1/art00012).

## GWG: stretch and grey-world gamma

A per-channel stretch between the 0.5th and 99.5th percentiles, then a gamma per channel that brings the channel means together (the grey-world assumption). It neutralises any cast, including a real one such as a sunset.

## PCS: per-channel stretch

The same stretch without grey balancing, after Limare et al., [*Simplest Color Balance*](https://www.ipol.im/pub/art/2011/llmps-scb), IPOL 2011. It is the most predictable method and keeps the original's warmth.

## Adjustments

Strength mixes the correction with the original. Black point, white point and temperature are applied on top. The browser's live preview (`unfade/static/correct.js`) runs the same arithmetic as `unfade/color_methods.py`, and `tests/test_preview_parity.py` checks that the two agree.

## ΔE

The comparison panel reports the mean CIE76 difference in CIE Lab (D65) between each method and the original, and between methods.

## Fingerprints

pHash and dHash are computed as the [imagehash](https://github.com/JohannesBuchner/imagehash) library (BSD-2-Clause) does, so its values can be compared directly.
