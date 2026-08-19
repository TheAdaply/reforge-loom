# MULTIREPO-SPEC — one loom server, many repositories (iteration spec, frozen)

Written by the orchestrator 2026-08-18, on HEAD 0f64c3c (254 tests green). This spec AMENDS
BUILD-SPEC for the multi-repo feature; where they conflict, this file wins and the change is
listed in §7 here. Everything not named here keeps its frozen BUILD-SPEC behavior.

## 0. Why (goal traceability)

PLAN §7 tenancy, pulled into the MVP by the /goal: "works with multiple repositories, easy to
configure, usable by us and sellable." The schema already keys nodes/plans/claims by `repo`;
this iteration finishes the job at the serve/route/CLI/dashboard layer.

## 1. CLI (frozen)

- `loom serve --repo-root PATH` — unchanged single-repo form (backward compatible).
- `loom serve --repo-root NAME=PATH --repo-root NAME2=PATH2 ...` — repeatable flag, NAME= prefix
  optional (defaults to basename). `--repo NAME` remains valid ONLY with a single un-prefixed
  --repo-root (its existing meaning); combining --repo with multiple roots is a hard error.
- Names must be unique; duplicate names = hard error at startup (beads empty-flag discipline).
- At boot the server indexes EVERY repo (fresh=full, warm=incremental — same rule as today),
  printing one `loom: indexed {"repo": NAME, ...}` line per repo.
- One db serves all repos (schema already repo-keyed). Default db path with multiple roots:
  first root's `.loom.sqlite3` UNLESS `--db` given (recommend --db in docs for multi-repo).
