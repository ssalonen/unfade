"""The archive of originals: files byte for byte, sidecars, recipes, code snapshots, safety."""
import json
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from tests.test_unfade import jpeg
from unfade import archive, jobs, version
from unfade.__main__ import main

NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "dc": "http://purl.org/dc/elements/1.1/",
      "xmp": "http://ns.adobe.com/xap/1.0/", "digiKam": "http://www.digikam.org/ns/1.0/", "exif": "http://ns.adobe.com/exif/1.0/",
      "mwg-rs": "http://www.metadataworkinggroup.com/schemas/regions/", "stArea": "http://ns.adobe.com/xmp/sType/Area#",
      "ca": archive.NAMESPACE, "xmpMM": "http://ns.adobe.com/xap/1.0/mm/"}


@pytest.fixture
def exported(env):
    """The scans folder corrected into out/ once, so the archive has copies to describe."""
    job = jobs.create({"kind": "local", "path": str(env / "files" / "scans"), "recursive": True},
                      {"kind": "local", "path": str(env / "files" / "out"), "keep_folders": True})
    jobs.run_export(job["id"], only_approved=False)
    return jobs.load(job["id"])


def test_local_archive_keeps_originals_records_copies_and_code(env, exported):
    target = env / "files" / "archive"
    assert main(["archive", "--source", f"local:{env / 'files' / 'scans'}", "--recursive", "--to", str(target)]) == 0
    originals = target / "originals"
    # Local sources keep their subfolders; every original is byte for byte the source file.
    for name in ("A001.jpg", "A002.jpg", "sub/B001.png"):
        assert (originals / name).read_bytes() == (env / "files" / "scans" / name).read_bytes()
        assert (originals / (name + ".xmp")).is_file() and (originals / (name + ".json")).is_file()
    record = json.loads((originals / "A001.jpg.json").read_text())
    exported_record = exported["items"][0]["exported"]
    assert record["orientation"] == 6 and record["sources"] == [{"kind": "local", "path": str(env / "files" / "scans" / "A001.jpg")}]
    [copy] = record["copies"]
    assert copy["uuid"] == exported_record["recipe"]["uuid"] and copy["sha256"] == exported_record["sha256"]
    assert copy["file"] == exported_record["target"] and copy["recipe"]["curve"] == exported_record["recipe"]["curve"]
    # The code that made the copy is archived, and its files hash to the recorded id.
    code_dir = target / "code" / f"unfade-{copy['recipe']['code']['id']}"
    assert version.digest(code_dir) == copy["recipe"]["code"]["id"] == version.code_version()["id"]
    assert archive.verify(str(target)) == []
    # code/recompute.py rebuilds the copy from the archive alone, pixel for pixel.
    check = subprocess.run([sys.executable, "-I", str(target / "code" / "recompute.py"), str(originals / "A001.jpg"),
                            "--check", exported_record["target"]], check=False, capture_output=True, text=True)
    assert check.returncode == 0 and "same pixels" in check.stdout, check.stderr


def test_rerun_is_additive_and_a_changed_original_stops_it(env, exported):
    target = env / "files" / "archive"
    source = {"kind": "local", "path": str(env / "files" / "scans"), "recursive": True}
    first = archive.run(source, str(target))
    assert (first["added"], first["unchanged"]) == (3, 0)
    (env / "files" / "scans" / "A003.jpg").write_bytes(jpeg((90, 80, 70)))
    second = archive.run(source, str(target))
    assert (second["added"], second["unchanged"]) == (1, 3)
    names = [entry["name"] for entry in json.loads((target / "manifest.json").read_text())["originals"]]
    assert names == ["A001.jpg", "A002.jpg", "A003.jpg", "sub/B001.png"]
    # An archived original must never silently change: verify flags it and the next run refuses.
    (target / "originals" / "A002.jpg").write_bytes(b"not the original")
    assert archive.verify(str(target)) == ["changed: originals/A002.jpg"]
    with pytest.raises(archive.ArchiveError, match="changed"):
        archive.run(source, str(target))


def test_archive_refuses_foreign_folders_and_nesting(env):
    scans = env / "files" / "scans"
    other = env / "files" / "other"
    other.mkdir()
    (other / "something.txt").write_text("not an archive")
    with pytest.raises(archive.ArchiveError, match="neither empty"):
        archive.run({"kind": "local", "path": str(scans)}, str(other))
    with pytest.raises(archive.ArchiveError, match="inside one another"):
        archive.run({"kind": "local", "path": str(scans)}, str(scans / "archive"))


