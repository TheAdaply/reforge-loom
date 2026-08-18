"""BUILD-SPEC §2/§5/§6/§7.4 — claim judgement, TTL law, server-side deny composition.

All claim SQL lives here (§11.11 — the v2 Postgres flip is a localized rewrite).

Transaction law (§2): the CALLER owns the transaction. `sweep`, `declare_plan`, `rescope`,
`renew`, `release` (and declare's plan-ID minting) assume an open `db.immediate(conn)` from
their `tools.py` adapter — one BEGIN IMMEDIATE around the whole read->judge->write cycle,
the write lock taken before the first read. `gate_decision` / `check_node` are the fast read
path: they judge on plain reads (the `ttl_expires > now` filter makes a stale sweep harmless)
and take the write lock only to sweep, so callers must NOT wrap them. No Python locks exist.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from typing import Any

from loom.indexer.naming import norm_path, prefix_candidates
from loom.server.db import immediate, iso, log_event
from loom.server.ids import mint_plan_id, node_ref, split_ref

CLAIM_TTL_S = 1800
TTL_FLOOR_S = 60
SWEEP_GRACE_S = 3600
MAX_DENY_CHARS = 9000
CLOSURE_MAX_NODES = 5000     # bound on one CONTAINS walk (§4 hierarchy); a pathological
                             # graph must never stall the gate's fast read path.
_SQL_VARS = 400              # IN(...) chunk size, well under SQLite's variable limit.

# §7.4 verbatim. UNSCOPED is hook-local (locator.py, M3) and never lives here.
FOREIGN_CLAIM_TMPL = """loom: BLOCKED — {ref} is claimed by "{owner_agent}" under plan {owner_plan_id} "{owner_title}", expires {owner_expires_iso} (in {minutes}m).
Its spec follows. Build against its declared interfaces, or rescope your plan around it, or wait for expiry.

