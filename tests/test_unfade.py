"""Standalone colour correction: local folders and Immich as source and destination."""
import base64
import hashlib
import io
import json
import re
import struct

import httpx
import imagecodecs
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms, ImageOps

from unfade import color_methods, encode, fingerprint, immich, jobs, locations, render
from unfade.__main__ import main
from unfade.web import create_app


def faded(color, size=(60, 40)):
    ramp = np.linspace(0.35, 0.75, size[0])[None, :, None] * np.linspace(0.8, 1.0, size[1])[:, None, None]
    return Image.fromarray((ramp * np.array(color) * 2).clip(0, 255).astype(np.uint8))


def jpeg(color, orientation=None):
    image = faded(color)
    exif = Image.Exif()
    exif[0x010F] = "Scanner Oy"
    if orientation:
        exif[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    """Retry pauses of milliseconds, so tests of dropped connections run fast."""
    monkeypatch.setattr(immich, "RETRY_BASE", 0.001)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("UNFADE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("UNFADE_ROOTS", str(tmp_path / "files"))
    monkeypatch.delenv("IMMICH_URL", raising=False)
    monkeypatch.delenv("IMMICH_API_KEY", raising=False)
    source = tmp_path / "files" / "scans"
    (source / "sub").mkdir(parents=True)
    (source / "A001.jpg").write_bytes(jpeg((100, 90, 80), orientation=6))
    (source / "A002.jpg").write_bytes(jpeg((110, 95, 75)))
    faded((80, 90, 120)).save(source / "sub" / "B001.png")
    (source / "notes.txt").write_text("not an image")
    return tmp_path


class FakeImmich:
    """Just enough of the Immich API, recording what was uploaded, tagged and added to albums."""

    def __init__(self):
        self.originals = {f"0000000{n}-0000-4000-8000-000000000000": jpeg(color) for n, color in enumerate(((100, 90, 80), (90, 100, 80)), 1)}
        ids = list(self.originals)
        self.assets = [{"id": asset_id, "type": "IMAGE", "originalFileName": f"D{n}.jpg", "originalPath": f"/photos/vanhat/D{n}.jpg",
                        "checksum": base64.b64encode(hashlib.sha1(self.originals[asset_id]).digest()).decode(),
                        "fileCreatedAt": "1975-06-01T12:00:00.000Z", "isTrashed": False}
                       for n, asset_id in enumerate(ids, 1)]
        self.tags = [{"id": "tag-source", "name": "K001", "value": "Kuvat/K001"}]
        self.albums = [{"id": "album-1", "albumName": "Kuvat", "assetCount": 0}, {"id": "album-2", "albumName": "Kesä 1975", "assetCount": 1}]
        self.album_members = {"album-1": set(ids), "album-2": {ids[0]}}
        # What the user set in Immich for the first original: kept in Immich, not in the file.
        self.details = {ids[0]: {"isFavorite": True, "tags": [{"id": "tag-source", "value": "Kuvat/K001"}, {"id": "tag-family", "value": "Perhe"}],
                                 "exifInfo": {"description": "Mökillä", "rating": 4, "dateTimeOriginal": "1975-06-01T09:00:00.000Z",
                                              "timeZone": "Europe/Helsinki", "latitude": 61.5, "longitude": 23.8}}}
        self.updates = {}
        # Stacks as the server keeps them: id -> {"primary", "assets"}; merging only by a named primary.
        self.stack_records, self.trashed, self.unstacked = {}, [], []
        self.uploaded, self.copies, self.library = [], [], {}
        self.uploads, self.tagged, self.album_added, self.searches = [], {}, [], []
        self.contents, self.stacks = [], []
        self.metadata, self.metadata_supported = {}, True
        self.tag_calls = []
        self.by_content = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret"
        path, method = request.url.path.removeprefix("/api"), request.method
        if path == "/users/me":
            return httpx.Response(200, json={"name": "Sami"})
        if path == "/tags" and method == "GET":
            return httpx.Response(200, json=self.tags)
        if path == "/tags" and method == "PUT":
            value = json.loads(request.content)["tags"][0]
            tag = next((tag for tag in self.tags if tag["value"] == value), None)
            if not tag:
                tag = {"id": f"tag-{len(self.tags)}", "name": value.rsplit("/", 1)[-1], "value": value}
                self.tags.append(tag)
            return httpx.Response(200, json=[tag])
        if path == "/albums":
            if "assetId" in request.url.params:
                asset_id = request.url.params["assetId"]
                return httpx.Response(200, json=[album for album in self.albums if asset_id in self.album_members.get(album["id"], set())])
            return httpx.Response(200, json=self.albums)
        if path == "/assets/copy" and method == "PUT":
            body = json.loads(request.content)
            assert body["sidecar"] is False and body["albums"] and body["sharedLinks"] and body["stack"]
            self.copies.append((body["sourceId"], body["targetId"]))
            for members in self.album_members.values():
                if body["sourceId"] in members:
                    members.add(body["targetId"])
            return httpx.Response(204)
        match = re.fullmatch(r"/assets/([^/]+)", path)
        if match and method == "GET":
            asset = next((asset for asset in self.assets + self.uploaded if asset["id"] == match[1]), {"id": match[1], "type": "IMAGE"})
            asset = {**asset, "libraryId": self.library.get(match[1])}
            stack = next(({"id": stack_id, "primaryAssetId": record["primary"]} for stack_id, record in self.stack_records.items()
                          if match[1] in record["assets"]), None)
            # Every original is in the source tag; copies get tags only from the export.
            tags = [{"id": "tag-source", "value": "Kuvat/K001"}] if match[1] in self.originals else []
            return httpx.Response(200, json={**asset, "tags": tags, "isFavorite": False, "exifInfo": {"fileSizeInByte": 1, "exifImageWidth": 1}, "stack": stack, **self.details.get(match[1], {})})
        if match and method == "PUT":
            self.updates[match[1]] = json.loads(request.content)
            return httpx.Response(200, json={})
        if path == "/view/folder/unique-paths":
            return httpx.Response(200, json=["/photos/vanhat"])
        if path == "/view/folder":
            return httpx.Response(200, json=[asset for asset in self.assets if asset["originalPath"].startswith(request.url.params["path"] + "/")])
        if path == "/search/metadata":
            body = json.loads(request.content)
            self.searches.append(body)
            if "tagIds" in body:
                members = set(self.tagged.get(body["tagIds"][0], [])) | ({asset["id"] for asset in self.assets} if body["tagIds"] == ["tag-source"] else set())
            else:
                members = self.album_members.get(body["albumIds"][0], set())
            # Trashed assets are not found, uploaded copies are once tagged or added to an album.
            found = [asset for asset in self.assets + self.uploaded if asset["id"] in members and asset["id"] not in self.trashed]
            # Two pages: the first answers with a v3.2 cursor, the second with nothing more.
            if "cursor" in body:
                return httpx.Response(200, json={"albums": {}, "assets": {"items": found[1:], "nextCursor": None, "nextPage": None}})
            return httpx.Response(200, json={"albums": {}, "assets": {"items": found[:1], "nextCursor": "next" if len(found) > 1 else None, "nextPage": None}})
        match = re.fullmatch(r"/assets/([^/]+)/thumbnail", path)
        if match:
            return httpx.Response(200, content=self.originals[match[1]])
        match = re.fullmatch(r"/assets/([^/]+)/original", path)
        if match:
            return httpx.Response(200, content=self.originals[match[1]])
        if path == "/assets" and method == "POST":
            fields = dict(re.findall(rb'name="(\w+)"\r\n\r\n([^\r]*)\r\n', request.content))
            assert fields[b"deviceId"] == b"unfade" and b"deviceAssetId" in fields
            content = _file_part(request.content)
            # Like Immich: the same bytes again are a duplicate of the asset already stored.
            known = self.by_content.get(hashlib.sha1(content).hexdigest())
            if known:
                return httpx.Response(200, json={"id": known, "status": "duplicate"})
            asset_id = f"new-{len(self.uploads)}"
            self.by_content[hashlib.sha1(content).hexdigest()] = asset_id
            self.contents.append(content)
            self.uploads.append({"id": asset_id, "fileCreatedAt": fields[b"fileCreatedAt"].decode(),
                                 "filename": re.search(rb'name="assetData"; filename="([^"]+)"', request.content)[1].decode()})
            self.uploaded.append({"id": asset_id, "type": "IMAGE", "originalFileName": self.uploads[-1]["filename"],
                                  "originalPath": f"/upload/{self.uploads[-1]['filename']}", "checksum": "", "isTrashed": False})
            return httpx.Response(201, json={"id": asset_id, "status": "created"})
        if path == "/tags/assets" and method == "PUT":
            body = json.loads(request.content)
            for tag_id in body["tagIds"]:
                self.tagged.setdefault(tag_id, []).extend(body["assetIds"])
            self.tag_calls.append(body)
            return httpx.Response(200, json={"count": len(body["tagIds"]) * len(body["assetIds"])})
        match = re.fullmatch(r"/tags/([^/]+)/assets", path)
        if match:
            self.tagged.setdefault(match[1], []).extend(json.loads(request.content)["ids"])
            return httpx.Response(200, json=[])
        match = re.fullmatch(r"/assets/([^/]+)/metadata", path)
        if match and method == "GET":
            return httpx.Response(200, json=[{"key": key, "value": value} for key, value in self.metadata.get(match[1], {}).items()])
        if match and method == "PUT":
            if not self.metadata_supported:
                return httpx.Response(400, json={"message": "key must be one of: mobile-app"})
            for entry in json.loads(request.content)["items"]:
                self.metadata.setdefault(match[1], {})[entry["key"]] = entry["value"]
            return httpx.Response(200, json=[])
        if path == "/stacks":
            named = json.loads(request.content)["assetIds"]
            self.stacks.append(named)
            members = set(named)
            for stack_id, record in list(self.stack_records.items()):
                if record["primary"] in named:
                    members |= record["assets"]
                    del self.stack_records[stack_id]
            for record in self.stack_records.values():
                record["assets"] -= members
            stack_id = f"stack-{len(self.stacks)}"
            self.stack_records[stack_id] = {"primary": named[0], "assets": members}
            return httpx.Response(201, json={"id": stack_id, "primaryAssetId": named[0], "assets": [{"id": member} for member in sorted(members)]})
        match = re.fullmatch(r"/stacks/([^/]+)/assets/([^/]+)", path)
        if match and method == "DELETE":
            self.stack_records[match[1]]["assets"].discard(match[2])
            self.unstacked.append(match[2])
            return httpx.Response(204)
        if path == "/assets" and method == "DELETE":
            body = json.loads(request.content)
            assert body["force"] is False
            self.trashed.extend(body["ids"])
            return httpx.Response(204)
        match = re.fullmatch(r"/albums/([^/]+)/assets", path)
        if match:
            self.album_added.extend(json.loads(request.content)["ids"])
            self.album_members.setdefault(match[1], set()).update(json.loads(request.content)["ids"])
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})


