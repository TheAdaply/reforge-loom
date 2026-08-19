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
# The floor's missing twin (FINDINGS I30). Without a ceiling `ttl_s` is unbounded, so
# `declare_plan(ttl_s=2**31)` mints a 68-year claim that no non-owner can release and no
# sweep can reach — a HARD LOCK, which the README says loom does not have ("advisory with
# TTL, never hard locks"; "a crashed agent never freezes the team"). Larger values than this
# also overflow `iso()` (`declare_plan` used to RAISE OSError/ValueError/OverflowError out
# of a tool that promises errors as data). 24h: longer than any plausible single session,
# short enough that the worst case is one day of stale advice, and a team that genuinely
# wants more re-declares. Clamped SILENTLY like the floor, and named in the event detail.
TTL_CEIL_S = 86_400
SWEEP_GRACE_S = 3600
MAX_DENY_CHARS = 9000
CLOSURE_MAX_NODES = 5000     # bound on one CONTAINS walk (§4 hierarchy); a pathological
                             # graph must never stall the gate's fast read path.
_SQL_VARS = 400              # IN(...) chunk size, well under SQLite's variable limit.

# U3 — the gate on §5.2's fuzzy last rung (see `_fuzzy_tail`). Module constants, not magic
# numbers: they are the two questions "is this query specific enough to claim on?" splits
# into, and a test moves them without rewriting a query.
FUZZY_MIN_TAIL = 4           # 'run', 'get', 'db' name too many things to name one.
FUZZY_MAX_HITS = 3           # ...and so does any tail this many distinct symbols share.

# §7.4 verbatim. UNSCOPED is hook-local (locator.py, M3) and never lives here.
FOREIGN_CLAIM_TMPL = """loom: BLOCKED — {ref} is claimed by "{owner_agent}" under plan {owner_plan_id} "{owner_title}", expires {owner_expires_iso} (in {minutes}m).
Its spec follows. Build against its declared interfaces, or rescope your plan around it, or wait for expiry.

{owner_spec_md}"""

OUT_OF_SCOPE_TMPL = 'loom: {ref} is outside your declared plan {plan_id} "{title}". Call rescope(plan_id="{plan_id}", add_targets=["{ref}"]), then retry this edit.'

NO_PLAN_TMPL = 'loom: no active plan for agent "{agent}". Before editing: write a spec from templates/spec.md, resolve every target with resolve_nodes, call declare_plan, then retry this edit.'

_PLAN_COLS = ("id", "agent", "repo", "branch", "title", "spec_md", "status", "created", "updated")
_HEADINGS = ("## Goal", "## Write targets", "## New/changed interfaces", "## Assumes", "## Out of scope")
# Every stem here must exist in `templates/spec.md`, or it validates nothing. Checked by
# tests/server/test_claims.py::test_every_placeholder_stem_is_in_the_shipped_template.
_PLACEHOLDERS = ("[short imperative title", "[your agent id", "[Two sentences",
                 "[Canonical node IDs", "[EXACT signatures", "[One line")

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
    """§5.2 resolution order, first hit wins; returns ALL candidates so callers never guess.

    The last rung — a bare substring match on the query's tail — is the only FUZZY one, and
    the only one gated (U3, below). Every step above it is an exact or a '/'-boundary match
    and is untouched.
    """
    path, qual = split_ref(q)
    steps = [                                            # exact ref -> path-suffix ref ->
        ("path=? AND qualname=?", (norm_path(path), qual)) if qual else None,
        # `auth.py::login` must find `src/api/routes/auth.py::login`: agents are TAUGHT the
        # path::qualname form, so the path half gets the same '/'-boundary suffix treatment
        # the qualname half always had (found in the conduit tryout, iteration 2).
        ("path LIKE '%/' || ? AND qualname=?", (norm_path(path), qual)) if qual else None,
        ("(path=? OR path LIKE '%/' || ?) AND qualname=''",
         (norm_path(q), norm_path(q))),                  # file -> qualname suffix.
        ("(qualname=? OR qualname LIKE '%/' || ?)", (q, q)),
    ]
    for step in steps:
        if step is None:
            continue
        rows = conn.execute("SELECT id, path, qualname, kind FROM nodes WHERE repo=? AND "
                            + step[0], (repo, *step[1])).fetchall()
        if rows:
            return rows
    return _fuzzy_tail(conn, repo, q.rsplit("/", 1)[-1])


