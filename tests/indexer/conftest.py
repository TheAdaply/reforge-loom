"""M1 fixtures: a writable copy of `tests/fixtures/pyrepo`, indexed into a tmp database.

Helpers are exposed through the `graph` FIXTURE rather than importable module functions.
`tests/` is not a package, so a helper reached by import would depend on pytest's rootdir
insertion — which happens to work (`tests/server/conftest.py` is imported by name that way)
but is not something to build a fixture contract on.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from loom.indexer.walk import index_repo
from loom.server.db import connect, init_db
from loom.server.ids import node_ref

FIXTURE_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "pyrepo"


class Graph:
    """Thin query surface over one indexed repo."""

    repo = "fx"

    def __init__(self, conn: sqlite3.Connection, root: str) -> None:
        self.conn, self.root = conn, root

    def reindex(self, changed_only: bool = True) -> dict:
        # index_repo owns its transactions (§11.45) — wrapping it would nest BEGINs.
        return index_repo(self.conn, self.repo, self.root, changed_only=changed_only)

    def nodes(self) -> dict[str, tuple[str, str, str]]:
        """node_id -> (path, qualname, kind)."""
        return {
            r["id"]: (r["path"], r["qualname"], r["kind"])
            for r in self.conn.execute(
                "SELECT id, path, qualname, kind FROM nodes WHERE repo=?", (self.repo,))
        }

    def refs(self) -> dict[str, str]:
        return {
            r["id"]: node_ref(r["path"], r["qualname"])
            for r in self.conn.execute("SELECT id, path, qualname FROM nodes WHERE repo=?", (self.repo,))
        }

    def edges(self, kind: str) -> set[tuple[str, str]]:
        """Edges of one kind as canonical `(src_ref, dst_ref)` pairs."""
        ref = self.refs()
        return {
            (ref.get(r["src"], r["src"]), ref.get(r["dst"], r["dst"]))
            for r in self.conn.execute("SELECT src, dst FROM edges WHERE kind=?", (kind,))
        }

    def raw_edges(self) -> set[tuple[str, str, str]]:
        return {(r["src"], r["dst"], r["kind"]) for r in self.conn.execute("SELECT src, dst, kind FROM edges")}

    def write(self, rel_path: str, text: str) -> None:
        with open(str(Path(self.root, *rel_path.split("/"))), "w") as fh:
            fh.write(text)

    def read(self, rel_path: str) -> str:
        with open(str(Path(self.root, *rel_path.split("/")))) as fh:
            return fh.read()


@pytest.fixture()
def repo_root(tmp_path) -> str:
    """An isolated, MUTABLE copy of the fixture repo (tests never edit the original)."""
    dst = tmp_path / "pyrepo"
    shutil.copytree(FIXTURE_SRC, dst)
    return str(dst)


@pytest.fixture()
def graph(repo_root: str, tmp_path) -> Iterator[Graph]:
    """The fixture repo after one FULL index."""
    db = str(tmp_path / "loom.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        g = Graph(conn, repo_root)
        g.reindex(changed_only=False)
        yield g
    finally:
        conn.close()
