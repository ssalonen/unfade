"""unfade against a real Immich over a connection that breaks: uploads and downloads cut off
part-way, an outage of several seconds, and the app stopped mid-export. The result must be the
same as over a good connection, with no duplicates and no broken files."""
from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from unfade import archive, immich, jobs
from unfade.__main__ import main
from e2e import immich_harness as harness


@pytest.fixture
def library(unfade_env, admin):
    api = unfade_env.client()
    ids = [harness.upload(api, f"K001-000{n}.jpg", harness.faded_photo(n, taken=datetime(1975, 6, 1, 12))) for n in (1, 2, 3)]
    harness.tag_each(api, {ids[0]: ["Kuvat/K001", "Mökki"], ids[1]: ["Kuvat/K001"], ids[2]: ["Kuvat/K001"]})
    harness.album(api, "Kesä 1975", ids[:2])
    harness.wait_for_jobs(unfade_env.url, admin)
    return {"api": api, "originals": ids, "url": unfade_env.url, "admin": admin}


@pytest.fixture
def proxy(library, monkeypatch):
    chaos = harness.ChaosProxy(library["url"])
    monkeypatch.setenv("IMMICH_URL", chaos.url)
    yield chaos
    chaos.close()


def copies_in_immich(api) -> list[dict]:
    return api.post("/search/metadata", json={"originalFileName": ".jxl", "size": 100}).json()["assets"]["items"]


def test_export_through_broken_transfers(library, proxy):
    proxy.cuts, proxy.after = 6, 8192  # originals ~30 KB down, copies ~140 KB up: both broken off
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "Kuvat/Korjattu"]) == 0
    harness.wait_for_jobs(library["url"], library["admin"])
    assert proxy.cut == 6
    copies = copies_in_immich(library["api"])
    assert sorted(copy["originalFileName"] for copy in copies) == ["K001-0001.jxl", "K001-0002.jxl", "K001-0003.jxl"]
    first = next(copy for copy in copies if copy["originalFileName"] == "K001-0001.jxl")
    tags = sorted(tag["value"] for tag in library["api"].get(f"/assets/{first['id']}").json()["tags"])
    assert tags == ["Kuvat/K001", "Kuvat/Korjattu", "Mökki"]


def test_export_stopped_after_an_upload_resumes_without_a_duplicate(library, monkeypatch):
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K"})
    add_tags = immich.Immich.add_tags

    def stop(*args, **kwargs):
        raise KeyboardInterrupt  # the app is stopped right after the first upload
    monkeypatch.setattr(immich.Immich, "add_tags", stop)
    with pytest.raises(KeyboardInterrupt):
        jobs.run_export(job["id"], only_approved=False)
    assert len(copies_in_immich(library["api"])) == 1
    monkeypatch.setattr(immich.Immich, "add_tags", add_tags)
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    harness.wait_for_jobs(library["url"], library["admin"])
    # Immich recognised the rebuilt first copy as the one it already had.
    assert result["errors"] == [] and len(copies_in_immich(library["api"])) == 3


def test_archive_through_an_outage_and_broken_downloads(library, proxy, tmp_path, monkeypatch):
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "K"]) == 0
    harness.wait_for_jobs(library["url"], library["admin"])
    # A fresh cache, so the archive downloads every original through the broken connection.
    monkeypatch.setenv("UNFADE_DATA_DIR", str(tmp_path / "fresh"))
    proxy.cuts, proxy.after, proxy.cut = 4, 8192, 0
    proxy.outage(4)
    target = tmp_path / "archive"
    assert main(["archive", "--source", "immich-tag:Kuvat/K001", "--to", str(target)]) == 0
    assert proxy.refused > 0 and proxy.cut == 4
    for asset_id in library["originals"]:
        original = library["api"].get(f"/assets/{asset_id}/original").content
        name = library["api"].get(f"/assets/{asset_id}").json()["originalFileName"]
        assert hashlib.sha256((target / "originals" / name).read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()
    assert archive.verify(str(target)) == []