def _fuzzy_tail(conn: sqlite3.Connection, repo: str, tail: str) -> list[sqlite3.Row]:
    """§5.2's last rung, behind U3's information gate: substring match or NOTHING.

    Posture adapted from graphiti (Apache-2.0) — no code copied; see CREDITS.md. Its
    dedup helpers refuse the MinHash path for short, low-information names and escalate
    on ambiguity instead of picking a winner; exact matching is never gated. loom keeps
    that posture and drops the
    entropy arithmetic: over qualnames, `len` and `COUNT(*)` measure the same thing Shannon
    entropy was standing in for, and a threshold an agent can predict beats one it cannot.

    loom's stakes are higher than graphiti's. A single accidental substring hit is not a
    ranked suggestion here — `_resolve_all` sees `len(rows) == 1` and promotes it straight
    to a CLAIM, so `run` could quietly claim `Server/run` while the agent meant
    `Pipeline/run`. Refusing returns `[]`, which drops the caller onto the honest path it
    already has: unresolved, with ranked `suggestions` the agent picks from by hand.
    """
    if len(tail) < FUZZY_MIN_TAIL:
        return []                                        # too little information to claim on
    rows = conn.execute("SELECT id, path, qualname, kind FROM nodes WHERE repo=? AND "
                        "qualname LIKE '%' || ? || '%' AND qualname<>''",
                        (repo, tail)).fetchall()
    # Each row is one distinct symbol (nodes are UNIQUE on repo/path/qualname), so this
    # counts symbols, not duplicates. A tail that names this many things names none of them.
    return [] if len(rows) > FUZZY_MAX_HITS else rows


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


def _scope_for_conflicts(conn: sqlite3.Connection, repo: str, ids: set[str]) -> set[str]:
    """The nodes a claim on `ids` is judged against: ancestors of `ids`, plus what `ids` contain.

    Deliberately the UNION of the two single-direction closures, never one mixed walk. A
    mixed walk pivots up through a File node and then back down into that file's other
    children, so declaring one function would put every sibling function into the conflict
    question and loom would coordinate at file granularity. A sibling is not my business.

    This is the same question `check_node` asks at gate time (which walks up only, because
    the gate already knows the exact node being edited), so declare and enforce agree.
    """
    if not ids:
        return set()
    return (contains_closure(conn, repo, ids, down=False)
            | contains_closure(conn, repo, ids, up=False))


