"""BUILD-SPEC §5 — the nine MCP tools, thin adapters over `claims.py`.

Registration rules (§5, all load-bearing): plain sync `def`, `@mcp.tool()` WITH parens,
docstring = description, an explicit `-> dict[str, Any]` annotation on every tool (else
clients silently lose `structured_content`), and errors returned as DATA, never raised.
Mutating tools own the transaction (`db.immediate`); `check` must NOT be wrapped —
`claims.gate_decision` manages its own bookkeeping lock (§2 transaction law).
`connection` is a zero-arg factory returning THIS thread's long-lived connection: the
SDK dispatches sync tools via `anyio.to_thread.run_sync`, so tool bodies run concurrently
and must not share one connection (see `app.connection_factory`).
No SQL lives in this module (§1: storage SQL is confined to db.py / claims.py).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from loom import __version__
from loom.server import claims
from loom.server.db import immediate, now_s
from loom.server.ids import split_ref

# MULTIREPO-SPEC §2: one server serves an ORDERED list of repo names. A tool argument of
# `""` means "the default repo" — `served[0]` — which is what preserves single-repo
# behavior verbatim. A non-empty name the server does not serve is the §5 error shape plus
# an additive `served` list, so the caller can fix the argument without a second round trip.

# A trailing file suffix (or an explicit `path::qual` ref) is what makes a `check` argument
# a PATH — the only form §6's path ladder may judge, because a brand-new file legitimately
# resolves to nothing and must still answer `new_path`.
_SUFFIX = re.compile(r"\.[A-Za-z0-9_]{1,8}$")


def _path_like(node: str) -> bool:
    return "::" in node or bool(_SUFFIX.search(split_ref(node)[0]))


def _needs_resolution(node: str, candidates: list[str]) -> dict[str, Any]:
    """§5.4 deny shape (+ additive `candidates`) for a `check` argument §5.2 cannot pin.

    A bare qualname that resolves to nothing — or to several nodes — must NEVER reach the
    path ladder, whose `new_path` allow would clear the caller over a live foreign claim.
    """
    shown = candidates[:5]
    which = f" Candidates: {', '.join(shown)}." if shown else ""
    return {"allow": False, "case": "needs_resolution", "node_id": None, "owner": None,
            "message": f'loom: "{node}" does not name exactly one node in this repo. Call '
                       f'resolve_nodes(queries=["{node}"]), then re-check the node_id it '
                       f"returns.{which}",
            "candidates": shown}


def register(mcp: MCPServer, connection: Callable[[], sqlite3.Connection],
             served: list[str]) -> None:
    """Define the §5 tool surface against the server's closed-over state (§5.11).

    `served` is the ORDERED list of repo names this server serves (MULTIREPO-SPEC §2, D4);
    `served[0]` is the default every `repo: str = ""` argument resolves to.
    """
    default = served[0]

    def pick(r: str) -> str | None:
        """'' -> the default repo; a served name -> itself; anything else -> None."""
        if not r:
            return default
        return r if r in served else None

    def unknown() -> dict[str, Any]:
        return {"ok": False, "reason": "unknown_repo", "served": list(served)}

    @mcp.tool()
    def health() -> dict[str, Any]:
        """Liveness of the loom gate: served repo, indexed node count, active plan count."""
        conn = connection()
        nodes, active = claims.counts(conn, default, now_s())
        return {"ok": True, "repo": default, "nodes": nodes, "active_plans": active,
                "version": __version__}

    @mcp.tool()
    def resolve_nodes(queries: list[str], repo: str = "") -> dict[str, Any]:
        """Resolve names, paths or refs to canonical node IDs. Ambiguity returns every candidate."""
        conn = connection()
        if (in_repo := pick(repo)) is None:
            return unknown()
        resolved = []
        for q in queries:
            matches = [{"node_id": r["id"], "ref": claims.node_ref(r["path"], r["qualname"]),
                        "path": r["path"], "qualname": r["qualname"], "kind": r["kind"]}
                       for r in claims.resolve_query(conn, in_repo, q)]
            resolved.append({"query": q, "matches": matches,
                             "suggestions": [] if matches else claims.suggestions(conn, in_repo, q)})
        return {"ok": True, "resolved": resolved}

    # B006 by the letter and correct in fact: the MCP SDK reads these signatures to build the
    # tool schemas, where `= []` is what declares an optional array. No body mutates one, and
    # `pyproject.toml` records the per-file ignore.
    @mcp.tool()
    def declare_plan(agent: str, title: str, spec_md: str, write_targets: list[str],
                     assumes: list[str] = [], branch: str = "", repo: str = "",
                     ttl_s: int = 1800) -> dict[str, Any]:
        """Claim write targets (plus their one-hop CALLS neighbours) and read assumes under a spec."""
        conn = connection()
        if (in_repo := pick(repo)) is None:
            return unknown()
        with immediate(conn):
            return claims.declare_plan(conn, agent=agent, repo=in_repo, branch=branch, title=title,
                                       spec_md=spec_md, write_targets=write_targets,
                                       assumes=assumes, ttl_s=ttl_s, now=now_s())

    @mcp.tool()
    def check(agent: str, node: str, repo: str = "") -> dict[str, Any]:
        """Ask whether this agent may edit a node right now; same core as the edit-time gate."""
        conn = connection()
        if (in_repo := pick(repo)) is None:
            return unknown()
        now = now_s()
        if node.startswith("n-") and claims.node_exists(conn, in_repo, node):
            d = claims.check_node(conn, repo=in_repo, agent=agent, node_id=node, now=now)
        else:
            rows = claims.resolve_query(conn, in_repo, node)   # §5.2 ladder first (claimsx-F2)
            if len(rows) == 1:
                d = claims.check_node(conn, repo=in_repo, agent=agent, node_id=rows[0]["id"],
                                      now=now)
            elif rows or not _path_like(node):
                return _needs_resolution(
                    node, [claims.node_ref(r["path"], r["qualname"]) for r in rows]
                    or claims.suggestions(conn, in_repo, node))
            else:
                path, qual = split_ref(node)
                d = claims.gate_decision(conn, repo=in_repo, agent=agent, path=path,
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
        if (in_repo := pick(repo)) is None:
            return unknown()
        now = now_s()
        with immediate(conn):
            claims.sweep(conn, in_repo, now)
        return {"ok": True, "claims": claims.active_claims(conn, in_repo, now)}

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