{owner_spec_md}"""

OUT_OF_SCOPE_TMPL = 'loom: {ref} is outside your declared plan {plan_id} "{title}". Call rescope(plan_id="{plan_id}", add_targets=["{ref}"]), then retry this edit.'

NO_PLAN_TMPL = 'loom: no active plan for agent "{agent}". Before editing: write a spec from templates/spec.md, resolve every target with resolve_nodes, call declare_plan, then retry this edit.'

_PLAN_COLS = ("id", "agent", "repo", "branch", "title", "spec_md", "status", "created", "updated")
_HEADINGS = ("## Goal", "## Write targets", "## New/changed interfaces", "## Assumes", "## Out of scope")
_PLACEHOLDERS = ("[short imperative title", "[your agent id", "[Two sentences",
                 "[Canonical node IDs", "[EXACT signatures", "[One line", "[Assumption")

# Active-claim predicate (§2), used verbatim everywhere. LEFT JOIN so an orphaned claim
# (plan row gone) is judged dead, never immortal.
_ACTIVE_FROM = ("FROM claims c LEFT JOIN plans p ON p.id = c.plan_id "
                "JOIN nodes n ON n.id = c.node_id ")
_ACTIVE_WHERE = "c.released IS NULL AND p.status='active' AND p.ttl_expires > ? "
_OWNER_COLS = ("SELECT c.node_id, c.mode, p.id AS pid, p.agent, p.title, p.spec_md, "
               "p.ttl_expires, n.path, n.qualname ")


def strip_html_comments(md: str) -> str:
    """Drop `<!-- ... -->` blocks before a spec is inlined into a deny message (§7.4)."""
    return re.sub(r"<!--.*?-->", "", md, flags=re.S)


def _arm(spec_md: str) -> str:
    # LOOM_ARM=claims_only blanks spec_md everywhere it is surfaced (papers 5.6, §9.1).
    return "" if os.environ.get("LOOM_ARM") == "claims_only" else spec_md


def _conflict(r: sqlite3.Row, kind: str) -> dict[str, Any]:
    return {"kind": kind, "node_id": r["node_id"], "ref": node_ref(r["path"], r["qualname"]),
            "owner_agent": r["agent"], "owner_plan_id": r["pid"], "owner_title": r["title"],
            "owner_spec_md": _arm(r["spec_md"]), "owner_expires_ts": r["ttl_expires"],
            "owner_expires_iso": iso(r["ttl_expires"])}


def compose_foreign_claim(owner: dict[str, Any]) -> str:
    """FOREIGN_CLAIM_TMPL with comments stripped and the whole message capped (§7.4).

    `owner` comes from `_conflict`, which already applied `_arm` — the arm is enforced once,
    at the single place an owner dict is minted, so every surface inherits it identically.
    """
    spec = strip_html_comments(owner["owner_spec_md"]).strip()
    minutes = max(0, int((owner["owner_expires_ts"] - time.time()) // 60))
    msg = FOREIGN_CLAIM_TMPL.format(**dict(owner, minutes=minutes, owner_spec_md=spec))
    if len(msg) > MAX_DENY_CHARS:
        tail = f'\n[spec truncated — call get_plan("{owner["owner_plan_id"]}") for the full text]'
        msg = msg[: MAX_DENY_CHARS - len(tail)] + tail
    return msg


def validate_spec(spec_md: str) -> list[str]:
    """§5.10: five headings, <=60 lines, <=8000 chars, no surviving placeholder stem."""
    errs = [f"missing heading: {h}" for h in _HEADINGS if h not in spec_md]
    lines = len(spec_md.splitlines())
    if lines > 60:
        errs.append(f"spec too long: {lines} lines (max 60)")
    if len(spec_md) > 8000:
        errs.append(f"spec too long: {len(spec_md)} chars (max 8000)")
    errs += [f"unfilled template placeholder: {p}" for p in _PLACEHOLDERS if p in spec_md]
    return errs


def sweep(conn: sqlite3.Connection, repo: str, now: float) -> list[str]:
    """Lazy sweep (§2): plans past `ttl_expires + 2xTTL grace` go 'expired', claims tombstoned."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM plans WHERE repo=? AND status='active' AND ttl_expires < ?",
        (repo, now - SWEEP_GRACE_S))]
    for pid in ids:
        conn.execute("UPDATE claims SET released=? WHERE plan_id=? AND released IS NULL",
                     (iso(now), pid))
        conn.execute("UPDATE plans SET status='expired', updated=? WHERE id=?", (iso(now), pid))
        log_event(conn, "loom", "expired", pid, repo)
    return ids


def resolve_query(conn: sqlite3.Connection, repo: str, q: str) -> list[sqlite3.Row]:
    """§5.2 resolution order, first hit wins; returns ALL candidates so callers never guess."""
    path, qual = split_ref(q)
    steps = [                                            # exact ref -> path-suffix ref ->
        ("path=? AND qualname=?", (norm_path(path), qual)) if qual else None,
        # `auth.py::login` must find `src/api/routes/auth.py::login`: agents are TAUGHT the
        # path::qualname form, so the path half gets the same '/'-boundary suffix treatment
        # the qualname half always had (found in the conduit tryout, iteration 2).
        ("path LIKE '%/' || ? AND qualname=?", (norm_path(path), qual)) if qual else None,
        ("(path=? OR path LIKE '%/' || ?) AND qualname=''",
         (norm_path(q), norm_path(q))),                  # file -> qualname suffix -> substring.
        ("(qualname=? OR qualname LIKE '%/' || ?)", (q, q)),
        ("qualname LIKE '%' || ? || '%' AND qualname<>''", (q.rsplit("/", 1)[-1],)),
    ]
    for step in steps:
        if step is None:
            continue
        rows = conn.execute("SELECT id, path, qualname, kind FROM nodes WHERE repo=? AND "
                            + step[0], (repo, *step[1])).fetchall()
        if rows:
            return rows
    return []


