"""BUILD-SPEC §5.11 / §6 — MCPServer construction, `GET /health`, `POST /gate`.

The nine §5 tools live in `loom.server.tools` and are registered here. State (db path,
repo, repo_root, the one long-lived connection) is built in `build_server` and closed
over — no env-var singletons (specgate §3.6). The hook speaks ONLY `POST /gate` on this
same port, never MCP (§11.14).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import threading
from collections.abc import Callable

from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from loom.server.claims import gate_decision
from loom.server.db import connect, init_db, now_s
from loom.server.tools import register

# The CLAUDE.snippet protocol text (§8.2), verbatim — free pull-through for agents
# whose CLAUDE.md drifted (specgate §2.1).
INSTRUCTIONS = """<!-- loom protocol v1 — written by `loom init`; edits here are overwritten on re-init -->
## loom — shared-repo coordination protocol

Before any code change in this repo:
1. Write a spec from loom's `templates/spec.md` (one page, all five sections, no unfilled brackets).
2. Resolve every write target and every assume to canonical node IDs with the loom `resolve_nodes`
   tool. IDs look like `relative/path.py::Class/method`; whole files are `relative/path.ext`.
3. Call `declare_plan(...)`. If the response carries conflicts, read each embedded spec, replan to
   build against its DECLARED interfaces — never against in-flight code — adjust your targets, and
   declare again. Warnings mean someone reads what you write, or you read what they write: honor
   their spec.
4. Edit normally. If the loom gate blocks an edit, follow the message: it either hands you the
   owning plan's spec to build around, or tells you to rescope, or to declare a plan first.
5. If your work grows beyond the declared targets, call `rescope(plan_id, add_targets, add_assumes)`
   BEFORE touching the new ground.
6. When tests pass and the branch merges, call `release(plan_id, agent)`.

Claims expire on a TTL (30 min) and renew automatically while you edit. If `renew` or `check` says
your plan is gone, re-declare — do not edit around a deny.
"""


def connection_factory(db_path: str) -> Callable[[], sqlite3.Connection]:
    """One long-lived connection PER THREAD, opened once and reused (specgate §3.2).

    The SDK runs every sync `@mcp.tool()` through `anyio.to_thread.run_sync`, so tool bodies
    execute concurrently in worker threads and CANNOT share one `sqlite3.Connection`: the
    second `BEGIN IMMEDIATE` raises "cannot start a transaction within a transaction", and
    worse, two threads' statements would interleave inside one transaction. Per-thread
    connections restore §2's law as written — SQLite's write lock serializes the racers,
    `busy_timeout=5000` makes the loser queue — with no Python lock and no per-call
    connect/close (which would blow the check budget). `threading.local` is a slot, not a
    lock; nothing here ever blocks a thread in Python.
    """
    local = threading.local()

    def get() -> sqlite3.Connection:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = local.conn = connect(db_path)
        return conn

    return get


def build_server(db_path: str, repo: str, repo_root: str) -> MCPServer:
    """Create the schema if needed, wire the per-thread connection factory and the tools."""
    init_db(db_path)
    conn = connection_factory(db_path)

    mcp = MCPServer(
        "loom",
        title="loom — spec-driven coordination gate",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    register(mcp, {"conn": conn, "repo": repo})

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        """Plain-HTTP liveness used by `loom init` to learn the repo salt (§11.19)."""
        return JSONResponse({"ok": True, "repo": repo})

    @mcp.custom_route("/gate", methods=["POST"])
    async def gate_route(request: Request) -> Response:
        """§6 wire contract. Always HTTP 200 with exactly the five frozen keys."""
        body = await request.json()
        asked = str(body.get("repo") or "")
        if asked and asked != repo:
            # Advisory posture: a repo we do not serve is never bricked (§6 case 2 spirit).
            return JSONResponse({"decision": "allow", "case": "unindexed", "message": "",
                                 "node_id": None, "plan_id": None})
        d = gate_decision(conn(), repo=repo, agent=str(body.get("agent") or ""),
                          path=str(body.get("path") or ""), qualname=body.get("qualname"),
                          now=now_s())
        return JSONResponse({k: d[k] for k in
                             ("decision", "case", "message", "node_id", "plan_id")})

    return mcp


def serve(host: str, port: int, db_path: str, repo: str, repo_root: str) -> None:
    """Run the streamable-http server; MCP endpoint is `/mcp`."""
    mcp = build_server(db_path, repo, repo_root)
    mcp.run(transport="streamable-http", host=host, port=port)


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom.server.app", description="run the loom server")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--repo", default="")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    # The repo salt is minted once, here, and echoed to every `loom init` (§11.19).
    repo = args.repo or os.path.basename(repo_root.rstrip("/")) or "repo"
    db_path = args.db or os.path.join(repo_root, ".loom.sqlite3")
    serve(args.host, args.port, db_path, repo, repo_root)


if __name__ == "__main__":
    main()
