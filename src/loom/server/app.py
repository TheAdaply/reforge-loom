"""BUILD-SPEC §5.11 / §6 — MCPServer construction and the four plain-HTTP routes:
`GET /health`, `GET /` + `GET /state` (the dashboard and its poll feed), `POST /gate`.

The nine §5 tools live in `loom.server.tools` and are registered here. State (the repo
salt and the one long-lived connection) is built in `build_server` and closed over — no
env-var singletons (specgate §3.6). The server never needs `repo_root`: it judges the
graph, and every path on the wire is already repo-root-relative. The hook speaks ONLY
`POST /gate` on this same port, never MCP (§11.14).
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

# `events.actor` holds AGENTS plus these two SYSTEM writers: the indexer (walk.py) and
# `loom` itself, which owns the TTL sweep's `expired` rows (claims.py `sweep`). Neither is
# a teammate, so neither may become an agent chip on the dashboard — a swept plan used to
# grow a permanent phantom "loom" agent. They stay visible in the event feed regardless.
SYSTEM_ACTORS = ("indexer", "loom")


def _template(name: str) -> str:
    """Absolute path of a shipped template; the package dir is the only source of truth."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", name)


# The CLAUDE.snippet protocol text (§8.2), read from the ONE file `loom init` also appends
# to CLAUDE.md — free pull-through for agents whose CLAUDE.md drifted (specgate §2.1).
# Read once at import: a second copy inlined here is a copy that silently drifts.
with open(_template("CLAUDE.snippet.md"), encoding="utf-8") as _fh:
    INSTRUCTIONS = _fh.read()


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


def build_server(db_path: str, repo: str) -> MCPServer:
    """Create the schema if needed, wire the per-thread connection factory, the §5 tools,
    and the four plain-HTTP routes. `repo` and the connection factory are closed over by
    every route below — they are the whole of the server's state."""
    init_db(db_path)
    conn = connection_factory(db_path)

    mcp = MCPServer(
        "loom",
        title="loom — spec-driven coordination gate",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    register(mcp, conn, repo)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        """Plain-HTTP liveness used by `loom init` to learn the repo salt (§11.19)."""
        return JSONResponse({"ok": True, "repo": repo})

    @mcp.custom_route("/", methods=["GET"])
    async def dashboard_route(request: Request) -> Response:
        """The one-page read-only dashboard (PLAN §7 'visibility', brought into MVP)."""
        with open(_template("dashboard.html"), encoding="utf-8") as fh:
            return Response(fh.read(), media_type="text/html")

    @mcp.custom_route("/state", methods=["GET"])
    async def state_route(request: Request) -> Response:
        """Dashboard poll feed. Fail-soft: under writer contention return ok:false with
        HTTP 200 — the page shows 'reconnecting', never a broken dashboard. Caps keep the
        payload sane on big repos; spec_md is deliberately NOT shipped (8KB x poll)."""
        try:
            c = conn()
            now = now_s()
            nodes = [dict(r) for r in c.execute(
                "SELECT id, path, qualname, kind FROM nodes WHERE repo = ? "
                "ORDER BY path, qualname LIMIT 600", (repo,))]
            edges = [dict(r) for r in c.execute(
                "SELECT e.src, e.dst, e.kind FROM edges e JOIN nodes n ON n.id = e.src "
                "WHERE n.repo = ? LIMIT 1500", (repo,))]
            plans = [dict(r) for r in c.execute(
                "SELECT id, agent, title, created, ttl_expires FROM plans "
                "WHERE repo = ? AND status = 'active' AND ttl_expires > ? "
                "ORDER BY created", (repo, now))]
            active_ids = [p["id"] for p in plans]
            claims = []
            if active_ids:
                marks = ",".join("?" * len(active_ids))
                claims = [dict(r) for r in c.execute(
                    f"SELECT c.node_id, c.plan_id, c.mode, p.agent FROM claims c "
                    f"JOIN plans p ON p.id = c.plan_id "
                    f"WHERE c.released IS NULL AND c.plan_id IN ({marks})", active_ids)]
            events = [dict(r) for r in c.execute(
                "SELECT ts, actor, action, detail FROM events "
                "ORDER BY rowid DESC LIMIT 50")]
            agents = [p["agent"] for p in plans]
            agents += [e["actor"] for e in reversed(events)
                       if e["actor"] not in SYSTEM_ACTORS]
            return JSONResponse({
                "ok": True, "repo": repo, "now": now,
                "counts": {"nodes": len(nodes), "edges": len(edges),
                           "plans": len(plans), "claims": len(claims)},
                "nodes": nodes, "edges": edges, "plans": plans, "claims": claims,
                "events": events, "agents": list(dict.fromkeys(agents)),
            })
        except sqlite3.OperationalError as exc:
            return JSONResponse({"ok": False, "error": type(exc).__name__})

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


def serve(host: str, port: int, db_path: str, repo: str) -> None:
    """Run the streamable-http server; MCP endpoint is `/mcp`."""
    mcp = build_server(db_path, repo)
    mcp.run(transport="streamable-http", host=host, port=port)


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom.server.app", description="run the loom server")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--repo", default="")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    # `--repo-root` survives as the DEFAULT source for the two values the server does use.
    repo_root = os.path.abspath(args.repo_root)
    # The repo salt is minted once, here, and echoed to every `loom init` (§11.19).
    # DELIBERATE DUPLICATE of `cli.main._repo_of`: both spell the same basename rule, and
    # they must stay in step. NOT shared by import — `loom.server` importing `loom.cli`
    # would invert the dependency (§9.2 lets the CLI reach into the server, never back),
    # and this module is spawned directly as `python -m loom.server.app` with no CLI.
    repo = args.repo or os.path.basename(repo_root.rstrip("/")) or "repo"
    db_path = args.db or os.path.join(repo_root, ".loom.sqlite3")
    serve(args.host, args.port, db_path, repo)


if __name__ == "__main__":
    main()
