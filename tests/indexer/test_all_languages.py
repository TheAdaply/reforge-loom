"""Bench E3 fix: every file gets a File node so file-level claims work in ANY language;
symbol parsing stays Python-only. Also covers the index-size cap and tests/ inclusion (W5)."""

import os

from loom.indexer.walk import _INDEX_CAP_BYTES, index_repo
from loom.server.db import connect, init_db


def _mk(root, rel, content=b"x = 1\n"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _index(tmp_path, root):
    db = str(tmp_path / "l.sqlite3")
    init_db(db)
    conn = connect(db)
    index_repo(conn, "r", root, changed_only=False)
    return conn


def test_non_python_files_get_file_nodes(tmp_path):
    root = str(tmp_path / "repo")
    _mk(root, "app.ts", b"export function hi() {}\n")
    _mk(root, "main.go", b"package main\n")
    _mk(root, "svc.py", b"def f():\n    return 1\n")
    conn = _index(tmp_path, root)
    rows = {(r["path"], r["qualname"]) for r in
            conn.execute("SELECT path, qualname FROM nodes WHERE repo='r'")}
    assert ("app.ts", "") in rows and ("main.go", "") in rows   # file-level, any language
    assert ("svc.py", "f") in rows                              # symbols still Python-only
    assert not any(p == "app.ts" and q for p, q in rows)        # no TS symbols invented


def test_oversize_files_are_skipped(tmp_path):
    root = str(tmp_path / "repo")
    _mk(root, "big.bin", b"\0" * (_INDEX_CAP_BYTES + 1))
    _mk(root, "ok.py", b"def g():\n    return 2\n")
    conn = _index(tmp_path, root)
    paths = {r["path"] for r in conn.execute("SELECT path FROM nodes WHERE repo='r'")}
    assert "big.bin" not in paths and "ok.py" in paths


def test_tests_dirs_are_indexed_and_gateable(tmp_path):
    root = str(tmp_path / "repo")
    _mk(root, "tests/test_thing.py", b"def test_a():\n    assert True\n")
    conn = _index(tmp_path, root)
    rows = {(r["path"], r["qualname"]) for r in
            conn.execute("SELECT path, qualname FROM nodes WHERE repo='r'")}
    assert ("tests/test_thing.py", "") in rows          # W5: test trees are gated now
    assert ("tests/test_thing.py", "test_a") in rows


def test_incremental_tracks_non_python_changes(tmp_path):
    root = str(tmp_path / "repo")
    _mk(root, "app.ts", b"v1\n")
    _mk(root, "svc.py", b"def f():\n    return 1\n")
    conn = _index(tmp_path, root)
    _mk(root, "app.ts", b"v2 changed\n")
    stats = index_repo(conn, "r", root, changed_only=True)
    assert stats["changed"] == ["app.ts"]
