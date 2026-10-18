"""Spot checks in the browser (Playwright): unfade's own UI running a job against the throwaway
Immich, and Immich's web UI showing the result. Screenshots go to e2e/artifacts/<test>/."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

import httpx
import pytest

from unfade.__main__ import main
from unfade import jobs
from e2e import immich_harness as harness

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def library(unfade_env, admin):
    api = unfade_env.client()
    ids = [harness.upload(api, f"K001-000{n}.jpg", harness.faded_photo(n, taken=datetime(1975, 6, 1, 12))) for n in (1, 2)]
    harness.wait_for_jobs(unfade_env.url, admin)
    harness.tag(api, "Kuvat/K001", ids)
    harness.album(api, "Kesä 1975", [ids[0]])
    api.put(f"/assets/{ids[0]}", json={"description": "Mökillä", "dateTimeOriginal": "1975-06-01T14:30:00.000+02:00",
                                       "latitude": 61.5, "longitude": 23.8}).raise_for_status()
    harness.wait_for_jobs(unfade_env.url, admin)
    return {"api": api, "originals": ids, "account": unfade_env, "admin": admin}


@pytest.fixture
def browser():
    with playwright.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


def immich_page(browser, account):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{account.url}/auth/login")
    page.fill("input#email", account.email)
    page.fill("input#password", account.password)
    page.get_by_role("button", name="Login").click()
    page.wait_for_url("**/photos", timeout=30000)
    return page


def test_immich_shows_the_stacked_copy_with_the_originals_details(library, browser, artifacts):
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "Kuvat/Korjattu"]) == 0
    harness.wait_for_jobs(library["account"].url, library["admin"])
    copy_id = next(item["exported"]["target"] for item in jobs.list_jobs()[0]["items"] if item["name"] == "K001-0001.jpg")
    page = immich_page(browser, library["account"])
    page.screenshot(path=f"{artifacts}/timeline.png")
    page.goto(f"{library['account'].url}/photos/{copy_id}")
    page.wait_for_load_state("networkidle")
    page.keyboard.press("i")
    page.get_by_text("Appears in").wait_for(timeout=15000)
    page.screenshot(path=f"{artifacts}/copy-info.png")
    text = page.locator("body").inner_text()
    # "Un-stack" is in the asset's menu only when it belongs to a stack.
    for expected in ("Jun 1, 1975", "GMT+02:00", "K001-0001.jxl", "Testiskanneri", "Kesä 1975", "Un-stack"):
        assert expected in text, expected
    # The description is an editable field, so it is its value rather than page text.
    assert page.locator("textarea").first.input_value() == "Mökillä"


def test_immich_trash_holds_the_replaced_originals(library, browser, artifacts):
    assert main(["run", "--source", "immich-tag:Kuvat/K001", "--dest", "immich", "--dest-tag", "K", "--replace-original"]) == 0
    harness.wait_for_jobs(library["account"].url, library["admin"])
    page = immich_page(browser, library["account"])
    page.goto(f"{library['account'].url}/trash")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path=f"{artifacts}/trash.png")
    assert page.locator("[data-asset-id]").count() == 2 or page.locator("img").count() >= 2
    page.goto(f"{library['account'].url}/photos")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    page.screenshot(path=f"{artifacts}/timeline-after-replace.png")


@pytest.fixture
def unfade_server(library, tmp_path):
    port = harness._free_port()
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    process = subprocess.Popen([sys.executable, "-m", "unfade", "serve", "--port", str(port)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        process.kill()
        raise RuntimeError(process.stdout.read())
    yield url
    process.terminate()
    process.wait(10)


def test_unfade_ui_job_from_immich_tag_to_immich(library, unfade_server, browser, artifacts):
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(unfade_server)
    assert "Immich yhdistetty käyttäjänä Testi Käyttäjä" in page.locator("body").inner_text()
    page.check("input[name=source_kind][value=immich-tag]")
    page.select_option("select[name=immich_tag]", label="Kuvat/K001")
    page.check("input[name=destination_kind][value=immich]")
    page.fill("input[name=destination_tag]", "Kuvat/Korjattu")
    page.check("input[name=original_mode][value=stack]")
    page.screenshot(path=f"{artifacts}/setup.png", full_page=True)
    page.click("#job-form > button")
    page.wait_for_url("**/jobs/*")
    page.locator(".clip-canvas").first.wait_for()
    page.wait_for_timeout(1500)
    page.screenshot(path=f"{artifacts}/job.png", full_page=True)
    # Tag sources get one group (plus any outliers), not one group per upload folder.
    assert page.locator(".color-group").count() == 1
    page.click("form[action$='/approve'] button")
    page.wait_for_url("**saved=all**")
    page.click("#export button")
    page.wait_for_function("document.querySelector('#export-status').textContent.includes('valmis')", timeout=60000)
    page.screenshot(path=f"{artifacts}/exported.png", full_page=True)
    assert "kirjoitettu 2" in page.locator("#export-status").inner_text()
    harness.wait_for_jobs(library["account"].url, library["admin"])
    tagged = library["api"].post("/search/metadata", json={"originalFileName": ".jxl", "size": 10}).json()["assets"]["items"]
    assert sorted(asset["originalFileName"] for asset in tagged) == ["K001-0001.jxl", "K001-0002.jxl"]


def test_previews_survive_a_dropped_first_request(tmp_path, monkeypatch, browser, artifacts):
    """Every preview's first request is aborted, as on a flaky connection; the cards still render."""
    scans = tmp_path / "scans"
    scans.mkdir()
    for n in (1, 2):
        (scans / f"S{n}.jpg").write_bytes(harness.faded_photo(n))
    env = {**os.environ, "PYTHONPATH": os.getcwd(), "UNFADE_DATA_DIR": str(tmp_path / "data"), "UNFADE_ROOTS": str(tmp_path)}
    env.pop("IMMICH_URL", None)
    port = harness._free_port()
    process = subprocess.Popen([sys.executable, "-m", "unfade", "serve", "--port", str(port)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(url, timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url)
        page.fill("#source-path", str(scans))
        page.fill("#destination-path", str(tmp_path / "out"))
        page.click("#job-form > button")
        page.wait_for_url("**/jobs/*")
        dropped = set()

        def flaky(route):
            if route.request.url not in dropped and "retry=" not in route.request.url:
                dropped.add(route.request.url)
                route.abort("connectionreset")
            else:
                route.continue_()
        page.route("**/preview/**", flaky)
        page.reload()
        page.wait_for_function("[...document.querySelectorAll('.clip-badge')].every(b => b.textContent.includes('leikkautuu'))", timeout=20000)
        page.screenshot(path=f"{artifacts}/previews-after-drops.png", full_page=True)
        assert dropped and "esikatselu puuttuu" not in page.locator("body").inner_text()
    finally:
        process.terminate()
        process.wait(10)
