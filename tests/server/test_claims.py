"""M2 — claim judgement, expansion, TTL law, deny composition (BUILD-SPEC §2/§5/§7.4).

Every direct `claims.*` mutator is wrapped in `db.immediate` here, mirroring the
production path (`tools.py` owns the transaction — §2 transaction law). `gate_decision`
and `check_node` are deliberately NOT wrapped: they take their own bookkeeping lock.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import REPO, SPEC, nid

from loom.server import claims
from loom.server.db import immediate, iso, now_s

FORBIDDEN = ("force", "bypass", "override", "unclaim", "release(", "--force")

AUTH = "svc.py::AuthService/authenticate"
LOGIN = "svc.py::login"
HASH = "util.py::hash_pw"
USER = "models.py::User"
LONELY = "iso.py::lonely"
UNRELATED = "svc.py::unrelated"


def declare(conn: sqlite3.Connection, agent: str, targets: list[str],
            assumes: list[str] = (), title: str = "t", ttl_s: int = 1800, spec: str = SPEC):
    with immediate(conn):
        return claims.declare_plan(conn, agent=agent, repo=REPO, branch="", title=title,
                                   spec_md=spec, write_targets=targets, assumes=list(assumes),
                                   ttl_s=ttl_s, now=now_s())


# --------------------------------------------------------------------- expansion


def test_declare_claims_targets_and_one_hop_call_neighbours(gconn: sqlite3.Connection) -> None:
    r = declare(gconn, "aria", [AUTH])
    assert r["ok"] is True
    # both CALLS directions: caller `login` and callee `hash_pw`.
    assert set(r["claimed_write"]) == {nid(AUTH), nid(LOGIN), nid(HASH)}
    assert r["expanded_from"] == {nid(AUTH): sorted([nid(LOGIN), nid(HASH)])}
    assert r["claimed_read"] == []


def test_imports_radius_is_zero(gconn: sqlite3.Connection) -> None:
    # svc.py IMPORTS util.py — a file-level write target must not drag util.py in.
    r = declare(gconn, "aria", ["svc.py"])
    assert r["claimed_write"] == [nid("svc.py")]
    assert r["expanded_from"] == {}


def test_expanded_neighbour_is_write_and_blocks_a_foreign_declare(
        gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [AUTH])
    r = declare(gconn, "bo", [LOGIN])  # LOGIN was claimed only by expansion
    assert r["ok"] is False and r["reason"] == "conflict"
    assert {c["kind"] for c in r["conflicts"]} == {"write-write"}
    assert nid(LOGIN) in {c["node_id"] for c in r["conflicts"]}


def test_expanded_neighbour_is_owner_editable_without_rescope(
        gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [AUTH])
    d = claims.gate_decision(gconn, repo=REPO, agent="aria", path="svc.py",
                             qualname="login", now=now_s())
    assert (d["decision"], d["case"]) == ("allow", "in_plan")


# --------------------------------------------------------------------- hierarchy (P0-2)


def test_a_file_claim_covers_every_symbol_it_contains(gconn: sqlite3.Connection) -> None:
    """claimsx-F1 leg (b): the owner of a file-level claim was denied `out_of_scope` on
    every symbol-narrowed Edit inside the file it had just declared."""
    a = declare(gconn, "aria", ["svc.py"])
    assert a["claimed_write"] == [nid("svc.py")]          # the CLAIM stays file-level ...
    for qual in (None, "login", "AuthService", "AuthService/authenticate"):
        d = claims.gate_decision(gconn, repo=REPO, agent="aria", path="svc.py",
                                 qualname=qual, now=now_s())
        assert (d["decision"], d["case"]) == ("allow", "in_plan"), qual
        assert d["plan_id"] == a["plan_id"]               # ... the JUDGEMENT walks CONTAINS


def test_a_foreign_file_claim_blocks_a_symbol_edit_two_levels_inside_it(
        gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", ["svc.py"])
    declare(gconn, "bo", [USER], title="bo plan")         # bo has a plan, so this is not step 5
    d = claims.gate_decision(gconn, repo=REPO, agent="bo", path="svc.py",
                             qualname="AuthService/authenticate", now=now_s())
    assert (d["decision"], d["case"]) == ("deny", "foreign_claim")
    assert d["plan_id"] == a["plan_id"] and d["owner"]["owner_agent"] == "aria"
    assert d["owner"]["ref"] == "svc.py"                  # the deny names the claimed container


def test_declaring_a_file_conflicts_with_a_live_claim_on_a_symbol_inside_it(
        gconn: sqlite3.Connection) -> None:
    """claimsx-F1 leg (a), the silent one: bo declared the whole file over aria's live
    symbol claim and BOTH were then cleared to write the same bytes."""
    a = declare(gconn, "aria", [AUTH])
    r = declare(gconn, "bo", ["svc.py"])
    assert r["ok"] is False and r["reason"] == "conflict"
    assert {c["kind"] for c in r["conflicts"]} == {"write-write"}
    assert a["plan_id"] in {c["owner_plan_id"] for c in r["conflicts"]}
    assert gconn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1   # nothing landed


def test_declaring_a_symbol_conflicts_with_a_live_claim_on_its_file(
        gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", ["svc.py"])
    r = declare(gconn, "bo", [AUTH])                      # two CONTAINS levels below svc.py
    assert r["ok"] is False and r["reason"] == "conflict"
    assert nid("svc.py") in {c["node_id"] for c in r["conflicts"]}


def test_a_class_claim_and_a_method_claim_are_the_same_ground(
        gconn: sqlite3.Connection) -> None:
    """xm-F4 / claimsx repro4-A: aria owned the class, bo owned a method inside it, and
    `list_claims` showed two owners on overlapping ground with both gates saying in_plan."""
    a = declare(gconn, "aria", ["svc.py::AuthService"])
    assert declare(gconn, "bo", [AUTH])["reason"] == "conflict"
    d = claims.gate_decision(gconn, repo=REPO, agent="aria", path="svc.py",
                             qualname="AuthService/authenticate", now=now_s())
    assert (d["decision"], d["case"], d["plan_id"]) == ("allow", "in_plan", a["plan_id"])


def test_contains_closure_walks_both_directions_transitively(gconn: sqlite3.Connection) -> None:
    up = claims.contains_closure(gconn, REPO, {nid(AUTH)}, down=False)
    assert up == {nid(AUTH), nid("svc.py::AuthService"), nid("svc.py")}
    down = claims.contains_closure(gconn, REPO, {nid("svc.py")}, up=False)
    assert down == {nid("svc.py"), nid("svc.py::AuthService"), nid(AUTH), nid(LOGIN),
                nid(UNRELATED)}
    assert claims.contains_closure(gconn, REPO, {nid(LONELY)}) == {nid(LONELY), nid("iso.py")}


def test_conflict_scope_is_ancestors_and_contained_never_siblings(
        gconn: sqlite3.Connection) -> None:
    """loom is function-level or it is nothing. `login` and `unrelated` share only a file
    with `authenticate`; neither may enter its conflict scope."""
    scope = claims._scope_for_conflicts(gconn, REPO, {nid(AUTH)})
    assert scope == {nid(AUTH), nid("svc.py::AuthService"), nid("svc.py")}
    assert nid(LOGIN) not in scope and nid(UNRELATED) not in scope
    # A file claim still reaches everything inside it.
    assert claims._scope_for_conflicts(gconn, REPO, {nid("svc.py")}) == {
        nid("svc.py"), nid("svc.py::AuthService"), nid(AUTH), nid(LOGIN), nid(UNRELATED)}


def test_two_agents_may_claim_two_unrelated_symbols_in_one_file(
        gconn: sqlite3.Connection) -> None:
    """The headline promise: sharing a file is not a collision. `unrelated` neither calls
    nor is called by `authenticate`, so only the CONTAINS pivot could make them contend."""
    assert declare(gconn, "aria", [AUTH])["ok"] is True
    got = declare(gconn, "bo", [UNRELATED])
    assert got["ok"] is True, got


# --------------------------------------------------------------------- conflicts


def test_write_write_conflict_claims_nothing_and_carries_the_owner_spec(
        gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    before = gconn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    r = declare(gconn, "bo", [USER])
    assert r["ok"] is False and r["reason"] == "conflict"
    c = r["conflicts"][0]
    assert c["kind"] == "write-write" and c["ref"] == USER
    assert c["owner_agent"] == "aria" and c["owner_plan_id"] == a["plan_id"]
    assert c["owner_spec_md"] == SPEC and c["owner_expires_iso"].endswith("Z")
    assert gconn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == before
    assert gconn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1


def test_write_read_warns_and_still_claims(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [LONELY], assumes=[USER])          # aria READS User
    r = declare(gconn, "bo", [USER])                          # bo WRITES User
    assert r["ok"] is True
    assert [w["kind"] for w in r["warnings"]] == ["write-read"]
    assert r["warnings"][0]["owner_spec_md"] == SPEC


def test_read_write_warns_and_still_claims(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER])                            # aria WRITES User
    r = declare(gconn, "bo", [LONELY], assumes=[USER])        # bo READS User
    assert r["ok"] is True and r["claimed_read"] == [nid(USER)]
    assert [w["kind"] for w in r["warnings"]] == ["read-write"]


def test_shared_read_never_conflicts(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [LONELY], assumes=[USER])
    r = declare(gconn, "bo", ["svc.py"], assumes=[USER])
    assert r["ok"] is True and r["warnings"] == []


def test_self_conflicts_are_skipped(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER])
    r = declare(gconn, "aria", [USER], title="second")
    assert r["ok"] is True and r["warnings"] == []


def test_read_set_excludes_anything_already_write_claimed(gconn: sqlite3.Connection) -> None:
    r = declare(gconn, "aria", [AUTH], assumes=[HASH])  # HASH arrives via expansion as write
    assert r["claimed_read"] == [] and nid(HASH) in r["claimed_write"]


# --------------------------------------------------------------------- TTL / sweep


def _age(conn: sqlite3.Connection, plan_id: str, ttl_expires: float) -> None:
    with immediate(conn):
        conn.execute("UPDATE plans SET ttl_expires=? WHERE id=?", (ttl_expires, plan_id))


def test_expired_plan_stops_blocking_before_the_sweep_runs(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    _age(gconn, a["plan_id"], now_s() - 1)          # expired, but inside the grace window
    r = declare(gconn, "bo", [USER])
    assert r["ok"] is True                          # the read filter, not the sweep, is law


def test_sweep_expires_and_tombstones_past_the_grace_window(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    _age(gconn, a["plan_id"], now_s() - claims.SWEEP_GRACE_S - 10)
    with immediate(gconn):
        swept = claims.sweep(gconn, REPO, now_s())
    assert swept == [a["plan_id"]]
    row = gconn.execute("SELECT status FROM plans WHERE id=?", (a["plan_id"],)).fetchone()
    assert row["status"] == "expired"
    assert gconn.execute("SELECT COUNT(*) FROM claims WHERE plan_id=? AND released IS NULL",
                         (a["plan_id"],)).fetchone()[0] == 0
    assert gconn.execute("SELECT COUNT(*) FROM claims WHERE plan_id=?",
                         (a["plan_id"],)).fetchone()[0] > 0   # tombstoned, never DELETEd


def test_renew_never_shortens_and_refuses_an_expired_plan(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    far = now_s() + 99999
    _age(gconn, a["plan_id"], far)
    with immediate(gconn):
        assert claims.renew(gconn, a["plan_id"], now_s())["expires_ts"] == far
    _age(gconn, a["plan_id"], now_s() - 1)
    with immediate(gconn):
        assert claims.renew(gconn, a["plan_id"], now_s()) == {"renewed": 0, "reason": "expired"}
    with immediate(gconn):
        assert claims.renew(gconn, "lm-nope", now_s()) == {"renewed": 0, "reason": "unknown_plan"}


def test_renew_after_release_reports_released(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    with immediate(gconn):
        claims.release(gconn, a["plan_id"], "aria", "done", now_s())
    with immediate(gconn):
        assert claims.renew(gconn, a["plan_id"], now_s()) == {"renewed": 0, "reason": "released"}


def test_ttl_floor_is_clamped_at_declare(gconn: sqlite3.Connection) -> None:
    now = now_s()
    r = declare(gconn, "aria", [USER], ttl_s=5)
    assert r["expires_ts"] >= now + claims.TTL_FLOOR_S
    detail = gconn.execute("SELECT detail FROM events WHERE action='declared'").fetchone()["detail"]
    assert "clamped" in detail


def test_implicit_renew_on_an_in_plan_gate_hit(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER], ttl_s=60)
    before = gconn.execute("SELECT ttl_expires FROM plans WHERE id=?",
                           (a["plan_id"],)).fetchone()[0]
    claims.gate_decision(gconn, repo=REPO, agent="aria", path="models.py", qualname="User",
                         now=now_s())
    after = gconn.execute("SELECT ttl_expires FROM plans WHERE id=?",
                          (a["plan_id"],)).fetchone()[0]
    assert after > before


# --------------------------------------------------------------------- orphans / release


def test_orphaned_claim_is_judged_dead(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    with immediate(gconn):
        gconn.execute("DELETE FROM plans WHERE id=?", (a["plan_id"],))
    assert claims.find_conflicts(gconn, REPO, {nid(USER)}, set(), set(), now_s()) == []
    assert claims.active_claims(gconn, REPO, now_s()) == []
    d = claims.gate_decision(gconn, repo=REPO, agent="bo", path="models.py", qualname="User",
                             now=now_s())
    assert d["case"] == "no_plan"          # dead claim, so we fall through to §6 step 6


def test_release_is_owner_only_and_tombstones(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [AUTH])
    with immediate(gconn):
        assert claims.release(gconn, a["plan_id"], "bo", "done", now_s()) == {
            "ok": False, "reason": "not_owner"}
    with immediate(gconn):
        r = claims.release(gconn, a["plan_id"], "aria", "superseded", now_s())
    assert r == {"ok": True, "released_claims": 3, "plan_status": "superseded"}
    with immediate(gconn):
        assert claims.release(gconn, a["plan_id"], "aria", "done", now_s())["reason"] == "not_active"
    assert declare(gconn, "bo", [AUTH])["ok"] is True


def test_rescope_paths(gconn: sqlite3.Connection) -> None:
    a = declare(gconn, "aria", [USER])
    with immediate(gconn):
        assert claims.rescope(gconn, plan_id="lm-nope", add_targets=[], add_assumes=[],
                              now=now_s()) == {"ok": False, "reason": "unknown_plan"}
    with immediate(gconn):
        r = claims.rescope(gconn, plan_id=a["plan_id"], add_targets=[AUTH],
                           add_assumes=[LONELY], now=now_s())
    assert r["ok"] is True and r["plan_id"] == a["plan_id"]
    assert set(r["claimed_write"]) == {nid(AUTH), nid(LOGIN), nid(HASH)}
    plan = claims.get_plan(gconn, a["plan_id"])
    assert USER in plan["write_claims"] and AUTH in plan["write_claims"]
    assert plan["read_claims"] == [LONELY]
    _age(gconn, a["plan_id"], now_s() - 1)
    with immediate(gconn):
        assert claims.rescope(gconn, plan_id=a["plan_id"], add_targets=[], add_assumes=[],
                              now=now_s()) == {"ok": False, "reason": "not_active"}


def test_rescope_conflict_claims_nothing_new(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER])
    b = declare(gconn, "bo", [LONELY])
    before = gconn.execute("SELECT COUNT(*) FROM claims WHERE plan_id=?",
                           (b["plan_id"],)).fetchone()[0]
    with immediate(gconn):
        r = claims.rescope(gconn, plan_id=b["plan_id"], add_targets=[USER], add_assumes=[],
                           now=now_s())
    assert r["ok"] is False and r["reason"] == "conflict"
    assert gconn.execute("SELECT COUNT(*) FROM claims WHERE plan_id=?",
                         (b["plan_id"],)).fetchone()[0] == before


# --------------------------------------------------------------------- resolution


def test_resolve_query_order(gconn: sqlite3.Connection) -> None:
    assert [r["id"] for r in claims.resolve_query(gconn, REPO, AUTH)] == [nid(AUTH)]
    assert [r["id"] for r in claims.resolve_query(gconn, REPO, "svc.py")] == [nid("svc.py")]
    assert [r["id"] for r in claims.resolve_query(gconn, REPO, "authenticate")] == [nid(AUTH)]
    assert [r["id"] for r in claims.resolve_query(gconn, REPO, "AuthService")] == \
        [nid("svc.py::AuthService")]
    assert [r["id"] for r in claims.resolve_query(gconn, REPO, "hentic")] == [nid(AUTH)]
    assert claims.resolve_query(gconn, REPO, "zzz") == []


def test_unresolvable_target_is_a_validation_error_with_suggestions(
        gconn: sqlite3.Connection) -> None:
    r = declare(gconn, "aria", ["authenticat3"])
    assert r["ok"] is False and r["reason"] == "validation"
    assert r["unresolved"][0]["query"] == "authenticat3"
    assert 0 < len(r["unresolved"][0]["suggestions"]) <= 5
    assert gconn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_suggestion_ranking_puts_the_tail_substring_first(gconn: sqlite3.Connection) -> None:
    assert claims.suggestions(gconn, REPO, "Foo/hash_pw")[0] == HASH


def test_declare_accepts_raw_node_ids(gconn: sqlite3.Connection) -> None:
    assert declare(gconn, "aria", [nid(USER)])["claimed_write"] == [nid(USER)]


# --------------------------------------------------------------------- spec validation


def test_validate_spec(gconn: sqlite3.Connection) -> None:
    assert claims.validate_spec(SPEC) == []
    assert "missing heading: ## Assumes" in claims.validate_spec(SPEC.replace("## Assumes", "## A"))
    assert any("max 60" in e for e in claims.validate_spec(SPEC + "\nx" * 80))
    assert any("max 8000" in e for e in claims.validate_spec(SPEC + "x" * 9000))
    holed = SPEC.replace("Add a TTL cache", "[Two sentences here]")
    assert any("[Two sentences" in e for e in claims.validate_spec(holed))
    bad = declare(gconn, "aria", [USER], spec="nothing here")
    assert bad["ok"] is False and bad["reason"] == "validation" and bad["validation_errors"]


# --------------------------------------------------------------------- deny composition


def _all_denies(conn: sqlite3.Connection) -> list[str]:
    a = declare(conn, "aria", [USER])
    foreign = claims.gate_decision(conn, repo=REPO, agent="bo", path="models.py",
                                   qualname="User", now=now_s())
    declare(conn, "bo", [LONELY], title="bo plan")
    oos = claims.gate_decision(conn, repo=REPO, agent="bo", path="svc.py",
                               qualname="login", now=now_s())
    nop = claims.gate_decision(conn, repo=REPO, agent="cy", path="svc.py",
                               qualname="login", now=now_s())
    assert (foreign["case"], oos["case"], nop["case"]) == ("foreign_claim", "out_of_scope",
                                                           "no_plan")
    assert foreign["plan_id"] == a["plan_id"]
    return [foreign["message"], oos["message"], nop["message"]]


def test_deny_messages_use_the_frozen_templates(gconn: sqlite3.Connection) -> None:
    foreign, oos, nop = _all_denies(gconn)
    assert foreign.startswith(f'loom: BLOCKED — {USER} is claimed by "aria" under plan lm-')
    assert "Its spec follows." in foreign and "## Out of scope" in foreign
    assert oos == claims.OUT_OF_SCOPE_TMPL.format(
        ref=LOGIN, plan_id=oos.split('plan ')[1].split(' ')[0], title="bo plan")
    assert nop == claims.NO_PLAN_TMPL.format(agent="cy")


def test_no_deny_surface_names_an_override_path(gconn: sqlite3.Connection) -> None:
    for msg in _all_denies(gconn):
        low = msg.lower()
        for bad in FORBIDDEN:
            assert bad not in low, f"deny copy names an escape hatch {bad!r}: {msg}"


def test_foreign_claim_strips_html_comments(gconn: sqlite3.Connection) -> None:
    declare(gconn, "aria", [USER], spec="<!-- secret note -->\n" + SPEC)
    d = claims.gate_decision(gconn, repo=REPO, agent="bo", path="models.py", qualname="User",
                             now=now_s())
    assert "secret note" not in d["message"] and "## Goal" in d["message"]


def test_foreign_claim_caps_the_whole_message_at_9000_chars() -> None:
    # Declare-time validation keeps specs <=8000 chars, so the cap is defence in depth:
    # exercise `compose_foreign_claim` directly with an oversized owner.
    owner = {"kind": "write-write", "node_id": "n-x", "ref": USER, "owner_agent": "aria",
             "owner_plan_id": "lm-4f2a", "owner_title": "harden", "owner_spec_md": "x" * 20000,
             "owner_expires_ts": now_s() + 610, "owner_expires_iso": iso(now_s() + 610)}
    msg = claims.compose_foreign_claim(owner)
    assert len(msg) == claims.MAX_DENY_CHARS
    assert msg.startswith("loom: BLOCKED —")   # the actionable header is never sacrificed
    assert msg.endswith('[spec truncated — call get_plan("lm-4f2a") for the full text]')
    assert "(in 10m)" in msg


def test_claims_only_arm_blanks_the_spec(gconn: sqlite3.Connection, monkeypatch) -> None:
    declare(gconn, "aria", [USER])
    monkeypatch.setenv("LOOM_ARM", "claims_only")
    r = declare(gconn, "bo", [USER])
    assert r["conflicts"][0]["owner_spec_md"] == ""
    d = claims.gate_decision(gconn, repo=REPO, agent="bo", path="models.py", qualname="User",
                             now=now_s())
    assert "## Goal" not in d["message"] and d["message"].startswith("loom: BLOCKED")


def test_every_decision_writes_one_event(gconn: sqlite3.Connection) -> None:
    before = gconn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    claims.gate_decision(gconn, repo=REPO, agent="cy", path="svc.py", qualname="login",
                         now=now_s())
    assert gconn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before + 1
    assert gconn.execute("SELECT action FROM events ORDER BY rowid DESC LIMIT 1"
                         ).fetchone()["action"] == "denied"


def test_the_three_server_side_templates_are_verbatim_from_the_frozen_spec() -> None:
    """§7.4 is a FROZEN artifact: the literals must appear in BUILD-SPEC.md character for
    character. UNSCOPED is deliberately absent — it is hook-local (locator.py, M3)."""
    import pathlib
    doc = pathlib.Path(__file__).resolve().parents[2] / "docs" / "BUILD-SPEC.md"
    if not doc.exists():                       # pragma: no cover - spec not vendored here
        pytest.skip("BUILD-SPEC.md not present")
    text = doc.read_text()
    for line in (claims.FOREIGN_CLAIM_TMPL.splitlines()[:2]
                 + [claims.OUT_OF_SCOPE_TMPL, claims.NO_PLAN_TMPL]):
        assert line in text, f"template drifted from the frozen §7.4 text: {line!r}"
    assert not hasattr(claims, "UNSCOPED_TMPL"), "UNSCOPED is hook-local (§7.4, GATE-2 edit 3)"


def test_strip_html_comments() -> None:
    assert claims.strip_html_comments("a<!--\nx\n-->b") == "ab"


def test_no_python_lock_anywhere_in_the_server() -> None:
    """§2: mutual exclusion is SQLite's BEGIN IMMEDIATE, never a Python lock.

    `threading.local` (app.connection_factory) is a per-thread slot, not a lock — it
    never blocks a thread — so only real synchronisation primitives are forbidden.
    """
    import ast
    import pathlib
    banned = {"Lock", "RLock", "Semaphore", "BoundedSemaphore", "Condition", "Barrier"}
    for p in pathlib.Path(claims.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text())):
            used = getattr(node, "attr", None) or getattr(node, "id", None)
            assert used not in banned, f"{p.name} takes a Python lock ({used}) — §2 forbids it"


def test_iso_round_trip_is_utc_z() -> None:
    assert iso(0.0) == "1970-01-01T00:00:00Z"


@pytest.mark.parametrize("bad", ["", "n-zzzzzzzz"])
def test_gate_on_an_unknown_ref_never_raises(gconn: sqlite3.Connection, bad: str) -> None:
    d = claims.gate_decision(gconn, repo=REPO, agent="aria", path=bad, qualname=None, now=now_s())
    assert d["decision"] == "allow" and d["case"] == "new_path"


class TestPathSuffixResolution:
    """Iteration-2 tryout fix: the taught `path.py::qualname` form must resolve on DEEP trees
    (found live on conduit: `auth.py::login` returned zero matches against
    `src/conduit/api/routes/auth.py::login`)."""

    @staticmethod
    def _seed_deep(gconn):
        from loom.server.db import iso, now_s
        from loom.server.ids import node_id
        rows = [("src/api/routes/auth.py", ""), ("src/api/routes/auth.py", "login"),
                ("src/core/security.py", "decode_jwt")]
        gconn.executemany(
            "INSERT OR IGNORE INTO nodes (id, repo, path, qualname, kind, updated) "
            "VALUES (?,?,?,?,?,?)",
            [(node_id("demo", p, q), "demo", p, q, "function" if q else "file", iso(now_s()))
             for p, q in rows])

    def test_basename_qualified_ref_resolves_by_path_suffix(self, gconn):
        from loom.server.claims import resolve_query
        self._seed_deep(gconn)
        rows = resolve_query(gconn, "demo", "auth.py::login")
        assert [(r["path"], r["qualname"]) for r in rows] == [("src/api/routes/auth.py", "login")]

    def test_partial_dir_suffix_also_resolves(self, gconn):
        from loom.server.claims import resolve_query
        self._seed_deep(gconn)
        rows = resolve_query(gconn, "demo", "routes/auth.py::login")
        assert len(rows) == 1 and rows[0]["path"] == "src/api/routes/auth.py"
        # non-'/'-boundary fragments must NOT match (outes/auth.py is not a suffix)
        assert resolve_query(gconn, "demo", "outes/auth.py::login") == []

    def test_bare_file_basename_resolves_file_node(self, gconn):
        from loom.server.claims import resolve_query
        self._seed_deep(gconn)
        rows = resolve_query(gconn, "demo", "auth.py")
        assert [(r["path"], r["qualname"]) for r in rows] == [("src/api/routes/auth.py", "")]


class TestFuzzyTailGate:
    """U3: §5.2's LAST rung — bare substring on the query tail — refuses low-information
    queries instead of guessing (graphiti `dedup_helpers`: gate the fuzzy path, never the
    exact ones; escalate on ambiguity). The failure being closed is not a bad suggestion,
    it is a bad CLAIM: `_resolve_all` promotes a lone substring hit straight to a write
    claim, so `run` could take `Server/runner` while the agent meant something else.
    """

    @staticmethod
    def _seed(gconn, quals: list[str]) -> None:
        from loom.server.db import iso, now_s
        from loom.server.ids import node_id
        gconn.executemany(
            "INSERT OR IGNORE INTO nodes (id, repo, path, qualname, kind, updated) "
            "VALUES (?,?,?,?,?,?)",
            [(node_id("demo", "srv.py", q), "demo", "srv.py", q, "Function", iso(now_s()))
             for q in quals])

    def test_a_short_tail_refuses_even_when_it_would_have_resolved_uniquely(self, gconn):
        """The exact live defect: one accidental substring hit used to become a claim."""
        self._seed(gconn, ["Server/runner"])
        assert claims.resolve_query(gconn, "demo", "run") == []

    def test_a_four_character_unique_tail_still_resolves(self, gconn):
        self._seed(gconn, ["Server/runner"])
        rows = claims.resolve_query(gconn, "demo", "unne")
        assert [(r["path"], r["qualname"]) for r in rows] == [("srv.py", "Server/runner")]

    def test_the_gate_counts_symbols_not_characters(self, gconn):
        """Three sharing a tail is still an answer (all three come back as candidates, and
        `_resolve_all` refuses to pick); a fourth means the tail names nothing in particular."""
        self._seed(gconn, ["A/handle", "B/handler", "C/handled"])
        assert len(claims.resolve_query(gconn, "demo", "handl")) == 3

        self._seed(gconn, ["D/handles"])
        assert claims.resolve_query(gconn, "demo", "handl") == []

    def test_the_exact_and_boundary_rungs_are_untouched_by_the_length_gate(self, gconn):
        """`go` is two characters and must STILL resolve: it is a '/'-boundary suffix, not a
        fuzzy match. Gating it would break every short method name in the repo."""
        self._seed(gconn, ["Runner/go"])
        rows = claims.resolve_query(gconn, "demo", "go")
        assert [(r["path"], r["qualname"]) for r in rows] == [("srv.py", "Runner/go")]

    def test_a_refused_query_lands_on_the_honest_unresolved_path(self, gconn):
        """`hen` is a substring of `AuthService/authenticate` and of nothing else in the
        fixture, so it used to claim it outright. Now it declares nothing and says why."""
        r = declare(gconn, "aria", ["hen"])

        assert r["ok"] is False and r["reason"] == "validation"
        assert r["unresolved"][0]["query"] == "hen"
        assert 0 < len(r["unresolved"][0]["suggestions"]) <= 5
        assert gconn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        # ...while the 6-character tail of the same symbol still resolves (frozen above).
        assert [r["id"] for r in claims.resolve_query(gconn, REPO, "hentic")] == [nid(AUTH)]
