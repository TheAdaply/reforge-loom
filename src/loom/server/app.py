"""BUILD-SPEC §5.11 / §6 — MCPServer construction and the four plain-HTTP routes:
`GET /health`, `GET /` + `GET /state` (the dashboard and its poll feed), `POST /gate`.

The nine §5 tools live in `loom.server.tools` and are registered here. State (the ORDERED
repo-salt map and the one long-lived connection) is built in `build_server` and closed
over — no env-var singletons (specgate §3.6). The server never needs `repo_root` to judge:
it judges the graph, and every path on the wire is already repo-root-relative — the roots
carried in `repos` are there so one process can name (and, via the CLI, index) several
repos. The hook speaks ONLY `POST /gate` on this same port, never MCP (§11.14).

MULTIREPO-SPEC §2 (D2-D5) is the multi-repo layer: one db, N repo salts, `served[0]` as
the default everywhere a name is omitted — which is exactly how the single-repo behavior
of BUILD-SPEC survives unchanged.
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


def _named(root: str) -> tuple[str, str]:
    """Split `NAME=PATH` into (name, path); a bare or path-shaped value gives ('', root).

    A `=` that appears AFTER a separator belongs to the path (`/tmp/a=b` is one directory),
    so only a separator-free head counts as a name — and an explicitly empty head is the
    beads empty-flag error, not a silent fallback to the basename.
    """
    head, sep, tail = root.partition("=")
    if not sep or "/" in head or os.sep in head:
        return "", root
    if not head.strip():
        raise ValueError(f"--repo-root {root!r} has an empty NAME; pass NAME=PATH")
    return head.strip(), tail


def parse_repo_roots(roots: list[str], repo: str = "") -> dict[str, str]:
    """MULTIREPO-SPEC §1 — `--repo-root` values to an ORDERED `{name: abs_root}` map.

    Each value is `NAME=PATH` or a bare `PATH` (name defaults to the basename). Order is
    the flag order and it is load-bearing: `served[0]` is the default repo for `/state`,
    for `""` tool arguments, and for `/health`'s back-compat `"repo"` key.

    `--repo NAME` keeps its BUILD-SPEC meaning — rename the one repo — so it is legal only
    with a single un-prefixed root; combining it with several roots (or with an explicit
    `NAME=`) is ambiguous and therefore a hard error, never a silent pick. Duplicate names
    are a hard error too: two roots under one salt would share every node id.

    Raises `ValueError` with the operator-facing text; callers turn that into an exit.
    """
    if not roots:
        raise ValueError("--repo-root is required; pass PATH or NAME=PATH")
    if repo and (len(roots) > 1 or _named(roots[0])[0]):
        raise ValueError("--repo names the single repo of a one-root server; with several "
                         "--repo-root flags (or a NAME=PATH form) use NAME=PATH instead")
    out: dict[str, str] = {}
    for root in roots:
        name, path = _named(root)
        if not path.strip():
            raise ValueError(f"--repo-root {root!r} has no path; pass a real value")
        abs_path = os.path.abspath(path.strip())
        name = repo or name or os.path.basename(abs_path.rstrip("/")) or "repo"
        if name in out:
            raise ValueError(f"duplicate repo name {name!r}: {out[name]} and {abs_path} "
                             "cannot share one salt; give each root its own NAME=PATH")
        out[name] = abs_path
    return out


def build_server(db_path: str, repos: dict[str, str]) -> MCPServer:
    """Create the schema if needed, wire the per-thread connection factory, the §5 tools,
    and the four plain-HTTP routes. `repos` (name -> repo_root, MULTIREPO-SPEC §2/D3) and
    the connection factory are closed over by every route below — they are the whole of
    the server's state. `served[0]` is the default repo everywhere a name is omitted."""
    init_db(db_path)
    conn = connection_factory(db_path)
    served = list(repos)
    if not served:
        raise ValueError("build_server needs at least one repo")
    default = served[0]

    mcp = MCPServer(
        "loom",
        title="loom — spec-driven coordination gate",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    register(mcp, conn, served)

    def selected(request: Request) -> str:
        """`?repo=NAME` when served, else the default — a mistyped name must not blank
        the dashboard, it must show the repo the operator most likely meant."""
        asked = request.query_params.get("repo") or ""
        return asked if asked in repos else default

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        """Plain-HTTP liveness used by `loom init` to learn the repo salt (§11.19).

        `repos` is the multi-repo answer; `repo` is kept as served[0] so every already
        initialized checkout and every frozen test keeps reading the key it knows (D2)."""
        return JSONResponse({"ok": True, "repo": default, "repos": served})

    @mcp.custom_route("/", methods=["GET"])
    async def dashboard_route(request: Request) -> Response:
        """The one-page read-only dashboard (PLAN §7 'visibility', brought into MVP)."""
        with open(_template("dashboard.html"), encoding="utf-8") as fh:
            return Response(fh.read(), media_type="text/html")

    @mcp.custom_route("/state", methods=["GET"])
    async def state_route(request: Request) -> Response:
        """Dashboard poll feed, scoped to `?repo=NAME` (D5). Fail-soft: under writer
        contention return ok:false with HTTP 200 — the page shows 'reconnecting', never a
        broken dashboard. Caps keep the payload sane on big repos; spec_md is deliberately
        NOT shipped (8KB x poll). `repos` ships every served name so the page can render a
        switcher without a second endpoint."""
        try:
            c = conn()
            repo = selected(request)
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
            # repo = '' is pre-migration history (§3): it belongs to no repo in particular,
            # so it is shown in every feed rather than disappearing from all of them.
            events = [dict(r) for r in c.execute(
                "SELECT ts, actor, action, detail FROM events WHERE repo = ? OR repo = '' "
                "ORDER BY rowid DESC LIMIT 50", (repo,))]
            agents = [p["agent"] for p in plans]
            agents += [e["actor"] for e in reversed(events)
                       if e["actor"] not in SYSTEM_ACTORS]
            return JSONResponse({
                "ok": True, "repo": repo, "repos": served, "now": now,
                "counts": {"nodes": len(nodes), "edges": len(edges),
                           "plans": len(plans), "claims": len(claims)},
                "nodes": nodes, "edges": edges, "plans": plans, "claims": claims,
                "events": events, "agents": list(dict.fromkeys(agents)),
            })
        except sqlite3.OperationalError as exc:
            return JSONResponse({"ok": False, "error": type(exc).__name__})

    @mcp.custom_route("/gate", methods=["POST"])
    async def gate_route(request: Request) -> Response:
        """§6 wire contract. Always HTTP 200 with exactly the five frozen keys.

        `body.repo` picks WHICH served repo judges this edit (MULTIREPO-SPEC §2). A name
        we do not serve — or none at all — is answered advisory: a repo this server knows
        nothing about is never bricked (§6 case 2 spirit), it is simply not ours to judge.
        """
        body = await request.json()
        asked = str(body.get("repo") or "")
        if asked not in repos:
            return JSONResponse({"decision": "allow", "case": "unindexed", "message": "",
                                 "node_id": None, "plan_id": None})
        d = gate_decision(conn(), repo=asked, agent=str(body.get("agent") or ""),
                          path=str(body.get("path") or ""), qualname=body.get("qualname"),
                          now=now_s())
        return JSONResponse({k: d[k] for k in
                             ("decision", "case", "message", "node_id", "plan_id")})

    return mcp


def serve(host: str, port: int, db_path: str, repos: dict[str, str]) -> None:
    """Run the streamable-http server; MCP endpoint is `/mcp`."""
    mcp = build_server(db_path, repos)
    mcp.run(transport="streamable-http", host=host, port=port)


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom.server.app", description="run the loom server")
    # Repeatable: `--repo-root PATH` (name = basename) or `--repo-root NAME=PATH` (§1).
    ap.add_argument("--repo-root", action="append", default=None)
    ap.add_argument("--repo", default="")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    # `--repo-root` survives as the DEFAULT source for the two values the server does use:
    # the repo salts (minted once, here, and echoed to every `loom init` — §11.19) and,
    # absent `--db`, the database location. Parsing lives with the server, not the CLI,
    # because this module is spawned directly as `python -m loom.server.app`; `loom serve`
    # imports the same `parse_repo_roots` so the two entry points cannot drift (§9.2 lets
    # the CLI reach into the server, never back).
    try:
        repos = parse_repo_roots(args.repo_root or ["."], args.repo)
    except ValueError as exc:
        raise SystemExit(f"loom: {exc}") from None
    db_path = args.db or os.path.join(next(iter(repos.values())), ".loom.sqlite3")
    serve(args.host, args.port, db_path, repos)


if __name__ == "__main__":
    main()