def suggestions(conn: sqlite3.Connection, repo: str, q: str) -> list[str]:
    """Up to 5 closest refs (§5.2 ranking, frozen): tail-substring first, then lexicographic."""
    pool = [node_ref(r["path"], r["qualname"])
            for r in conn.execute("SELECT path, qualname FROM nodes WHERE repo=?", (repo,))]
    tail = re.split(r"::|/|\.", q)[-1].lower() or q.lower()
    return sorted(pool, key=lambda r: (tail not in r.lower(), r))[:5]


def _resolve_all(conn: sqlite3.Connection, repo: str,
                 queries: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    unresolved: list[dict[str, Any]] = []
    for q in queries:
        if q.startswith("n-") and conn.execute(
                "SELECT 1 FROM nodes WHERE id=? AND repo=?", (q, repo)).fetchone():
            ids.append(q)
            continue
        rows = resolve_query(conn, repo, q)
        if len(rows) == 1:
            ids.append(rows[0]["id"])
        else:
            cands = [node_ref(r["path"], r["qualname"]) for r in rows]
            unresolved.append({"query": q, "suggestions": cands or suggestions(conn, repo, q)})
    return ids, unresolved


def resolve_gate_target(conn: sqlite3.Connection, repo: str, path: str,
                        qualname: str | None) -> tuple[str | None, str]:
    """(node_id | None, case-hint) via §4's longest-prefix rule; §6 steps 1-2."""
    if not conn.execute("SELECT 1 FROM nodes WHERE repo=? LIMIT 1", (repo,)).fetchone():
        return None, "unindexed"
    rows = conn.execute("SELECT id, qualname FROM nodes WHERE repo=? AND path=?",
                        (repo, norm_path(path))).fetchall()
    if not rows:
        return None, "new_path"
    by_q = {r["qualname"]: r["id"] for r in rows}
    for cand in prefix_candidates(qualname or ""):
        if cand in by_q:
            return by_q[cand], "resolved"
    return by_q.get("") or by_q[sorted(by_q)[0]], "resolved"


def expand_write_targets(conn: sqlite3.Connection, repo: str,
                         node_ids: set[str]) -> dict[str, set[str]]:
    """One hop over CALLS in BOTH directions; IMPORTS radius 0 (§5.3, §11.13)."""
    out: dict[str, set[str]] = {}
    for nid in node_ids:
        rows = conn.execute(
            "SELECT n.id AS id FROM edges e JOIN nodes n ON n.id=e.dst "
            "WHERE e.kind='CALLS' AND e.src=? AND n.repo=? "
            "UNION SELECT n.id FROM edges e JOIN nodes n ON n.id=e.src "
            "WHERE e.kind='CALLS' AND e.dst=? AND n.repo=?", (nid, repo, nid, repo))
        nb = {r["id"] for r in rows} - node_ids
        if nb:
            out[nid] = nb
    return out


def _chunks(items: list[str]) -> list[list[str]]:
    """Split an id list into IN(...)-sized batches. Both callers guard against empty."""
    return [items[i:i + _SQL_VARS] for i in range(0, len(items), _SQL_VARS)]


def contains_closure(conn: sqlite3.Connection, repo: str, node_ids: set[str],
                     *, up: bool = True, down: bool = True) -> set[str]:
    """§4 containment relatives of `node_ids`, transitively, over `edges.kind='CONTAINS'`.

    Direction is frozen in the DDL: src = container (File|Class), dst = contained. `up`
    walks to containers (a symbol's Class then its File), `down` to the contained set (a
    File's classes/functions, a Class's methods). Includes the input ids. Pure reads, so
    it is safe both inside the caller's BEGIN IMMEDIATE and on the unwrapped gate path.
    """
    seen = set(node_ids)
    frontier = sorted(seen)
    while frontier and len(seen) < CLOSURE_MAX_NODES:
        found: set[str] = set()
        for chunk in _chunks(frontier):
            marks = ",".join("?" * len(chunk))
            if up:
                found.update(r["id"] for r in conn.execute(
                    "SELECT e.src AS id FROM edges e JOIN nodes n ON n.id=e.src "
                    f"WHERE e.kind='CONTAINS' AND e.dst IN ({marks}) AND n.repo=?",
                    (*chunk, repo)))
            if down:
                found.update(r["id"] for r in conn.execute(
                    "SELECT e.dst AS id FROM edges e JOIN nodes n ON n.id=e.dst "
                    f"WHERE e.kind='CONTAINS' AND e.src IN ({marks}) AND n.repo=?",
                    (*chunk, repo)))
        frontier = sorted(found - seen)
        seen.update(frontier)
    return seen


def find_conflicts(conn: sqlite3.Connection, repo: str, write_set: set[str], read_set: set[str],
                   own_plan_ids: set[str], now: float) -> list[dict[str, Any]]:
    """Active foreign claims intersecting my sets. write-write blocks; mixed modes warn.

    Each wanted node is judged over its CONTAINS closure (§4): declaring a whole file
    intersects live claims on the symbols inside it, and declaring a symbol intersects a
    live claim on its file/class. The closure widens the QUESTION only — the claim rows
    written by declare/rescope are still exactly the resolved targets plus §5.3's CALLS hop.
    """
    w = contains_closure(conn, repo, write_set) if write_set else set()
    r = contains_closure(conn, repo, read_set) if read_set else set()
    wanted = {n: "write" for n in w}
    wanted.update({n: "read" for n in r if n not in w})
    if not wanted:
        return []
    rows: list[sqlite3.Row] = []
    for chunk in _chunks(sorted(wanted)):
        marks = ",".join("?" * len(chunk))
        rows += conn.execute(
            _OWNER_COLS + _ACTIVE_FROM + f"WHERE c.node_id IN ({marks}) AND " + _ACTIVE_WHERE
            + "AND p.repo=?", (*chunk, now, repo)).fetchall()
    out = []
    for r in rows:
        kind = f"{wanted[r['node_id']]}-{r['mode']}"
        if r["pid"] in own_plan_ids or kind == "read-read":
            continue  # self-conflicts and shared^shared never conflict
        out.append(_conflict(r, kind))
    return out


def _own_plan_ids(conn: sqlite3.Connection, repo: str, agent: str, now: float) -> set[str]:
    return {r["id"] for r in conn.execute(
        "SELECT id FROM plans WHERE repo=? AND agent=? AND status='active' AND ttl_expires > ?",
        (repo, agent, now))}


def _insert_claims(conn: sqlite3.Connection, plan_id: str, node_ids: set[str],
                   mode: str, now: float) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO claims (node_id, plan_id, mode, created) VALUES (?,?,?,?)",
        [(n, plan_id, mode, iso(now)) for n in sorted(node_ids)])