@pytest.fixture
def fake_immich(env, monkeypatch):
    fake = FakeImmich()
    monkeypatch.setenv("IMMICH_URL", "http://immich.test")
    monkeypatch.setenv("IMMICH_API_KEY", "secret")
    monkeypatch.setattr(immich, "_transport", httpx.MockTransport(fake.handler))
    return fake


def approve_all(job_id):
    for group in jobs.load(job_id)["groups"]:
        jobs.approve_group(job_id, group["id"])


def create(client, **fields):
    return client.post("/jobs", data=fields, follow_redirects=False)


def test_local_folder_to_local_folder(env):
    client = TestClient(create_app())
    assert "Immich is not available" in client.get("/").text
    response = create(client, source_kind="local", source_path=str(env / "files" / "scans"), source_recursive="on",
                      destination_kind="local", destination_path=str(env / "files" / "out"), keep_folders="on", method="fade", suggest_groups="on")
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[1]
    job = jobs.load(job_id)
    assert [item["name"] for item in job["items"]] == ["A001.jpg", "A002.jpg", "sub/B001.png"]

    # The top folder and the subfolder are proposed as two groups.
    assert [(group["label"], jobs.members(job, group["id"])) for group in job["groups"]] == [("Top folder", [0, 1]), ("sub", [2])]

    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200 and "sub/B001.png" in page.text and 'data-item-id="000002"' in page.text
    assert client.get(f"/jobs/{job_id}/thumbnail/2").headers["content-type"] == "image/jpeg"
    for view in ("original", "corrected", "adjusted"):
        preview = client.get(f"/jobs/{job_id}/preview/0?view={view}")
        assert preview.headers["content-type"] == "image/jpeg"
    # Orientation 6 is applied, so the 60×40 scan previews upright as 40×60.
    assert Image.open(io.BytesIO(client.get(f"/jobs/{job_id}/preview/0?view=original").content)).size == (40, 60)
    readings = client.get(f"/jobs/{job_id}/groups/1/delta-e").json()
    assert set(readings["items"]) == {"0", "1"} and set(readings["average"]["original"]) == {"fade", "gwg", "pcs"}

    # Nothing is approved yet, so nothing is exported.
    jobs.start_export(job_id)
    jobs.wait(job_id, 60)
    assert jobs.load(job_id)["export"]["waiting"] == 3 and not any((env / "files" / "out").iterdir())

    client.post(f"/jobs/{job_id}/groups/2", data={"label": "Sininen", "method": "gwg"})
    client.post(f"/jobs/{job_id}/items/1", data={"strength": "50", "black": "2", "white": "0", "temperature": "5"})
    response = client.post(f"/jobs/{job_id}/groups/1/approve", follow_redirects=False)
    assert "group=2" in response.headers["location"]
    assert "saved=all" in client.post(f"/jobs/{job_id}/groups/2/approve", follow_redirects=False).headers["location"]
    job = jobs.load(job_id)
    assert job["groups"][1] == {"id": 2, "label": "Sininen", "method": "gwg", "approved": True}
    assert job["items"][1]["adjustments"]["strength"] == 50

    originals = {path: path.read_bytes() for path in (env / "files" / "scans").rglob("*.*")}
    client.post(f"/jobs/{job_id}/export")
    jobs.wait(job_id, 60)
    status = client.get(f"/jobs/{job_id}/export").json()
    assert status["state"] == "done" and status["written"] == 3 and not status["errors"]
    out = env / "files" / "out"
    assert sorted(str(path.relative_to(out)) for path in out.rglob("*.*")) == ["A001.jxl", "A002.jxl", "sub/B001.jxl"]
    content = (out / "A001.jxl").read_bytes()
    pixels = imagecodecs.jpegxl_decode(content)
    assert pixels.dtype == np.uint16 and pixels.shape == (60, 40, 3)
    exif = Image.Exif()
    exif.load(b"Exif\x00\x00" + jxl_boxes(content)[b"Exif"][4:])
    assert exif[0x0112] == 1 and exif[0x010F] == "Scanner Oy"
    recipe = encode.read_recipe(jxl_boxes(content)[b"xml "])
    source = env / "files" / "scans" / "A001.jpg"
    assert recipe["Method"] == "fade" and recipe["SourceName"] == "A001.jpg" and recipe["SourcePath"] == str(source)
    assert recipe["SourceSHA256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert recipe["SourceBytes"] == str(source.stat().st_size) and recipe["SourceOrientation"] == "6"
    assert recipe["SourceSHA1"] == hashlib.sha1(source.read_bytes()).hexdigest()
    upright_source = np.asarray(ImageOps.exif_transpose(Image.open(source)).convert("RGB"))
    assert recipe["SourcePHash"] == fingerprint.phash(upright_source) and recipe["SourceDHash"] == fingerprint.dhash(upright_source)
    assert recipe["PHash"] == fingerprint.phash(pixels) and len(recipe["DHash"]) == 16
    assert re.fullmatch(r"uuid:[0-9a-f-]{36}", recipe["DocumentID"]) and "SourceUUID" not in recipe
    assert jobs.load(job_id)["items"][0]["exported"]["recipe"]["uuid"] == recipe["DocumentID"][5:]
    stored = np.asarray(Image.open(source).convert("RGB"))
    assert recipe["SourcePixelFormat"] == "60x40 RGB 8-bit"  # as stored, before orientation 6
    assert recipe["SourcePixelSHA256"] == hashlib.sha256(stored.tobytes()).hexdigest()
    # The recipe alone recomputes the copy from the original, pixel for pixel.
    original, _ = render.read_full(source)
    fitted = {"table": color_methods.tables(recipe["Curve"], 256), "saturation": recipe["Curve"]["saturation"]}
    again = (color_methods.apply(original, fitted, recipe["Adjustments"]) * 65535 + 0.5).astype(np.uint16)
    assert np.array_equal(again, pixels)
    assert all(path.read_bytes() == content for path, content in originals.items()), "originals must stay untouched"

    # An adjustment withdraws the group's approval; after approving again only that image is written.
    jobs.set_adjustments(job_id, 0, {"strength": 80, "black": 0, "white": 0, "temperature": 0})
    assert not jobs.load(job_id)["groups"][0]["approved"]
    jobs.approve_group(job_id, 1)
    jobs.start_export(job_id)
    jobs.wait(job_id, 60)
    status = jobs.load(job_id)["export"]
    assert (status["written"], status["skipped"]) == (1, 2)


def test_grouping_board_saves_moves_and_new_groups(env):
    client = TestClient(create_app())
    response = create(client, source_kind="local", source_path=str(env / "files" / "scans"), source_recursive="on",
                      destination_kind="local", destination_path=str(env / "files" / "out"), suggest_groups="on")
    job_id = response.headers["location"].rsplit("/", 1)[1]
    jobs.approve_group(job_id, 1)
    jobs.approve_group(job_id, 2)
    revision = jobs.revision(jobs.load(job_id))
    state = {"groups": [{"key": "g-1", "label": "Kaikki"}, {"key": "new-1", "label": "Sisäkuvat"}],
             "assignments": {"000000": "g-1", "000001": "new-1", "000002": "g-1"}}
    response = client.post(f"/jobs/{job_id}/grouping", data={"state": json.dumps(state), "revision": revision}, follow_redirects=False)
    assert response.status_code == 303
    job = jobs.load(job_id)
    # Group 2 is gone, both remaining groups changed members, so neither stays approved.
    assert [(group["id"], group["label"], group["approved"]) for group in job["groups"]] == [(1, "Kaikki", False), (3, "Sisäkuvat", False)]
    assert [item["group"] for item in job["items"]] == [1, 3, 1]
    # The page that loaded the old grouping may not overwrite the new one.
    stale = client.post(f"/jobs/{job_id}/grouping", data={"state": json.dumps(state), "revision": revision})
    assert stale.status_code == 409
    missing = {**state, "assignments": {"000000": "g-1"}}
    assert client.post(f"/jobs/{job_id}/grouping", data={"state": json.dumps(missing), "revision": jobs.revision(job)}).status_code == 422


def test_outliers_get_their_own_review_group(env):
    folder = env / "files" / "batch"
    folder.mkdir()
    for number in range(5):
        faded((110, 95, 80)).save(folder / f"S{number}.jpg")
    faded((20, 18, 15)).save(folder / "S9.jpg")
    job = jobs.create({"kind": "local", "path": str(folder)}, {"kind": "local", "path": str(env / "files" / "out")})
    assert [(group["label"], jobs.members(job, group["id"])) for group in job["groups"]] == [
        ("All images", [0, 1, 2, 3, 4]), ("possibly dark · check", [5])]
    plain = jobs.create({"kind": "local", "path": str(folder)}, {"kind": "local", "path": str(env / "files" / "out")}, suggest=False)
    assert len(plain["groups"]) == 1


def test_local_destination_may_not_overwrite_the_originals(env):
    client = TestClient(create_app())
    scans = str(env / "files" / "scans")
    response = create(client, source_kind="local", source_path=scans, destination_kind="local", destination_path=scans, method="fade")
    assert response.status_code == 422 and "file name suffix" in response.text
    assert create(client, source_kind="local", source_path=scans, destination_kind="local", destination_path=scans,
                  destination_suffix="-korjattu", method="fade").status_code == 303


def test_local_folders_stay_inside_allowed_roots(env):
    client = TestClient(create_app())
    response = create(client, source_kind="local", source_path=str(env), destination_kind="local", destination_path=str(env / "files" / "x"))
    assert response.status_code == 422 and "outside the allowed roots" in response.text
    listing = client.get("/api/folders", params={"path": str(env / "files" / "scans")}).json()
    assert listing["dirs"] == ["sub"] and listing["images"] == 2
    assert client.get("/api/folders", params={"path": "/"}).status_code == 422


def test_immich_tag_to_immich_tag_and_album(fake_immich, env):
    client = TestClient(create_app())
    home = client.get("/").text
    assert "Connected to Immich as Sami" in home and "Kuvat/K001" in home and "/photos/vanhat" in home
    response = create(client, source_kind="immich-tag", immich_tag="tag-source",
                      destination_kind="immich", destination_tag="Kuvat/Korjattu", destination_album="album-1", method="fade", original_mode="stack")
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[1]
    job = jobs.load(job_id)
    assert [item["name"] for item in job["items"]] == ["D1.jpg", "D2.jpg"]
    assert fake_immich.searches[0]["tagIds"] == ["tag-source"] and fake_immich.searches[1]["cursor"] == "next"
    assert client.get(f"/jobs/{job_id}/preview/1").status_code == 200

    approve_all(job_id)
    client.post(f"/jobs/{job_id}/export")
    jobs.wait(job_id, 60)
    assert jobs.load(job_id)["export"]["errors"] == []
    assert [upload["filename"] for upload in fake_immich.uploads] == ["D1.jxl", "D2.jxl"]
    assert fake_immich.uploads[0]["fileCreatedAt"] == "1975-06-01T12:00:00.000Z"
    new_tag = next(tag for tag in fake_immich.tags if tag["value"] == "Kuvat/Korjattu")
    assert fake_immich.tagged[new_tag["id"]] == ["new-0", "new-1"]
    assert fake_immich.album_added == ["new-0", "new-1"]
    assert [item["exported"]["target"] for item in jobs.load(job_id)["items"]] == ["new-0", "new-1"]
    originals = [asset["id"] for asset in fake_immich.assets]
    assert fake_immich.stacks == [["new-0", originals[0]], ["new-1", originals[1]]]
    first = encode.read_recipe(jxl_boxes(fake_immich.contents[0])[b"xml "])
    assert first["SourceImmichAsset"] == originals[0]
    # The original got a UUID in Immich's metadata only; the copy names it and carries its own.
    source_uuid = fake_immich.metadata[originals[0]]["unfade"]["uuid"]
    assert first["SourceUUID"] == source_uuid and first["SourceSHA1"] == hashlib.sha1(fake_immich.originals[originals[0]]).hexdigest()
    copy_meta = fake_immich.metadata["new-0"]["unfade"]
    assert copy_meta["uuid"] == first["DocumentID"][5:] and copy_meta["source"]["uuid"] == source_uuid
    assert copy_meta["source"]["immich_asset"] == originals[0] and copy_meta["phash"] == first["PHash"]

    # A changed image goes up as a new asset whose stack takes in the earlier copy's stack.
    jobs.set_adjustments(job_id, 0, {"strength": 50, "black": 0, "white": 0, "temperature": 0})
    approve_all(job_id)
    jobs.start_export(job_id)
    jobs.wait(job_id, 60)
    assert fake_immich.stacks[-1] == ["new-2", "new-0", originals[0]]
    # The original keeps its UUID across exports; each copy has its own.
    again = encode.read_recipe(jxl_boxes(fake_immich.contents[-1])[b"xml "])
    assert again["SourceUUID"] == source_uuid and again["DocumentID"] != first["DocumentID"]


def test_old_immich_without_free_metadata_keys_still_exports(fake_immich, env):
    fake_immich.metadata_supported = False
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "Korjattu", "stack": True})
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    assert result["errors"] == [] and result["written"] == 2 and len(result["warnings"]) == 1
    assert "SourceUUID" not in encode.read_recipe(jxl_boxes(fake_immich.contents[0])[b"xml "])