def test_names_are_fixed_and_clashes_get_the_digest(tmp_path):
    store = archive.Archive(tmp_path / "a")
    store.manifest["originals"].append({"name": "IMG_1.jpg", "sha256": "aa" * 32})
    store.by_sha = {entry["sha256"]: entry for entry in store.manifest["originals"]}
    assert store.name_for("IMG_1.jpg", "aa" * 32) == "IMG_1.jpg"
    assert store.name_for("img_1.JPG", "bb" * 32) == "img_1-bbbbbbbb.JPG"
    assert store.name_for("IMG_2.jpg", "cc" * 32) == "IMG_2.jpg"


def test_sidecar_carries_immich_metadata_people_and_copies():
    record = {
        "name": "K001-0001.jpg", "sha256": "ab" * 32, "sha1": "cd" * 20, "size": 10, "phash": "f0" * 8,
        "sources": [{"kind": "immich", "asset_id": "asset-1"}],
        "immich": {"uuid": "11111111-1111-4111-8111-111111111111", "favorite": True, "trashed": False, "archived": False, "shared": False,
                   "albums": [{"id": "a", "name": "Kesä & meri"}],
                   "asset": {"tags": [{"value": "Kuvat/K001"}, {"value": "Mökki"}],
                             "exifInfo": {"description": "Mökillä", "rating": 4, "dateTimeOriginal": "1975-06-01T12:30:00+00:00",
                                          "timeZone": "Europe/Helsinki", "latitude": 61.5, "longitude": -23.8}},
                   "faces": [{"person": {"name": "Mummo"}, "imageWidth": 480, "imageHeight": 320,
                              "boundingBoxX1": 100, "boundingBoxY1": 60, "boundingBoxX2": 180, "boundingBoxY2": 150},
                             {"person": None, "imageWidth": 480, "imageHeight": 320,
                              "boundingBoxX1": 0, "boundingBoxY1": 0, "boundingBoxX2": 10, "boundingBoxY2": 10}]},
        "copies": [{"uuid": "c-1", "immich_asset": "copy-1", "name": "K001-0001.jxl", "sha256": "ef" * 32, "method": "fade",
                    "recipe": {"method": "fade", "curve": {"kind": "fade"}, "adjustments": {"strength": 100}, "code": {"id": "0123456789abcdef"}}}],
    }
    root = ElementTree.fromstring(archive.sidecar(record).decode().split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
    description = root.find(".//rdf:Description", NS)
    assert description is not None
    assert description.findtext("dc:description/rdf:Alt/rdf:li", namespaces=NS) == "Mökillä"
    assert [li.text for li in description.findall("digiKam:TagsList/rdf:Seq/rdf:li", NS)] == ["Kuvat/K001", "Mökki"]
    assert description.findtext("xmp:Rating", namespaces=NS) == "4"
    assert description.findtext("exif:DateTimeOriginal", namespaces=NS) == "1975-06-01T14:30:00.000+02:00"
    assert description.findtext("exif:GPSLongitude", namespaces=NS) == "23,48.000000W"
    # Only named people become regions; the area is the box's centre and size, normalised.
    [region] = description.findall(".//mwg-rs:RegionList/rdf:Bag/rdf:li", NS)
    assert region.findtext("mwg-rs:Name", namespaces=NS) == "Mummo"
    assert [region.findtext(f"mwg-rs:Area/stArea:{key}", namespaces=NS) for key in "xywh"] == ["0.291667", "0.328125", "0.166667", "0.281250"]
    assert [li.text for li in description.findall("ca:Albums/rdf:Bag/rdf:li", NS)] == ["Kesä & meri"]
    [copy] = description.findall("ca:Copies/rdf:Bag/rdf:li", NS)
    assert copy.findtext("ca:CodeVersion", namespaces=NS) == "0123456789abcdef" and json.loads(copy.findtext("ca:Recipe", default="", namespaces=NS))["method"] == "fade"
    assert description.findtext("xmpMM:DocumentID", namespaces=NS) == "uuid:11111111-1111-4111-8111-111111111111"