def _intake(conn: sqlite3.Connection, repo: str, agent: str, targets: list[str],
            assumes: list[str], now: float, errs: list[str],
            why: str) -> tuple[dict[str, Any] | None, set[str], set[str],
                               dict[str, set[str]], list[dict[str, Any]]]:
    """Resolve -> expand -> judge, shared by declare and rescope.

    Returns `(refusal | None, write_set, read_set, expanded_from, warnings)`; a non-None
    refusal is the caller's whole all-or-nothing response and nothing has been claimed.
    """
    wids, unresolved = _resolve_all(conn, repo, targets)
    rids, more = _resolve_all(conn, repo, assumes)
    unresolved += more
    if errs or unresolved:
        errs = errs + (["unresolved node reference(s)"] if unresolved else [])
        return ({"ok": False, "reason": "validation", "validation_errors": errs,
                 "unresolved": unresolved}, set(), set(), {}, [])
    expanded = expand_write_targets(conn, repo, set(wids))
    write_set = set(wids) | {n for s in expanded.values() for n in s}
    read_set = set(rids) - write_set
    warnings = find_conflicts(conn, repo, write_set, read_set,
                              _own_plan_ids(conn, repo, agent, now), now)
    if any(c["kind"] == "write-write" for c in warnings):
        log_event(conn, agent, "denied", f"{why} conflict", repo)
        return ({"ok": False, "reason": "conflict", "conflicts": warnings}, set(), set(), {}, [])
    return None, write_set, read_set, expanded, warnings


