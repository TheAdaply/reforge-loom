# loom BUILD-SPEC — the frozen implementation contract

> **SUPERSEDED IN PART.** This document is the frozen contract the MVP was built against and is
> never edited. `MULTIREPO-SPEC.md` (deltas D1-D6) and `ITERATION-2-SPEC.md` (D7-D11) amend it, and
> the U1/U2/U3 recon fixes came after both. Where they conflict, the newest amendment wins; where
> an amendment conflicts with the code, the code wins. For the current agent-facing contract read
> `docs/protocol.md`, and for the document map read `docs/README.md`.

Status: **FROZEN** (harden pass, 2026-08-18). Supersedes nothing; implements
`PLAN-v1.md` + all GATE-1 fixes. Every extraction correction is folded in — a coder agent needs
**only this file plus its own milestone brief (§9)**; the extractions (since moved to
`docs/archive/extractions/`) are provenance, not required reading. PLAN-v1.md is user-authored verbatim and is NOT edited; every
correction to it lives in §11 DECISIONS-DELTA.

`$LOOM` below means the root of your checkout of this repository — expand it yourself. The
build-session shell notes that used to sit here have moved to the root `CLAUDE.md`.

---

## 1. TECH PINS (frozen)

| What | Pin | Why / source |
|---|---|---|
| Python | **3.12** (`.python-version` = `3.12`, `requires-python = ">=3.12"`) | specgate §2.5 |
| Package/build | **uv**, backend `uv_build>=0.11.7,<0.12.0`, **`src/loom/` layout** (mandatory with uv_build — PLAN §3's flat layout does not build; specgate C5.2) | specgate §2.5 |
| MCP SDK | **`mcp>=2.0.0`**. Surface: `from mcp.server import MCPServer`; `@mcp.tool()`; `mcp.run(transport="streamable-http", host=..., port=...)`; default path **`/mcp`**; plain HTTP routes via `@mcp.custom_route(path, methods=[...])` (async Starlette handler — **verified present in the installed 2.0.0 SDK**, `mcp/server/mcpserver/server.py:975`). There is NO FastMCP in 2.0.0. | specgate §2.1, C5.1; SDK verified this pass |
| tree-sitter | **`tree-sitter>=0.25.2`** + **`tree-sitter-python`**. API style (verified on 0.25.2 AND 0.26.0, falkordb §3.3): `Query(language, src)` constructor + `QueryCursor(q).captures(node)` / `.matches(node)`. **NEVER `Language.query()`** (removed in 0.26). Multi-capture queries MUST use `.matches()` (falkordb §2.9). M0 runs the 3-line probe (§9-M0) as belt-and-braces. | falkordb §2.9/§3.3 |
| Tests | `pytest>=9.1.1` (PEP 735 `[dependency-groups] dev`) | specgate §2.5 |
| Storage | **stdlib `sqlite3`. NO SQLAlchemy in MVP** — one store, one process. Postgres flip is v2 (a rewrite of `db.py` only; keep all SQL in `db.py`/`claims.py`, none inline elsewhere). Overrides PLAN §7 "SQLAlchemy from day one" — see §11.11. | task order |
| Hook deps | **stdlib only**: `urllib.request`, `ast`, `json`, `tomllib`, `fcntl`. The hook must never import `mcp` (starlette/otel import cost per PreToolUse process — specgate C5.6). | specgate §5.3/§5.6 |
| git (eval only) | `>= 2.38` for `git merge-tree --write-tree`; assert at harness start, else fall back to scratch-worktree merge + `--abort` | papers C5.7 |

Size budgets (hard, cut features not correctness): **server/ < 700 lines, indexer/ < 300,
hook/ < 180, cli/ < 220** (comments/blank excluded; check with a plain line count minus blanks).
cli raised from 150 (GATE-2 edit 6): `init`'s mandatory steps (§7.5 merge + idempotency + gate
verification + CLAUDE.md append + config.toml write) plus five more verbs are all load-bearing —
nothing cuttable without breaking the frozen §7.5 registration contract. See §11.24.

---

## 2. FROZEN ARTIFACT — SQLite DDL + connection pragmas

> DECISIONS pointer (not an edit — this section stays historical): `events` has since gained a
> `repo TEXT NOT NULL DEFAULT ''` column, applied to existing databases by a guarded
> `ALTER TABLE` in `init_db` (`db.MIGRATIONS`). See MULTIREPO-SPEC §3 / delta D1.

`src/loom/server/db.py` module constant `DDL`, executed by `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS nodes (
  id         TEXT PRIMARY KEY,               -- "n-" + 8 base36 chars, §3
  repo       TEXT NOT NULL,
  path       TEXT NOT NULL,                  -- repo-root-relative, POSIX separators
  qualname   TEXT NOT NULL DEFAULT '',       -- '' = file-level node; else 'Class/method' (§4)
  kind       TEXT NOT NULL,                  -- 'File' | 'Class' | 'Function'
  body_hash  TEXT NOT NULL DEFAULT '',       -- sha256 hex of node source (file content for File)
  sig_hash   TEXT NOT NULL DEFAULT '',       -- sha256 hex of def/class header line(s); v2 impact needs it, add NOW (papers 5.3)
  start_line INTEGER,                        -- 1-based, non-identifying (locator aid)
  end_line   INTEGER,
  updated    TEXT NOT NULL,                  -- ISO-8601 UTC
  UNIQUE (repo, path, qualname)
);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_qualname ON nodes(repo, qualname);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_path     ON nodes(repo, path);

CREATE TABLE IF NOT EXISTS edges (
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  kind TEXT NOT NULL,                        -- 'CALLS' | 'IMPORTS' | 'CONTAINS'; plain TEXT, NO
                                             -- CHECK constraint (v2 adds EXTENDS/USES without a
                                             -- migration — papers 5.1). CONTAINS is canonical,
                                             -- never DEFINES (falkordb C3).
                                             -- Direction (frozen, GATE-2 edit 7):
                                             --   CALLS    src = caller node,          dst = callee node
                                             --   IMPORTS  src = importing File node,  dst = imported File node
                                             --   CONTAINS src = container (File|Class), dst = contained (Class|Function)
  PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS plans (
  id          TEXT PRIMARY KEY,              -- "lm-" + >=6 base36 chars, §3
  agent       TEXT NOT NULL,
  repo        TEXT NOT NULL,
  branch      TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  spec_md     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',-- 'active' | 'done' | 'expired' | 'superseded'
  created     TEXT NOT NULL,                 -- ISO-8601 UTC
  updated     TEXT NOT NULL,
  ttl_expires REAL NOT NULL                  -- unix seconds UTC (float)
);
CREATE INDEX IF NOT EXISTS idx_plans_repo_status  ON plans(repo, status);
CREATE INDEX IF NOT EXISTS idx_plans_agent_status ON plans(agent, status);

CREATE TABLE IF NOT EXISTS claims (
  node_id  TEXT NOT NULL,
  plan_id  TEXT NOT NULL,
  mode     TEXT NOT NULL,                    -- 'write' | 'read'
  created  TEXT NOT NULL,
  released TEXT,                             -- tombstone, never DELETE (agent-mail §2.1)
  PRIMARY KEY (node_id, plan_id, mode)
);
CREATE INDEX IF NOT EXISTS idx_claims_node ON claims(node_id, released);
CREATE INDEX IF NOT EXISTS idx_claims_plan ON claims(plan_id, released);

CREATE TABLE IF NOT EXISTS events (
  ts     TEXT NOT NULL,                      -- ISO-8601 UTC
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,                      -- declared|denied|allowed|released|expired|rescoped|renewed|bypass|indexed
  detail TEXT NOT NULL DEFAULT ''
);
```

**Connection factory (`db.connect`) sets, on EVERY connection** (specgate §3.1 — none are optional):

```python
con.execute("PRAGMA journal_mode=WAL")      # persists in the file; harmless to repeat
con.execute("PRAGMA busy_timeout=5000")     # REQUIRED: losers of a write race must queue, not error
con.execute("PRAGMA foreign_keys=ON")
con.execute("PRAGMA synchronous=NORMAL")    # correct pairing with WAL
con.row_factory = sqlite3.Row
```

**Transaction law** (specgate §2.3 + agent-mail §2.5, two independent production witnesses):
`declare_plan`, `rescope`, `renew`, `release`, plan-ID minting, and the sweep each run their whole
read→judge→write cycle inside ONE `BEGIN IMMEDIATE` transaction (`db.immediate(conn)` context
manager; the write lock is taken **before the first read**). `check`/`/gate` reads may use a plain
deferred read. There is no `threading.Lock` anywhere in the server.

