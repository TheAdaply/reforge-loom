"""M2 — the nine §5 tools over an in-process MCP client (specgate §3.5).

`Client(mcp_instance)` connects without a transport, so this exercises the real
registration surface: `structured_content` only survives when every tool carries its
explicit `-> dict[str, Any]` annotation (§5, specgate C5.5).
"""

from __future__ import annotations

import asyncio
from typing import Any

from conftest import SPEC, seed
from mcp import Client

from loom.server.app import build_server

AUTH = "svc.py::AuthService/authenticate"
USER = "models.py::User"
LONELY = "iso.py::lonely"


def run(mcp, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Call tools in order over one in-process client session, returning structured payloads."""
    async def go() -> list[dict[str, Any]]:
        async with Client(mcp) as c:
            return [(await c.call_tool(n, a)).structured_content for n, a in calls]
    return asyncio.run(go())


def server(tmp_path):
    db = str(tmp_path / "loom.sqlite3")
    seed(db)
    # MULTIREPO-SPEC D3: build_server takes {name: repo_root}; one entry = today's server.
    return build_server(db, {"demo": str(tmp_path)})


def test_every_tool_is_registered_and_annotated(tmp_path) -> None:
    mcp = server(tmp_path)

    async def go():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {"health", "resolve_nodes", "declare_plan", "check", "rescope",
                          "get_plan", "list_claims", "renew", "release"}
    for t in tools.values():
        assert t.description, f"{t.name} has no docstring description"
        assert t.output_schema is not None, f"{t.name} lost its structured output schema"


def test_health_reports_the_seeded_graph(tmp_path) -> None:
    (h,) = run(server(tmp_path), [("health", {})])
    assert h == {"ok": True, "repo": "demo", "nodes": 11, "active_plans": 0, "version": "0.1.0"}


def test_resolve_nodes_matches_and_suggests(tmp_path) -> None:
    (r,) = run(server(tmp_path), [("resolve_nodes", {"queries": ["authenticate", "nope_x"]})])
    hit, miss = r["resolved"]
    assert hit["matches"][0]["ref"] == AUTH
    assert hit["matches"][0]["kind"] == "Function" and hit["suggestions"] == []
    assert miss["matches"] == [] and 0 < len(miss["suggestions"]) <= 5


def test_full_lifecycle_over_the_tool_surface(tmp_path) -> None:
    mcp = server(tmp_path)
    (declared,) = run(mcp, [("declare_plan", {
        "agent": "aria", "title": "cache auth", "spec_md": SPEC,
        "write_targets": [AUTH], "assumes": [USER], "branch": "feat/cache"})])
    pid = declared["plan_id"]
    assert declared["ok"] is True and pid.startswith("lm-")
    assert declared["expires_iso"].endswith("Z") and declared["warnings"] == []

    ok, blocked, absent = run(mcp, [
        ("check", {"agent": "aria", "node": AUTH}),
        ("check", {"agent": "bo", "node": AUTH}),
        ("check", {"agent": "aria", "node": "brand/new.py"})])
    assert ok == {"allow": True, "case": "in_plan", "plan_id": pid}
    assert blocked["allow"] is False and blocked["case"] == "foreign_claim"
    assert blocked["owner"]["owner_plan_id"] == pid and "## Goal" in blocked["message"]
    assert absent == {"allow": True, "case": "new_path", "plan_id": None}

    (plan,) = run(mcp, [("get_plan", {"plan_id": pid})])
    p = plan["plan"]
    assert p["agent"] == "aria" and p["branch"] == "feat/cache" and p["status"] == "active"
    assert p["spec_md"] == SPEC and AUTH in p["write_claims"] and p["read_claims"] == [USER]

    (listed,) = run(mcp, [("list_claims", {})])
    assert {c["ref"] for c in listed["claims"]} == set(p["write_claims"] + p["read_claims"])
    assert all(c["agent"] == "aria" and c["plan_id"] == pid for c in listed["claims"])

    (widened,) = run(mcp, [("rescope", {"plan_id": pid, "add_targets": [LONELY]})])
    assert widened["ok"] is True and widened["claimed_write"]
    (plan2,) = run(mcp, [("get_plan", {"plan_id": pid})])
    assert LONELY in plan2["plan"]["write_claims"] and AUTH in plan2["plan"]["write_claims"]

    (renewed,) = run(mcp, [("renew", {"plan_id": pid, "agent": "aria"})])
    assert renewed["renewed"] == 1 and renewed["expires_ts"] >= widened["expires_ts"]
    # break3 chaos-F6: renew is owner-only, exactly like release two lines below.
    (poached,) = run(mcp, [("renew", {"plan_id": pid, "agent": "bo"})])
    assert poached == {"renewed": 0, "reason": "not_owner"}

    (bad,) = run(mcp, [("release", {"plan_id": pid, "agent": "bo"})])
    assert bad == {"ok": False, "reason": "not_owner"}
    (rel,) = run(mcp, [("release", {"plan_id": pid, "agent": "aria"})])
    assert rel["ok"] is True and rel["plan_status"] == "done" and rel["released_claims"] > 0
    (after,) = run(mcp, [("check", {"agent": "bo", "node": AUTH})])
    assert after["allow"] is False and after["case"] == "no_plan"   # claim is gone


def test_check_resolves_a_bare_symbol_name_instead_of_allowing_it_as_a_new_path(
        tmp_path) -> None:
    """claimsx-F2: `check(agent, "authenticate")` fell through to the PATH ladder, matched
    no indexed node and answered `new_path` allow over a live foreign write claim."""
    mcp = server(tmp_path)
    run(mcp, [("declare_plan", {"agent": "aria", "title": "t", "spec_md": SPEC,
                                "write_targets": [AUTH]})])
    bare, qual, ident = run(mcp, [
        ("check", {"agent": "bo", "node": "authenticate"}),
        ("check", {"agent": "bo", "node": "AuthService/authenticate"}),
        ("check", {"agent": "bo", "node": AUTH})])
    for r in (bare, qual, ident):
        assert r["allow"] is False and r["case"] == "foreign_claim"
        assert r["owner"]["owner_agent"] == "aria"
    # and the owner is still cleared through the same ladder
    (own,) = run(mcp, [("check", {"agent": "aria", "node": "authenticate"})])
    assert own["allow"] is True and own["case"] == "in_plan"


def test_check_denies_needs_resolution_when_it_cannot_pin_one_node(tmp_path) -> None:
    mcp = server(tmp_path)
    unknown, absent_id, ambiguous, new_file = run(mcp, [
        ("check", {"agent": "bo", "node": "no_such_symbol"}),
        ("check", {"agent": "bo", "node": "n-zzzzzzzz"}),
        ("check", {"agent": "bo", "node": "auth"}),     # matches AuthService AND its method
        ("check", {"agent": "bo", "node": "brand/new.py"})])
    assert ambiguous["case"] == "needs_resolution"
    assert set(ambiguous["candidates"]) == {"svc.py::AuthService", AUTH}
    for r in (unknown, absent_id, ambiguous):
        assert r["allow"] is False and r["case"] == "needs_resolution"
        assert "resolve_nodes" in r["message"] and r["node_id"] is None and r["owner"] is None
    assert 0 < len(unknown["candidates"]) <= 5          # §5.2 suggestions, additive key
    # A path-shaped argument still reaches §6's path ladder: a brand-new file is allowed.
    assert new_file == {"allow": True, "case": "new_path", "plan_id": None}


def test_conflict_response_embeds_the_owner_spec(tmp_path) -> None:
    mcp = server(tmp_path)
    args = {"title": "t", "spec_md": SPEC, "write_targets": [USER]}
    run(mcp, [("declare_plan", dict(args, agent="aria"))])
    (r,) = run(mcp, [("declare_plan", dict(args, agent="bo"))])
    assert r == {"ok": False, "reason": "conflict", "conflicts": r["conflicts"]}
    assert r["conflicts"][0]["owner_spec_md"] == SPEC
    assert r["conflicts"][0]["kind"] == "write-write"


def test_errors_are_data_never_raised(tmp_path) -> None:
    mcp = server(tmp_path)
    results = run(mcp, [
        ("health", {}),
        ("resolve_nodes", {"queries": [], "repo": "other"}),
        ("declare_plan", {"agent": "a", "title": "t", "spec_md": SPEC,
                          "write_targets": [USER], "repo": "other"}),
        ("list_claims", {"repo": "other"}),
        ("get_plan", {"plan_id": "lm-nope"}),
        ("renew", {"plan_id": "lm-nope", "agent": "a"}),
        ("release", {"plan_id": "lm-nope", "agent": "a"}),
        ("rescope", {"plan_id": "lm-nope"}),
        ("declare_plan", {"agent": "a", "title": "t", "spec_md": "empty",
                          "write_targets": [USER]}),
    ])
    assert results[0]["ok"] is True
    for r in results[1:4]:
        # MULTIREPO-SPEC §2: an unserved repo name is `unknown_repo` + the served list.
        assert r == {"ok": False, "reason": "unknown_repo", "served": ["demo"]}
    assert results[4] == {"ok": False, "reason": "unknown_plan"}
    assert results[5] == {"renewed": 0, "reason": "unknown_plan"}
    assert results[6] == {"ok": False, "reason": "unknown_plan"}
    assert results[7] == {"ok": False, "reason": "unknown_plan"}
    assert results[8]["reason"] == "validation" and results[8]["validation_errors"]


def test_busy_lock_is_returned_as_data_not_raised(tmp_path, monkeypatch) -> None:
    """break4 RT-1: BEGIN IMMEDIATE raising SQLITE_BUSY past `busy_timeout` (the BC3-2
    whole-index window) must surface as the §5 data shape, never escape the tool body."""
    import sqlite3

    from loom.server import claims as claims_mod

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(claims_mod, "declare_plan", boom)
    [res] = run(server(tmp_path), [("declare_plan", {
        "agent": "aria", "title": "t", "spec_md": SPEC,
        "write_targets": ["models.py::User"]})])
    assert res["ok"] is False and res["reason"] == "busy" and res["retry"] is True
    assert "retry" in res["message"]


def test_tx_reraises_operational_errors_that_are_not_lock_contention(tmp_path) -> None:
    """`_tx` maps ONLY the busy/locked class to a retry verdict; a schema error is a real
    defect and must keep raising."""
    import sqlite3

    import pytest as _pytest

    from loom.server.db import connect
    from loom.server.tools import _tx

    conn = connect(str(tmp_path / "t.sqlite3"))
    try:
        with _pytest.raises(sqlite3.OperationalError):
            _tx(conn, lambda: conn.execute("SELECT * FROM no_such_table").fetchall())
    finally:
        conn.close()
