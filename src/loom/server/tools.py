"""BUILD-SPEC §5 — the nine MCP tools, thin adapters over `claims.py`.

Registration rules (§5, all load-bearing): plain sync `def`, `@mcp.tool()` WITH parens,
docstring = description, an explicit `-> dict[str, Any]` annotation on every tool (else
clients silently lose `structured_content`), and errors returned as DATA, never raised.
Mutating tools own the transaction (`db.immediate`); `check` must NOT be wrapped —
`claims.gate_decision` manages its own bookkeeping lock (§2 transaction law).
`state["conn"]` is a zero-arg factory returning THIS thread's long-lived connection: the
SDK dispatches sync tools via `anyio.to_thread.run_sync`, so tool bodies run concurrently
and must not share one connection (see `app.connection_factory`).
No SQL lives in this module (§1: storage SQL is confined to db.py / claims.py).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from loom import __version__
from loom.server import claims
from loom.server.db import immediate, now_s
from loom.server.ids import split_ref

_WRONG_REPO: dict[str, Any] = {"ok": False, "reason": "wrong_repo"}


def register(mcp: MCPServer, state: dict[str, Any]) -> None:
    """Define the §5 tool surface against the server's closed-over state (§5.11)."""
    connection: Callable[[], sqlite3.Connection] = state["conn"]
    served: str = state["repo"]

    def ok_repo(r: str) -> bool:
        return not r or r == served

    @mcp.tool()
    def health() -> dict[str, Any]:
        """Liveness of the loom gate: served repo, indexed node count, active plan count."""
        conn = connection()
        nodes, active = claims.counts(conn, served, now_s())
        return {"ok": True, "repo": served, "nodes": nodes, "active_plans": active,
                "version": __version__}

    @mcp.tool()
    def resolve_nodes(queries: list[str], repo: str = "") -> dict[str, Any]:
        """Resolve names, paths or refs to canonical node IDs. Ambiguity returns every candidate."""
        conn = connection()
        if not ok_repo(repo):
            return _WRONG_REPO
        resolved = []
        for q in queries:
            matches = [{"node_id": r["id"], "ref": claims.node_ref(r["path"], r["qualname"]),
                        "path": r["path"], "qualname": r["qualname"], "kind": r["kind"]}
                       for r in claims.resolve_query(conn, served, q)]
            resolved.append({"query": q, "matches": matches,
                             "suggestions": [] if matches else claims.suggestions(conn, served, q)})
        return {"ok": True, "resolved": resolved}

    @mcp.tool()
    def declare_plan(agent: str, title: str, spec_md: str, write_targets: list[str],
                     assumes: list[str] = [], branch: str = "", repo: str = "",
                     ttl_s: int = 1800) -> dict[str, Any]:
        """Claim write targets (plus their one-hop CALLS neighbours) and read assumes under a spec."""
        conn = connection()
        if not ok_repo(repo):
            return _WRONG_REPO
        with immediate(conn):
            return claims.declare_plan(conn, agent=agent, repo=served, branch=branch, title=title,
                                       spec_md=spec_md, write_targets=write_targets,
                                       assumes=assumes, ttl_s=ttl_s, now=now_s())

    @mcp.tool()
    def check(agent: str, node: str, repo: str = "") -> dict[str, Any]:
        """Ask whether this agent may edit a node right now; same core as the edit-time gate."""
        conn = connection()
        if not ok_repo(repo):
            return _WRONG_REPO
        now = now_s()
        if node.startswith("n-") and claims.node_exists(conn, served, node):
            d = claims.check_node(conn, repo=served, agent=agent, node_id=node, now=now)
        else:
            path, qual = split_ref(node)
            d = claims.gate_decision(conn, repo=served, agent=agent, path=path,
                                     qualname=qual or None, now=now)
        if d["decision"] == "allow":
            return {"allow": True, "case": d["case"], "plan_id": d["plan_id"]}
        return {"allow": False, "case": d["case"], "message": d["message"], "owner": d["owner"],
                "node_id": d["node_id"]}

    @mcp.tool()
    def rescope(plan_id: str, add_targets: list[str] = [],
                add_assumes: list[str] = []) -> dict[str, Any]:
        """Widen an active plan before touching new ground; renews its TTL on success."""
        conn = connection()
        with immediate(conn):
            return claims.rescope(conn, plan_id=plan_id, add_targets=add_targets,
                                  add_assumes=add_assumes, now=now_s())

    @mcp.tool()
    def get_plan(plan_id: str) -> dict[str, Any]:
        """Fetch a plan's full spec and its current write/read claim refs."""
        conn = connection()
        plan = claims.get_plan(conn, plan_id)
        return {"ok": True, "plan": plan} if plan else {"ok": False, "reason": "unknown_plan"}

    @mcp.tool()
    def list_claims(repo: str = "") -> dict[str, Any]:
        """List every active claim in this repo with its owner, plan and expiry."""
        conn = connection()
        if not ok_repo(repo):
            return _WRONG_REPO
        now = now_s()
        with immediate(conn):
            claims.sweep(conn, served, now)
        return {"ok": True, "claims": claims.active_claims(conn, served, now)}

    @mcp.tool()
    def renew(plan_id: str) -> dict[str, Any]:
        """Extend an active plan's TTL. `renewed: 0` is a verdict: re-declare, do not edit on."""
        conn = connection()
        with immediate(conn):
            return claims.renew(conn, plan_id, now_s())

    @mcp.tool()
    def release(plan_id: str, agent: str, status: str = "done") -> dict[str, Any]:
        """Owner-only: tombstone a plan's claims and close it as done or superseded."""
        conn = connection()
        with immediate(conn):
            return claims.release(conn, plan_id, agent, status, now_s())
