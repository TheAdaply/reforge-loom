"""Shared pytest fixtures (M0). Everything is tmp-path scoped — no ambient state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from loom.server.db import connect, init_db


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
