import pytest

from tests.test_unfade import env, fake_immich, quick_retries  # noqa: F401 - shared pytest fixtures


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Tests read messages in English unless they switch; the machine's locale does not matter."""
    monkeypatch.setenv("UNFADE_LANG", "en")
