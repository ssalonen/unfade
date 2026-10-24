"""A throwaway Immich for unfade's end-to-end tests, and the API calls the tests set it up with.

`ImmichServer.start()` runs e2e/docker-compose.yml under its own compose project on a free port;
`stop()` removes the containers and their volumes. UNFADE_E2E_URL points the tests at a server
that is already running instead (it must allow admin sign-up, i.e. be fresh, or accept
UNFADE_E2E_ADMIN_EMAIL / UNFADE_E2E_ADMIN_PASSWORD).
"""
from __future__ import annotations

import io
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import numpy as np
from PIL import Image

COMPOSE_FILE = Path(__file__).with_name("docker-compose.yml")
ADMIN_EMAIL = os.environ.get("UNFADE_E2E_ADMIN_EMAIL", "admin@unfade.test")
ADMIN_PASSWORD = os.environ.get("UNFADE_E2E_ADMIN_PASSWORD", "unfade-e2e-admin")


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], check=False, capture_output=True).returncode == 0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ImmichServer:
    def __init__(self):
        self.project = f"unfade-e2e-{secrets.token_hex(3)}"
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def _compose(self, *args: str, timeout: float = 900) -> subprocess.CompletedProcess:
        env = {**os.environ, "UNFADE_E2E_PORT": str(self.port)}
        files = ["-f", str(COMPOSE_FILE)]
        if os.environ.get("IMMICH_VERSION"):
            # Another Immich than the one pinned in the compose file (kept literal for Dependabot).
            override = Path(tempfile.gettempdir()) / f"{self.project}.override.yml"
            override.write_text(f"services:\n  immich-server:\n    image: ghcr.io/immich-app/immich-server:{os.environ['IMMICH_VERSION']}\n")
            files += ["-f", str(override)]
        return subprocess.run(["docker", "compose", *files, "-p", self.project, *args],
                              env=env, check=False, capture_output=True, text=True, timeout=timeout)

    def start(self) -> None:
        # A registry that is busy or rate-limits (429 "toomanyrequests") fails a pull outright;
        # pull first and try again a few times rather than fail every test on a passing refusal.
        for pause in (10, 30, 60, None):
            pulled = self._compose("pull", "--quiet")
            if not pulled.returncode or pause is None:
                break
            time.sleep(pause)
        result = self._compose("up", "-d", "--wait")
        if result.returncode:
            logs = self._compose("logs", "--tail", "80").stdout
            self.stop()
            raise RuntimeError(f"Immich did not start:\n{result.stderr}\n{logs}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if httpx2.get(f"{self.url}/api/server/ping", timeout=5).json().get("res") == "pong":
                    return
            except (httpx2.HTTPError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError("Immich did not answer /api/server/ping")

    def logs(self) -> str:
        return self._compose("logs", "--tail", "200").stdout

    def stop(self) -> None:
        self._compose("down", "-v", "--remove-orphans", timeout=300)
        (Path(tempfile.gettempdir()) / f"{self.project}.override.yml").unlink(missing_ok=True)


@dataclass
class Account:
    """One Immich user: a session token for setup and checks, an API key for unfade."""
    url: str
    email: str
    password: str
    token: str
    api_key: str

    def client(self, key: bool = False) -> httpx2.Client:
        headers = {"x-api-key": self.api_key} if key else {"Authorization": f"Bearer {self.token}"}
        return httpx2.Client(base_url=f"{self.url}/api", headers=headers, timeout=60)


def _login(url: str, email: str, password: str) -> str:
    response = httpx2.post(f"{url}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    response.raise_for_status()
    return response.json()["accessToken"]


def admin_token(url: str) -> str:
    """Sign the admin up on a fresh server (refused once there is one), then log in."""
    httpx2.post(f"{url}/api/auth/admin-sign-up", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Admin"}, timeout=30)
    return _login(url, ADMIN_EMAIL, ADMIN_PASSWORD)


def new_account(url: str, admin: str) -> Account:
    """A fresh user with an all-permissions API key, so every test starts from an empty library."""
    email = f"user-{secrets.token_hex(4)}@unfade.test"
    password = secrets.token_hex(8)
    headers = {"Authorization": f"Bearer {admin}"}
    response = httpx2.post(f"{url}/api/admin/users", headers=headers, timeout=30,
                          json={"email": email, "password": password, "name": "Testi Käyttäjä", "shouldChangePassword": False})
    response.raise_for_status()
    token = _login(url, email, password)
    # Skip the web UI's first-login onboarding, so the spot checks land on the timeline.
    httpx2.put(f"{url}/api/users/me/onboarding", headers={"Authorization": f"Bearer {token}"}, json={"isOnboarded": True}, timeout=30).raise_for_status()
    response = httpx2.post(f"{url}/api/api-keys", headers={"Authorization": f"Bearer {token}"}, json={"name": "unfade", "permissions": ["all"]}, timeout=30)
    response.raise_for_status()
    return Account(url, email, password, token, response.json()["secret"])


def wait_for_jobs(url: str, admin: str, timeout: float = 180) -> None:
    """Wait until Immich's background queues (metadata, thumbnails, sidecars...) are idle."""
    headers = {"Authorization": f"Bearer {admin}"}
    deadline = time.monotonic() + timeout
    idle_since = None
    while time.monotonic() < deadline:
        response = httpx2.get(f"{url}/api/queues", headers=headers, timeout=30)
        if response.status_code == 404:  # Immich v2: the legacy jobs endpoint
            counts = [queue["jobCounts"] for queue in httpx2.get(f"{url}/api/jobs", headers=headers, timeout=30).json().values()]
        else:
            counts = [queue["statistics"] for queue in response.json()]
        busy = sum(count.get("active", 0) + count.get("waiting", 0) + count.get("delayed", 0) for count in counts)
        if busy == 0:
            # Finished jobs queue follow-up jobs; idle twice in a row means really idle.
            if idle_since and time.monotonic() - idle_since > 1.5:
                return
            idle_since = idle_since or time.monotonic()
        else:
            idle_since = None
        time.sleep(0.5)
    raise TimeoutError("Immich's job queues did not go idle")


def faded_photo(seed: int, cast=(1.0, 0.78, 0.6), size=(480, 320), taken: datetime | None = None, make: str = "Testiskanneri") -> bytes:
    """A deterministic faded photo: a varied scene, low contrast, a warm cast, EXIF."""
    rng = np.random.default_rng(seed)
    height, width = size[1], size[0]
    y, x = np.mgrid[0:height, 0:width]
    scene = np.stack([np.sin(x / rng.uniform(20, 70) + seed), np.cos(y / rng.uniform(20, 70)), np.sin((x + y) / rng.uniform(30, 90))], -1)
    pixels = ((0.3 + 0.35 * (scene * 0.5 + 0.5) + rng.normal(0, 0.02, scene.shape)) * np.array(cast)).clip(0, 1)
    exif = Image.Exif()
    exif[0x010F] = make
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")
    buffer = io.BytesIO()
    Image.fromarray((pixels * 255).astype(np.uint8)).save(buffer, "JPEG", quality=92, exif=exif.tobytes())
    return buffer.getvalue()


def upload(api: httpx2.Client, name: str, content: bytes) -> str:
    now = datetime.now(UTC).isoformat()
    response = api.post("/assets", files={"assetData": (name, content)},
                        data={"deviceAssetId": f"e2e-{name}", "deviceId": "unfade-e2e", "fileCreatedAt": now, "fileModifiedAt": now})
    response.raise_for_status()
    return response.json()["id"]


def wait_for_metadata(api: httpx2.Client, asset_ids: list[str], timeout: float = 120) -> None:
    """Wait until Immich has read each asset's metadata (its image size is known). Idle queues
    are not enough on a fresh server, where extraction may not have started yet."""
    deadline = time.monotonic() + timeout
    for asset_id in asset_ids:
        while not (api.get(f"/assets/{asset_id}").json().get("exifInfo") or {}).get("exifImageWidth"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Immich did not read the metadata of {asset_id}")
            time.sleep(0.25)


def tag(api: httpx2.Client, value: str, asset_ids: list[str]) -> str:
    """Tag assets once Immich has read them: its metadata extraction of a fresh upload replaces
    tags added earlier with the file's (none). Give an asset several tags with tag_each."""
    wait_for_metadata(api, asset_ids)
    tag_id = api.put("/tags", json={"tags": [value]}).raise_for_status().json()[0]["id"]
    api.put(f"/tags/{tag_id}/assets", json={"ids": asset_ids}).raise_for_status()
    return tag_id


def tag_each(api: httpx2.Client, wanted: dict[str, list[str]]) -> None:
    """Tag assets with {asset id: [tag values]}, one update per asset (see Immich.add_tags)."""
    wait_for_metadata(api, list(wanted))
    ids = {value: api.put("/tags", json={"tags": [value]}).raise_for_status().json()[0]["id"]
           for values in wanted.values() for value in values}
    for asset_id, values in wanted.items():
        api.put("/tags/assets", json={"tagIds": [ids[value] for value in values], "assetIds": [asset_id]}).raise_for_status()


def album(api: httpx2.Client, name: str, asset_ids: list[str]) -> str:
    return api.post("/albums", json={"albumName": name, "assetIds": asset_ids}).raise_for_status().json()["id"]


class ChaosProxy:
    """A TCP proxy in front of Immich that breaks connections on purpose.

    `cuts` connections are reset mid-transfer once they have carried `after` bytes in either
    direction (an upload or download broken off part-way); during `outage(seconds)` every new
    connection is reset at once. Point IMMICH_URL at `url`.
    """

    def __init__(self, target: str, cuts: int = 0, after: int = 16384):
        parsed = httpx2.URL(target)
        self.target = (parsed.host, parsed.port)
        self.cuts, self.after, self.cut, self.refused = cuts, after, 0, 0
        self.down_until = 0.0
        self.lock = threading.Lock()
        self.server = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}"
        threading.Thread(target=self._accept, daemon=True).start()

    def outage(self, seconds: float) -> None:
        self.down_until = time.monotonic() + seconds

    @staticmethod
    def _reset(*sockets) -> None:
        # shutdown() wakes the other pipe thread blocked in recv(); close() alone would leave the
        # socket open under it and the client waiting for its read timeout.
        for sock in sockets:
            for step in (lambda sock=sock: sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)),
                         lambda sock=sock: sock.shutdown(socket.SHUT_RDWR), sock.close):
                try:
                    step()
                except OSError:
                    pass

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            if time.monotonic() < self.down_until:
                self.refused += 1
                self._reset(client)
                continue
            try:
                upstream = socket.create_connection(self.target)
            except OSError:
                self._reset(client)
                continue
            state = {"bytes": 0, "closed": False}
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(source, sink, client, upstream, state), daemon=True).start()

    def _pipe(self, source, sink, client, upstream, state) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                with self.lock:
                    state["bytes"] += len(data)
                    breaking = state["bytes"] > self.after and self.cut < self.cuts and not state.get("cut")
                    if breaking:
                        self.cut += 1
                        state["cut"] = True
                if breaking:
                    sink.sendall(data[: len(data) // 2])
                    self._reset(client, upstream)
                    return
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self.server.close()


def launch_chromium(playwright):
    """Playwright's own Chromium, or the browser named by UNFADE_E2E_CHROMIUM (for a machine that
    already has a Chromium and cannot download the one this Playwright version expects)."""
    return playwright.chromium.launch(executable_path=os.environ.get("UNFADE_E2E_CHROMIUM") or None)