**Active-claim predicate (authoritative, used verbatim everywhere):**
`claims.released IS NULL AND plans.status='active' AND plans.ttl_expires > :now`, joined
**LEFT JOIN plans ON plans.id = claims.plan_id** so an orphaned claim (plan row gone) is judged
dead, never immortal (agent-mail orphan rule, upstream #161).

**Canonical TTL set (GATE-1 fix 6):** TTL **1800 s** at declare; renewal implicit on every
`check`/`/gate` hit by the owning agent AND explicit via `renew()`, both setting
`ttl_expires = max(current, now + 1800)` (never shortens); floor 60 s (warn at declare, clamp at
renew); **an expired or non-active plan cannot be renewed** — `{renewed: 0}`, re-declare; lazy
sweep at the top of `declare_plan`/`rescope`/`check`/`list_claims`/`/gate` flips
`status='expired'` + tombstones its claims for plans with `ttl_expires < now - 3600` (2×TTL
bookkeeping grace — safe because the read filter above already stops honoring the claim at expiry).
No background sweeper thread in MVP (agent-mail ADAPT 4).

---

## 3. FROZEN ARTIFACT — IDs (`src/loom/server/ids.py`)

```python
BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"

def encode_base36(data: bytes, length: int) -> str:
    """Beads' EncodeBase36, ported exactly (beads §2.1.1):
    int.from_bytes(data, 'big') -> repeated divmod(n, 36) collecting BASE36[r] -> reverse;
    left-pad with '0' to `length`; if longer, keep the LAST `length` chars (least-significant)."""

LENGTH_TO_BYTES = {3: 2, 4: 3, 5: 4, 6: 4, 7: 5, 8: 5}   # beads table, incl. the 6/8 reuse

def beads_hash_id(prefix: str, title: str, description: str, creator: str,
                  ts_ns: int, length: int, nonce: int) -> str:
    """content = f"{title}|{description}|{creator}|{ts_ns}|{nonce}", UTF-8, sha256;
    take digest[:LENGTH_TO_BYTES[length]]; encode_base36(_, length); return f"{prefix}-{short}"."""

def node_ref(path: str, qualname: str = "") -> str:
    """'path::qualname' when qualname else 'path'. The display/agent-input form."""

def split_ref(ref: str) -> tuple[str, str]:
    """Inverse of node_ref: rsplit('::', 1); ('path', '') when no '::'."""

def node_id(repo: str, path: str, qualname: str = "") -> str:
    """DETERMINISTIC, content-addressed, NO timestamp/nonce (beads ADAPT 1):
    'n-' + encode_base36(sha256((repo + "\\x00" + node_ref(path, qualname)).encode()).digest()[:5], 8)
    NUL separator = the repo salt boundary (beads C7: '|' is ambiguous under concatenation).
    Length 8 => 36^8 ≈ 2.8e12; birthday p≈0.25 at ~1.2M nodes. No collision loop for nodes: the
    UNIQUE(repo, path, qualname) constraint raises on a true hash collision, which we WANT to see."""

def mint_plan_id(conn, title: str, spec_md: str, agent: str, now_ns: int) -> str:
    """ENTROPIC (beads recipe kept whole): inside the caller's BEGIN IMMEDIATE tx,
    for length in (6, 7, 8): for nonce in range(10):
        cand = beads_hash_id("lm", title, spec_md, agent, now_ns, length, nonce)
        if not conn.execute("SELECT 1 FROM plans WHERE id=?", (cand,)).fetchone(): return cand
    raise RuntimeError (beads §2.1.3 shape; SELECT 1, not COUNT)."""
```

**Node IDs are minted SERVER-SIDE ONLY** (beads C3). The hook and agents send `(path, qualname)`
strings or refs; the server resolves. Client-side minting silently reintroduces the collision the
single-store design exists to kill.

**Nanosecond trap (beads §2.1.2, hit during extraction):** compute `now_ns` with
`time.time_ns()` or integer arithmetic — NEVER `int(ts.timestamp() * 1e9)` (float rounding mints
wrong IDs silently).

**Golden vector test (port verbatim into `tests/test_ids.py`, M0):**
inputs `prefix="bd"`, `title="Fix login"`, `description="Details"`, `creator="jira-import"`,
`ts_ns=1704164645006000000` (= 2024-01-02T03:04:05.006Z), `nonce=0` →
`{3: "bd-vju", 4: "bd-8d8e", 5: "bd-bi3tk", 6: "bd-8bi3tk", 7: "bd-r5sr6bm", 8: "bd-8r5sr6bm"}`.
The extraction executed this vector against the spec above and all six match (beads §2.1.2).

---

## 4. FROZEN ARTIFACT — canonical qualname convention (GATE-1 fix 2)

- Within-file separator is **`/`** — Serena's real `NAME_PATH_SEP` (`serena/symbol.py:26`). The
  plan's `Class.method` dotted form does not exist in Serena and is dead everywhere in loom.
- Path↔symbol joiner is **`::`** — loom's own convention (cannot appear in POSIX paths or
  Python identifiers). Full ref: `src/conduit/core/security.py::decode_jwt_token`,
  `services/parsing/document_parser.py::DocumentParser/_resolve_ref`.
- File-level node: qualname `''`; ref is just the path (`README.md`, `src/conduit/models/document.py`).
- Overload/duplicate suffix `[i]` (0-based, Serena syntax): when a second CLAIMABLE definition
  with an identical qualname appears in one file (e.g. two same-name `def f` as direct children of
  the same module or class body — legal Python, the second shadows the first), the second gets
  `qualname[1]`, third `[2]`, in source order, counted over KEPT definitions only (those passing
  the block-statement rule below). The locator's `collect_symbol_spans` applies the SAME counter,
  so a claim on `f[1]` and an edit inside the second `f` meet on one qualname. This machinery is
  defensive necessity, not polish: without it, twin top-level defs violate
  UNIQUE(repo, path, qualname) and crash the indexer. Expect zero of these in the demo.
- Paths are repo-root-relative, POSIX-normalized:
  `norm_path(p) = str(PurePosixPath(p.replace("\\", "/")))` (specgate §3.4, keep verbatim).
- **Claimable-symbol granularity rule:** claimable nodes are module-level functions, classes
  (incl. classes nested in classes), and methods (functions whose ancestor chain contains classes
  only). **A function nested inside another function is NOT a node — it rolls up to its enclosing
  function** (specgate `collect_qualnames` discipline). Consequently `Outer/method` exists;
  `Outer/method/inner` is never minted, and both the indexer and the locator apply the same rule —
  this kills the claim-outer/edit-inner false deny.
- **Block-statement discovery rule (GATE-2 edit 2; frozen — the ONE statement of it):** a
  def/class is claimable **iff every node between it and the module root is a `class_definition`
  (or its `decorated_definition` wrapper)** — never a block statement (`if`/`try`/`with`/`for`/
  `while`/`match`), never a function body. Definitions nested in block statements are NOT nodes:
  a method under `if TYPE_CHECKING:` inside a class body is never minted (consistent with the
  frozen §9.1 queries — falkordb §2.3 documents guarded methods as missed; accepted, now by rule).
  Both engines enforce the SAME ancestor predicate: `walk.py` DISCARDS any query match whose
  ancestor chain fails the check (`_QUERY_CLASS_METHODS` is UNANCHORED and also matches
  class_definitions nested under blocks or inside functions — the ancestor filter, not the query
  text, is the claimability rule); `locator.py`'s ast visitor descends only `Module` → `ClassDef`
  containers. An edit inside a discarded def falls to the narrowest enclosing claimable span
  (inside a class → the Class node; module level → file-level) — §6 resolution needs no special
  case, and §4's indexer/locator same-rule promise holds.
- Calls found inside a closure attribute to the nearest claimable ancestor; calls in a class body
  (decorators, field initialisers) attribute to the Class node; module-level calls to the File node
  (falkordb §3.2 own_calls bucketing — one CALLS edge per call site, never double-attributed).
- **Serena `name_path` resolution rule (server-side, frozen):** given `(path, name_path)` from a
  Serena tool, strip a leading `/`, then try qualname candidates longest-first —
  `A/b/c` → `A/b/c`, `A/b`, `A` (`naming.prefix_candidates`) — against
  `SELECT id FROM nodes WHERE repo=? AND path=? AND qualname=?`. First hit wins; no hit →
  file-level node for `path`; no node rows for `path` at all → gate case `new_path` (allow).

---

## 5. FROZEN ARTIFACT — MCP tool surface (`src/loom/server/tools.py`)

Registration rules (specgate §2.1, all load-bearing): plain **sync `def`**, decorated
`@mcp.tool()` (with parens); docstring = description; **every tool keeps an explicit
`-> dict[str, Any]` return annotation** or clients silently lose `structured_content`
(specgate C5.5); errors are **data** (`{"ok": false, ...}`), never raised. Identity is
caller-asserted `agent: str` in MVP (accepted specgate limitation; token identity is v2 — §11).
`repo` params default `""` = the served repo; a non-empty mismatch returns
`{"ok": false, "reason": "wrong_repo"}`. Timestamps: `*_ts` unix float + `*_iso` ISO-8601 UTC `Z`.

> DECISIONS pointer (not an edit — this section stays historical): the code returns
> `{"ok": false, "reason": "unknown_repo", "served": [...]}` per MULTIREPO-SPEC §2 (D3).
> `src/loom/server/tools.py`; also `docs/protocol.md`.

**Conflict object (one shape everywhere):**

```json
{"kind": "write-write" | "write-read" | "read-write",
 "node_id": "n-...", "ref": "path.py::Qual/name",
 "owner_agent": "aria", "owner_plan_id": "lm-4f2a", "owner_title": "harden authenticate",
 "owner_spec_md": "<full spec, inline — the fetch step is free, no second call>",
 "owner_expires_ts": 1755500407.0, "owner_expires_iso": "2026-08-18T14:20:07Z"}
```

Conflict rule (PLAN §4.2, exact): write-write on the same node **blocks**; my-write vs their-read
(`write-read`) and my-read vs their-write (`read-write`) **warn** — surfaced with the owner's spec
attached, plan still created. shared∧shared never conflicts. Self-conflicts (same agent, own plans)
are skipped.

### 5.1 `health() -> dict[str, Any]`
`{"ok": true, "repo": "<repo>", "nodes": <int>, "active_plans": <int>, "version": "0.1.0"}`

### 5.2 `resolve_nodes(queries: list[str], repo: str = "") -> dict[str, Any]`
Per query, resolution order (indexed SQL, narrow projection — beads C8; suffix matching
right-to-left in spirit of serena §2.1): exact ref (`path::qualname`) → exact path (file node) →
exact qualname → qualname suffix on `/` boundaries (`qualname = ? OR qualname LIKE '%/' || ?`) →
substring on the last component. Ambiguity returns ALL candidates, never guesses.

```json
{"ok": true, "resolved": [
  {"query": "decode_jwt_token",
   "matches": [{"node_id": "n-...", "ref": "src/conduit/core/security.py::decode_jwt_token",
                "path": "...", "qualname": "decode_jwt_token", "kind": "Function"}],
   "suggestions": []}]}
```
`matches: []` + `suggestions: [up to 5 closest]` when unresolvable. Suggestions ranking (GATE-2
edit 5; frozen, specgate `_closest` adapted — self-contained here): candidate pool = ALL refs for
the repo (`node_ref(path, qualname)` over every node row); then
`tail = re.split(r"::|/|\.", query)[-1].lower() or query.lower()` and
`sorted(pool, key=lambda r: (tail not in r.lower(), r))[:5]` — refs containing the tail as a
substring rank first, ties break lexicographically.