def test_immich_folder_and_album_sources_to_local(fake_immich, env):
    client = TestClient(create_app())
    response = create(client, source_kind="immich-folder", immich_folder="/photos/vanhat",
                      destination_kind="local", destination_path=str(env / "files" / "from-immich"), method="pcs")
    job_id = response.headers["location"].rsplit("/", 1)[1]
    approve_all(job_id)
    jobs.start_export(job_id)
    jobs.wait(job_id, 60)
    assert sorted(path.name for path in (env / "files" / "from-immich").iterdir()) == ["D1.jxl", "D2.jxl"]

    response = create(client, source_kind="immich-album", immich_album="album-1", destination_kind="local", destination_path=str(env / "files" / "a"))
    assert response.status_code == 303 and fake_immich.searches[-2]["albumIds"] == ["album-1"]


def test_immich_destination_needs_a_tag_or_album(fake_immich, env):
    client = TestClient(create_app())
    response = create(client, source_kind="local", source_path=str(env / "files" / "scans"), destination_kind="immich", destination_tag="")
    assert response.status_code == 422 and "a tag or an album" in response.text


def test_command_line_run(fake_immich, env, capsys):
    code = main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", f"local:{env / 'files' / 'cli'}", "--suffix=-k", "--method", "gwg", "--format", "jpeg"])
    assert code == 0
    assert sorted(path.name for path in (env / "files" / "cli").iterdir()) == ["D1-k.jpg", "D2-k.jpg"]
    small = Image.open(env / "files" / "cli" / "D1-k.jpg")
    assert encode.read_recipe(small.info["xmp"])["Method"] == "gwg"
    assert "Written 2" in capsys.readouterr().out


