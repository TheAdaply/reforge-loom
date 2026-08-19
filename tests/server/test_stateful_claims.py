"""Hypothesis STATEFUL model of the claim lifecycle (§2/§5/§6).

Operations: declare_plan / rescope / renew / release / time-warp expiry / check_node /
gate_decision, over 3 agents and the indexed `tests/fixtures/pyrepo` graph, in-process
against an in-memory db. Transaction discipline mirrors `server/tools.py` exactly:
mutators run inside `db.immediate`, `check_node` / `gate_decision` run bare.

Invariants asserted after every step:
  I1  at most ONE agent holds effective write authority over any node
  I2  a released / expired plan never appears in the active-claim set
  I3  every deny names a plan that is active at that instant (or names none)
  I4  check_node allows exactly the agents that own the node right now
  I5  every conflict response embeds the owner's spec_md
  I6  no operation raises

These are INDEPENDENT cross-checks, not self-validation: `_owners` recomputes authority from
the raw `claims`/`plans` rows against a precomputed static CONTAINS up-closure, so I1 and I4
can disagree with `check_node` — and do.

I1 has two modes. `strict` is the law as written in the README ("Overlapping plans are
refused at declare time ... so the merge conflict never gets written"). It FAILS on a known
defect: a §5.3 CALLS-hop expansion can claim a *container* (Class) node without ever
contending for that container's children, yet `check_node` grants authority downward from
any container claim — so two agents end up allowed on one method. See
`test_known_defect_expansion_container_grants_uncontended_authority` below, which pins the
minimal case. The default mode quarantines exactly that residue so the machine can keep
looking for other defects; set LOOM_FUZZ_STRICT=1 to run the law as written.

CI budget is deliberately small — 50 examples x 40 steps, well under a second — because this
is a regression NET, not the search. `LOOM_FUZZ_EXAMPLES` raises it, capped at 200 so no
configuration can turn the suite into a fuzzing run; the long searches (2500 examples) belong
in a scratch harness, not in `pytest tests`.

    LOOM_FUZZ_EXAMPLES=200 uv run --directory <loom> pytest tests/server/test_stateful_claims.py
    LOOM_FUZZ_STRICT=1 ...      # I1 as the README states it — fails today, see the xfail below
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from loom.indexer.walk import index_repo
from loom.server import claims as C
from loom.server.db import DDL, connect, immediate, init_db
from loom.server.ids import node_ref

REPO = "fx"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pyrepo"
AGENTS = ["alice", "bob", "carol"]
STRICT = os.environ.get("LOOM_FUZZ_STRICT") == "1"

SPEC = """# Spec: cache authenticate

**Agent**: aria

## Goal

Add a TTL cache in front of the target. Auth endpoints drop to <5ms on hit.

## Write targets

- the declared targets

## New/changed interfaces

- CHANGED `authenticate(self, email: str, password: str) -> AuthResult`

## Assumes

- lib/util.py::helper

## Out of scope

Session storage and token refresh are untouched.
"""


def _index_template() -> tuple[list[tuple], list[tuple]]:
    """Index the fixture repo ONCE, at import, and keep the rows as a template.

    A copytree + tree-sitter parse per Hypothesis example would make even the CI budget
    slow and the 2000-example search infeasible; the graph is immutable for this machine,
    so replaying rows into `:memory:` is the same graph for a thousandth of the cost.
    """
    tmp = tempfile.mkdtemp()
    try:
        root = os.path.join(tmp, "pyrepo")
        shutil.copytree(FIXTURE, root)
        db = os.path.join(tmp, "loom.sqlite3")
        init_db(db)
        c = connect(db)
        with immediate(c) as cc:
            index_repo(cc, REPO, root, changed_only=False)
        nodes = [tuple(r) for r in c.execute(
            "SELECT id,repo,path,qualname,kind,body_hash,sig_hash,start_line,end_line,updated "
            "FROM nodes")]
        edges = [tuple(r) for r in c.execute("SELECT src,dst,kind FROM edges")]
        c.close()
        return nodes, edges
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_NODES, _EDGES = _index_template()

REF_TO_ID: dict[str, str] = {node_ref(n[2], n[3]): n[0] for n in _NODES}
ID_TO_REF: dict[str, str] = {v: k for k, v in REF_TO_ID.items()}
ALL_REFS: list[str] = sorted(REF_TO_ID)
PATHS = sorted({r.split("::")[0] for r in ALL_REFS})
QUALNAMES = sorted({r.split("::", 1)[1] for r in ALL_REFS if "::" in r})


def build_conn() -> sqlite3.Connection:
    """A fresh in-memory db holding the fixture graph and empty plan/claim tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.executemany("INSERT INTO nodes (id,repo,path,qualname,kind,body_hash,sig_hash,"
                     "start_line,end_line,updated) VALUES (?,?,?,?,?,?,?,?,?,?)", _NODES)
    conn.executemany("INSERT INTO edges (src,dst,kind) VALUES (?,?,?)", _EDGES)
    return conn