### 5.3 `declare_plan(agent: str, title: str, spec_md: str, write_targets: list[str], assumes: list[str] = [], branch: str = "", repo: str = "", ttl_s: int = 1800) -> dict[str, Any]`
Atomic, one `BEGIN IMMEDIATE`: lazy-sweep → validate `spec_md` (§5.10) → resolve every
target/assume (ids or refs; any unresolvable → validation error with suggestions) → expand write
targets **one hop over CALLS in both directions; IMPORTS radius 0** (falkordb C6, papers 5.2) →
intersect (expanded ∪ assumes) with active foreign claims → all-or-nothing.
**Expanded one-hop neighbors are claimed as WRITE** (GATE-2 edit 4; PLAN §4.2 "claims
everything"): they land in `claimed_write` (provenance recorded in `expanded_from`), are
owner-editable per §6 step 3 without a rescope, and BLOCK foreign write declares (write-write).
A neighbor vs a foreign read surfaces as a `write-read` warn like any other write claim. Nothing
lands in `claimed_read` except the explicit `assumes`.

Success:
```json
{"ok": true, "plan_id": "lm-9c1x", "expires_ts": 1755500407.0, "expires_iso": "...",
 "claimed_write": ["n-..."], "claimed_read": ["n-..."],
 "expanded_from": {"n-target": ["n-neighbor1"]},
 "warnings": [ /* read-case Conflict objects, spec_md inline */ ]}
```
Write-write conflict (NOTHING claimed):
```json
{"ok": false, "reason": "conflict", "conflicts": [ /* Conflict objects, spec_md inline */ ]}
```
Validation failure:
```json
{"ok": false, "reason": "validation", "validation_errors": ["missing heading: ## Assumes", ...],
 "unresolved": [{"query": "...", "suggestions": ["..."]}]}
```

### 5.4 `check(agent: str, node: str, repo: str = "") -> dict[str, Any]`
`node` = node_id or ref. The agent-facing fast query; same core as `/gate` (§6). Sub-10ms warm is
the **server-side handler** budget (§11.14). Side effect: implicit renew of the caller's active
plans (canonical TTL set).
`{"allow": true, "case": "in_plan"|"new_path"|"unindexed", "plan_id": "lm-..."|null}` |
`{"allow": false, "case": "foreign_claim"|"out_of_scope"|"no_plan", "message": "<composed deny, §7>", "owner": <Conflict|null>, "node_id": "n-..."|null}`

### 5.5 `rescope(plan_id: str, add_targets: list[str] = [], add_assumes: list[str] = []) -> dict[str, Any]`
Same atomicity, expansion, and response shapes as `declare_plan` (success adds
`"plan_id"` echo; conflict claims nothing new; existing claims are untouched). Renews TTL on
success. Unknown/inactive plan → `{"ok": false, "reason": "unknown_plan"|"not_active"}`.

### 5.6 `get_plan(plan_id: str) -> dict[str, Any]`
`{"ok": true, "plan": {"id","agent","repo","branch","title","spec_md","status","created","updated",
"expires_ts","expires_iso","write_claims": [refs...], "read_claims": [refs...]}}` |
`{"ok": false, "reason": "unknown_plan"}`

### 5.7 `list_claims(repo: str = "") -> dict[str, Any]`
Lazy-sweeps first. `{"ok": true, "claims": [{"node_id","ref","mode","plan_id","agent","title",
"expires_ts","expires_iso"}]}` — active claims only.

### 5.8 `renew(plan_id: str) -> dict[str, Any]`
`{"renewed": 1, "expires_ts": ..., "expires_iso": "..."}` |
`{"renewed": 0, "reason": "expired"|"released"|"unknown_plan"}` — rows-affected-zero is a typed
verdict, never silence (beads §2.2.4/C5); the protocol tells the agent to re-declare on it.

### 5.9 `release(plan_id: str, agent: str, status: str = "done") -> dict[str, Any]`
Owner-only (`agent` must equal `plans.agent`); `status ∈ {"done", "superseded"}`. Tombstones the
plan's claims (`released = now`), sets plan status, logs event.
`{"ok": true, "released_claims": <int>, "plan_status": "done"}` |
`{"ok": false, "reason": "not_owner"|"unknown_plan"|"not_active"}`. No force flag exists.

### 5.10 spec_md validation (inside declare_plan; ~10 lines)
Reject (reason `validation`) when: any of the five headings missing (`## Goal`,
`## Write targets`, `## New/changed interfaces`, `## Assumes`, `## Out of scope`); more than
**60 lines** or **8000 chars**; or any literal template-placeholder stem survives:
`[short imperative title`, `[your agent id`, `[Two sentences`, `[Canonical node IDs`,
`[EXACT signatures`, `[One line`, `[Assumption` (exact-substring checks — zero false positives on
`list[str]`-style signatures). Spec-vs-args set equality is v1.1, not MVP.

### 5.11 Server construction (`src/loom/server/app.py`)
```python
mcp = MCPServer("loom", title="loom — spec-driven coordination gate",
                instructions=INSTRUCTIONS, version="0.1.0")
```
`INSTRUCTIONS` = the CLAUDE.snippet protocol text (§8.2) — free pull-through for agents whose
CLAUDE.md drifted (specgate §2.1). Serve: `mcp.run(transport="streamable-http", host=args.host,
port=args.port)`; host default `0.0.0.0`, port default **8790**, MCP endpoint `/mcp`.
State (db path, repo, repo_root) is built in `serve()` and closed over — no env-var singletons
(specgate §3.6). One long-lived `sqlite3.Connection` per server process
(`check_same_thread=False`; specgate §3.2 — per-call connect/close blows the check budget), plus
short-lived connections in CLI paths.

Custom plain-HTTP routes on the SAME port (verified `custom_route` SDK surface):
- `GET /health` → `{"ok": true, "repo": "<repo>"}` (used by `loom init` ping).
- `POST /gate` → §6. The hook speaks ONLY this route — never MCP (handshake cost, §11.14).

---

## 6. FROZEN ARTIFACT — the `/gate` wire contract (hook ↔ server)

Request (JSON body; `path` repo-root-relative POSIX via `norm_path`; `qualname` null = file-level
or unknown — server resolves via §4's longest-prefix rule):

```json
{"agent": "agent-a", "repo": "conduit", "path": "src/conduit/core/security.py",
 "qualname": "decode_jwt_token", "tool_name": "Edit"}
```

Response, always HTTP 200 (the hook treats any non-200/timeout/parse failure as fail-open):

```json
{"decision": "allow" | "deny",
 "case": "in_plan" | "new_path" | "unindexed" | "foreign_claim" | "out_of_scope" | "no_plan",
 "message": "<fully composed deny text per §7; empty string on allow>",
 "node_id": "n-..." | null, "plan_id": "lm-..." | null}
```

Server-side decision order (in `claims.gate_decision`, shared with the `check` tool):
1. lazy sweep;
2. resolve `(path, qualname)` → node (§4 rule); **repo has zero node rows at all → allow,
   case `unindexed`** (server not yet indexed; advisory posture — never brick edits);
   **no node rows for `path` → allow, case `new_path`** (creating new files is always allowed —
   claims protect indexed symbols; §11.20); resolved to nothing but path is indexed →
   file-level node;
3. node in any of the caller's own active plans' **write** claims → allow `in_plan`
   (+ implicit renew of that plan);
4. node holds an active foreign **write** claim → deny `foreign_claim`;
5. caller has ≥1 active plan → deny `out_of_scope` (names the most recently updated plan);
6. otherwise → deny `no_plan`.
A foreign **read** claim never blocks an in-plan write (the read owner was warned at declare time;
push-warning on next check is v2). A node the caller merely *assumes* (read claim) is NOT editable
→ falls to case 5. Every decision writes one `events` row.

---

## 7. FROZEN ARTIFACT — hook contract (`src/loom/hook/`)

### 7.1 stdin fields consumed (everything else ignored; parse leniently — hooks-contract §4.5, serena §2.5)
`tool_name` (missing/non-string → PASS); `tool_input` (dict; non-dict → PASS; snake_case with
camelCase fallback per field: `tool_input`/`toolInput`); from `tool_input`: `file_path` (Edit /
Write / legacy MultiEdit), `notebook_path` (NotebookEdit), `relative_path` + `name_path` **or
`name_path_pattern`** (Serena — `safe_delete_symbol` uses the `_pattern` spelling, serena §2.3
trap), `old_string`, `replace_all`, `edits` (legacy), `dry_run` (replace_in_files);
`session_id` / `agent_id` → audit log only. `file_path` is always absolute and authoritative —
never `cwd`-joined; normalize `\\`→`/` before computing the repo-relative path (hooks-contract §2.1).

### 7.2 Locator (`locator.py`, stdlib `ast` ONLY — tree-sitter lives in the indexer, §11)
Returns one of `PASS` (not gateable), `GATE(path, qualname|None)`, `DENY_LOCAL(message)`:
- `Edit`: read the on-disk file; `idx = source.find(old_string)`; absent, or multiple occurrences
  with `replace_all` → file-level; else line range → narrowest enclosing claimable span.
  Spans come from `collect_symbol_spans(source)` — specgate's `collect_qualnames` visitor emitting
  `(qualname, lineno, end_lineno)` for module functions, classes, and methods, **not descending
  into functions** (§4 granularity), dotted join replaced by `/`. `SyntaxError` → file-level.
- `Write`, `NotebookEdit` (via `notebook_path`), legacy `MultiEdit`: file-level.
- Serena symbol tools (`replace_symbol_body`, `insert_after_symbol`, `insert_before_symbol`,
  `rename_symbol`, `safe_delete_symbol`): `qualname = (name_path or name_path_pattern).lstrip("/")`,
  `path = relative_path` — no parsing; server resolves by longest prefix (§4).
- Serena file tools (`create_text_file`, `replace_content`, `delete_lines`, `replace_lines`,
  `insert_at_line`): file-level via `relative_path`.
- `replace_in_files`: `dry_run` true → PASS; `relative_path` names one file → file-level; else
  DENY_LOCAL (no server round trip) with the `UNSCOPED` template — §7.4 verbatim, composed
  HOOK-side in `locator.py` (the one exception to server-side composition).
- Unknown tool, unrecognized keys, path outside `repo_root` → PASS. Never guess, never crash.

### 7.3 gate.py exit contract — **exactly two exit codes, 0 and 2** (hooks-contract §3.2; M3 test)

| Case | stdout | stderr | exit |
|---|---|---|---|
| PASS / allow (`in_plan`, `new_path`, `unindexed`) | nothing | nothing | 0 |
| deny (server `deny`, or DENY_LOCAL) | nothing | the `message` | **2** |
| fail-open (timeout, conn refused, non-200, bad JSON, missing config, ANY exception) | `{"systemMessage": "loom: coordination server unreachable — edit allowed, claims NOT checked"}` | `loom: WARNING — gate failed open ({reason}); coordination degraded` | 0 |

Never emit `permissionDecision: "allow"` (it would suppress the user's own permission prompts —
hooks-contract §4.1). Never exit 1 (silently open gate with an ugly notice — hooks-contract 5.8);
`main()` wraps everything in `try/except BaseException` → fail-open branch. Fail-open dual channel
is deliberate: stderr on exit 0 never reaches user or model (hooks-ref `:771`), so the visible loud
warning is the `systemMessage`; the stderr line satisfies the audit/debug log (§11.15). Budget:
gate.py's own HTTP timeout **1.5 s** (`urllib.request.urlopen(req, timeout=1.5)`), total wall ≈2 s;
the settings `"timeout": 5` is only a backstop whose output is discarded (hooks-contract §2.4).

Config: `~/.loom/config.toml` (`server_url`, `agent`, `repo`, `repo_root`), read with `tomllib`;
missing/broken → fail-open with `systemMessage: "loom: not initialized — run loom init"`.
Audit: append one JSON line per decision to `~/.loom/gate-audit.jsonl` under `fcntl.flock(LOCK_EX)`,
strings truncated to 256 chars: `{ts, session_id, agent_id, tool_name, path, qualname, decision,
case, plan_id}` (hooks-contract P4).

### 7.4 Deny message templates (frozen verbatim; FOREIGN_CLAIM / OUT_OF_SCOPE / NO_PLAN composed SERVER-side in `claims.py` and relayed by the hook; UNSCOPED is the ONE hook-local template — see its entry)

`FOREIGN_CLAIM` (`{minutes}` = whole minutes to expiry, floor 0):
```
loom: BLOCKED — {ref} is claimed by "{owner_agent}" under plan {owner_plan_id} "{owner_title}", expires {owner_expires_iso} (in {minutes}m).
Its spec follows. Build against its declared interfaces, or rescope your plan around it, or wait for expiry.

{owner_spec_md}
```
Assembly: strip HTML comments (`<!-- -->`) from `spec_md` first; cap the WHOLE message at
**9000 chars** by trimming the spec tail and appending
`\n[spec truncated — call get_plan("{owner_plan_id}") for the full text]` — the actionable header
is never sacrificed (hooks-contract §2.5, 10k hard cap at `:885`).

`OUT_OF_SCOPE`:
```
loom: {ref} is outside your declared plan {plan_id} "{title}". Call rescope(plan_id="{plan_id}", add_targets=["{ref}"]), then retry this edit.
```

`NO_PLAN`:
```
loom: no active plan for agent "{agent}". Before editing: write a spec from templates/spec.md, resolve every target with resolve_nodes, call declare_plan, then retry this edit.
```

`UNSCOPED` (**hook-local — the ONLY template composed in the hook**, GATE-2 edit 3: emitted by
`locator.py` as DENY_LOCAL for §7.2's unscoped `replace_in_files` case, no server round trip. The
server never emits it — §6 has no unscoped case — so the literal lives as `UNSCOPED_TMPL` in
`hook/locator.py` (M3), NOT in `claims.py`; the hook may not import `server.*` (§9.2), so M3
carries this string itself. M3's no-override forbidden-substring test covers it like the other
three):
```
loom: replace_in_files across an unscoped path set cannot be claim-checked; scope it to one file or use symbolic edits.
```

**No-override law (GATE-1 fix 5; beads wy-yuclk):** no deny surface ever names a force/override/
bypass/steal path. Frozen forbidden substrings, asserted (case-insensitive) in BOTH M2's template
unit test and M3's relay test: `force`, `bypass`, `override`, `unclaim`, `release(`, `--force`.
Messages are declarative reports, never `SYSTEM:`-style imperatives (prompt-injection defense trap,
hooks-contract §2.5 `:977`). `LOOM_BYPASS` exists only in human docs and `loom init` output; any
use is written to `events` as `action='bypass'`.

### 7.5 settings.json registration (frozen JSON `loom init` merges in)

`loom init` READ-MODIFY-WRITEs `<repo_root>/.claude/settings.json` — merges into any existing
`hooks.PreToolUse` array, never overwrites, idempotent keyed on the loom command string
(hooks-contract 5.5) — inserting these two matcher groups (`{LOOM_GATE}` = the absolute path from
`shutil.which("loom-gate")`, resolved at init time; error out if not found):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [
          {"type": "command", "command": "{LOOM_GATE}", "args": [],
           "timeout": 5, "statusMessage": "loom: checking claims"}
        ]
      },
      {
        "matcher": "mcp__.*__(replace_symbol_body|insert_after_symbol|insert_before_symbol|rename_symbol|safe_delete_symbol|create_text_file|replace_content|replace_in_files|delete_lines|replace_lines|insert_at_line)",
        "hooks": [
          {"type": "command", "command": "{LOOM_GATE}", "args": [],
           "timeout": 5, "statusMessage": "loom: checking claims"}
        ]
      }
    ]
  }
}
```

Notes (all frozen): first group stays on the exact-string path (`|` list); `MultiEdit` is retained
defensively though gone from the current tool surface — no MultiEdit acceptance case required
(hooks-contract 5.1). Second group is the **suffix regex** — never `mcp__serena__.*`, the server
key is user-minted (GATE-1 fix 4); gate.py additionally re-derives classification from the
tool-name suffix after the last `__`. After writing, init runs `{LOOM_GATE}` once with a synthetic
`no_plan` payload and asserts exit 2 — a mistyped path leaves the gate silently disabled
(hooks-ref `:809`). `loom init` also appends §8.2 to the repo CLAUDE.md (idempotent, marker
comment) and writes `~/.loom/config.toml` with `repo` echoed from `GET /health` — one spelling of
the repo salt, minted at `loom serve` (§11.19).

---

## 8. FROZEN ARTIFACTS — templates (ship in-package: `src/loom/templates/`)

### 8.1 `spec.md` (verbatim; spec-kit fill discipline, loom's five fields)

```markdown
<!--
  loom spec. ONE PAGE, HARD CAP 60 LINES. This file is injected verbatim into other agents'
  deny messages and conflict responses, so every line you add is a tax they pay on every clash.
  Fill EVERY [bracket] and delete none of the five *(mandatory)* headings; write `none` if empty.
  Node IDs are canonical: `relative/path.py::Class/method` (files: `relative/path.ext`).
  Run resolve_nodes on every ID BEFORE declare_plan. Spec discipline inspired by
  github/spec-kit (MIT, Copyright GitHub, Inc.).