def find_conflicts(conn: sqlite3.Connection, repo: str, write_set: set[str], read_set: set[str],
                   own_plan_ids: set[str], now: float, *,
                   expanded: set[str] = frozenset()) -> list[dict[str, Any]]:
    """Active foreign claims intersecting my sets. write-write blocks; mixed modes warn.

    Each wanted node is judged over its CONTAINS scope (§4): declaring a whole file
    intersects live claims on the symbols inside it, and declaring a symbol intersects a
    live claim on its file/class. The scope widens the QUESTION only — the claim rows
    written by declare/rescope are still exactly the resolved targets plus §5.3's CALLS hop.

    `expanded` names the members of `write_set` that arrived via §5.3's CALLS hop rather
    than the agent's own targets. Those scope UP ONLY: calling a class must contend with a
    claim on that class or its file, but must NOT down-explode into every method the agent
    never named — that residue re-created file-granular conflicts one hop out (council W2,
    second half; the first half was the mixed-walk pivot fixed in `_scope_for_conflicts`).

    Contention is origin-aware, symmetric with `check_node`'s authority rule (BC3-1): a
    foreign claim sitting on an ANCESTOR of what I want conflicts only when its
    origin='target' (the owner NAMED that container). A container claim minted by
    someone's CALLS hop grants them nothing below it, so it blocks nothing below it.
    """
    explicit = write_set - expanded
    exact = set(write_set)
    down = contains_closure(conn, repo, explicit, up=False) - exact
    up = (contains_closure(conn, repo, explicit, down=False)
          | contains_closure(conn, repo, set(expanded) & write_set, down=False)) - exact - down
    r_exact = set(read_set)
    r_down = contains_closure(conn, repo, r_exact, up=False) - r_exact
    r_up = contains_closure(conn, repo, r_exact, down=False) - r_exact - r_down
    # node -> (my mode, ancestor_only): ancestor_only rows count iff their origin='target'.
    wanted: dict[str, tuple[str, bool]] = {}
    for n in exact | down:
        wanted[n] = ("write", False)
    for n in up:
        wanted.setdefault(n, ("write", True))
    for n in r_exact | r_down:
        wanted.setdefault(n, ("read", False))
    for n in r_up:
        wanted.setdefault(n, ("read", True))
    if not wanted:
        return []
    rows: list[sqlite3.Row] = []
    for chunk in _chunks(sorted(wanted)):
        marks = ",".join("?" * len(chunk))
        rows += conn.execute(
            _OWNER_COLS.replace("SELECT ", "SELECT c.origin, ")
            + _ACTIVE_FROM + f"WHERE c.node_id IN ({marks}) AND " + _ACTIVE_WHERE
            + "AND p.repo=?", (*chunk, now, repo)).fetchall()
    out = []
    for r in rows:
        my_mode, ancestor_only = wanted[r["node_id"]]
        if ancestor_only and r["origin"] != "target":
            continue  # a CALLS-hop container claim owns nothing below itself
        kind = f"{my_mode}-{r['mode']}"
        if r["pid"] in own_plan_ids or kind == "read-read":
            continue  # self-conflicts and shared^shared never conflict
        out.append(_conflict(r, kind))
    return out


def _own_plan_ids(conn: sqlite3.Connection, repo: str, agent: str, now: float) -> set[str]:
    return {r["id"] for r in conn.execute(
        "SELECT id FROM plans WHERE repo=? AND agent=? AND status='active' AND ttl_expires > ?",
        (repo, agent, now))}


def _insert_claims(conn: sqlite3.Connection, plan_id: str, node_ids: set[str],
                   mode: str, now: float, origin: str = "target") -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO claims (node_id, plan_id, mode, origin, created)"
        " VALUES (?,?,?,?,?)",
        [(n, plan_id, mode, origin, iso(now)) for n in sorted(node_ids)])


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
                              _own_plan_ids(conn, repo, agent, now), now,
                              expanded=write_set - set(wids))
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
    ttl = min(TTL_CEIL_S, max(TTL_FLOOR_S, ttl_s or CLAIM_TTL_S))
    expires = now + ttl
    plan_id = mint_plan_id(conn, title, spec_md, agent, time.time_ns())
    conn.execute(
        "INSERT INTO plans (id,agent,repo,branch,title,spec_md,status,created,updated,ttl_expires)"
        " VALUES (?,?,?,?,?,?,'active',?,?,?)",
        (plan_id, agent, repo, branch, title, spec_md, iso(now), iso(now), expires))
    # BC3-1 authority model: a claim's ORIGIN bounds its authority. Named targets carry
    # full §4 container authority; nodes swept in by §5.3's CALLS hop authorize (and are
    # contended, see find_conflicts) on themselves only — calling a class is not owning it.
    # NOT `expanded.keys()`: expand_write_targets omits hop-less targets from the dict.
    swept = {n for s in expanded.values() for n in s}
    _insert_claims(conn, plan_id, write_set - swept, "write", now)
    _insert_claims(conn, plan_id, swept, "write", now, origin="expanded")
    _insert_claims(conn, plan_id, read_set, "read", now)
    detail = plan_id + (f" ttl clamped to {TTL_FLOOR_S}s" if ttl_s and ttl_s < TTL_FLOOR_S
                        else f" ttl clamped to {TTL_CEIL_S}s" if ttl_s > TTL_CEIL_S else "")
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
    swept = {n for s in expanded.values() for n in s}
    _insert_claims(conn, plan_id, write_set - swept, "write", now)
    _insert_claims(conn, plan_id, swept, "write", now, origin="expanded")
    _insert_claims(conn, plan_id, read_set, "read", now)
    # Naming a node the plan once held via a CALLS hop PROMOTES it to full authority —
    # the INSERT OR IGNORE above keeps the old 'expanded' row, so flip origin explicitly.
    conn.executemany(
        "UPDATE claims SET origin='target' WHERE plan_id=? AND node_id=? AND mode='write'",
        [(plan_id, n) for n in sorted(write_set - swept)])
    expires = max(p["ttl_expires"], now + CLAIM_TTL_S)
    conn.execute("UPDATE plans SET ttl_expires=?, updated=? WHERE id=?",
                 (expires, iso(now), plan_id))
    log_event(conn, p["agent"], "rescoped", plan_id, p["repo"])
    return _granted(plan_id, expires, write_set, read_set, expanded, warnings)


