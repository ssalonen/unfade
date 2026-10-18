"""Dropped connections, broken transfers and interrupted runs: unfade retries what can be
retried, never stores a broken download, and resumes without duplicates."""
import json

import httpx
import pytest

from unfade import archive, immich, jobs
from tests.test_unfade import FakeImmich, env, fake_immich, quick_retries  # noqa: F401  (fixtures)


class Flaky:
    """Wraps the fake Immich: the first `failures` matching requests fail in the given way."""

    def __init__(self, fake, failures: int, matches, failure):
        self.fake, self.failures, self.matches, self.failure, self.seen = fake, failures, matches, failure, 0

    def __call__(self, request):
        if self.matches(request) and self.failures > 0:
            self.failures -= 1
            self.seen += 1
            return self.failure(request, self.fake)
        return self.fake.handler(request)


def cut_off(request, fake):
    raise httpx.ReadError("connection reset by peer", request=request)


def stored_then_cut_off(request, fake):
    # The server gets and keeps the upload, but the answer never arrives.
    fake.handler(request)
    raise httpx.RemoteProtocolError("server disconnected without sending a response", request=request)


def use(monkeypatch, transport):
    monkeypatch.setattr(immich, "_transport", httpx.MockTransport(transport))


def test_requests_are_retried_through_drops_and_overload(fake_immich, monkeypatch):
    flaky = Flaky(fake_immich, 3, lambda request: request.url.path == "/api/tags",
                  lambda request, fake: httpx.Response(503, text="busy") if flaky.seen % 2 else cut_off(request, fake))
    use(monkeypatch, flaky)
    assert [tag["value"] for tag in immich.client().tags()] == ["Kuvat/K001"] and flaky.seen == 3


def test_retries_stop_and_say_so(fake_immich, monkeypatch):
    use(monkeypatch, Flaky(fake_immich, 99, lambda request: True, cut_off))
    with pytest.raises(immich.ImmichError, match="8 yritystä"):
        immich.client().tags()


def test_client_errors_are_not_retried(fake_immich, monkeypatch):
    flaky = Flaky(fake_immich, 1, lambda request: True, lambda request, fake: httpx.Response(404, json={"message": "no"}))
    use(monkeypatch, flaky)
    with pytest.raises(immich.ImmichError, match="404"):
        immich.client().tags()


def test_a_broken_download_is_fetched_again(fake_immich, env, monkeypatch):
    asset_id = fake_immich.assets[0]["id"]
    flaky = Flaky(fake_immich, 2, lambda request: request.url.path.endswith("/original"),
                  lambda request, fake: httpx.Response(200, content=fake.originals[asset_id][:100]))
    use(monkeypatch, flaky)
    target = env / "download.jpg"
    immich.client().download(asset_id, target, fake_immich.assets[0]["checksum"])
    assert target.read_bytes() == fake_immich.originals[asset_id] and flaky.seen == 2


def test_an_upload_whose_answer_is_lost_does_not_duplicate(fake_immich, env, monkeypatch):
    use(monkeypatch, Flaky(fake_immich, 1, lambda request: request.url.path == "/api/assets" and request.method == "POST", stored_then_cut_off))
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K"})
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    assert result["errors"] == [] and len(fake_immich.uploads) == 2
    assert [item["exported"]["target"] for item in jobs.load(job["id"])["items"]] == ["new-0", "new-1"]


def test_an_export_stopped_mid_upload_resumes_without_duplicates(fake_immich, env, monkeypatch):
    job = jobs.create({"kind": "immich-tag", "name": "Kuvat/K001"}, {"kind": "immich", "tag": "K"})

    def die(*args, **kwargs):
        raise KeyboardInterrupt  # the app stopped right after the upload, before saving the record
    add_tags = immich.Immich.add_tags
    monkeypatch.setattr(immich.Immich, "add_tags", die)
    with pytest.raises(KeyboardInterrupt):
        jobs.run_export(job["id"], only_approved=False)
    stopped = jobs.load(job["id"])
    assert jobs.export_state(stopped) == "interrupted" and stopped["items"][0]["pending"] and not stopped["items"][0]["exported"]
    monkeypatch.setattr(immich.Immich, "add_tags", add_tags)
    result = jobs.run_export(job["id"], only_approved=False)["export"]
    # The first image is rebuilt with the same UUID, so Immich sees the same bytes again.
    assert result["errors"] == [] and len(fake_immich.uploads) == 2
    items = jobs.load(job["id"])["items"]
    assert items[0]["exported"]["target"] == "new-0" and "pending" not in items[0]
    assert items[0]["exported"]["recipe"]["uuid"] == stopped["items"][0]["pending"]["uuid"]


def test_an_archive_run_cut_short_resumes(env, monkeypatch):
    target = env / "files" / "archive"
    source = {"kind": "local", "path": str(env / "files" / "scans"), "recursive": True}
    calls = {"n": 0}
    real = archive._thumbnail

    def drop_on_second(path):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("connection to the NAS share lost")
        return real(path)
    monkeypatch.setattr(archive, "_thumbnail", drop_on_second)
    with pytest.raises(OSError):
        archive.run(source, str(target))
    # What got in before the break is listed: the second original was copied and verified.
    listed = [entry["name"] for entry in json.loads((target / "manifest.json").read_text())["originals"]]
    assert listed == ["A001.jpg", "A002.jpg"]
    monkeypatch.setattr(archive, "_thumbnail", real)
    result = archive.run(source, str(target))
    assert result["originals"] == 3 and archive.verify(str(target)) == []
    names = [entry["name"] for entry in json.loads((target / "manifest.json").read_text())["originals"]]
    assert names == ["A001.jpg", "A002.jpg", "sub/B001.png"]


def test_an_unlisted_copy_of_the_original_is_adopted(env):
    # A run stopped between copying an original in and listing it leaves exactly this behind.
    target = env / "files" / "archive"
    source = {"kind": "local", "path": str(env / "files" / "scans")}
    archive.run(source, str(target))
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["originals"] = [entry for entry in manifest["originals"] if entry["name"] != "A002.jpg"]
    (target / "manifest.json").write_text(json.dumps(manifest))
    assert archive.run(source, str(target))["originals"] == 2 and archive.verify(str(target)) == []
    assert "A002.jpg" in [entry["name"] for entry in json.loads((target / "manifest.json").read_text())["originals"]]


def test_a_stray_file_that_is_not_the_original_still_stops_the_archive(env):
    target = env / "files" / "archive"
    source = {"kind": "local", "path": str(env / "files" / "scans")}
    archive.run(source, str(target))
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["originals"] = [entry for entry in manifest["originals"] if entry["name"] != "A002.jpg"]
    (target / "manifest.json").write_text(json.dumps(manifest))
    (target / "originals" / "A002.jpg").write_bytes(b"something else")
    with pytest.raises(archive.ArchiveError, match="eikä vastaa alkuperäistä"):
        archive.run(source, str(target))