def test_output_names_do_not_clash():
    taken = set()
    destination = {"suffix": "", "keep_folders": False}
    stems = [locations.output_stem({"key": key, "name": name}, destination, taken)
             for key, name in (("a", "x/IMG.heic"), ("b", "y/IMG.heic"), ("c", "scan.TIF"))]
    assert stems[0] == "IMG" and stems[1].startswith("IMG-") and stems[2] == "scan"


def test_sixteen_bit_tiff_keeps_its_depth(env):
    folder = env / "files" / "deep"
    folder.mkdir()
    ramp = (np.linspace(0.3, 0.7, 120)[None, :, None] * np.array([1.0, 0.85, 0.7]) * np.ones((80, 1, 1)) * 65535).astype(np.uint16)
    imagecodecs.imwrite(folder / "deep.tif", ramp)
    job = jobs.create({"kind": "local", "path": str(folder)}, {"kind": "local", "path": str(env / "files" / "out")})
    jobs.run_export(job["id"], only_approved=False)
    pixels = imagecodecs.jpegxl_decode((env / "files" / "out" / "deep.jxl").read_bytes())
    # An 8-bit pipeline could give at most 256 levels per channel; the 16-bit one keeps the ramp's steps.
    assert pixels.dtype == np.uint16 and len(np.unique(pixels[0, :, 0])) > 100
    recipe = encode.read_recipe(jxl_boxes((env / "files" / "out" / "deep.jxl").read_bytes())[b"xml "])
    assert recipe["SourcePixelFormat"] == "120x80 RGB 16-bit LE"
    assert recipe["SourcePixelSHA256"] == hashlib.sha256(ramp.astype("<u2").tobytes()).hexdigest()


