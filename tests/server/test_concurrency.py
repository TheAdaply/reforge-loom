"""M2 — the transaction law under real contention (BUILD-SPEC §2, §9.3 M2).

A subprocess server (`python -m loom.server.app`, PIPE logs, try/finally terminate —
specgate §2.6) is driven by TWO independent HTTP MCP clients whose `declare_plan` calls
are fired with `asyncio.gather`. `BEGIN IMMEDIATE` takes the write lock before the first
read, so exactly one wins; `busy_timeout=5000` makes the loser queue rather than raise
`sqlite3.OperationalError`. There is no `threading.Lock` in the server to make this work.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest
from conftest import SPEC, seed, server_process
from mcp import Client

from loom.server.claims import gate_decision
from loom.server.db import connect, now_s

AUTH = "svc.py::AuthService/authenticate"
USER = "models.py::User"
LONELY = "iso.py::lonely"


def post(port: int, path: str, body: dict | None = None, timeout: float = 5.0) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        assert r.status == 200
        return json.loads(r.read())


@pytest.fixture()
def live(tmp_path) -> Iterator[tuple[int, str]]:
    """(port, db_path) of a subprocess server serving the seeded fixture graph."""
    db = str(tmp_path / "loom.sqlite3")
    seed(db)                                   # pre-seed BEFORE the server opens the file
    with server_process("--repo-root", str(tmp_path), "--repo", "demo", "--db", db) as (port, _p):
        yield port, db


def declare_args(agent: str, targets: list[str]) -> dict:
    return {"agent": agent, "title": f"{agent} plan", "spec_md": SPEC, "write_targets": targets}


def test_two_clients_racing_the_same_target_produce_exactly_one_winner(live) -> None:
    port, db = live
    url = f"http://127.0.0.1:{port}/mcp"

    async def race() -> list[dict]:
        async with Client(url) as a, Client(url) as b:
            calls = [a.call_tool("declare_plan", declare_args("aria", [AUTH])),
                     b.call_tool("declare_plan", declare_args("bo", [AUTH]))]
            return [r.structured_content for r in await asyncio.gather(*calls)]

    results = asyncio.run(race())
    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1 and len(losers) == 1, results
    assert losers[0]["reason"] == "conflict"
    # The loser gets the winner's FULL spec inline — no second call (§5 conflict object).
    conflict = losers[0]["conflicts"][0]
    assert conflict["owner_spec_md"] == SPEC
    assert conflict["owner_plan_id"] == winners[0]["plan_id"]
    assert conflict["kind"] == "write-write"

    # Nothing partial landed: only the winner's claims exist.
    conn = connect(db)
    try:
        plans = conn.execute("SELECT id FROM plans").fetchall()
        assert [p["id"] for p in plans] == [winners[0]["plan_id"]]
        owners = conn.execute("SELECT DISTINCT plan_id FROM claims").fetchall()
        assert [o["plan_id"] for o in owners] == [winners[0]["plan_id"]]
    finally:
        conn.close()


def test_a_burst_of_concurrent_declares_never_raises_operational_error(live) -> None:
    """busy_timeout=5000 proof: losers of a write race queue, they do not error."""
    port, _ = live
    url = f"http://127.0.0.1:{port}/mcp"
    # Three disjoint CONTAINS subtrees, two racers each: svc.py (AUTH is two levels inside
    # it), models.py (User inside it), iso.py (lonely inside it).
    targets = [[AUTH], ["svc.py"], [USER], ["models.py"], [LONELY], ["iso.py"]]

    async def burst() -> list[dict]:
        clients = [Client(url) for _ in targets]
        async with clients[0] as c0, clients[1] as c1, clients[2] as c2, \
                clients[3] as c3, clients[4] as c4, clients[5] as c5:
            live_clients = [c0, c1, c2, c3, c4, c5]
            calls = [c.call_tool("declare_plan", declare_args(f"ag{i}", t))
                     for i, (c, t) in enumerate(zip(live_clients, targets, strict=True))]
            return [r.structured_content for r in await asyncio.gather(*calls)]

    results = asyncio.run(burst())
    for r in results:
        assert "OperationalError" not in json.dumps(r)
        assert r.get("ok") is True or r.get("reason") == "conflict"
    # Claims are hierarchical (P0-2): a file-level target and a symbol inside it are the
    # same ground, so BEGIN IMMEDIATE serializes each pair to exactly one winner —
    # deterministically 3 winners and 3 conflicts, whatever order the six land in.
    assert sum(1 for r in results if r.get("ok")) == 3
    assert sum(1 for r in results if r.get("reason") == "conflict") == 3


def test_the_plain_http_gate_and_health_routes_speak_the_frozen_wire(live) -> None:
    port, _ = live
    # MULTIREPO-SPEC D2: `repos` is additive; `repo` stays served[0] for back-compat.
    assert post(port, "/health") == {"ok": True, "repo": "demo", "repos": ["demo"],
                                     "auth": "open"}   # ITERATION-2 D8, additive

    url = f"http://127.0.0.1:{port}/mcp"

    async def declare() -> dict:
        async with Client(url) as c:
            return (await c.call_tool("declare_plan", declare_args("aria", [USER]))
                    ).structured_content

    plan = asyncio.run(declare())

    body = {"agent": "aria", "repo": "demo", "path": "models.py", "qualname": "User",
            "tool_name": "Edit"}
    allowed = post(port, "/gate", body)
    assert set(allowed) == {"decision", "case", "message", "node_id", "plan_id"}
    assert allowed["decision"] == "allow" and allowed["case"] == "in_plan"
    assert allowed["plan_id"] == plan["plan_id"] and allowed["message"] == ""

    denied = post(port, "/gate", dict(body, agent="bo"))
    assert set(denied) == {"decision", "case", "message", "node_id", "plan_id"}
    assert denied["decision"] == "deny" and denied["case"] == "foreign_claim"
    assert denied["message"].startswith("loom: BLOCKED —") and SPEC.strip() in denied["message"]
    for bad in ("force", "bypass", "override", "unclaim", "release(", "--force"):
        assert bad not in denied["message"].lower()

    fresh = post(port, "/gate", dict(body, path="totally/new.py", qualname=None))
    assert fresh["decision"] == "allow" and fresh["case"] == "new_path"

    other = post(port, "/gate", dict(body, agent="bo", repo="not-this-repo"))
    assert other["decision"] == "allow" and other["case"] == "unindexed"


def test_warm_gate_decision_median_is_under_10ms(gconn) -> None:
    """§11.14: the 10 ms budget binds the SERVER-side handler, not the hook round trip."""
    from test_claims import declare
    declare(gconn, "aria", ["models.py::User"])
    samples = []
    for _ in range(100):
        t0 = time.perf_counter()
        gate_decision(gconn, repo="demo", agent="bo", path="models.py", qualname="User",
                      now=now_s())
        samples.append((time.perf_counter() - t0) * 1000)
    median = statistics.median(samples)
    assert median < 10.0, f"warm gate_decision median {median:.2f} ms exceeds the 10 ms budget"