-->

# Spec: [short imperative title, e.g. "Cache authenticate() results"]

**Agent**: [your agent id]  **Plan**: [plan_id, written back after declare_plan]  **Repo/branch**: [repo] / [branch]

## Goal *(mandatory)*

[Two sentences. Sentence 1: what changes and why, e.g. "Add a 60s TTL cache in front of
authenticate() so repeated logins skip the bcrypt round." Sentence 2: the observable outcome,
e.g. "Auth-heavy endpoints drop from ~120ms to <5ms on cache hit; behaviour is unchanged on miss."]

## Write targets *(mandatory)*

[Canonical node IDs you will EDIT. One per line. Must equal write_targets[] in declare_plan.]

- [src/auth/service.py::AuthService/authenticate]
- [src/auth/cache.py  — file-level ID for a new or non-code file]

## New/changed interfaces *(mandatory)*

[EXACT signatures other agents may build against. Include the full signature and return type;
mark each ADDED / CHANGED / UNCHANGED-BUT-LOAD-BEARING. Write `none` if you change no interface.
A blocked agent codes against THIS, never against your in-flight source.]

- CHANGED `AuthService.authenticate(self, email: str, password: str, *, use_cache: bool = True) -> AuthResult`
  (was `(self, email: str, password: str) -> AuthResult`; return type and raised exceptions unchanged)
- ADDED `AuthCache.get(key: str) -> AuthResult | None`
- ADDED `AuthCache.put(key: str, value: AuthResult, ttl_s: int = 60) -> None`

## Assumes *(mandatory)*

[Canonical node IDs you RELY ON but will NOT edit, each with the exact signature you rely on.
These become read claims — if someone changes them, you get warned. Write `none` if nothing.]

- [src/auth/models.py::AuthResult] — relies on `AuthResult(user_id: str, token: str, expires_at: datetime)` being a frozen dataclass
- [src/auth/hashing.py::verify_password] — relies on `verify_password(plain: str, hashed: str) -> bool`

## Out of scope *(mandatory)*

[One line. Name the adjacent ground you are NOT taking, so a peer can claim it safely, e.g.
"Session storage, token refresh, and the password-reset flow are untouched."]
```

### 8.2 `CLAUDE.snippet.md` (verbatim; also the MCPServer `instructions` string)

```markdown
<!-- loom protocol v1 — written by `loom init`; edits here are overwritten on re-init -->
## loom — shared-repo coordination protocol

Before any code change in this repo:
1. Write a spec from loom's `templates/spec.md` (one page, all five sections, no unfilled brackets).
2. Resolve every write target and every assume to canonical node IDs with the loom `resolve_nodes`
   tool. IDs look like `relative/path.py::Class/method`; whole files are `relative/path.ext`.
3. Call `declare_plan(...)`. If the response carries conflicts, read each embedded spec, replan to
   build against its DECLARED interfaces — never against in-flight code — adjust your targets, and
   declare again. Warnings mean someone reads what you write, or you read what they write: honor
   their spec.
4. Edit normally. If the loom gate blocks an edit, follow the message: it either hands you the
   owning plan's spec to build around, or tells you to rescope, or to declare a plan first.
5. If your work grows beyond the declared targets, call `rescope(plan_id, add_targets, add_assumes)`
   BEFORE touching the new ground.
6. When tests pass and the branch merges, call `release(plan_id, agent)`.