def test_pixel_digest_ignores_metadata_edits(env):
    folder = env / "files" / "edited"
    folder.mkdir()
    image = faded((100, 90, 80))
    image.save(folder / "a.png")
    exif = Image.Exif()
    exif[0x010F] = "Toinen skanneri"
    image.save(folder / "b.png", exif=exif.tobytes())
    digests = [render.pixel_digest(render.decode(folder / name)[0]) for name in ("a.png", "b.png")]
    assert digests[0] == digests[1]
    assert (folder / "a.png").read_bytes() != (folder / "b.png").read_bytes()


def test_other_colour_profile_goes_to_png_with_the_profile(env):
    folder = env / "files" / "profiled"
    folder.mkdir()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("XYZ")).tobytes()
    faded((100, 90, 80)).save(folder / "p.jpg", icc_profile=profile)
    job = jobs.create({"kind": "local", "path": str(folder)}, {"kind": "local", "path": str(env / "files" / "out")})
    jobs.run_export(job["id"], only_approved=False)
    written = Image.open(env / "files" / "out" / "p.png")
    assert written.info["icc_profile"] == profile
    assert encode.read_recipe(written.info["XML:com.adobe.xmp"].encode())["SourceName"] == "p.jpg"
    assert imagecodecs.png_decode((env / "files" / "out" / "p.png").read_bytes()).dtype == np.uint16


