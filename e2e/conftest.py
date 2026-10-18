"""Fixtures for the end-to-end tests: one throwaway Immich per session, a fresh user per test.

Run with `uv run pytest e2e` (Docker required; the tests are skipped without it). Set
UNFADE_E2E_KEEP=1 to leave the server running afterwards, UNFADE_E2E_URL to use a running one.
"""
from __future__ import annotations

import os
import signal

import pytest

from e2e import immich_harness as harness


def _interrupt(*_):
    raise KeyboardInterrupt


@pytest.fixture(scope="session")
def immich_url():
    if os.environ.get("UNFADE_E2E_URL"):
        yield os.environ["UNFADE_E2E_URL"].rstrip("/")
        return
    if not harness.docker_available():
        pytest.skip("Docker is not available for the throwaway Immich")
    server = harness.ImmichServer()
    # A terminated run (CI cancel, timeout) still tears its Immich down: SIGTERM becomes the
    # KeyboardInterrupt that pytest unwinds through its fixtures.
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        server.start()
        yield server.url
    finally:
        if os.environ.get("UNFADE_E2E_KEEP"):
            print(f"\nImmich left running at {server.url} (compose project {server.project})")
        else:
            server.stop()
        signal.signal(signal.SIGTERM, previous)


@pytest.fixture(scope="session")
def admin(immich_url):
    return harness.admin_token(immich_url)


@pytest.fixture
def account(immich_url, admin):
    return harness.new_account(immich_url, admin)


@pytest.fixture
def unfade_env(account, tmp_path, monkeypatch):
    """Point unfade at the throwaway Immich as the test's user."""
    from unfade import immich
    monkeypatch.setenv("IMMICH_URL", account.url)
    monkeypatch.setenv("IMMICH_API_KEY", account.api_key)
    monkeypatch.setenv("UNFADE_DATA_DIR", str(tmp_path / "unfade-data"))
    monkeypatch.setenv("UNFADE_ROOTS", str(tmp_path))
    monkeypatch.setattr(immich, "_transport", None)
    return account


@pytest.fixture
def artifacts(request):
    """Where the web UI spot checks keep their screenshots (git-ignored)."""
    folder = os.path.join(os.path.dirname(__file__), "artifacts", request.node.name)
    os.makedirs(folder, exist_ok=True)
    return folder