# Static CONTAINS up-closure of every node (the graph is immutable for this machine).
_probe = build_conn()
UP = {nid: frozenset(C.contains_closure(_probe, REPO, {nid}, down=False)) for nid in ID_TO_REF}
_probe.close()

refs = st.sampled_from(ALL_REFS)
agents = st.sampled_from(AGENTS)


class ClaimLifecycle(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.conn = build_conn()
        self.now = 1_000_000.0
        self.plans: dict[str, str] = {}          # plan_id -> owning agent
        self.order: list[str] = []               # creation order; the model addresses plans
                                                 # by INDEX so the strategy shape never
                                                 # depends on minted (time-seeded) ids.
        self.expansion: set[tuple[str, str]] = set()   # (plan_id, node_id) claimed via CALLS hop
        self.log: list[str] = []

    @initialize()
    def _start(self) -> None:
        self.log.append("start")

    # ---------------------------------------------------------------- helpers

    def _active_write_rows(self) -> list[tuple[str, str, str]]:
        return [(r["node_id"], r["plan_id"], r["agent"]) for r in self.conn.execute(
            "SELECT c.node_id, c.plan_id, p.agent FROM claims c JOIN plans p ON p.id=c.plan_id "
            "WHERE c.released IS NULL AND c.mode='write' AND p.status='active' "
            "AND p.ttl_expires > ? AND p.repo=?", (self.now, REPO))]

    def _owners(self, node_id: str, *, strict: bool = True) -> set[str]:
        """Agents that effectively hold write authority over `node_id` right now."""
        out = set()
        for nid, pid, agent in self._active_write_rows():
            if nid not in UP[node_id]:
                continue
            if not strict and nid != node_id and (pid, nid) in self.expansion:
                continue          # quarantined: the known expansion/container defect
            out.add(agent)
        return out

    def _pick(self, idx: int) -> str:
        """Address a plan by CREATION INDEX, never by its minted id (ids are time-seeded,
        so a `sampled_from(ids)` pool changes between Hypothesis replays -> FlakyStrategy)."""
        return self.order[idx] if idx < len(self.order) else "lm-nosuchplan"

    def _plan_active(self, plan_id: str) -> bool:
        r = self.conn.execute("SELECT status, ttl_expires FROM plans WHERE id=?",
                              (plan_id,)).fetchone()
        return bool(r and r["status"] == "active" and r["ttl_expires"] > self.now)

    def _record(self, res: dict, agent: str) -> None:
        if not res.get("ok"):
            return
        pid = res["plan_id"]
        if pid not in self.plans:
            self.order.append(pid)
        self.plans[pid] = agent
        for members in res.get("expanded_from", {}).values():
            for nid in members:
                self.expansion.add((pid, nid))

    def _check_deny(self, d: dict) -> None:
        # I3 — a deny must name an active plan, or name none at all (`no_plan`).
        if d["decision"] == "deny" and d["plan_id"] is not None:
            assert self._plan_active(d["plan_id"]), \
                f"I3 deny {d['case']} names non-active plan {d['plan_id']}"
        if d["decision"] == "deny" and d["case"] == "foreign_claim":
            # I5 — conflict responses embed the owner's spec.
            assert d["owner"] and d["owner"]["owner_spec_md"].strip(), "I5 empty owner_spec_md"
            assert d["owner"]["owner_spec_md"].splitlines()[0] in d["message"], \
                "I5 spec not embedded in the deny message"

    # ------------------------------------------------------------------ rules

    @rule(agent=agents, tgts=st.lists(refs, min_size=1, max_size=3, unique=True),
          assumes=st.lists(refs, max_size=2, unique=True),
          ttl=st.sampled_from([0, 30, 600, 1800, 2 ** 31]))
    def declare(self, agent, tgts, assumes, ttl):
        with immediate(self.conn):
            res = C.declare_plan(self.conn, agent=agent, repo=REPO, branch="b", title="t",
                                 spec_md=SPEC, write_targets=tgts, assumes=assumes,
                                 ttl_s=ttl, now=self.now)
        self.log.append(f"declare({agent!r},{tgts!r},ttl={ttl}) -> {res.get('reason', 'ok')}")
        if not res.get("ok"):
            assert res["reason"] in ("validation", "conflict")
            if res["reason"] == "conflict":
                for c in res["conflicts"]:
                    assert c["owner_spec_md"].strip(), "I5 empty owner_spec_md on declare refusal"
                    assert self._plan_active(c["owner_plan_id"]), \
                        "I3 declare refusal names a non-active plan"
        else:
            # The TTL law is a floor AND a ceiling (break3 chaos-F4/F5): no `ttl_s` an agent
            # can send may mint a claim outside the band, and none may raise on the way.
            assert self.now + C.TTL_FLOOR_S <= res["expires_ts"] <= self.now + C.TTL_CEIL_S
        self._record(res, agent)

    @rule(idx=st.integers(0, 7), extra=st.lists(refs, min_size=1, max_size=2, unique=True))
    def rescope(self, idx, extra):
        pid = self._pick(idx)
        with immediate(self.conn):
            res = C.rescope(self.conn, plan_id=pid, add_targets=extra, add_assumes=[],
                            now=self.now)
        self.log.append(f"rescope({pid!r},{extra!r}) -> {res.get('reason', 'ok')}")
        if not res.get("ok"):
            assert res["reason"] in ("unknown_plan", "not_active", "validation", "conflict")
            if res["reason"] == "conflict":
                for c in res["conflicts"]:
                    assert c["owner_spec_md"].strip(), "I5 empty owner_spec_md on rescope refusal"
                    assert self._plan_active(c["owner_plan_id"]), \
                        "I3 rescope refusal names a non-active plan"
        else:
            self._record(res, self.plans[pid])   # rescope keeps the plan's original owner

    @rule(idx=st.integers(0, 7), agent=agents)
    def renew(self, idx, agent):
        pid = self._pick(idx)
        # break3 chaos-F6: renewal is owner-only, so the predicate is active AND mine.
        expect = int(self._plan_active(pid) and self.plans.get(pid) == agent)
        with immediate(self.conn):
            res = C.renew(self.conn, pid, agent, self.now)
        self.log.append(f"renew({pid!r},{agent!r}) -> {res}")
        assert res["renewed"] == expect, \
            "renew disagrees with the active-AND-owned predicate"

    @rule(idx=st.integers(0, 7), agent=agents,
          status=st.sampled_from(["done", "superseded", "junk"]))
    def release(self, idx, agent, status):
        pid = self._pick(idx)
        with immediate(self.conn):
            res = C.release(self.conn, pid, agent, status, self.now)
        self.log.append(f"release({pid!r},{agent!r},{status!r}) -> {res.get('reason', 'ok')}")
        if res.get("ok"):
            assert self.plans.get(pid) == agent, "release granted to a non-owner"
            assert not self.conn.execute(
                "SELECT 1 FROM claims WHERE plan_id=? AND released IS NULL", (pid,)).fetchone(), \
                "I2 released plan still holds live claims"
        else:
            assert res["reason"] in ("unknown_plan", "not_owner", "not_active")

    @rule(delta=st.sampled_from([1.0, 61.0, 900.0, 1801.0, 3601.0, 5401.0]))
    def warp(self, delta):
        self.now += delta
        self.log.append(f"warp(+{delta}) now={self.now}")

    @rule(agent=agents, ref=refs)
    def check(self, agent, ref):
        nid = REF_TO_ID[ref]
        owners_before = self._owners(nid, strict=True)
        d = C.check_node(self.conn, repo=REPO, agent=agent, node_id=nid, now=self.now)
        self.log.append(f"check({agent!r},{ref!r}) -> {d['decision']}/{d['case']}")
        self._check_deny(d)
        # I4 — allow iff this agent owns the node at this instant.
        assert (d["decision"] == "allow") == (agent in owners_before), \
            f"I4 check({agent},{ref}) -> {d['decision']} but owners={sorted(owners_before)}"

    @rule(agent=agents, qual=st.one_of(st.none(), st.sampled_from(QUALNAMES)),
          path=st.one_of(st.sampled_from(PATHS), st.just("brand/new_file.py"),
                         st.just("no_suffix_at_all")))
    def gate(self, agent, qual, path):
        d = C.gate_decision(self.conn, repo=REPO, agent=agent, path=path, qualname=qual,
                            now=self.now)
        self.log.append(f"gate({agent!r},{path!r},{qual!r}) -> {d['decision']}/{d['case']}")
        self._check_deny(d)
        if path == "brand/new_file.py":
            assert d["decision"] == "allow" and d["case"] == "new_path", \
                "a brand-new path must always be allowed"

    # ------------------------------------------------------------- invariants

    @invariant()
    def one_writer_per_node(self):
        for nid, ref in ID_TO_REF.items():
            owners = self._owners(nid, strict=STRICT)
            assert len(owners) <= 1, \
                f"I1 {ref} is writable by {sorted(owners)} simultaneously"

    @invariant()
    def dead_plans_never_block(self):
        live = {r["plan_id"] for r in self.conn.execute(
            "SELECT plan_id FROM claims WHERE released IS NULL")}
        for pid in live:
            r = self.conn.execute("SELECT status FROM plans WHERE id=?", (pid,)).fetchone()
            if r and r["status"] != "active":
                pytest.fail(f"I2 plan {pid} is {r['status']} but still holds untombstoned claims")
        for row in C.active_claims(self.conn, REPO, self.now):
            assert self._plan_active(row["plan_id"]), \
                f"I2 active_claims lists dead plan {row['plan_id']}"

    def teardown(self) -> None:
        self.conn.close()


# `deadline=None` because a step can index-scan the whole fixture graph and a shared CI box
# would otherwise flake on timing, never on a claim rule. The budget is an env knob with a
# HARD CAP: a regression net that can be turned into a multi-minute fuzzing run by an
# ambient environment variable is a flaky suite waiting to happen.
_BUDGET = min(200, max(1, int(os.environ.get("LOOM_FUZZ_EXAMPLES", "50"))))
ClaimLifecycle.TestCase.settings = settings(
    max_examples=_BUDGET, stateful_step_count=int(os.environ.get("LOOM_FUZZ_STEPS", "40")),
    deadline=None, suppress_health_check=list(HealthCheck))
TestClaimLifecycle = ClaimLifecycle.TestCase


@pytest.mark.xfail(reason="known defect: a CALLS-hop expansion claim on a container grants "
                          "uncontended write authority over every node it contains "
                          "(break3 fuzz-F1, BACKLOG — the fix changes the authority model)",
                   strict=True)
def test_known_defect_expansion_container_grants_uncontended_authority():
    """alice and bob declare two different helpers; both end up owning one method.

    Deliberately asserts ONLY the invariant, never that either declare is granted, so it
    passes under EITHER fix direction — deny the second declare, or stop letting an
    expansion-acquired container claim confer downward authority at check time. `strict=True`
    turns the fix into an XPASS failure that says "delete this marker".
    """
    conn = build_conn()
    now = 1_000_000.0
    victim = "svc.py::AuthService/authenticate"
    holders = []
    for agent, target in (("alice", "lib/util.py::helper"), ("bob", "lib/util.py::make_tag")):
        with immediate(conn):
            r = C.declare_plan(conn, agent=agent, repo=REPO, branch="", title="t", spec_md=SPEC,
                               write_targets=[target], assumes=[], ttl_s=1800, now=now)
        if r.get("ok"):
            holders.append(agent)
    allowed = [a for a in holders
               if C.check_node(conn, repo=REPO, agent=a, node_id=REF_TO_ID[victim],
                               now=now)["decision"] == "allow"]
    conn.close()
    assert len(allowed) <= 1, f"{victim} is writable by {allowed} at the same time"
