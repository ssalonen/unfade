"""The archive of originals against a real, throwaway Immich: originals byte for byte (also from
the trash), Immich's metadata as sidecars that Immich itself reads back, and copies that the
archive alone recomputes."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import httpx
import pytest

from unfade import archive
from unfade.__main__ import main
from e2e import immich_harness as harness


@pytest.fixture
def library(unfade_env, admin):
    """Three faded photos in tag Kuvat/K001; the first also tagged Mökki, in an album, with details and a named face."""
    api = unfade_env.client()
    ids = [harness.upload(api, f"K001-000{n}.jpg", harness.faded_photo(n, taken=datetime(1975, 6, 1, 12))) for n in (1, 2, 3)]
    harness.wait_for_jobs(unfade_env.url, admin)
    harness.tag_each(api, {ids[0]: ["Kuvat/K001", "Mökki"], ids[1]: ["Kuvat/K001"], ids[2]: ["Kuvat/K001"]})
    harness.album(api, "Kesä 1975", ids[:2])
    api.put(f"/assets/{ids[0]}", json={"description": "Mökillä", "rating": 4, "isFavorite": True, "latitude": 61.5, "longitude": 23.8,
                                       "dateTimeOriginal": "1975-06-01T14:30:00.000+02:00"}).raise_for_status()
    person = api.post("/people", json={"name": "Mummo"}).raise_for_status().json()
    api.post("/faces", json={"assetId": ids[0], "personId": person["id"], "imageWidth": 480, "imageHeight": 320,
                             "x": 100, "y": 60, "width": 80, "height": 90}).raise_for_status()
    harness.wait_for_jobs(unfade_env.url, admin)
    return {"api": api, "originals": ids, "url": unfade_env.url, "admin": admin}


def record(target, name):
    return json.loads((target / "originals" / f"{name}.json").read_text(encoding="utf-8"))


def test_archive_after_stacking_keeps_originals_metadata_and_recipes(library, tmp_path):
    api, originals = library["api"], library["originals"]
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "Kuvat/Korjattu"]) == 0
    harness.wait_for_jobs(library["url"], library["admin"])
    target = tmp_path / "archive"
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0

    # Flat, original names; the copies (also under the tag) are not archived as originals.
    stored = sorted(path.name for path in (target / "originals").iterdir() if path.suffix == ".jpg")
    assert stored == ["K001-0001.jpg", "K001-0002.jpg", "K001-0003.jpg"]
    first = record(target, "K001-0001.jpg")
    asset = api.get(f"/assets/{originals[0]}").json()
    original = api.get(f"/assets/{originals[0]}/original").content
    assert (target / "originals" / "K001-0001.jpg").read_bytes() == original
    assert first["sha1"] == base64.b64decode(asset["checksum"]).hex() == hashlib.sha1(original).hexdigest()
    assert first["immich"]["albums"][0]["name"] == "Kesä 1975" and first["immich"]["favorite"] is True
    assert [face["person"]["name"] for face in first["immich"]["faces"]] == ["Mummo"]
    assert sorted(tag["value"] for tag in first["immich"]["asset"]["tags"]) == ["Kuvat/K001", "Mökki"]

    # The copy is referenced with its Immich id, digest and recipe, and the archive alone rebuilds it.
    [copy] = first["copies"]
    stored_copy = api.get(f"/assets/{copy['immich_asset']}/original").content
    assert copy["sha256"] == hashlib.sha256(stored_copy).hexdigest() and copy["recipe"]["method"] == "fade"
    (tmp_path / "copy.jxl").write_bytes(stored_copy)
    check = subprocess.run([sys.executable, "-I", str(target / "code" / "recompute.py"), str(target / "originals" / "K001-0001.jpg"),
                            "--check", str(tmp_path / "copy.jxl")], capture_output=True, text=True)
    assert check.returncode == 0 and "same pixels" in check.stdout, check.stderr
    assert archive.verify(str(target)) == []
    index = (target / "index.html").read_text(encoding="utf-8")
    assert "Mökki" in index and "Kesä 1975" in index and "1975" in index

    # A second run adds nothing and keeps every original byte for byte.
    before = {path: path.read_bytes() for path in (target / "originals").glob("*.jpg")}
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0
    assert {path: path.read_bytes() for path in (target / "originals").glob("*.jpg")} == before


def test_archive_includes_originals_already_in_the_trash(library, tmp_path):
    api, originals = library["api"], library["originals"]
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "K", "--replace-original"]) == 0
    harness.wait_for_jobs(library["url"], library["admin"])
    assert all(api.get(f"/assets/{asset_id}").json()["isTrashed"] for asset_id in originals)
    target = tmp_path / "archive"
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0
    first = record(target, "K001-0001.jpg")
    assert first["immich"]["trashed"] is True and (target / "originals" / "K001-0001.jpg").read_bytes() == api.get(f"/assets/{originals[0]}/original").content
    [copy] = first["copies"]
    assert copy["trashed"] is False and copy["immich_asset"] != originals[0]


def test_immich_reads_the_archived_sidecar_back(library, tmp_path, immich_url, admin):
    target = tmp_path / "archive"
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0
    headers = {"Authorization": f"Bearer {admin}"}
    config = httpx.get(f"{immich_url}/api/system-config", headers=headers).json()
    previous = config["metadata"]["faces"]["import"]
    config["metadata"]["faces"]["import"] = True
    httpx.put(f"{immich_url}/api/system-config", headers=headers, json=config).raise_for_status()
    try:
        fresh = harness.new_account(immich_url, admin).client()
        original = target / "originals" / "K001-0001.jpg"
        now = datetime.now(timezone.utc).isoformat()
        uploaded = fresh.post("/assets", data={"deviceAssetId": "archive", "deviceId": "archive", "fileCreatedAt": now, "fileModifiedAt": now},
                              files={"assetData": (original.name, original.read_bytes()),
                                     "sidecarData": (original.name + ".xmp", (target / "originals" / "K001-0001.jpg.xmp").read_bytes())})
        asset_id = uploaded.raise_for_status().json()["id"]
        harness.wait_for_jobs(immich_url, admin)
        asset = fresh.get(f"/assets/{asset_id}").json()
        exif = asset["exifInfo"]
        assert (exif["description"], exif["rating"], exif["latitude"], exif["longitude"]) == ("Mökillä", 4, 61.5, 23.8)
        assert exif["dateTimeOriginal"].startswith("1975-06-01T12:30:00") and exif["timeZone"] == "Europe/Helsinki"
        assert sorted(tag["value"] for tag in asset["tags"]) == ["Kuvat/K001", "Mökki"]
        assert [face["person"]["name"] for face in fresh.get("/faces", params={"id": asset_id}).json()] == ["Mummo"]
    finally:
        config["metadata"]["faces"]["import"] = previous
        httpx.put(f"{immich_url}/api/system-config", headers=headers, json=config).raise_for_status()


def test_archive_index_in_the_browser(library, tmp_path, artifacts):
    playwright = pytest.importorskip("playwright.sync_api")
    target = tmp_path / "archive"
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.goto((target / "index.html").as_uri())
        page.screenshot(path=f"{artifacts}/index.png", full_page=True)
        assert page.locator("figure img").count() >= 3
        assert page.locator("img").evaluate_all("images => images.every(image => image.naturalWidth > 0)")
        # One view at a time, picked with the buttons; the choice is kept in the address.
        page.get_by_role("button", name="Albumit (1)").click()
        assert page.locator("#albumit").is_visible() and not page.locator("#tunnisteet").is_visible()
        assert page.get_by_role("button", name="Albumit (1)").get_attribute("aria-pressed") == "true" and page.url.endswith("#albumit")
        page.screenshot(path=f"{artifacts}/index-albums.png", full_page=True)
        page.get_by_role("button", name="Tunnisteet (2)").click()
        assert page.locator("#tunnisteet").is_visible() and not page.locator("#albumit").is_visible()
        page.reload()
        assert page.locator("#tunnisteet").is_visible() and not page.locator("#kaikki").is_visible()
        browser.close()
