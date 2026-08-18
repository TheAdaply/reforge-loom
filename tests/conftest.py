"""Shared pytest fixtures (M0). Everything is tmp-path scoped — no ambient state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from loom.server.db import connect, init_db


@pytest.fixture(autouse=True)
def no_ambient_loom_token(monkeypatch) -> None:
    """ITERATION-2-SPEC §5a — the suite never inherits an operator's `LOOM_TOKEN`.

    Every subprocess rig here spawns with `os.environ`, so an ambient token would arm
    `resolve_token` inside servers the test meant to start OPEN: `/health` answers
    `auth=token`, the headerless `/state` and `/gate` calls turn 401, and the frozen-wire
    asserts red — on that machine only, which is the worst kind of red. Autouse and
    tests-wide so no rig has to remember. A test that WANTS the env var sets it in its own
    body; `monkeypatch.setenv` there runs after this fixture and wins.
    """
    monkeypatch.delenv("LOOM_TOKEN", raising=False)


@pytest.fixture()
def db_path(tmp_path) -> str:
    """Path to a fresh, schema-initialized loom database."""
    p = str(tmp_path / "loom.sqlite3")
    init_db(p)
    return p


@pytest.fixture()
def conn(db_path: str) -> Iterator[sqlite3.Connection]:
    """A connection to the fresh db with every §2 pragma applied."""
    c = connect(db_path)
    try:
        yield c
    finally:
        c.close()