def _granted(plan_id: str, expires: float, write_set: set[str], read_set: set[str],
             expanded: dict[str, set[str]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "plan_id": plan_id, "expires_ts": expires, "expires_iso": iso(expires),
            "claimed_write": sorted(write_set), "claimed_read": sorted(read_set),
            "expanded_from": {k: sorted(v) for k, v in expanded.items()}, "warnings": warnings}


def declare_plan(conn: sqlite3.Connection, *, agent: str, repo: str, branch: str, title: str,
                 spec_md: str, write_targets: list[str], assumes: list[str], ttl_s: int,
                 now: float) -> dict[str, Any]:
    """§5.3 — atomic, all-or-nothing. Caller supplies the BEGIN IMMEDIATE transaction."""
    sweep(conn, repo, now)
    refusal, write_set, read_set, expanded, warnings = _intake(
        conn, repo, agent, write_targets, assumes, now, validate_spec(spec_md), "declare")
    if refusal:
        return refusal
    ttl = max(TTL_FLOOR_S, ttl_s or CLAIM_TTL_S)
    expires = now + ttl
    plan_id = mint_plan_id(conn, title, spec_md, agent, time.time_ns())
    conn.execute(
        "INSERT INTO plans (id,agent,repo,branch,title,spec_md,status,created,updated,ttl_expires)"
        " VALUES (?,?,?,?,?,?,'active',?,?,?)",
        (plan_id, agent, repo, branch, title, spec_md, iso(now), iso(now), expires))
    _insert_claims(conn, plan_id, write_set, "write", now)
    _insert_claims(conn, plan_id, read_set, "read", now)
    detail = plan_id + (f" ttl clamped to {TTL_FLOOR_S}s" if ttl_s and ttl_s < TTL_FLOOR_S else "")
    log_event(conn, agent, "declared", detail, repo)
    return _granted(plan_id, expires, write_set, read_set, expanded, warnings)


def rescope(conn: sqlite3.Connection, *, plan_id: str, add_targets: list[str],
            add_assumes: list[str], now: float) -> dict[str, Any]:
    """§5.5 — same atomicity, expansion and shapes as declare; existing claims untouched."""
    sql = "SELECT agent, repo, ttl_expires, status FROM plans WHERE id=?"
    p = conn.execute(sql, (plan_id,)).fetchone()
    if not p:
        return {"ok": False, "reason": "unknown_plan"}
    sweep(conn, p["repo"], now)
    p = conn.execute(sql, (plan_id,)).fetchone()          # the sweep may have expired it
    if p["status"] != "active" or p["ttl_expires"] <= now:
        return {"ok": False, "reason": "not_active"}
    refusal, write_set, read_set, expanded, warnings = _intake(
        conn, p["repo"], p["agent"], add_targets, add_assumes, now, [], "rescope")
    if refusal:
        return refusal
    _insert_claims(conn, plan_id, write_set, "write", now)
    _insert_claims(conn, plan_id, read_set, "read", now)
    expires = max(p["ttl_expires"], now + CLAIM_TTL_S)
    conn.execute("UPDATE plans SET ttl_expires=?, updated=? WHERE id=?",
                 (expires, iso(now), plan_id))
    log_event(conn, p["agent"], "rescoped", plan_id, p["repo"])
    return _granted(plan_id, expires, write_set, read_set, expanded, warnings)


def renew(conn: sqlite3.Connection, plan_id: str, now: float) -> dict[str, Any]:
    """§5.8 — never shortens; an expired or non-active plan cannot be renewed."""
    p = conn.execute("SELECT agent, repo, status, ttl_expires FROM plans WHERE id=?",
                     (plan_id,)).fetchone()
    if not p:
        return {"renewed": 0, "reason": "unknown_plan"}
    if p["status"] in ("done", "superseded"):
        return {"renewed": 0, "reason": "released"}
    if p["status"] != "active" or p["ttl_expires"] <= now:
        return {"renewed": 0, "reason": "expired"}
    expires = max(p["ttl_expires"], now + CLAIM_TTL_S)
    conn.execute("UPDATE plans SET ttl_expires=?, updated=? WHERE id=?",
                 (expires, iso(now), plan_id))
    log_event(conn, p["agent"], "renewed", plan_id, p["repo"])
    return {"renewed": 1, "expires_ts": expires, "expires_iso": iso(expires)}


def release(conn: sqlite3.Connection, plan_id: str, agent: str, status: str,
            now: float) -> dict[str, Any]:
    """§5.9 — owner-only, tombstones claims, no force flag exists."""
    p = conn.execute("SELECT agent, repo, status FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not p:
        return {"ok": False, "reason": "unknown_plan"}
    if p["agent"] != agent:
        return {"ok": False, "reason": "not_owner"}
    if p["status"] != "active":
        return {"ok": False, "reason": "not_active"}
    status = status if status in ("done", "superseded") else "done"
    cur = conn.execute("UPDATE claims SET released=? WHERE plan_id=? AND released IS NULL",
                       (iso(now), plan_id))
    n = cur.rowcount
    conn.execute("UPDATE plans SET status=?, updated=? WHERE id=?", (status, iso(now), plan_id))
    log_event(conn, agent, "released", plan_id, p["repo"])
    return {"ok": True, "released_claims": n, "plan_status": status}


def counts(conn: sqlite3.Connection, repo: str, now: float) -> tuple[int, int]:
    """(indexed nodes, active plans) for `health` (§5.1)."""
    nodes = conn.execute("SELECT COUNT(*) c FROM nodes WHERE repo=?", (repo,)).fetchone()["c"]
    plans = conn.execute("SELECT COUNT(*) c FROM plans WHERE repo=? AND status='active' "
                         "AND ttl_expires > ?", (repo, now)).fetchone()["c"]
    return nodes, plans


def node_exists(conn: sqlite3.Connection, repo: str, node_id: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM nodes WHERE id=? AND repo=?",
                             (node_id, repo)).fetchone())


def get_plan(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
    """§5.6 plan payload; claim lists are REFS, non-released only."""
    p = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not p:
        return None
    plan: dict[str, Any] = {k: p[k] for k in _PLAN_COLS}
    plan.update({"expires_ts": p["ttl_expires"], "expires_iso": iso(p["ttl_expires"]),
                 "write_claims": [], "read_claims": []})
    for r in conn.execute(
            "SELECT c.mode, n.path, n.qualname FROM claims c JOIN nodes n ON n.id=c.node_id "
            "WHERE c.plan_id=? AND c.released IS NULL ORDER BY n.path, n.qualname", (plan_id,)):
        plan[r["mode"] + "_claims"].append(node_ref(r["path"], r["qualname"]))
    return plan


def active_claims(conn: sqlite3.Connection, repo: str, now: float) -> list[dict[str, Any]]:
    """§5.7 rows — the active-claim predicate verbatim, orphans excluded by the LEFT JOIN."""
    rows = conn.execute(_OWNER_COLS + _ACTIVE_FROM + "WHERE " + _ACTIVE_WHERE
                        + "AND p.repo=? ORDER BY n.path, n.qualname", (now, repo)).fetchall()
    return [{"node_id": r["node_id"], "ref": node_ref(r["path"], r["qualname"]), "mode": r["mode"],
             "plan_id": r["pid"], "agent": r["agent"], "title": r["title"],
             "expires_ts": r["ttl_expires"], "expires_iso": iso(r["ttl_expires"])} for r in rows]


def _decide(conn: sqlite3.Connection, repo: str, agent: str, decision: str, case: str,
            message: str, node_id: str | None, plan_id: str | None,
            owner: dict[str, Any] | None = None) -> dict[str, Any]:
    log_event(conn, agent, "allowed" if decision == "allow" else "denied",
              f"{case} {node_id or ''}".strip(), repo)
    return {"decision": decision, "case": case, "message": message, "node_id": node_id,
            "plan_id": plan_id, "owner": owner}


def check_node(conn: sqlite3.Connection, *, repo: str, agent: str, node_id: str,
               now: float) -> dict[str, Any]:
    """§6 steps 3-6 on an already-resolved node. Plain reads; never wrap in a transaction.

    Judged over the node AND its CONTAINS ancestors (§4): a write claim on the containing
    File/Class covers every symbol inside it — so its owner is allowed on their own file,
    and a foreign container claim blocks the symbol edit instead of silently clearing it.
    """
    scope = sorted(contains_closure(conn, repo, {node_id}, down=False))
    marks = ",".join("?" * len(scope))
    mine = conn.execute(
        "SELECT p.id AS pid " + _ACTIVE_FROM + f"WHERE c.node_id IN ({marks}) AND "
        "c.mode='write' AND " + _ACTIVE_WHERE + "AND p.agent=? AND p.repo=? "
        "ORDER BY p.updated DESC LIMIT 1", (*scope, now, agent, repo)).fetchone()
    if mine:
        # Implicit renew of the matched plan; the judge lives in the WHERE, so no tx.
        conn.execute("UPDATE plans SET ttl_expires=MAX(ttl_expires, ?), updated=? "
                     "WHERE id=? AND status='active' AND ttl_expires > ?",
                     (now + CLAIM_TTL_S, iso(now), mine["pid"], now))
        return _decide(conn, repo, agent, "allow", "in_plan", "", node_id, mine["pid"])
    foreign = conn.execute(
        _OWNER_COLS + _ACTIVE_FROM + f"WHERE c.node_id IN ({marks}) AND c.mode='write' AND "
        + _ACTIVE_WHERE + "AND p.agent<>? ORDER BY p.ttl_expires DESC LIMIT 1",
        (*scope, now, agent)).fetchone()
    if foreign:
        owner = _conflict(foreign, "write-write")
        return _decide(conn, repo, agent, "deny", "foreign_claim",
                       compose_foreign_claim(owner), node_id, owner["owner_plan_id"], owner)
    ref = node_ref(*(conn.execute("SELECT path, qualname FROM nodes WHERE id=?",
                                  (node_id,)).fetchone() or ("", "")))
    held = conn.execute(
        "SELECT id, title FROM plans WHERE repo=? AND agent=? AND status='active' "
        "AND ttl_expires > ? ORDER BY updated DESC LIMIT 1", (repo, agent, now)).fetchone()
    if held:
        return _decide(conn, repo, agent, "deny", "out_of_scope",
                       OUT_OF_SCOPE_TMPL.format(ref=ref, plan_id=held["id"], title=held["title"]),
                       node_id, held["id"])
    return _decide(conn, repo, agent, "deny", "no_plan", NO_PLAN_TMPL.format(agent=agent),
                   node_id, None)


def gate_decision(conn: sqlite3.Connection, *, repo: str, agent: str, path: str,
                  qualname: str | None, now: float) -> dict[str, Any]:
    """§6 decision order, shared by `POST /gate` and the `check` tool. Not caller-wrapped."""
    if conn.execute("SELECT 1 FROM plans WHERE repo=? AND status='active' AND ttl_expires < ? "
                    "LIMIT 1", (repo, now - SWEEP_GRACE_S)).fetchone():
        with immediate(conn):
            sweep(conn, repo, now)
    node_id, hint = resolve_gate_target(conn, repo, path, qualname)
    if node_id is None:
        return _decide(conn, repo, agent, "allow", hint, "", None, None)
    return check_node(conn, repo=repo, agent=agent, node_id=node_id, now=now)