@pytest.mark.parametrize("orientation", range(1, 9))
def test_upright_matches_pillow(orientation):
    pixels = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    image = Image.fromarray(pixels)
    exif = image.getexif()
    exif[0x0112] = orientation
    buffer = io.BytesIO()
    image.save(buffer, "PNG", exif=exif.tobytes())
    expected = np.asarray(ImageOps.exif_transpose(Image.open(buffer)))
    assert np.array_equal(render.upright(pixels, orientation), expected)


def test_recipe_merges_into_source_xmp():
    source = '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmpMM:DocumentID="xmp.did:source-1"><dc:title>Kesä &amp; meri</dc:title></rdf:Description></rdf:RDF></x:xmpmeta>'.encode()
    recipe = {"uuid": "11111111-1111-4111-8111-111111111111", "method": "pcs", "curve": {"kind": "pcs"}, "adjustments": {"strength": 100},
              "source": {"name": "a<b>.jpg", "sha256": "ab", "document_id": encode.document_id(source)}}
    once = encode.recipe_xmp(recipe, source)
    twice = encode.recipe_xmp({**recipe, "uuid": "22222222-2222-4222-8222-222222222222", "method": "gwg"}, once)
    assert b"<dc:title>Kes\xc3\xa4 &amp; meri</dc:title>" in twice and twice.count(b"unfade:Method") == 2
    assert encode.read_recipe(once)["SourceName"] == "a<b>.jpg"
    assert encode.read_recipe(twice) == {**encode.read_recipe(once), "Method": "gwg", "DocumentID": "uuid:22222222-2222-4222-8222-222222222222"}
    # The source's own DocumentID moves to SourceDocumentID; the copy has exactly one DocumentID.
    assert encode.read_recipe(once)["SourceDocumentID"] == "xmp.did:source-1"
    assert once.count(b"DocumentID>uuid:") == 1 and b'xmpMM:DocumentID="xmp.did:source-1"' not in once


