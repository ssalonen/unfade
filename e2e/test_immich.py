"""unfade against a real, throwaway Immich: what the copies, originals, stacks, albums, tags and
metadata look like on the server afterwards. Each test has its own Immich user."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import imagecodecs
import numpy as np
import pytest

from unfade import color_methods, encode, jobs, render
from unfade.__main__ import main
from e2e import immich_harness as harness

DETAILS = {"description": "Mökillä", "rating": 4, "isFavorite": True, "latitude": 61.5, "longitude": 23.8,
           "dateTimeOriginal": "1975-06-01T14:30:00.000+02:00"}


@pytest.fixture
def library(unfade_env, admin):
    """Two faded photos in tag Kuvat/K001; the first also in an album, with details set in Immich."""
    api = unfade_env.client()
    ids = [harness.upload(api, f"K001-000{n}.jpg", harness.faded_photo(n, taken=datetime(1975, 6, 1, 12))) for n in (1, 2)]
    harness.wait_for_jobs(unfade_env.url, admin)
    harness.tag(api, "Kuvat/K001", ids)
    album_id = harness.album(api, "Kesä 1975", [ids[0]])
    api.put(f"/assets/{ids[0]}", json=DETAILS).raise_for_status()
    harness.wait_for_jobs(unfade_env.url, admin)
    return {"api": api, "originals": ids, "album": album_id, "url": unfade_env.url, "admin": admin}


def run(*args: str) -> dict:
    assert main(["run", "--source", "immich-tag:Kuvat/K001", *args]) == 0
    return jobs.list_jobs()[0]


def settle(library):
    harness.wait_for_jobs(library["url"], library["admin"])


def copies_of(job) -> list[str]:
    """The uploaded copies, in the order of the originals' file names (K001-0001, K001-0002)."""
    return [item["exported"]["target"] for item in sorted(job["items"], key=lambda item: item["name"])]


def test_stack_mode_copy_carries_everything(library):
    api, originals = library["api"], library["originals"]
    job = run("--dest", "immich", "--dest-tag", "Kuvat/Korjattu")
    settle(library)
    assert job["export"]["errors"] == [] and job["export"]["warnings"] == []
    copy_id = copies_of(job)[0]
    copy = api.get(f"/assets/{copy_id}").json()

    # Immich takes the lossless JPEG XL, reads the EXIF from its container and renders a preview.
    assert copy["originalFileName"] == "K001-0001.jxl" and copy["originalMimeType"] == "image/jxl"
    assert copy["exifInfo"]["make"] == "Testiskanneri" and copy["exifInfo"]["exifImageWidth"] == 480
    preview = api.get(f"/assets/{copy_id}/thumbnail", params={"size": "preview"})
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("image/")

    # Stacked with the original, the copy on top.
    stack = api.get(f"/stacks/{copy['stack']['id']}").json()
    assert stack["primaryAssetId"] == copy_id and {asset["id"] for asset in stack["assets"]} == {copy_id, originals[0]}

    # The original's Immich-side details, tags and album, plus the destination tag.
    exif = copy["exifInfo"]
    assert (exif["description"], exif["rating"], copy["isFavorite"]) == ("Mökillä", 4, True)
    assert (exif["latitude"], exif["longitude"]) == (61.5, 23.8)
    assert exif["dateTimeOriginal"].startswith("1975-06-01T12:30:00") and exif["timeZone"] == "Europe/Helsinki"
    assert {tag["value"] for tag in copy["tags"]} == {"Kuvat/K001", "Kuvat/Korjattu"}
    assert [album["id"] for album in api.get("/albums", params={"assetId": copy_id}).json()] == [library["album"]]

    # UUIDs in Immich's metadata: the original keeps its own, the copy names it.
    original_meta = api.get(f"/assets/{originals[0]}/metadata/unfade").json()["value"]
    copy_meta = api.get(f"/assets/{copy_id}/metadata/unfade").json()["value"]
    assert copy_meta["source"]["uuid"] == original_meta["uuid"] and copy_meta["source"]["immich_asset"] == originals[0]


