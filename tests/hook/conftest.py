"""M3 hook-test rig: a canned-response stub server + an isolated HOME/repo per test.

M3 NEVER imports M2 code — the stub returns frozen §6 JSON copied verbatim from BUILD-SPEC,
so `tests/hook` proves the hook contract on its own. Everything is tmp-scoped: the real
`~/.loom/` and the real `.claude/settings.json` are never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pretooluse"

# The tmp repo's single module; every Edit fixture's `old_string` is a literal slice of it.
DEMO_SRC = '''"""demo module."""

import os


class AuthService:
    def authenticate(self, email, password):
        token = _mint(email)
        return token

    def logout(self, token):
        return None


def _mint(email):
    def _inner():
        return os.urandom(8)

    return _inner()


def helper():
    return 1
'''


class _StubHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        self.server.requests.append(json.loads(raw or b"{}"))
        if self.server.delay:
            time.sleep(self.server.delay)
        body = json.dumps(self.server.response).encode("utf-8")
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — `loom init` pings GET /health
        body = json.dumps({"ok": True, "repo": "demo"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture()
def stub() -> Iterator[ThreadingHTTPServer]:
    """A fresh canned-response server per test (daemon threads: the sleeper cannot hang us)."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    srv.daemon_threads = True
    srv.requests = []
    srv.response = {"decision": "allow", "case": "in_plan", "message": "", "node_id": None,
                    "plan_id": None}
    srv.delay = 0.0
    srv.status = 200
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture()
def repo_root(tmp_path: Path) -> str:
    """A throwaway repo containing `src/app.py` and an (empty) notebooks dir."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "notebooks").mkdir()
    (root / "src" / "app.py").write_text(DEMO_SRC, encoding="utf-8")
    return str(root)


@pytest.fixture()
def home(tmp_path: Path) -> str:
    """An isolated HOME so `~/.loom/` never resolves to the developer's real one."""
    h = tmp_path / "home"
    (h / ".loom").mkdir(parents=True)
    return str(h)


@pytest.fixture()
def configure(home: str) -> Callable[..., None]:
    """Write `~/.loom/config.toml` into the isolated HOME."""

    def _write(server_url: str, repo_root_dir: str, agent: str = "akash-mbp") -> None:
        Path(home, ".loom", "config.toml").write_text(
            f'server_url = "{server_url}"\nagent = "{agent}"\nrepo = "demo"\n'
            f'repo_root = "{repo_root_dir}"\n',
            encoding="utf-8",
        )

    return _write


@pytest.fixture()
def payload(repo_root: str) -> Callable[[str], str]:
    """Fixture JSONs carry the `__REPO_ROOT__` token; absolute paths are minted per run."""

    def _load(name: str) -> str:
        return FIXTURES.joinpath(f"{name}.json").read_text(encoding="utf-8").replace(
            "__REPO_ROOT__", repo_root
        )

    return _load


@pytest.fixture()
def run_gate(home: str) -> Callable[..., subprocess.CompletedProcess]:
    """Run gate.py AS A SUBPROCESS (the only honest test of its exit contract)."""

    def _run(payload: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "HOME": home}
        env.pop("LOOM_AGENT_MODE", None)
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-m", "loom.hook.gate"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

    return _run