Claims expire on a TTL (30 min) and renew automatically while you edit. If `renew` or `check` says
your plan is gone, re-declare — do not edit around a deny.
```

---

## 9. MODULE MAP, DIRECTORY OWNERSHIP, MILESTONE BRIEFS

### 9.0 Layout (src-layout mandated by uv_build; changes PLAN §3 — §11.17)

```
$LOOM/
  pyproject.toml  .python-version  THIRD_PARTY_NOTICES.md
  third_party/LICENSES/falkordb-code-graph.txt
  src/loom/__init__.py                     # __version__ = "0.1.0"
  src/loom/server/{__init__,app,db,ids,claims,tools}.py
  src/loom/indexer/{__init__,walk,naming}.py
  src/loom/indexer/queries/{__init__,python}.py   # query STRINGS as .py constants (not .scm files:
                                                  # they are compiled via Query(lang, str), and
                                                  # in-package strings avoid resource loading)
  src/loom/hook/{__init__,gate,locator}.py
  src/loom/cli/{__init__,main}.py
  src/loom/eval/{__init__,harness,metrics}.py
  src/loom/templates/{spec.md,CLAUDE.snippet.md}
  tests/conftest.py  tests/test_ids.py  tests/test_naming.py  tests/test_m0_smoke.py
  tests/indexer/{test_walk,test_queries,test_incremental}.py
  tests/fixtures/pyrepo/...                # M1's adversarial fixture repo
  tests/server/{test_claims,test_tools,test_gate_endpoint,test_concurrency}.py
  tests/hook/{test_gate,test_locator}.py
  tests/fixtures/pretooluse/*.json
  tests/eval/test_metrics.py
```

**`__init__.py` law (frozen):** ALL `__init__.py` files are EMPTY, except `src/loom/__init__.py`
which contains only `__version__ = "0.1.0"`. No re-exports, no imports, ever — gate.py's stdlib-only
budget (§1) depends on `import loom.indexer.naming` and `import loom.hook.*` transitively executing
nothing (an `from .app import ...` in `server/__init__.py` would drag mcp/starlette into every
PreToolUse process and destroy the ~2 s budget).

`pyproject.toml` (frozen essentials — specgate §2.5 shape):
name `loom`, version `0.1.0`, `requires-python = ">=3.12"`,
`dependencies = ["mcp>=2.0.0", "tree-sitter>=0.25.2", "tree-sitter-python>=0.23"]`,
`[project.scripts] loom = "loom.cli.main:main"` and `loom-gate = "loom.hook.gate:main"`,
`[dependency-groups] dev = ["pytest>=9.1.1"]`,
`[build-system] requires = ["uv_build>=0.11.7,<0.12.0"], build-backend = "uv_build"`.

### 9.1 Per-file function signatures (frozen interfaces; cross-module imports ONLY through these)

**`server/db.py`** (M0, complete):
`DDL: str` · `def connect(db_path: str) -> sqlite3.Connection` (pragmas §2, Row factory,
`check_same_thread=False`) · `def init_db(db_path: str) -> None` ·
`@contextmanager def immediate(conn) -> Iterator[sqlite3.Connection]` (BEGIN IMMEDIATE /
commit / rollback-on-raise) · `def log_event(conn, actor: str, action: str, detail: str = "") -> None` ·
`def now_s() -> float` · `def iso(ts: float) -> str`

**`server/ids.py`** (M0, complete): §3 verbatim.

**`indexer/naming.py`** (M0, complete):
`NAME_SEP = "/"` · `def qualname(components: Sequence[str]) -> str` ·
`def norm_path(p: str) -> str` · `def prefix_candidates(name_path: str) -> list[str]`
(longest-first, leading-`/` stripped) · `def node_ref(path, qualname="") -> str` /
`def split_ref(ref) -> tuple[str, str]` re-exported from ids (single definition in ids.py).

**`indexer/queries/python.py`** (M1): FalkorDB-derived query strings VERBATIM (MIT — carry the
NOTICE header: `# Portions derived from FalkorDB/code-graph (api/analyzers/python/).` +
`# Copyright (c) 2024 FalkorDB. MIT License. See third_party/LICENSES/falkordb-code-graph.txt`).
The five strings, FROZEN HERE (GATE-2 edit 1 — copy this block verbatim into the module;
falkordb.md stays provenance-only), plus the call capture `"(call) @reference.call"` scoped to
the enclosing entity node, never the file root (falkordb §2.4):

```python
_QUERY_TOP_LEVEL_FUNC = """
(module (function_definition name: (identifier) @name) @def)
(module (decorated_definition
    definition: (function_definition name: (identifier) @name)) @def)
"""

_QUERY_TOP_LEVEL_CLASS = """
(module (class_definition name: (identifier) @name) @def)
(module (decorated_definition
    definition: (class_definition name: (identifier) @name)) @def)
"""

_QUERY_CLASS_METHODS = """
(class_definition
    name: (identifier) @class_name
    body: (block (function_definition name: (identifier) @method_name) @method_def))
(class_definition
    name: (identifier) @class_name
    body: (block (decorated_definition
        definition: (function_definition name: (identifier) @method_name) @method_def)))
"""

# Plain ``import x`` / ``import x.y`` / ``import x as y`` / ``import x.y as z``.
_QUERY_IMPORT = """
(import_statement) @stmt
"""

# ``from x import y`` / ``from x import y as z`` / ``from . import y`` / ``from .x import y``.
_QUERY_IMPORT_FROM = """
(import_from_statement) @stmt
"""
```

FalkorDB's `_QUERY_TOP_LEVEL_ASSIGN` is **OUT** (deliberate, stated once here): loom's node kind
vocabulary is File|Class|Function (§2) — no Variable nodes in MVP. Decorated forms capture the
`decorated_definition` as `@def` — unwrap to the inner definition for name/span (falkordb §2.8).
`_QUERY_CLASS_METHODS` is UNANCHORED (matches class_definitions at any depth); claimability is
enforced by §4's block-statement ancestor filter applied in `walk.py` to every **def/class**
match, not by the query text — import and call captures are NEVER ancestor-filtered (M1's
guarded-import IMPORTS edge depends on this). Compiled ONCE at import with
`Query(language, s)`; helpers `def captures(q, node) -> dict[str, list]` /
`def matches(q, node) -> list[tuple[int, dict]]` (multi-capture queries use `matches` ONLY —
falkordb §2.9). `class Resolver:` — the tree-sitter static call/import resolver (falkordb §2.7/2.8
scope): `def index_file(self, rel_path: str, tree) -> None` (pass 1: defs + per-file import table,
exact+suffix module index) · `def resolve_calls(self, rel_path: str, tree) -> list[tuple[str, str, str]]`
(pass 2: `(src_qualname, dst_path, dst_qualname)`); precision rules frozen: methods excluded from
bare-name fallback, ambiguity dropped (resolve only a unique candidate), wildcard imports ignored,
relative-import climbing with the `__init__.py` off-by-one, `Foo()` resolves to the class node.
CUT to fit <300 lines: no assignment-type inference (`x = Foo(); x.m()` unresolved — accepted).

**`indexer/walk.py`** (M1):
`EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "site-packages", "build", "dist", "__pycache__", "frontend", "alembic", "tests"}`
(tests/frontends muted from day one — conduit-verify ADAPT 6, C7 row) ·
`def discover_files(repo_root: str) -> list[str]` (rel POSIX paths, `*.py`, excludes) ·
`def index_repo(conn, repo: str, repo_root: str, changed_only: bool = False) -> dict[str, Any]`
(two passes: (1) per file — file node + entity walk (source-ordered, decorator-unwrapped,
granularity §4 incl. the block-statement ancestor filter and the `[i]` duplicate counter) →
nodes + CONTAINS (src=container, dst=contained — §2); (2) imports → File→File IMPORTS
(src=importer); then Resolver pass →
CALLS with own-calls bucketing §4; returns `{"files": n, "nodes": n, "edges": n, "changed": [...]}`)
· `def index_file(conn, repo, repo_root, rel_path, source: bytes) -> None` ·
`def delete_file_nodes(conn, repo, rel_path) -> None` (drop the file's nodes + all edges touching
them — falkordb §2.12). Incremental (`changed_only`): compare each file's sha256 against the stored
File-node `body_hash`; re-index changed/new, delete removed; **inbound CALLS from unchanged files
may go stale until they change — accepted MVP caveat** (falkordb C5, recorded in the M1 brief).
Claims on deleted nodes: leave tombstoned-orphan (sweep-safe by the LEFT-JOIN predicate), log event.
Entity extraction detail: name via `child_by_field_name('name')`; docstring guard
`node.type == 'string'`; `body_hash = sha256(node source)`; `sig_hash = sha256(header line(s) —
from node start to the body block's start)`.

**`server/claims.py`** (M2):
`CLAIM_TTL_S = 1800` · `TTL_FLOOR_S = 60` · `SWEEP_GRACE_S = 3600` ·
`FOREIGN_CLAIM_TMPL / OUT_OF_SCOPE_TMPL / NO_PLAN_TMPL: str` (the three SERVER-side §7.4
templates verbatim; `UNSCOPED` is hook-local and lives in `locator.py` — §7.4, GATE-2 edit 3) ·
`def sweep(conn, repo, now) -> list[str]` · `def resolve_query(conn, repo, q: str) -> list[sqlite3.Row]`
(§5.2 order) · `def resolve_gate_target(conn, repo, path, qualname) -> tuple[str | None, str]`
(node_id-or-None, case-hint; §4 longest-prefix) ·
`def expand_write_targets(conn, repo, node_ids: set[str]) -> dict[str, set[str]]` (one hop CALLS
both directions, IMPORTS 0) · `def find_conflicts(conn, repo, write_set, read_set, own_plan_ids, now) -> list[dict]` ·
`def declare_plan(conn, *, agent, repo, branch, title, spec_md, write_targets, assumes, ttl_s, now) -> dict` ·
`def rescope(conn, *, plan_id, add_targets, add_assumes, now) -> dict` ·
`def check_node(conn, *, repo, agent, node_id, now) -> dict` ·
`def gate_decision(conn, *, repo, agent, path, qualname, now) -> dict` (§6 order; composes messages) ·
`def renew(conn, plan_id, now) -> dict` · `def release(conn, plan_id, agent, status, now) -> dict` ·
`def validate_spec(spec_md: str) -> list[str]` (§5.10) ·
`def compose_foreign_claim(owner: dict) -> str` (comment-strip + 9000-char cap §7.4) ·
`def strip_html_comments(md: str) -> str`

**`server/tools.py`** (M2): `def register(mcp: MCPServer, state) -> None` defining the nine
`@mcp.tool()` functions of §5, each a thin adapter over `claims.py` inside `db.immediate` where
mutating. **`server/app.py`** (M0 skeleton: MCPServer + health tool + /health route; M2 completes:
/gate route + tools + state): `INSTRUCTIONS: str` · `def build_server(db_path, repo, repo_root) -> MCPServer` ·
`def serve(host: str, port: int, db_path: str, repo: str, repo_root: str) -> None` · `def main() -> None`.

**`hook/gate.py`** (M3): `def main() -> None` · `def load_config() -> dict | None` ·
`def decide(payload: dict, cfg: dict) -> tuple[int, str, str]` (pure: (exit_code, stdout, stderr);
IO only in main + locator file read + HTTP — hooks-contract P2) ·
`def call_gate(cfg: dict, body: dict, timeout_s: float = 1.5) -> dict` (urllib) ·
`def audit(rec: dict) -> None` · `def fail_open(reason: str) -> tuple[int, str, str]`.
**`hook/locator.py`** (M3): `UNSCOPED_TMPL: str` (§7.4 verbatim — the one hook-local template) ·
`@dataclass class Located: action: str; path: str = ""; qualname: str | None = None; message: str = ""`
(`action ∈ {"pass","gate","deny_local"}`) · `def locate(tool_name: str, tool_input: dict, repo_root: str) -> Located` ·
`def collect_symbol_spans(source: str) -> list[tuple[str, int, int]]` ·
`def enclosing_qualname(source: str, start_line: int, end_line: int) -> str | None`.

**`cli/main.py`** (M0 stub `serve` only; M3 completes): `def main() -> None` — verbs:
`loom serve --repo-root PATH [--repo NAME] [--host 0.0.0.0] [--port 8790] [--db PATH]`
(db default `<repo_root>/.loom.sqlite3`; indexes on boot if nodes empty) ·
`loom init --server URL [--agent NAME]` (ping /health → repo; write `~/.loom/config.toml`;
merge settings.json §7.5; append CLAUDE snippet; verify gate; print the LOOM_BYPASS note here —
the ONLY agent-invisible place it is documented) ·
`loom index --repo-root PATH [--db PATH] [--changed]` (direct DB; WAL+busy_timeout make this safe
while serving) · `loom ls [--db PATH] [--json]` · `loom show <plan-or-node-id> [--db PATH]` ·
`loom release <plan_id> --agent NAME [--db PATH]`. ls/show/release are LOCAL-DB admin verbs
(agents use MCP tools; remote CLI is v2 — §11). Conventions frozen from beads §2.3: empty-string
scope value on a narrowing flag = hard error, never wildcard; agent mode auto-detect
(`CLAUDE_CODE` env or `LOOM_AGENT_MODE=1`) → one-line-per-row, no color; truncation notices to
stderr only when stderr is a tty; `--json` errors are JSON.

**`eval/metrics.py`** (M4; papers §2.5 verbatim contract):
`DUP_JACCARD = 0.8` · `@dataclass(frozen=True) class Hunk` (path, base_start, base_len, new_start,
new_len, added, deleted; `.lines`) · `def parse_hunks(repo: str, base: str, branch: str) -> list[Hunk]`
(`git diff --unified=0`, normalization: strip trailing ws, collapse space runs, drop blank/comment
lines, keep case+indent) · `def merge_conflict_files(repo: str, a: str, b: str) -> list[str]`
(`git merge-tree --write-tree --name-only`; exit 0 clean, 1 conflicts, **≥2 raise**) ·
`def compute_metrics(hunks_a, hunks_b, merge_conflicts: list[str]) -> dict` (pure; dup via mutual-
best Jaccard≥0.8 one-to-one, dup charges `min(a,b)`; overlap via base-range interval clusters,
charge `max(side sums)`; dup beats conflict, each hunk charged once; composite
`wasted_work_share = (dup+conflict)/total ∈ [0,1]`, `total==0 → None`; asserts both invariants) ·
`def assert_baseline_green(worktree: str) -> None`.
**`eval/harness.py`** (M4): `def wait_for_port(port, timeout=15.0) -> None` ·
`def run_server(db, repo_root, port) -> subprocess.Popen` (sys.executable -m, PIPE logs, try/finally
terminate — specgate §2.6, logs NOT devnull) · `def demo() -> None` (the scripted collision, M4
brief) · `def main() -> None` (`--demo`). Arm flag `LOOM_ARM ∈ {none, claims_only, full}` read by
the server: `claims_only` blanks `spec_md` in conflict objects and deny messages (papers 5.6 — the
A′ arm that isolates loom's thesis; ~10 lines, spec'd now, exercised post-MVP).

### 9.2 Directory ownership (parallel-phase edit rights; violations = merge pain)

| Owner | May create/edit ONLY |
|---|---|
| **M0** | everything shared, delivered COMPLETE before M1–M3 start: `pyproject.toml`, `.python-version`, `THIRD_PARTY_NOTICES.md`, all `__init__.py`, `src/loom/server/db.py`, `src/loom/server/ids.py`, `src/loom/indexer/naming.py`, `src/loom/server/app.py` (skeleton), `src/loom/cli/main.py` (serve stub), `tests/conftest.py`, `tests/test_ids.py`, `tests/test_naming.py`, `tests/test_m0_smoke.py` |
| **M1** | `src/loom/indexer/walk.py`, `src/loom/indexer/queries/**`, `tests/indexer/**`, `tests/fixtures/pyrepo/**`, `third_party/LICENSES/**` |
| **M2** | `src/loom/server/app.py` (extend), `src/loom/server/claims.py`, `src/loom/server/tools.py`, `tests/server/**` |
| **M3** | `src/loom/hook/**`, `src/loom/cli/main.py` (fill verbs), `src/loom/templates/**`, `tests/hook/**`, `tests/fixtures/pretooluse/**` |
| **M4** | `src/loom/eval/**`, `tests/eval/**` |

Cross-module imports allowed ONLY: M1 → `server.db`, `server.ids`, `indexer.naming`;
M2 → `server.db`, `server.ids`, `indexer.naming`; M3's hook → **`indexer.naming` only** (stdlib
otherwise — never `server.*`, never `mcp`); M3's cli → `server.app`, `server.db`, `indexer.walk`;
M4 → `mcp` client + subprocess + `server.db` (read). Anything else: stop and renegotiate the spec.

### 9.3 MILESTONE BRIEFS (each self-contained: this file + the brief is the coder's whole world)

Common to every brief: edit only the files in your whitelist (§9.2), and read this file fully
before writing code.

---

#### M0 — scaffold + frozen shared modules (blocks everyone; do first, do completely)

Deliver: layout §9.0 (all `__init__.py` EMPTY per the §9.0 law; `loom/__init__.py` =
`__version__` only); `pyproject.toml` + `.python-version` per §9.0; `db.py` COMPLETE (§2);
`ids.py` COMPLETE (§3); `naming.py` COMPLETE (§4 string helpers); `app.py` skeleton — MCPServer
construction §5.11, the `health` MCP tool, `GET /health` custom route, `serve()`/`main()`;
`cli/main.py` stub dispatching `serve`; `conftest.py` (tmp-db fixture); the three M0 test files;
`THIRD_PARTY_NOTICES.md` seeded with the beads notice:
`Portions of loom's ID-minting and claim-lease logic are derived from beads`
`(https://github.com/steveyegge/beads), MIT License, Copyright (c) 2025 Beads Contributors.`
plus one-line credits: serena (MIT, (c) 2025 Oraios AI), spec-kit (MIT, (c) GitHub, Inc.),
FalkorDB code-graph (MIT, (c) 2024 FalkorDB).

Whitelist: §9.2 M0 row. Acceptance (ALL must pass, run exactly these):
```
uv sync --directory $LOOM
uv run --directory $LOOM \
  python -c "from tree_sitter import Language, Query, QueryCursor; import tree_sitter_python; \
l = Language(tree_sitter_python.language()); q = Query(l, '(function_definition name: (identifier) @name)'); \
print('ts-probe-ok')"
uv run --directory $LOOM \
  pytest tests/test_ids.py tests/test_naming.py tests/test_m0_smoke.py -q
```
Probe fails → STOP, report the actual tree-sitter API surface; do not improvise. Tests must cover:
the §3 golden vector (all six lengths); node_id determinism + NUL-salt separation
(`node_id("a","b/c","")` ≠ `node_id("a/b","c","")`); `tests/test_m0_smoke.py` boots the server
**in-process** — `from mcp import Client; async with Client(mcp_instance) as c:` (a `Client` given
an `MCPServer` instance connects in-process, specgate §3.5) — and asserts `health` returns
`structured_content["ok"] is True`.

---

#### M1 — indexer (owns `indexer/` only)

Deliver: `queries/python.py` + `walk.py` per §9.1; `tests/fixtures/pyrepo/` — a small purpose-built
package (~6 files) containing EVERY adversarial case from conduit-verify §2.4: function-local
imports, a second shadowing `from datetime import timedelta` inside a function, `TYPE_CHECKING`-
guarded import (emit the IMPORTS edge — recommendation accepted), decorator-wrapped `async def`
(qualname anchors to the inner `function_definition`), call sites hiding in parameter defaults
(`Depends(get_db)` shape), `@staticmethod` inside a large class, a nested closure (must NOT mint a
node; its calls attribute to the enclosing function), a class-body call (attributes to the Class
node), a method guarded under `if TYPE_CHECKING:` inside a class body (must NOT be minted — §4
block-statement rule; assert its absence from the node set), two same-name top-level `def` twins
as direct module children (second gets `[1]` — §4 duplicate counter over kept defs), relative
imports (`from . import x`, `from ..up import deep`), `import a.b as ab` + `ab.f()` call.
`tests/indexer/test_walk.py` asserts the EXACT expected node set (ids, qualnames, kinds) and
CONTAINS edges; `test_queries.py` asserts ≥20 known CALLS/IMPORTS edges from a checklist literal in
the test (the M1 "20 call sites" acceptance, fixture-based); `test_incremental.py`: full index,
mutate ONE fixture file, `index_repo(..., changed_only=True)`, assert only that file's nodes/edges
changed (snapshot-diff via a Counter of rows — falkordb test idiom) and that unrelated node ids are
stable. Record in a comment: inbound-CALLS-stale caveat accepted (falkordb C5).

Whitelist: §9.2 M1 row. Acceptance:
```
uv run --directory $LOOM \
  pytest tests/indexer -q
```
Stretch (only if the local conduit clone exists; do not block on it):
`uv run --directory .../loom python -m loom.indexer.walk --repo-root <conduit-clone> --stats`
printing node/edge counts.

---

#### M2 — server tools + gate endpoint (owns `server/` only; app.py extend-in-place)

Deliver: `claims.py`, `tools.py` per §9.1/§5/§6/§7.4; extend `app.py` with `POST /gate` and tool
registration. Transaction law §2 is non-negotiable (`BEGIN IMMEDIATE` around every
read-judge-write; no Python locks). Lazy sweep per §2. Message templates §7.4 verbatim (the three
SERVER-side ones — `UNSCOPED` is hook-local, M3's problem), composed server-side, comment-strip +
9000-char cap. Events row per decision.

Tests (`tests/server/`): `test_claims.py` — all three conflict kinds surfaced; write-write claims
NOTHING; read cases warn-and-claim; expansion = one hop CALLS both directions, IMPORTS zero, and
expanded neighbors land in `claimed_write` (§5.3): they BLOCK a foreign write declare and are
owner-editable per §6 step 3;
sweep/renew/release semantics incl. `{renewed: 0}` after expiry and orphan-claim sweepability
(delete the plan row manually, assert the claim is judged dead); spec validation §5.10; the
**no-override assertion** — every composed deny contains none of the §7.4 forbidden substrings.
`test_gate_endpoint.py` — §6 decision order incl. `new_path` allow, longest-prefix Serena
resolution, file-level fallback. `test_concurrency.py` — subprocess server
(`sys.executable -m loom.server.app --port ... --db <tmp>`, `wait_for_port`, PIPE logs,
try/finally terminate — specgate §2.6), TWO HTTP `Client`s, `asyncio.gather` of `declare_plan` on
overlapping targets: EXACTLY one `ok: true`; the loser's response embeds the winner's full
`spec_md`; no `sqlite3.OperationalError` anywhere (busy_timeout proof). Timing: warm
`claims.gate_decision` in-process over 100 calls, median < 10 ms (server-side budget, §11.14).

Whitelist: §9.2 M2 row. Acceptance:
```
uv run --directory $LOOM \
  pytest tests/server -q
```

---

#### M3 — hook + cli + templates (owns `hook/`, `cli/`, `templates/` only)

Deliver: `gate.py` + `locator.py` per §7; `cli/main.py` verbs per §9.1 (init per §7.5 — settings
MERGE + idempotency + post-write gate verification + CLAUDE.md append + config.toml write);
`templates/spec.md` + `templates/CLAUDE.snippet.md` §8 VERBATIM. The hook imports stdlib +
`loom.indexer.naming` only. gate.py exits ONLY 0 or 2.

Tests (`tests/hook/`): fixture JSONs under `tests/fixtures/pretooluse/` —
`in_plan_allow`, `foreign_claim_deny`, `out_of_scope_deny`, `no_plan_deny`, `server_down_failopen`,
`unknown_tool_pass`, `subagent_fields_present` (has `agent_id`/`agent_type`),
`missing_permission_mode`, `serena_replace_symbol_body`, `serena_safe_delete_pattern`
(`name_path_pattern` key), `replace_in_files_unscoped` (local deny), `notebook_edit` — each piped
to `gate.py` **as a subprocess** with a canned-response stub server (a ~20-line stdlib
`http.server` thread in the test returning frozen §6 JSON per case — M3 NEVER imports M2 code;
the stub responses are copied from §6/§7.4 verbatim). Assert `(exit_code, stderr substring, stdout)`
per §7.3 for all four gate cases + fail-open (`systemMessage` on stdout, warning line on stderr,
exit 0; stub sleeps > 1.5 s for the timeout case) + the no-override forbidden-substring assertion
on every deny + "exits only 0 or 2" (feed garbage stdin, assert 0). `test_locator.py`: Edit
old_string→enclosing method; ambiguous replace_all→file-level; closure edit→enclosing function
qualname; SyntaxError→file-level; Serena param mapping incl. `name_path_pattern`.
CLI: `init` merge test on a settings.json that already has a user hook (merge, idempotent re-run,
no duplicate); `ls/show/release` against a tmp db.

Whitelist: §9.2 M3 row. Acceptance:
```
uv run --directory $LOOM \
  pytest tests/hook -q
```

---

#### M4 — eval skeleton + ONE scripted collision demo (owns `eval/` only; MVP-cut scope)

Deliver: `metrics.py` + `harness.py` per §9.1. `tests/eval/test_metrics.py` — pure-function cases
from papers §2.5: identical hunks → rate 1.0; disjoint files → 0.0; overlap-but-different → conflict
only; one hunk vs two opposing → one-to-one matching holds; `total==0 → None`; invariants assert.
`demo()`: boots the server on a throwaway db + `tests/fixtures/pyrepo` copy, indexes, then scripts
the headline sequence with two in-process actors: A `declare_plan` on
`svc.py::AuthService/authenticate`-analog (fixture symbol) with a filled spec → B `declare_plan`
overlapping → B receives A's spec embedded in the conflict → B re-declares against non-overlapping
targets + assumes (non-overlapping AFTER one-hop expansion, §5.3 — pick fixture symbols in
disjoint call components, since expanded neighbors are write-claimed and block) → B's simulated
edit on A's node runs `loom-gate` as a subprocess and is DENIED
with A's spec in stderr → B's edit on its own node is ALLOWED → A `release` → prints a transcript
with inline asserts (the demo IS the test — specgate §2.6) and one §2.5-shaped metrics JSON row
(synthetic hunks). Baseline machinery for the real conduit runs (deselect list, `--no-cov`,
`assert_baseline_green`) ships as code + config, not exercised in CI. Real three-arm conduit runs
are post-MVP.

Whitelist: §9.2 M4 row. Acceptance:
```
uv run --directory $LOOM \
  pytest tests/eval -q
uv run --directory $LOOM \
  python -m loom.eval.harness --demo
```

---

## 10. MVP CUTS (enforced; from the PLAN addendum — anything here appearing in a diff is a defect)

> DECISIONS pointer (not an edit — this section stays historical): two items on this list have
> since shipped. **multi-repo serve** landed per MULTIREPO-SPEC (D1-D6) and **token identity**
> landed as the optional shared token per ITERATION-2-SPEC §3 (D8-D9). Everything else on the list
> is still out.

OUT: rename tolerance / body-hash claim transfer (v1.5) · `server/impact.py` and everything
CodePlan-derived (v2, `LOOM_IMPACT` off; only the `sig_hash` column and free-TEXT `edges.kind`
land now as migration insurance) · waitlists · hot-node policies · eval arm C (glue stack) ·
webhooks (no post-merge automation at all in MVP; `loom index` manual — even the git post-merge
hook is deferred) · web visibility page · pre-commit `guard.py` (optional in PLAN, cut for 12 h) ·
background sweeper thread (lazy-only) · multi-repo serve · token identity · Windows anything ·
notebook symbol-level mapping (file-level only) · `jet_brains_*` tools in the default matcher ·
spec-vs-args set-equality validation. IN and mandatory: hook fail-open exactly per §7.3.

---

## 11. DECISIONS-DELTA — every correction to PLAN-v1 (PLAN text itself is untouched)

1. **mcp_agent_mail attribution/license.** `github.com/Dicklesworthstone/mcp_agent_mail`
   (Jeffrey Emanuel), "MIT + OpenAI/Anthropic rider". Patterns only, zero verbatim code; never
   vendored; **never an eval target or harness input** (the rider's "use" names benchmarking and
   evaluation harnesses explicitly). PLAN's steveyegge attribution was wrong.
2. **FastMCP is dead; MCPServer is the surface** (mcp 2.0.0): `from mcp.server import MCPServer`,
   `run(transport="streamable-http")`, path `/mcp`, `@mcp.custom_route` for plain HTTP. Verified
   against the installed SDK (specgate C5.1 + this pass).
3. **beads is `github.com/steveyegge/beads`**, not gastownhall (go.mod verified); MIT notice
   shipped in `THIRD_PARTY_NOTICES.md`. Its real status set has no `claimed`/`expired`; loom's four
   plan statuses stand, but loom's `expired` is terminal bookkeeping (spec kept for audit), not a
   return-to-pool (beads C2 / ADAPT 7).
4. **Eval-target verdict (conduit-verify Q4): YES — `Akasxh/conduit` IS the eval target.** It is
   NOT a RealWorld implementation (PLAN §2/§6 false premise). Pair 1 survives with real symbols
   (`auth.py::login`, shared node `core/security.py::decode_jwt_token`, cache site
   `core/middleware.py::TenantMiddleware/dispatch`). Pair 2 is re-specified: Document editing vs
   Document retention/moderation across `models/document.py::Document` +
   `schemas/documents.py::DocumentUploadResponse`/`DocumentDetailResponse`. Local clone path in
   config, NEVER a git submodule (no upstream LICENSE — backlog: add MIT to Akasxh/conduit).
   Baseline is 1039 passed / 4 failed; the harness carries the frozen deselect list
   (conduit-verify §2.2) + `--no-cov` + pre-flight `assert_baseline_green`. Language: Python;
   index `src/**/*.py` only. Fallback if RealWorld parity is ever demanded:
   `gothinkster/django-realworld-example-app`.
5. **Qualname convention** (GATE-1 fix 2): `path.py::Class/method` — `/` inside the symbol
   (Serena's `NAME_PATH_SEP`), `::` as loom's joiner. PLAN §2's Serena bullet
   (`relative/path.py::Class.method`) is wrong on both halves (serena C1); replacement manifest
   text lives in serena.md C1. Symbol identity is stored as the `(path, qualname)` pair.
6. **Deny transport** (fix 3): exit 2 + stderr primary; JSON deny demoted to noted alternative;
   M3 asserts exit codes + stderr.
7. **Serena matcher** (fix 4): suffix regex `mcp__.*__(tool|...)`; a hardcoded `mcp__serena__.*`
   silently no-ops on user-minted keys and plugin prefixes.
8. **No escape hatch in deny copy** (fix 5): forbidden-substring test in M2 + M3; `LOOM_BYPASS`
   documented only in `loom init` output/human docs, always audited.
9. **Canonical TTL set** (fix 6): 1800 s / implicit renew-on-check to `max(current, now+1800)` /
   floor 60 s / no renew after expiry / read-filter authoritative / lazy sweep, 2×TTL grace.
10. **grite license tripwire** (fix 7): `github.com/neul-labs/grite` LICENSE is UNREAD — no code
    copying, no citation of its numbers as loom's. **Backlog item, assigned to github-miner.**
    papers.md now carries a provenance caveat (fix 9): re-verify the CodePlan 16-row table before
    any v2 `LOOM_IMPACT` enablement.
11. **stdlib sqlite3, no SQLAlchemy** — overrides PLAN §7 "SQLAlchemy from day one" by task order:
    one store, one process; all SQL confined to `db.py`/`claims.py` so the Postgres flip (v2) is a
    localized rewrite, not a migration of call sites.
12. **`CONTAINS`, never `DEFINES`** (falkordb C3); `edges.kind` is free TEXT (papers 5.1);
    `nodes.sig_hash` added now (papers 5.3).
13. **IMPORTS expansion radius = 0 from day one** (falkordb C6, papers 5.2): File→File IMPORTS one
    hop claims whole packages; CALLS stays one hop from *declared intent* (explicitly NOT CodePlan
    impact analysis).
14. **The 10 ms target binds the server-side handler only** (specgate 5.3). The hook's end-to-end
    budget is the 1.5 s client timeout + fail-open. The hook speaks plain `POST /gate` on the MCP
    port via `custom_route` — never the MCP handshake, never `import mcp`.
15. **Fail-open channel reconciliation**: the task letter's "one loud stderr line + exit 0" is
    kept, AND the visible warning is `{"systemMessage": ...}` on stdout — stderr on exit 0 reaches
    only the debug log (hooks-ref `:771`), so stdout JSON is the only loud channel that exists.
    Settings `"timeout": 5` is a backstop whose output is discarded (`:817`); the warning must come
    from gate.py's own timeout path.
16. **MultiEdit is gone from the documented tool surface** (hooks-contract 5.1): kept in the
    matcher defensively; no required MultiEdit acceptance case; `NotebookEdit` uses
    `notebook_path`.
17. **Layout is `src/loom/**`** (uv_build requirement; PLAN §3's flat layout does not build —
    specgate C5.2); `templates/` ships inside the package; `eval/` is `src/loom/eval/`.
18. **spec-kit contributes fill discipline only** — the five spec fields are loom's own
    (spec-kit C5.1); 60-line/8000-char spec cap + declare-time validation (§5.10) because
    `spec_md` is a per-deny token tax capped at 10k hook-output chars.
19. **Repo identity**: the `repo` salt string is minted once at `loom serve` (default: repo-root
    basename) and echoed to every `loom init` via `GET /health` — one spelling, one place. Node IDs
    are minted server-side only (beads C3).
20. **New files are always editable** (`new_path` allow): claims protect indexed symbols; gating
    file creation would brick scaffolding. The declared-targets discipline still covers new files
    via file-level refs in the spec.
21. **"Wasted-work share" is loom's composite** built from grite's separate duplicate-rate /
    conflicting-edits / goodput (papers 5.4); grite's absolute numbers are simulated N=32 and are
    never quoted as loom expectations (papers 5.5); the claims-only A′ arm is spec'd as the
    `LOOM_ARM` flag (papers 5.6) even though full three-arm runs are post-MVP.
22. **`loom init` merges `.claude/settings.json`** (never overwrites), is idempotent, and verifies
    the gate post-write (hooks-contract 5.5/`:809`); the gate also fires inside subagents —
    audit records `agent_id`/`session_id` (hooks-contract 5.7).
23. **specgate confirmations**: check-then-act = one `BEGIN IMMEDIATE` transaction (double
    witness with agent-mail #129/#130); `_gate_lock` superseded; every MCP tool return-annotated;
    in-process `Client(mcp_instance)` for unit tests, subprocess + HTTP for concurrency/demo.
24. **cli line budget is 220, not 150** (GATE-2 edit 6) — overrides PLAN §3 AND the original task
    budget, sanctioned by the gate. `init` alone (settings.json read-modify-write with idempotent
    merge, CLAUDE.md marker-append, config.toml write, /health ping, synthetic-payload gate
    verification, bypass-note print) plus five more verbs plus the beads CLI conventions lands
    180–230 non-blank lines; every init step is load-bearing for §7.5's frozen registration
    contract, so nothing is cuttable. server/indexer/hook budgets unchanged. Do not "fix" this
    back to 150.

25. **Machine-absolute paths and the build-session ENV GOTCHAS blocks removed for publication.**
    Every `/Users/...` literal in this file is now `$LOOM`, and the six repeated ENV GOTCHAS
    blocks (a build-session shell preamble, whose one durable item was "no `timeout` binary")
    are deleted. Their durable content lives in the root `CLAUDE.md`. No contract text changed.

26. **Conflict scope is the UNION of the two single-direction CONTAINS closures, not one mixed
    walk** (`claims._scope_for_conflicts`). §4 said a claim is judged over its containment
    closure and the implementation read that as one transitive up-and-down walk, which pivots
    through the File node and pulls in every sibling. Declaring one function therefore contended
    with every other function in the file — file granularity, contradicting the product's whole
    claim — while the gate's `check_node` already asked the narrower question. Declare and
    enforce now agree. A file claim still covers everything inside it; a symbol claim still
    collides with a claim on its class or file. Pinned by
    `test_conflict_scope_is_ancestors_and_contained_never_siblings` and
    `test_two_agents_may_claim_two_unrelated_symbols_in_one_file`.

27. **`loom index --changed` gets `default=False`, and `--full` is added.** `main()` merges
    `{"default": None} | opts`, so the flag's default was `None`, which downstream means "choose
    automatically" — the flag was a no-op and there was no way to force a full rebuild.

28. **The dashboard's truncation note reads the numbers instead of stating them.** It said
    "graph truncated to first 600 nodes", a copy of `STATE_NODE_CAP` that a later cap change
    would silently falsify. It now says "showing N of M nodes" from `/state`'s own
    `counts`/`totals`. The fabric also gained a static legend, because its visual vocabulary
    was discoverable only by hovering — no help to anyone reading a screenshot.

29. **`loom init` writes `~/.loom/config.toml` only when there is none** (§7.5 amended). It is
    still READ as the last discovery fallback. Rewriting it on every init re-created the exact
    stale-global bug the per-repo `.claude/loom.toml` was added to fix: initializing a second
    repo repointed the first repo's fallback at the second repo's server, which answers `allow`
    for a salt it does not serve.

30. **`POST /gate` answers its own contract on a malformed body** (P2-3). An unparseable body, a
    JSON array or scalar, and a non-string `qualname` used to raise, so the route returned 500 —
    which the hook fails open on, silently, while the server looked healthy. The five frozen wire
    keys are unchanged.

31. **`THIRD_PARTY_NOTICES.md` restructured for publication; §12 below stays the historical
    record.** The three code-derived notices (beads, FalkorDB/code-graph, Serena) are kept and
    STRENGTHENED — each now carries the full upstream license text under `third_party/LICENSES/`
    and names the loom files that implement it, and `pyproject.toml`'s `license-files` makes them
    travel into built artifacts. The five PATTERN-ONLY entries (spec-kit, mcp_agent_mail, graft,
    graphiti, graphify) moved to `CREDITS.md` with their "no code from it is included" sentences
    intact: no code is included, so no license is relied on, so no notice obligation exists for
    them. Nothing was deleted.

32. **`TTL_FLOOR_S` gets its twin: `TTL_CEIL_S = 86_400`** (§5.3, §7.4 constants; FINDINGS I30).
    `declare_plan` clamped `ttl_s` UP only, so `ttl_s=2**31` minted a 68-year claim — no
    non-owner may release it and no sweep reaches it, i.e. a hard lock in a system whose README
    says it has none. Larger values overflowed `iso()` and RAISED `OSError` / `ValueError` /
    `OverflowError` out of a tool surface that promises errors as data. Now
    `min(TTL_CEIL_S, max(TTL_FLOOR_S, ttl_s or CLAIM_TTL_S))`, clamped silently like the floor
    and named in the `declared` event detail. Implicit renew, `renew` and `rescope` all extend
    to `now + CLAIM_TTL_S`, which is inside the band by construction.

33. **§5.8 `renew` takes an agent and is OWNER-ONLY: `renew(plan_id: str, agent: str)`.** The
    check is `release`'s, verbatim, and was simply absent. Every deny message hands the blocked
    agent an `owner_plan_id` (§7.4), so the agent a claim is blocking could extend that claim
    indefinitely, one call at a time. New refusal `{"renewed": 0, "reason": "not_owner"}`;
    `claims.renew(conn, plan_id, agent, now)` mirrors `claims.release`'s argument order.

34. **Path identity includes UNICODE FORM: `norm_path` NFC-normalizes** (§4; completes P0-1's
    "one file, one identity"). `café.py` has two byte spellings — decomposed (`cafe` + U+0301,
    what a macOS zip or Finder copy leaves on disk) and composed (U+00E9, what a keyboard, a
    fresh clone and every LLM emit). APFS opens both as ONE file; loom compared strings, so the
    indexer keyed the graph on whichever it walked and a gate call in the other spelling
    resolved nothing -> `new_path` -> ALLOW over a live foreign write claim, and the edit
    landed. One form on both sides: `indexer.naming.norm_path` (which keys the graph) and
    `hook.locator._rel` (which produces the wire path, over `realpath`'s output AND over
    `repo_root`, or the prefix test itself splits). NOTE: a database indexed before this change
    holds decomposed keys and needs one `loom index --full`.

35. **`POST /gate` answers its own contract when the DECISION fails, not only the parse**
    (extends 30). On a full disk the audit INSERT inside `gate_decision` raises
    `sqlite3.OperationalError: database or disk is full`, so the route 500'd and every hook in
    the fleet failed open — while `/health` still answered `{"ok": true}`. `gate_route` now
    wraps the decision in the same guard the parse has and returns the same advisory allow. The
    five frozen wire keys and the six documented cases are unchanged; the operator signal is a
    stderr line, because the audit trail is precisely what has failed.

36. **Two honesty fixes in `loom doctor`** (MULTIREPO-SPEC §4). Check 8 "gate round-trip" piped
    the §7.5 `VERIFY_PAYLOAD`, which `locator.deny_local` refuses HOOK-side — it never reached
    `call_gate`, so the row printed PASS with the server dead, `/gate` 500ing, or the token
    stale. It now pipes a `Write` to a never-written path under the repo root, which must reach
    the server, and PASSes only on exit 0 with a silent stderr (a fail-open, a bypass and a deny
    all write there). Check 9 "index freshness" printed FAIL when it could not ask at all,
    contradicting its own documented WARN-only status; cannot-tell is now a WARN.

37. **Two smaller records with the same shape.** `hook.gate.fail_open` stamps the audit record
    `decision: "fail_open"`, `case: "fail_open"`, `reason: <cause>` instead of falling through
    to `main`'s `setdefault("decision", "allow")` — `~/.loom/gate-audit.jsonl` is what an
    incident is reconstructed from and could not tell an OFF gate from a checked edit.
    `cli._merge_settings` guards the SHAPE of `hooks` (object) and `hooks.PreToolUse` (array)
    with the same `_die` its JSONDecodeError guard uses; both wrong shapes used to dump an
    `AttributeError` traceback from the middle of `loom init`.

38. **BC3-1: a claim's origin bounds its authority** (amends §4 container authority and §5.3
    expansion; FINDINGS BC3-1). `claims` gains `origin` — `'target'` for nodes the agent
    NAMED, `'expanded'` for nodes swept in by the CALLS hop (guarded migration; pre-migration
    rows read `'target'`, the generous-and-correct reading for dbs minted before origins were
    recorded). An `'expanded'` claim authorizes and contends on its own node ONLY: `check_node`
    accepts an ANCESTOR claim only when it is `origin='target'`, and `find_conflicts` skips
    ancestor-only contention rows that are not — calling a class is not owning it. Naming a
    previously-swept node (rescope) PROMOTES its claim to `'target'`; otherwise claims are
    first-wins and never demoted (INSERT OR IGNORE). Closes the fuzz F1/F1b two-writers case:
    one CALLS hop no longer buys downward ownership of a Class or File, while named targets
    keep full §4 downward authority and up∪down contention (council W2 preserved).

39. **§9.1's `EXCLUDE_DIRS` listing is superseded** (council W5; FINDINGS BC3-5). `tests`,
    `frontend` and `alembic` are INDEXED now — an ungated test tree was a silent coordination
    hole. The excluded set is vendor/tooling only; `indexer/walk.py::EXCLUDE_DIRS` is the
    authority, and `test_exclude_dirs_are_pruned` pins both directions.

---

## 12. THIRD-PARTY NOTICES (frozen content for `THIRD_PARTY_NOTICES.md`)

```
Portions of loom's ID-minting and claim-lease logic are derived from
beads (https://github.com/steveyegge/beads), MIT License,
Copyright (c) 2025 Beads Contributors.

Portions of loom's indexer (tree-sitter capture queries and static resolver design) are derived
from FalkorDB/code-graph (https://github.com/FalkorDB/code-graph), MIT License,
Copyright (c) 2024 FalkorDB. Full license: third_party/LICENSES/falkordb-code-graph.txt

Symbol name-path convention and hook input-parsing patterns derived from
Serena (https://github.com/oraios/serena), MIT License, Copyright (c) 2025 Oraios AI.

Spec discipline inspired by github/spec-kit (MIT, Copyright GitHub, Inc.).

mcp_agent_mail (https://github.com/Dicklesworthstone/mcp_agent_mail) informed TTL-lease and
deny-message design as PATTERNS ONLY; no code from it is included (MIT + OpenAI/Anthropic rider).
```

— END OF FROZEN CONTRACT —
