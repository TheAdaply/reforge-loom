"""M2 — §6 server-side decision order, resolution rules and the wire projection.

`claims.gate_decision` IS the endpoint's whole body, so the decision order is driven
directly here; the real HTTP round trip over `POST /gate` is covered once, against a
subprocess server, in `test_concurrency.py`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from conftest import REPO, SPEC, nid
from test_claims import AUTH, LOGIN, USER, declare

from loom.server.claims import gate_decision, resolve_gate_target
from loom.server.db import connect, immediate, init_db, now_s

WIRE_KEYS = {"decision", "case", "message", "node_id", "plan_id"}


def gate(conn: sqlite3.Connection, agent: str, path: str, qualname: str | None = None) -> dict:
    return gate_decision(conn, repo=REPO, agent=agent, path=path, qualname=qualname, now=now_s())


# ------------------------------------------------------------------ §6 steps 1-2


def test_a_repo_with_no_nodes_at_all_is_allowed_as_unindexed(tmp_path) -> None:
    db = str(tmp_path / "empty.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        d = gate(conn, "aria", "anything.py", "Whatever")
        assert (d["decision"], d["case"]) == ("allow", "unindexed")
        assert d["node_id"] is None and d["plan_id"] is None and d["message"] == ""
    finally:
        conn.close()


def test_an_unknown_path_in_an_indexed_repo_is_new_path(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER])                      # aria has a plan, so this is not step 5
    d = gate(gconn, "aria", "brand/new_module.py", "Thing")
    assert (d["decision"], d["case"]) == ("allow", "new_path")
    assert d["node_id"] is None


def test_paths_are_posix_normalised_before_resolution(gconn: sqlite3.Connection) -> None:
    assert resolve_gate_target(gconn, REPO, "./svc.py", None)[0] == nid("svc.py")
    assert resolve_gate_target(gconn, REPO, "svc.py\\", None)[0] == nid("svc.py")


# ------------------------------------------------------------------ §4 resolution


def test_serena_name_path_resolves_longest_prefix_first(gconn: sqlite3.Connection) -> None:
    # `/AuthService/authenticate` -> exact; deeper paths fall back up the chain (§4).
    assert resolve_gate_target(gconn, REPO, "svc.py", "/AuthService/authenticate")[0] == nid(AUTH)
    assert resolve_gate_target(gconn, REPO, "svc.py", "AuthService/authenticate/inner")[0] == \
        nid(AUTH)
    assert resolve_gate_target(gconn, REPO, "svc.py", "AuthService")[0] == \
        nid("svc.py::AuthService")


def test_unknown_qualname_on_a_known_path_falls_to_the_file_node(
        gconn: sqlite3.Connection) -> None:
    assert resolve_gate_target(gconn, REPO, "svc.py", "NoSuchSymbol")[0] == nid("svc.py")
    assert resolve_gate_target(gconn, REPO, "svc.py", None)[0] == nid("svc.py")


# ------------------------------------------------------------------ §6 steps 3-6


def test_decision_order_in_plan_then_foreign_then_out_of_scope_then_no_plan(
        gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    b = declare(gconn, "bo", [LOGIN], title="bo work")

    own = gate(gconn, "aria", "models.py", "User")
    assert (own["decision"], own["case"], own["plan_id"]) == ("allow", "in_plan", a["plan_id"])

    foreign = gate(gconn, "bo", "models.py", "User")
    assert (foreign["decision"], foreign["case"]) == ("deny", "foreign_claim")
    assert foreign["plan_id"] == a["plan_id"] and foreign["node_id"] == nid(USER)
    assert foreign["owner"]["owner_agent"] == "aria"

    oos = gate(gconn, "bo", "util.py", "hash_pw")
    assert (oos["decision"], oos["case"], oos["plan_id"]) == ("deny", "out_of_scope", b["plan_id"])
    assert "bo work" in oos["message"] and "util.py::hash_pw" in oos["message"]

    none = gate(gconn, "cy", "util.py", "hash_pw")
    assert (none["decision"], none["case"], none["plan_id"]) == ("deny", "no_plan", None)
    assert '"cy"' in none["message"]


def test_a_file_level_claim_is_hierarchical_end_to_end(gconn: sqlite3.Connection) -> None:
    """P0-2 end to end: declare the whole file the way app.INSTRUCTIONS step 2 tells agents
    to ("whole files are `relative/path.ext`"), then drive the §6 decision order over it."""
    a = declare(gconn, "aria", ["svc.py"], title="rewrite svc.py")

    inside = gate(gconn, "aria", "svc.py", "AuthService/authenticate")   # two levels deep
    assert (inside["decision"], inside["case"], inside["plan_id"]) == \
        ("allow", "in_plan", a["plan_id"])

    foreign = gate(gconn, "bo", "svc.py", "login")
    assert (foreign["decision"], foreign["case"]) == ("deny", "foreign_claim")
    assert foreign["plan_id"] == a["plan_id"] and foreign["node_id"] == nid(LOGIN)
    assert 'is claimed by "aria"' in foreign["message"]

    assert declare(gconn, "bo", [AUTH])["reason"] == "conflict"   # and bo cannot claim in


def test_a_foreign_read_claim_never_blocks_an_in_plan_write(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [LOGIN], assumes=[USER])       # aria READS User
    declare(gconn, "bo", [USER], title="bo owns user")    # bo WRITES User (warned at declare)
    d = gate(gconn, "bo", "models.py", "User")
    assert (d["decision"], d["case"]) == ("allow", "in_plan")


def test_an_assumed_node_is_not_editable_by_its_reader(gconn: sqlite3.Connection) -> None:
    # A read claim grants no write right — it falls through to §6 step 5.
    a = declare(gconn, "aria", ["iso.py::lonely"], assumes=[USER], title="reader")
    d = gate(gconn, "aria", "models.py", "User")
    assert (d["decision"], d["case"], d["plan_id"]) == ("deny", "out_of_scope", a["plan_id"])
    assert "models.py::User" in d["message"]


def test_out_of_scope_names_the_most_recently_updated_plan(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER], title="older")
    newer = declare(gconn, "aria", ["iso.py::lonely"], title="newer")
    with immediate(gconn):
        gconn.execute("UPDATE plans SET updated='2099-01-01T00:00:00Z' WHERE id=?",
                      (newer["plan_id"],))
    d = gate(gconn, "aria", "util.py", "hash_pw")
    assert d["case"] == "out_of_scope" and d["plan_id"] == newer["plan_id"]
    assert "newer" in d["message"]


def test_expired_plan_stops_allowing_its_owner(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    with immediate(gconn):
        gconn.execute("UPDATE plans SET ttl_expires=? WHERE id=?", (now_s() - 1, a["plan_id"]))
    d = gate(gconn, "aria", "models.py", "User")
    assert (d["decision"], d["case"]) == ("deny", "no_plan")


# ------------------------------------------------------------------ wire projection


def test_every_decision_carries_the_five_wire_keys(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER])
    for agent, path, qual in [("aria", "models.py", "User"), ("bo", "models.py", "User"),
                              ("bo", "util.py", "hash_pw"), ("aria", "new.py", None)]:
        d = gate(gconn, agent, path, qual)
        assert set(d) >= WIRE_KEYS
        assert d["decision"] in ("allow", "deny")
        assert (d["message"] == "") is (d["decision"] == "allow")


def test_gate_never_raises_on_hostile_input(gconn: sqlite3.Connection) -> None:
    for path, qual in [("", None), ("../../etc/passwd", "x"), ("svc.py", "/" * 50),
                       ("svc.py", "A/B/C/D/E"), ("svc.py::weird", "")]:
        d = gate(gconn, "aria", path, qual)
        assert d["decision"] in ("allow", "deny")


def test_spec_is_inlined_in_the_deny_so_the_blocked_agent_needs_no_second_call(
        gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER], spec=SPEC)
    d = gate(gconn, "bo", "models.py", "User")
    for heading in ("## Goal", "## Write targets", "## New/changed interfaces", "## Assumes",
                    "## Out of scope"):
        assert heading in d["message"]


# ------------------------------------------------------- the ROUTE's always-200 contract


def _gate_route(db: str, repos: dict[str, str]):
    """The real `POST /gate` endpoint, reachable without a server process or an HTTP client.

    `build_server` closes the routes over its state, so the only handle on `gate_route` is
    the Starlette app it builds. Driving the coroutine with a hand-made ASGI scope keeps this
    test on the ROUTE (where the try/except lives) instead of on `gate_decision` below it.
    """
    from loom.server.app import build_server

    app = build_server(db, repos).streamable_http_app(streamable_http_path="/mcp",
                                                      host="127.0.0.1")
    return next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/gate")


def _post(endpoint, body: bytes) -> tuple[int, dict]:
    from starlette.requests import Request

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": "/gate", "query_string": b"",
             "headers": [(b"content-type", b"application/json")]}
    resp = asyncio.run(endpoint(Request(scope, receive)))
    return resp.status_code, json.loads(bytes(resp.body).decode("utf-8"))


def test_gate_answers_200_and_the_five_keys_when_the_decision_itself_fails(
        graph_db: str, tmp_path, monkeypatch, capsys) -> None:
    """break3 chaos-F2: on a FULL DISK the audit INSERT inside `gate_decision` raised
    `sqlite3.OperationalError: database or disk is full`, so `/gate` answered HTTP 500 and
    every hook in the fleet failed open — silently, while `/health` still said `{"ok": true}`.
    §6/protocol.md:204 is unconditional: always HTTP 200, always exactly these five keys.
    """
    import loom.server.app as app_module

    endpoint = _gate_route(graph_db, {REPO: str(tmp_path)})
    body = json.dumps({"agent": "bo", "repo": REPO, "path": "models.py",
                       "qualname": "User"}).encode("utf-8")

    status, ok_answer = _post(endpoint, body)
    assert status == 200 and set(ok_answer) == WIRE_KEYS      # the route works at all

    def full_disk(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(app_module, "gate_decision", full_disk)
    status, answer = _post(endpoint, body)

    assert status == 200
    assert set(answer) == WIRE_KEYS                           # exactly five, no error key
    assert answer["decision"] == "allow"                      # advisory, never a hard deny
    # BC3-3 (§11.46): its own case — "unindexed" meaning both "repo not indexed" and
    # "server failed to judge" was the one-signal-two-meanings ambiguity cli-F1 closed
    # for origin. The hook branches on `decision` alone, so behavior is unchanged.
    assert answer["case"] == "error"
    # ...and the operator is told, since the audit trail is the thing that just failed.
    assert "WARNING" in capsys.readouterr().err
