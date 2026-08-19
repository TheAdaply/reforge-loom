"""M2 fixtures — a hand-seeded node/edge graph.

The indexer (M1) is a sibling milestone, so `tests/server` seeds `nodes`/`edges` with
plain INSERTs through `ids.node_id`, which is exactly what the indexer will mint. The
topology is deliberate:

    svc.py::login ──CALLS──▶ svc.py::AuthService/authenticate ──CALLS──▶ util.py::hash_pw
    svc.py       ──IMPORTS─▶ util.py                (radius 0: never expanded)
    models.py::User, iso.py::lonely                 (disjoint — safe re-declare ground)
    svc.py::unrelated                               (shares only a FILE with the above)

CONTAINS mirrors what M1 mints (`walk.py::_write_file_nodes`, tests/indexer EXPECTED_CONTAINS):
src = container, dst = contained, one edge per nesting level, so the chain is
`svc.py -> svc.py::AuthService -> svc.py::AuthService/authenticate`. Both endpoints are
FULL refs — a bare `"AuthService"` would mint the id of a nonexistent FILE node named
`AuthService` and leave the edge dangling.
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from loom.server.db import connect, init_db, iso, now_s
from loom.server.ids import node_id, split_ref

REPO = "demo"

# (path, qualname, kind)
NODES = [
    ("svc.py", "", "File"),
    ("svc.py", "AuthService", "Class"),
    ("svc.py", "AuthService/authenticate", "Function"),
    ("svc.py", "login", "Function"),
    ("svc.py", "unrelated", "Function"),
    ("util.py", "", "File"),
    ("util.py", "hash_pw", "Function"),
    ("models.py", "", "File"),
    ("models.py", "User", "Class"),
    ("iso.py", "", "File"),
    ("iso.py", "lonely", "Function"),
]

EDGES = [
    ("svc.py::login", "svc.py::AuthService/authenticate", "CALLS"),
    ("svc.py::AuthService/authenticate", "util.py::hash_pw", "CALLS"),
    ("svc.py", "util.py", "IMPORTS"),
    ("svc.py", "svc.py::AuthService", "CONTAINS"),
    ("svc.py::AuthService", "svc.py::AuthService/authenticate", "CONTAINS"),
    ("svc.py", "svc.py::login", "CONTAINS"),
    ("svc.py", "svc.py::unrelated", "CONTAINS"),
    ("util.py", "util.py::hash_pw", "CONTAINS"),
    ("models.py", "models.py::User", "CONTAINS"),
    ("iso.py", "iso.py::lonely", "CONTAINS"),
]


def nid(ref: str) -> str:
    """Node id for a canonical ref in the seeded repo."""
    return node_id(REPO, *split_ref(ref))


def seed(path: str) -> None:
    """Create the schema and insert the fixture graph into the db at `path`."""
    init_db(path)
    con = connect(path)
    try:
        con.executemany(
            "INSERT OR IGNORE INTO nodes (id, repo, path, qualname, kind, updated) "
            "VALUES (?,?,?,?,?,?)",
            [(node_id(REPO, p, q), REPO, p, q, k, iso(now_s())) for p, q, k in NODES])
        con.executemany(
            "INSERT OR IGNORE INTO edges (src, dst, kind) VALUES (?,?,?)",
            [(nid(s), nid(d), k) for s, d, k in EDGES])
    finally:
        con.close()


SPEC = """# Spec: cache authenticate

**Agent**: aria

## Goal

Add a TTL cache in front of authenticate. Auth endpoints drop to <5ms on hit.

## Write targets

- svc.py::AuthService/authenticate

## New/changed interfaces

- CHANGED `authenticate(self, email: str, password: str) -> AuthResult`

## Assumes

- util.py::hash_pw

## Out of scope

Session storage and token refresh are untouched.
"""


@pytest.fixture()
def graph_db(tmp_path) -> str:
    """Path to a db holding the fixture graph."""
    p = str(tmp_path / "loom.sqlite3")
    seed(p)
    return p


@pytest.fixture()
def gconn(graph_db: str) -> Iterator[sqlite3.Connection]:
    """Connection to the seeded fixture graph."""
    c = connect(graph_db)
    try:
        yield c
    finally:
        c.close()


# --------------------------------------------------------------- the subprocess-server rig
#
# ONE copy of the boot/teardown dance for the five `tests/server` files that need a REAL
# server (specgate §2.6: `python -m loom.server.app`, PIPE logs, try/finally terminate).
# Each of those files keeps its own thin fixture, because the five rigs genuinely differ in
# what they BOOT — tokened, two-root, half-indexed, pre-seeded — and in nothing else. The
# duplicated halves were `free_port`, the readiness poll and the terminate/kill teardown,
# in four, five and five copies respectively; those live here now and nowhere else.


def free_port() -> int:
    """An ephemeral port, released before the server binds it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    """Poll until the server accepts connections; a server that DIED reports its own stderr
    rather than timing out silently."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:                              # pragma: no cover
            raise RuntimeError(f"server died: {proc.communicate()[1]}")
        try:
            socket.create_connection(("127.0.0.1", port), 0.25).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"server did not open port {port}")       # pragma: no cover


@contextmanager
def server_process(*args: str) -> Iterator[tuple[int, subprocess.Popen]]:
    """A live `loom.server.app` subprocess on a fresh port; `args` are the caller's own boot
    flags (`--repo-root`, `--db`, `--token`, ...). Yields `(port, proc)` — the Popen is part
    of the contract: `test_doctor` kills the server mid-test to exercise its FAIL rows."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--host", "127.0.0.1", "--port", str(port),
         *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_port(port, proc)
        yield port, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                        # pragma: no cover
            proc.kill()
