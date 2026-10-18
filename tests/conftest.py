import pytest


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Tests read messages in English unless they switch; the machine's locale does not matter."""
    monkeypatch.setenv("UNFADE_LANG", "en")