- `loom init --server URL [--repo NAME] [--agent A] [--repo-root PATH]`:
  - Server serves ONE repo → NAME optional, auto-selected (today's behavior).
  - Server serves MANY → NAME required; if omitted, die listing the served names verbatim.
  - Everything else unchanged (settings merge, .mcp.json, CLAUDE.md, per-repo .claude/loom.toml).
- `loom doctor [--repo-root PATH]` — NEW verb, §4.
- `loom ls/show/release/index` — unchanged (`index --repo` already exists and is the multi-repo
  re-index path).

## 2. Server surface (frozen)

- `build_server(db_path, repos: dict[str, str])` — name → repo_root. (`serve()` same shape.)
- `GET /health` → `{"ok": true, "repos": ["name1", "name2", ...]}`.
  BACK-COMPAT: keep `"repo"` key = first served name, so existing inits/tests keep working.
- `POST /gate`: body.repo ∈ served → `gate_decision(conn, repo=body.repo, ...)` for THAT repo.
  body.repo not served or empty → `allow/unindexed` (advisory posture, unchanged).
- `GET /state?repo=NAME` → today's payload scoped to NAME; missing/unknown `repo` param → first
  served repo. Payload gains `"repos": [...]` (all served) so the dashboard can render a switcher.
- `GET /` — unchanged route; the page reads `repos` from /state.
- MCP tools: every tool that takes `repo: str = ""` resolves "" → first served repo (single-repo
  behavior preserved); a non-served non-empty repo returns the §5-shaped error
  `{ok: false, reason: "unknown_repo", served: [...]}` (additive, no frozen-shape change).
  `register(mcp, connection, served)` where served is the ordered name list; tools get the
  default repo from served[0].

## 3. Storage (one migration)

- `events` gains a `repo TEXT NOT NULL DEFAULT ''` column. Migration: guarded
  `ALTER TABLE events ADD COLUMN` in `init_db` (idempotent via PRAGMA table_info check).
- `log_event(conn, actor, action, detail, repo="")` — additive param; existing call sites updated
  to pass their repo. `/state` filters events by repo (rows with '' repo — pre-migration history —
  are shown in every repo's feed; acceptable, documented).
- DDL text in db.py updated for FRESH databases; §2 of BUILD-SPEC gets a DECISIONS pointer, not
  an edit (BUILD-SPEC stays historical).

## 4. `loom doctor` (frozen behavior — the sellability tool)

Run inside a repo checkout. Prints a PASS/FAIL/WARN table, exits 0 iff no FAIL:
1. config — found via the gate's own discovery (LOOM_CONFIG > walk-up .claude/loom.toml >
   ~/.loom/config.toml); prints which one won.
2. server — GET /health within 3s; prints served repos.
3. repo match — config's repo ∈ served list.
4. gate binary — `loom-gate` on PATH and executable.
5. hook registered — .claude/settings.json (walk-up from cwd) contains the loom-gate command in a
   PreToolUse group.
6. mcp registered — .mcp.json (walk-up) has mcpServers.loom.url pointing at config's server.
7. gate round-trip — pipe the §7.5 VERIFY_PAYLOAD through the real loom-gate subprocess with this
   env; expect exit 2 (deny) — proves the whole chain.
8. index freshness — /state?repo= counts.nodes > 0, WARN if 0 ("run loom index --repo NAME").
Implementation: cli/main.py + reusing gate.load_config/config_start_dir. Budget ~90 lines incl.
table printing. Tests: tmp rig with a real subprocess server (pattern from test_dashboard.py).

## 5. Dashboard (small, additive)

- Header gains a repo switcher when `state.repos.length > 1`: monospace chips (like agent chips,
  neutral outline; active repo chip filled ink-on-paper). Click → re-poll with ?repo=NAME.
  Single repo → no switcher rendered (today's look unchanged).
- Everything else identical; the poll URL carries the selected repo.

## 6. Tests (the bar)

- Unit: serve-arg parsing (NAME=PATH forms, duplicate-name error, --repo+multi error); events
  migration idempotency; tools default-repo resolution + unknown_repo shape.
- Integration (subprocess server, two fixture repos — tests/fixtures/pyrepo plus a tiny second
  fixture `tests/fixtures/pyrepo2/` with 2 files, distinct symbol names):
  (a) /health lists both; (b) declare in repo A does not conflict with same-qualname declare in
  repo B (salt isolation — THE multi-repo correctness property); (c) /gate routes: same path+
  qualname string denied in A (claimed) and allowed in B; (d) /state?repo= scopes nodes AND
  events; (e) init against the 2-repo server: no --repo dies listing names, --repo works;
  (f) doctor full PASS on a good rig, FAIL rows on: dead server, missing hook.
- Full suite stays green; existing tests unmodified except where they assert /health's exact
  payload (extend, don't break: "repo" key preserved).

## 7. BUILD-SPEC deltas introduced here (for the record)

D1 events.repo column (migration §3). D2 /health payload gains repos[] (repo kept). D3
build_server/serve signature repos-dict. D4 register() served-list. D5 /state repo param +
repos[]. D6 new CLI forms + doctor. No changes to: §7.4 templates, /gate wire keys, ids/qualname
rules, hook contract, claim semantics.

**Delta on §4 above (the code is right, this document is the record).** The shipped table has
TEN rows — §4's list predates the auth row (ITERATION-2 D11) and the index-staleness row (U2).
Two of its rows also no longer behave as written, and BUILD-SPEC §11.36 carries the reasoning:

- **gate round-trip** does NOT pipe the §7.5 `VERIFY_PAYLOAD` and does not expect exit 2. That
  payload is refused HOOK-side by `locator.deny_local`, so the row printed PASS against a dead
  server, a 500ing `/gate` and a stale token alike — it proved only that the binary runs. It now
  pipes a `Write` to a never-written path under the repo root, which must reach `/gate`, and
  PASSes on exit 0 with a silent stderr. (`loom init`'s own post-write verification keeps the
  §7.5 payload and its exit-2 expectation: there the point IS the hook-side deny.)
- **index freshness** WARNs, never FAILs — including when there is no server to ask.

## 8. Out of scope (this iteration)

Cross-repo edges/claims; per-repo auth; org tenancy above repo; dashboard multi-repo aggregate
view; PyPI publishing (needs the user's account decision); Postgres.
