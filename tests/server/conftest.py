"""M2 fixtures — a hand-seeded node/edge graph.

The indexer (M1) is a sibling milestone, so `tests/server` seeds `nodes`/`edges` with
plain INSERTs through `ids.node_id`, which is exactly what the indexer will mint. The
topology is deliberate:

    svc.py::login ──CALLS──▶ svc.py::AuthService/authenticate ──CALLS──▶ util.py::hash_pw
    svc.py       ──IMPORTS─▶ util.py                (radius 0: never expanded)
    models.py::User, iso.py::lonely                 (disjoint — safe re-declare ground)
"""

from __future__ import annotations

import sqlite3
from typing import Iterator

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
    ("svc.py", "AuthService", "CONTAINS"),
    ("svc.py", "login", "CONTAINS"),
    ("util.py", "hash_pw", "CONTAINS"),
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
