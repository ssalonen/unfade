"""The English and Finnish user interface: every text has a translation, and the language switch works."""
import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unfade import color_methods, i18n, locations
from unfade.locale_fi import FI
from unfade.web import create_app

PACKAGE = Path(__file__).resolve().parents[1] / "unfade"
# Raised in English by the modules that decide a copy's pixels (they stay free of i18n so the
# archived code snapshot does not change with a translation) and translated where shown.
UNTRANSLATED_ERRORS = ["Unknown correction method", "Invalid adjustment", "Adjustment out of range",
                       "Unsupported image bit depth", "Could not read the JPEG XL container"]


def texts_in_python() -> set[str]:
    found = set()
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "N_") and node.args:
                found.update(_literals(node.args[0]))
    return found


def _literals(node) -> list[str]:
    """The texts a t() argument can be: a literal, or either branch of `a if condition else b`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


def texts_in_templates() -> set[str]:
    found = set()
    for path in (PACKAGE / "templates").glob("*.html"):
        text = path.read_text()
        found.update(re.findall(r"""\bt\("((?:[^"\\]|\\.)*)\"""", text))
        found.update(re.findall(r"""\bt\('((?:[^'\\]|\\.)*)'""", text))
        # Labels in the templates' own (value, label) tuples, shown through t(label).
        found.update(re.findall(r"""\('[a-z-]+', '([A-Z][^']+)'\)""", text))
    return found


def all_texts() -> set[str]:
    return (texts_in_python() | texts_in_templates() | set(i18n.script_texts())
            | {description for _, description in color_methods.METHOD_LABELS.values()}
            | set(locations.FORMAT_LABELS.values()) | set(UNTRANSLATED_ERRORS))


def test_every_text_has_a_finnish_translation_and_none_is_unused():
    texts = all_texts()
    assert sorted(texts - set(FI)) == []
    assert sorted(set(FI) - texts) == []


def test_translations_keep_their_placeholders():
    for english, finnish in FI.items():
        assert sorted(re.findall(r"\{(\w+)", english)) == sorted(re.findall(r"\{(\w+)", finnish)), english


def test_no_finnish_is_left_outside_the_catalog():
    finnish = re.compile(r"[äöÄÖ]")
    for path in [*PACKAGE.glob("*.py"), *(PACKAGE / "templates").glob("*.html"), *(PACKAGE / "static").glob("*.js"), *(PACKAGE / "static").glob("*.css")]:
        if path.name != "locale_fi.py":
            assert not finnish.search(path.read_text()), path.name


@pytest.mark.parametrize(("header", "expected"), [
    ("fi-FI,fi;q=0.9,en;q=0.8", "fi"), ("en-US,en;q=0.9,fi;q=0.8", "en"), ("de-DE,fi;q=0.5", "fi"),
    ("de-DE", "en"), ("", "en"), ("fi;q=0", "en"), ("sv,fi;q=0.4,en;q=0.6", "en"),
])
def test_browser_language(header, expected):
    assert i18n.from_accept_language(header) == expected


def test_command_line_language_follows_unfade_lang_then_the_locale(monkeypatch):
    for name in ("UNFADE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    assert i18n.language() == "en"
    monkeypatch.setenv("LANG", "fi_FI.UTF-8")
    assert i18n.language() == "fi" and i18n.t("Jobs") == "Työt"
    monkeypatch.setenv("UNFADE_LANG", "en")
    assert i18n.language() == "en" and i18n.t("Jobs") == "Jobs"


def test_detail_after_a_known_message_is_kept(monkeypatch):
    monkeypatch.setenv("UNFADE_LANG", "fi")
    assert i18n.t("Unsupported image bit depth: uint32") == f"{FI['Unsupported image bit depth']}: uint32"
    assert i18n.t("Something else: detail") == "Something else: detail"


def test_web_ui_follows_the_browser_and_the_switch(env):
    client = TestClient(create_app())
    english = client.get("/", headers={"Accept-Language": "en-GB,en"})
    assert '<html lang="en">' in english.text and "New job" in english.text and "Uusi työ" not in english.text
    finnish = client.get("/", headers={"Accept-Language": "fi-FI,fi;q=0.9"})
    assert '<html lang="fi">' in finnish.text and "Uusi työ" in finnish.text
    # The scripts get their texts in the page's language.
    assert '"Browse folders": "Selaa kansioita"' in finnish.text
    # The switch wins over the browser and is remembered.
    switched = client.get("/?lang=fi", headers={"Accept-Language": "en"})
    assert "Uusi työ" in switched.text and client.cookies.get("unfade-lang") == "fi"
    assert "Uusi työ" in client.get("/", headers={"Accept-Language": "en"}).text
    assert "New job" in client.get("/?lang=en").text and "New job" in client.get("/", headers={"Accept-Language": "fi"}).text


def test_errors_are_shown_in_the_chosen_language(env):
    client = TestClient(create_app())
    form = {"source_kind": "local", "source_path": str(env / "files" / "missing"), "destination_kind": "local",
            "destination_path": str(env / "files" / "out"), "method": "fade", "format": "jxl"}
    assert "Folder not found" in client.post("/jobs?lang=en", data=form).text
    assert "Kansiota ei löydy" in client.post("/jobs?lang=fi", data=form).text


def test_suggested_group_names_follow_the_language_and_typed_names_stay(env):
    from unfade import jobs
    client = TestClient(create_app())
    job = jobs.create({"kind": "local", "path": str(env / "files" / "scans")}, {"kind": "local", "path": str(env / "files" / "out")})
    assert job["groups"][0]["label"] == "All images"
    assert "Kaikki kuvat" in client.get(f"/jobs/{job['id']}?lang=fi").text
    # Saving the method with the shown (Finnish) name keeps the stored name language-neutral.
    client.post(f"/jobs/{job['id']}/groups/1", data={"label": "Kaikki kuvat", "method": "gwg"})
    assert jobs.load(job["id"])["groups"][0] == {**jobs.load(job["id"])["groups"][0], "label": "All images", "method": "gwg"}
    assert "All images" in client.get(f"/jobs/{job['id']}?lang=en").text
    client.post(f"/jobs/{job['id']}/groups/1", data={"label": "Kesä · rannalla", "method": "gwg"})
    assert "Kesä · rannalla" in client.get(f"/jobs/{job['id']}?lang=en").text
    assert i18n.label("sub · possibly dark · check") == "sub · possibly dark · check"
    token = i18n.use("fi")
    try:
        assert i18n.label("sub · possibly dark · check") == "sub · mahdollisesti tumma · tarkista"
    finally:
        i18n.reset(token)