def jxl_boxes(content):
    boxes, position = {}, 0
    while position < len(content):
        size, kind = struct.unpack(">I4s", content[position:position + 8])
        boxes[kind] = content[position + 8:position + size]
        position += size
    return boxes


def _file_part(body):
    part = body.split(b'name="assetData"', 1)[1]
    return part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]


def test_perceptual_hashes_survive_reencoding_but_not_another_picture():
    rng = np.random.default_rng(3)
    y, x = np.mgrid[0:300, 0:450]
    scene = (np.stack([np.sin(x / 40), np.cos(y / 30), np.sin((x + y) / 55)], -1) * 0.4 + 0.5 + rng.normal(0, 0.03, (300, 450, 3))).clip(0, 1)
    pixels = (scene * 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).resize((300, 200)).save(buffer, "JPEG", quality=60)
    smaller = np.asarray(Image.open(buffer))
    other = (np.stack([np.cos(x / 25), np.sin(y / 45), np.cos((x - y) / 35)], -1) * 0.4 * 255 + 128).astype(np.uint8)
    for algorithm in (fingerprint.phash, fingerprint.dhash):
        assert fingerprint.distance(algorithm(pixels), algorithm(smaller)) <= 6
        assert fingerprint.distance(algorithm(pixels), algorithm(other)) > 16


def test_copy_gets_the_originals_albums_tags_and_immich_details(fake_immich, env):
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "Kuvat/K001", "stack": True, "copy_details": True})
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    assert result["errors"] == [] and result["warnings"] == []
    # Finland had no daylight saving time in 1975, so the zone gives +02:00 in June.
    assert fake_immich.updates["new-0"] == {"isFavorite": True, "description": "Mökillä", "rating": 4,
                                            "dateTimeOriginal": "1975-06-01T11:00:00.000+02:00", "latitude": 61.5, "longitude": 23.8}
    assert "new-0" in fake_immich.tagged["tag-family"] and fake_immich.tagged["tag-source"].count("new-0") == 1
    # One tag update per copy with every tag: separate calls race inside Immich and lose tags.
    calls = [call for call in fake_immich.tag_calls if call["assetIds"] == ["new-0"]]
    assert len(calls) == 1 and sorted(calls[0]["tagIds"]) == ["tag-family", "tag-source"]
    assert "new-0" in fake_immich.album_members["album-2"]
    # The second original has nothing set in Immich: only the favourite flag (off) is written.
    assert fake_immich.updates["new-1"] == {"isFavorite": False}


def test_copy_details_is_only_for_immich_sources(fake_immich, env):
    job = jobs.create({"kind": "local", "path": str(env / "files" / "scans")}, {"kind": "immich", "tag": "Korjattu", "copy_details": True, "stack": True})
    assert job["destination"]["copy_details"] is False and job["destination"]["stack"] is False


@pytest.mark.parametrize(("zone", "expected"), [
    ("UTC+2", "1975-06-01T11:00:00.000+02:00"), ("UTC-03:30", "1975-06-01T05:30:00.000-03:30"),
    (None, "1975-06-01T09:00:00.000+00:00"), ("Not/AZone", "1975-06-01T09:00:00.000+00:00"),
])
def test_local_time_keeps_the_instant_and_the_zone(zone, expected):
    assert jobs._local_time("1975-06-01T09:00:00.000Z", zone) == expected