def renew(conn: sqlite3.Connection, plan_id: str, agent: str, now: float) -> dict[str, Any]:
    """§5.8 — owner-only; never shortens; an expired or non-active plan cannot be renewed.

    The owner check mirrors `release`'s and was simply missing (break3 chaos-F6). Every deny
    message hands the blocked agent an `owner_plan_id` (§7.4), so without it the agent a
    claim is blocking could extend that claim — indefinitely, one call at a time. A plan's
    lifetime must only ever be extended by the agent doing the work it describes.
    """
    p = conn.execute("SELECT agent, repo, status, ttl_expires FROM plans WHERE id=?",
                     (plan_id,)).fetchone()
    if not p:
        return {"renewed": 0, "reason": "unknown_plan"}
    if p["agent"] != agent:
        return {"renewed": 0, "reason": "not_owner"}
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

    Judged over the node AND its CONTAINS ancestors (§4), with BC3-1's authority rule: a
    claim on the node itself counts whatever its origin, but an ANCESTOR (File/Class) claim
    carries authority over the contained node only when origin='target' — the agent NAMED
    the container. A container swept in by §5.3's CALLS hop authorizes nothing below
    itself, exactly as find_conflicts contends it with nothing below itself; the two sides
    of the model agree again, which is what the strict-xfail fuzz case pinned.
    """
    scope = sorted(contains_closure(conn, repo, {node_id}, down=False))
    ancestors = [n for n in scope if n != node_id]
    a_marks = ",".join("?" * len(ancestors))
    node_pred = ("(c.node_id=?" +
                 (f" OR (c.node_id IN ({a_marks}) AND c.origin='target')" if ancestors else "")
                 + ") ")
    scope_params = (node_id, *ancestors)
    mine = conn.execute(
        "SELECT p.id AS pid " + _ACTIVE_FROM + "WHERE " + node_pred + "AND "
        "c.mode='write' AND " + _ACTIVE_WHERE + "AND p.agent=? AND p.repo=? "
        "ORDER BY p.updated DESC LIMIT 1", (*scope_params, now, agent, repo)).fetchone()
    if mine:
        # Implicit renew of the matched plan; the judge lives in the WHERE, so no tx.
        conn.execute("UPDATE plans SET ttl_expires=MAX(ttl_expires, ?), updated=? "
                     "WHERE id=? AND status='active' AND ttl_expires > ?",
                     (now + CLAIM_TTL_S, iso(now), mine["pid"], now))
        return _decide(conn, repo, agent, "allow", "in_plan", "", node_id, mine["pid"])
    foreign = conn.execute(
        _OWNER_COLS + _ACTIVE_FROM + "WHERE " + node_pred + "AND c.mode='write' AND "
        + _ACTIVE_WHERE + "AND p.agent<>? AND p.repo=? ORDER BY p.ttl_expires DESC LIMIT 1",
        (*scope_params, now, agent, repo)).fetchone()
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
