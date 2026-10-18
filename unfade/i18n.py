"""User-facing text in English or Finnish.

Text is written in English in the code, templates and scripts, wrapped in t(); the Finnish
translations live in unfade/locale_fi.py, keyed by the English text. The language is chosen per
request in the web UI (a ?lang= switch kept in a cookie, else the browser's Accept-Language) and
from UNFADE_LANG or the locale (LC_ALL, LC_MESSAGES, LANG) on the command line; English otherwise.
"""
from __future__ import annotations

import contextvars
import os
import re
from pathlib import Path

from unfade.locale_fi import FI

LANGUAGES = ("en", "fi")
LANGUAGE_NAMES = {"en": "English", "fi": "Suomi"}
_language: contextvars.ContextVar[str | None] = contextvars.ContextVar("language", default=None)
STATIC = Path(__file__).parent / "static"


def default_language() -> str:
    """The command line's language: UNFADE_LANG, else the locale; English unless it says Finnish."""
    for name in ("UNFADE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if value:
            return "fi" if value.lower().startswith("fi") else "en"
    return "en"


def language() -> str:
    return _language.get() or default_language()


def use(code: str) -> contextvars.Token:
    """Switch the current context to a language; returns a token for reset()."""
    return _language.set(code if code in LANGUAGES else "en")


def reset(token: contextvars.Token) -> None:
    _language.reset(token)


def from_accept_language(header: str) -> str:
    """The first supported language in an Accept-Language header, by quality; English otherwise."""
    choices = []
    for position, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        code = tag.strip().lower().split("-")[0]
        if code in LANGUAGES and quality > 0:
            choices.append((-quality, position, code))
    return min(choices)[2] if choices else "en"


def t(text: str, /, **values) -> str:
    """`text` in the current language, with {name} placeholders filled from `values`.

    A message that is not in the catalog as a whole is looked up by the part before ": " (the
    rest is detail, such as an error from Immich), so "Folder not found: /x" finds "Folder not found".
    """
    if language() == "fi":
        if text in FI:
            text = FI[text]
        elif ": " in text:
            head, _, detail = text.partition(": ")
            if head in FI:
                text = f"{FI[head]}: {detail}"
    return text.format(**values) if values else text


def N_(text: str) -> str:
    """Mark text stored as it is and translated when shown, such as a suggested group's name."""
    return text


def label(text: str) -> str:
    """A stored name shown in the current language: each " · " part that is a known text is translated,
    so a suggested name like "Top folder · possibly dark · check" follows the language and a
    name someone typed stays as typed."""
    parts, shown, start = text.split(" · "), [], 0
    while start < len(parts):
        # The longest run of parts that is a known text, since a text may itself contain " · ".
        end = next((end for end in range(len(parts), start, -1) if " · ".join(parts[start:end]) in FI), None)
        if end is None:
            shown.append(parts[start])
            start += 1
        else:
            shown.append(t(" · ".join(parts[start:end])))
            start = end
    return " · ".join(shown)


def script_texts() -> dict[str, str]:
    """The browser scripts' texts in the current language, for window.unfadeText."""
    keys = set()
    for path in STATIC.glob("*.js"):
        keys.update(re.findall(r"\bt\('((?:[^'\\]|\\.)*)'", path.read_text()))
    return {key: t(key) for key in sorted(keys)}
