"""M0 smoke — schema + pragmas (§2) and the in-process MCP `health` tool (§5.1/§5.11).

The server is booted IN-PROCESS: `Client` given an `MCPServer` instance connects without a
transport (specgate §3.5). The dev dependency group is frozen to pytest only, so the async
block is driven by `asyncio.run` inside a plain sync test.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from mcp import Client

from loom import __version__
from loom.server.app import INSTRUCTIONS, build_server
from loom.server.db import connect, immediate, log_event


def test_schema_has_every_frozen_table(conn: sqlite3.Connection) -> None:
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"nodes", "edges", "plans", "claims", "events"} <= names


def test_nodes_unique_constraint_is_live(conn: sqlite3.Connection) -> None:
    row = ("n-1", "demo", "a.py", "f", "Function", "", "", 1, 2, "2026-01-01T00:00:00Z")
    conn.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)", ("n-2",) + row[1:])


def test_connection_pragmas_are_all_set(db_path: str) -> None:
    c = connect(db_path)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert c.row_factory is sqlite3.Row
    finally:
        c.close()


def test_immediate_commits_and_rolls_back(conn: sqlite3.Connection) -> None:
    with immediate(conn):
        log_event(conn, "aria", "declared", "lm-1")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

    with pytest.raises(ValueError), immediate(conn):
        log_event(conn, "aria", "denied", "lm-2")
        raise ValueError("boom")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_instructions_carry_the_protocol_snippet() -> None:
    assert "loom — shared-repo coordination protocol" in INSTRUCTIONS
    for step in ("resolve_nodes", "declare_plan", "rescope", "release"):
        assert step in INSTRUCTIONS


def test_health_tool_over_an_in_process_client(tmp_path) -> None:
    mcp = build_server(str(tmp_path / "loom.sqlite3"), {"demo": str(tmp_path)})

    async def call() -> dict:
        async with Client(mcp) as client:
            result = await client.call_tool("health", {})
        return result.structured_content

    payload = asyncio.run(call())
    assert payload["ok"] is True
    assert payload["repo"] == "demo"
    assert payload["nodes"] == 0
    assert payload["active_plans"] == 0
    assert payload["version"] == __version__ == "0.1.0"