def test_copy_downloaded_from_immich_recomputes_from_its_recipe(library, tmp_path):
    api, originals = library["api"], library["originals"]
    job = run("--dest", "immich", "--dest-tag", "K", "--method", "gwg")
    stored = api.get(f"/assets/{copies_of(job)[0]}/original").content
    original = api.get(f"/assets/{originals[0]}/original").content
    recipe = encode.read_recipe(box(stored, b"xml "))
    assert recipe["Method"] == "gwg" and recipe["SourceImmichAsset"] == originals[0]
    assert recipe["SourceSHA256"] == hashlib.sha256(original).hexdigest() and recipe["SourceSHA1"] == hashlib.sha1(original).hexdigest()
    # The original as Immich serves it, plus the recipe, gives the stored copy pixel for pixel.
    (tmp_path / "original.jpg").write_bytes(original)
    decoded, _ = render.read_full(tmp_path / "original.jpg")
    fitted = {"table": color_methods.tables(recipe["Curve"], 256), "saturation": recipe["Curve"]["saturation"]}
    again = (color_methods.apply(decoded, fitted, recipe["Adjustments"]) * 65535 + 0.5).astype(np.uint16)
    pixels = imagecodecs.jpegxl_decode(stored)
    assert pixels.dtype == np.uint16 and np.array_equal(again, pixels)


def test_second_run_skips_copies_merges_the_stack_and_replaces_the_earlier_copy(library):
    api, originals = library["api"], library["originals"]
    first = copies_of(run("--dest", "immich", "--dest-tag", "K"))
    settle(library)
    second_job = run("--dest", "immich", "--dest-tag", "K", "--replace-previous")
    settle(library)
    # The copies inherited tag Kuvat/K001; they are recognised and not corrected again.
    assert second_job["skipped_copies"] == 2 and sorted(item["key"] for item in second_job["items"]) == sorted(originals)
    second = copies_of(second_job)
    assert all(api.get(f"/assets/{asset_id}").json()["isTrashed"] for asset_id in first)
    stack = api.get(f"/stacks/{api.get(f'/assets/{second[0]}').json()['stack']['id']}").json()
    assert stack["primaryAssetId"] == second[0] and {asset["id"] for asset in stack["assets"]} == {second[0], originals[0]}


def test_replace_mode_hands_over_and_trashes_the_original(library):
    api, originals = library["api"], library["originals"]
    link = api.post("/shared-links", json={"type": "INDIVIDUAL", "assetIds": [originals[0]]}).raise_for_status().json()
    job = run("--dest", "immich", "--dest-tag", "K", "--replace-original")
    settle(library)
    assert job["export"]["errors"] == [] and job["export"]["warnings"] == []
    copy_ids = copies_of(job)
    assert all(api.get(f"/assets/{asset_id}").json()["isTrashed"] for asset_id in originals)
    copy = api.get(f"/assets/{copy_ids[0]}").json()
    assert not copy["isTrashed"] and copy["isFavorite"] and copy["exifInfo"]["description"] == "Mökillä"
    assert [album["id"] for album in api.get("/albums", params={"assetId": copy_ids[0]}).json()] == [library["album"]]
    shared = api.get(f"/shared-links/{link['id']}").json()
    assert copy_ids[0] in {asset["id"] for asset in shared["assets"]}
    # Only copies are left under the tag, so there is nothing more to correct.
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "K"]) == 2


def test_immich_folder_source_to_local_lossless_files(library, tmp_path):
    api, originals = library["api"], library["originals"]
    folder = str(Path(api.get(f"/assets/{originals[0]}").json()["originalPath"]).parent)
    out = tmp_path / "korjatut"
    assert main(["run", "--source", f"immich-folder:{folder}", "--dest", f"local:{out}"]) == 0
    written = sorted(out.iterdir())
    assert [path.suffix for path in written] == [".jxl"] * len(written) and written
    recipe = encode.read_recipe(box(written[0].read_bytes(), b"xml "))
    assert recipe["SourceImmichAsset"] in originals


def box(content: bytes, kind: bytes) -> bytes:
    """One box of a JPEG XL container."""
    position = 0
    while position < len(content):
        size = int.from_bytes(content[position:position + 4], "big")
        if content[position + 4:position + 8] == kind:
            return content[position + 8:position + size]
        position += size
    raise KeyError(kind)