def test_replace_previous_trashes_the_earlier_copy_within_a_job(fake_immich, env):
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K", "stack": True, "replace_previous": True})
    jobs.run_export(job["id"], only_approved=False)
    assert fake_immich.trashed == []
    jobs.set_adjustments(job["id"], 0, {"strength": 60, "black": 0, "white": 0, "temperature": 0})
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    original = fake_immich.assets[0]["id"]
    assert result["warnings"] == [] and fake_immich.trashed == ["new-0"] and fake_immich.unstacked == ["new-0"]
    assert next(record for record in fake_immich.stack_records.values() if record["primary"] == "new-2")["assets"] == {"new-2", original}


def test_replace_previous_finds_copies_from_earlier_runs_and_spares_other_versions(fake_immich, env, capsys):
    destination = ["--dest", "immich", "--dest-tag", "K", "--replace-previous"]
    assert main(["run", "--source", "immich-tag:Kuvat/K001", *destination]) == 0
    original = fake_immich.assets[0]["id"]
    # Someone added their own edit of the first image to its stack; it has no unfade metadata.
    stack_id = next(stack_id for stack_id, record in fake_immich.stack_records.items() if original in record["assets"])
    fake_immich.stack_records[stack_id]["assets"].add("hand-edit")
    assert main(["run", "--source", "immich-tag:Kuvat/K001", *destination]) == 0
    # A new job knows nothing of the first run's uploads; the stack and the copies' metadata do.
    assert sorted(fake_immich.trashed) == ["new-0", "new-1"]
    merged = next(record for record in fake_immich.stack_records.values() if record["primary"] == "new-2")
    assert merged["assets"] == {"new-2", original, "hand-edit"}
    assert original not in fake_immich.trashed


def test_replace_previous_keeps_a_recorded_copy_that_names_another_original(fake_immich, env):
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K", "replace_previous": True})
    jobs.run_export(job["id"], only_approved=False)
    fake_immich.metadata["new-0"]["unfade"]["source"]["immich_asset"] = "someone-else"
    result = jobs.run_export(job["id"], only_approved=False, force=True)["export"]
    assert "new-0" not in fake_immich.trashed and "new-1" in fake_immich.trashed
    assert any("another original" in warning for warning in result["warnings"])


def test_replace_original_hands_everything_over_and_trashes_the_original(fake_immich, env):
    originals = [asset["id"] for asset in fake_immich.assets]
    fake_immich.library[originals[1]] = "external-library"
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "Korjattu", "replace_original": True, "stack": True})
    assert job["destination"]["stack"] is False and job["destination"]["copy_details"] is True
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    assert fake_immich.copies == [(originals[0], "new-0"), (originals[1], "new-1")] and fake_immich.stacks == []
    assert "new-0" in fake_immich.album_members["album-2"] and fake_immich.updates["new-0"]["description"] == "Mökillä"
    # The external library's original stays: Immich would not delete its file and would import it again.
    assert fake_immich.trashed == [originals[0]]
    assert len(result["warnings"]) == 1 and "external library" in result["warnings"][0]

    # A re-export takes over from the copy that replaced the original, and replaces it in turn.
    jobs.set_adjustments(job["id"], 0, {"strength": 70, "black": 0, "white": 0, "temperature": 0})
    jobs.run_export(job["id"], only_approved=False)
    assert fake_immich.copies[-1] == ("new-0", "new-2") and fake_immich.trashed[-1] == "new-0"
    assert fake_immich.trashed.count(originals[0]) == 1


def test_sources_skip_unfade_copies(fake_immich, env):
    # Copies inherit the original's tags, so the same tag now lists originals and copies.
    jobs.run_export(jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K", "copy_details": True})["id"],
                    only_approved=False)
    assert {"new-0", "new-1"} <= set(fake_immich.tagged["tag-source"])
    again = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K"})
    assert [item["key"] for item in again["items"]] == [asset["id"] for asset in fake_immich.assets] and again["skipped_copies"] == 2


def test_nothing_left_to_correct_after_replacing(fake_immich, env):
    jobs.run_export(jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K", "replace_original": True})["id"],
                    only_approved=False)
    with pytest.raises(ValueError, match="no images to correct"):
        jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K"})


def test_immich_tag_sources_ignore_upload_folders_and_sort_by_name(fake_immich, env):
    # Immich keeps uploads in random folders (upload/<user>/1f/7b/...): not a grouping, not an order.
    fake_immich.assets[0]["originalPath"], fake_immich.assets[1]["originalPath"] = "/upload/u/ff/01/D1.jpg", "/upload/u/00/02/D2.jpg"
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "local", "path": str(env / "files" / "out")})
    assert [item["name"] for item in job["items"]] == ["D1.jpg", "D2.jpg"] and len(job["groups"]) == 1
