# loom — spec-driven build plan (v1, user-authored, 2026-08-18)

> Verbatim input plan. Known stale facts to correct during hardening:
> (a) mcp_agent_mail is github.com/Dicklesworthstone/mcp_agent_mail (NOT steveyegge), license is
> "MIT + OpenAI/Anthropic rider" — patterns only, zero verbatim code;
> (b) "FastMCP app" is stale — mcp SDK 2.0.0 removed FastMCP; the surface is
> `from mcp.server import MCPServer` + `run(transport="streamable-http")` (proven working in
> ../specgate, this repo's own prior MVP);
> (c) specgate (sibling dir) is cherry-pick source #8 — our own verified code: MCPServer wiring,
> check-then-act lock lesson, AST qualname collection, uv/pyproject shape, HTTP demo harness.

## 0. What this is

One small system of our own that replaces the glue stack. A single coordination server that holds
the code graph, the plans, and the claims in one store, a Claude Code hook that enforces claims at
edit time, and a protocol file that tells agents how to behave. No runtime dependency on beads,
Agent Mail, FalkorDB, or Serena. We lift their best patterns, the code is ours.

## 1. Decisions locked

- One server, one store. Graph, plans, claims, and specs live in a single SQLite database behind a
  single process. check and claim become one transaction. This kills the race window and the
  three-stores-drift problem that capped the glue stack at 78.
- Enforcement stays in the Claude Code PreToolUse hook. Exit 2 blocks the edit, stderr carries the
  reason, the reason names the owning plan. The deny message is the spec pull-through.
- Claims are advisory with TTL, never hard locks. Expiry plus renewal, borrowed from Agent Mail. A
  crashed agent never freezes the team.
- Symbol granularity for claims, file granularity as fallback for non-code files (config, docs).
- MVP is one language (pick the demo repo's language, Python or TypeScript). The indexer is
  tree-sitter, so more languages are added by adding capture queries, not new code paths.
- Seamless setup is a requirement, not a nice-to-have. Two commands per user, total. `loom serve`
  once for the team, `loom init` once per user. init registers the hook, writes the CLAUDE.md
  snippet, and stores the server URL and agent identity. Zero manual steps after that.
- Serena is optional and never load-bearing. If a user has it, the hook also matches its edit
  tools. If not, the hook maps plain Edit/Write calls to symbols itself.

## 2. Cherry-pick manifest

What we take, from where, and where it lands in our repo. Patterns and small excerpts, not
dependencies.

- FalkorDB code-graph (github.com/FalkorDB/code-graph and its analyzers)
  - Take: tree-sitter capture queries for function and class definitions and call sites, and the
    node/edge schema (File, Class, Function, DEFINES, CALLS, IMPORTS).
  - Lands in: `indexer/queries/` and the `nodes`/`edges` tables.
- beads (github.com/gastownhall/beads)
  - Take: hash-based short IDs so two users never mint colliding IDs, the claim state machine
    (open, claimed, done, expired), discovered-from lineage for drift, and the CLI verb ergonomics
    (ls, show, claim, done).
  - Lands in: `server/ids.py`, `server/claims.py`, `cli/`.
- mcp_agent_mail (github.com/Dicklesworthstone/mcp_agent_mail)
  - Take: TTL lease semantics with renewal, advisory-by-design philosophy, the
    deny-with-actionable-next-step message format, and the guard script shape.
  - Lands in: `server/claims.py` (TTL sweeper), `hook/gate.py` (message format).
- Serena (github.com/oraios/serena)
  - Take: the canonical symbol ID convention, `relative/path.py::Class.method`, so our node IDs
    match what LSP tooling produces later.
  - Lands in: `indexer/naming.py`. Also documented as an optional companion MCP in the README.
- CodePlan (arXiv 2309.12499)
  - Take: the edit-classification to affected-relation rules table, for merge-time impact marking.
  - Lands in: `server/impact.py`, v2 flag, off in MVP.
- GitHub spec-kit (github.com/github/spec-kit)
  - Take: spec template structure, trimmed to one page. Goal, targets, interfaces, assumes, out of
    scope.
  - Lands in: `templates/spec.md`.
- Claude Code hooks (docs plus github.com/kornysietsma/claude-code-permissions-hook)
  - Take: PreToolUse matcher config, the exit-2 plus stderr contract, settings.json wiring.
  - Lands in: `hook/` and the `loom init` writer.
- grite (arXiv 2606.19616)
  - Take: the wasted-work metric, share of duplicated or conflicting work, for our eval harness.
  - Lands in: `eval/metrics.py`.
- RealWorld / Conduit example apps
  - Take: the demo codebase and the overlapping task-pair design.
  - Lands in: `eval/target-repo` as a submodule.

## 3. Repo layout

```
loom/
  server/        MCP app (MCPServer), SQLite, tools, claims, ids, impact (v2)
  indexer/       tree-sitter walk, capture queries, naming, incremental re-index
  hook/          gate.py (PreToolUse), locator.py (edit to symbol), guard.py (pre-commit, optional)
  cli/           loom serve | init | index | ls | show | release
  templates/     spec.md, CLAUDE.snippet.md
  eval/          harness, metrics, task pairs, target-repo submodule
  tests/
```

Target size. Server 500 to 700 lines, indexer 200 to 300, hook 120 to 180, cli 150. Small enough
that our own agents build it from these specs.

## 4. System spec

### 4.1 Data model (SQLite, WAL mode)

- `nodes(id, repo, path, qualname, kind, body_hash, updated)` where id is a short hash of repo plus
  qualname.
- `edges(src, dst, kind)` with kind in CALLS, IMPORTS, CONTAINS.
- `plans(id, agent, repo, branch, title, spec_md, status, created, updated, ttl_expires)` with
  status in active, done, expired, superseded.
- `claims(node_id, plan_id, mode)` with mode write or read. Write is exclusive per node. Read is
  shared and represents the assumes list.
- `events(ts, actor, action, detail)` append-only log. This is our audit trail and demo data.
- Rename tolerance, v1.5. On re-index, an unmatched removed node whose body_hash equals a new
  node's hash transfers its claims to the new node.

### 4.2 MCP tools (the server's whole API)

- `resolve_nodes(names_or_paths) -> node ids`. Fuzzy in, canonical out. Agents plan in canonical
  IDs only.
- `declare_plan(agent, repo, branch, title, spec_md, write_targets[], assumes[]) -> {plan_id} |
  {conflicts}`. Atomic. In one transaction the server expands write_targets by one hop over CALLS
  and IMPORTS, intersects the expanded set and the assumes with existing write claims, and either
  claims everything or claims nothing. On conflict the response embeds each clashing plan's full
  spec_md inline, so the fetch step is free and there is no second call.
- `check(agent, node_id) -> allow | deny(owner_plan, spec_md)`. The hook's fast path. Sub-10ms
  target.
- `rescope(plan_id, add_targets[], add_assumes[]) -> ok | conflicts`. Same atomicity as
  declare_plan. Called on drift.
- `get_plan(plan_id)`, `list_claims(repo)`, `renew(plan_id)`, `release(plan_id)`. Release also
  fires on plan done.
- Conflict rule, exact. A conflict is write-write on the same node, or my write against someone's
  read (I would break their assumption), or my read against someone's write (my assumption is
  being changed). All three are surfaced, write-write blocks, the read cases warn with the owner's
  spec attached.

### 4.3 Hook behavior

- PreToolUse matcher on Edit, Write, MultiEdit, and the Serena edit tools if present.
- `locator.py` maps the edit to a symbol. Parse the target file with tree-sitter, find the
  enclosing function or class for the edited range, emit the canonical ID. Non-code files map to a
  file-level ID.
- Gate logic. Symbol inside my active plan's write claims, allow. Claimed by another active plan,
  exit 2 with stderr `claimed by <agent> under plan <id>: <title>. Its spec follows. Build against
  its declared interfaces or rescope.` followed by the spec. Unclaimed and outside my plan, exit 2
  with `outside your declared plan. Call rescope first.` No active plan at all, exit 2 with
  `declare a plan before editing.`
- The deny path is the protocol's teeth. Everything else is choreography.

### 4.4 Protocol (CLAUDE.snippet.md, written by loom init)

- Before any code change, write a spec from `templates/spec.md`, resolve every target and assume
  to node IDs with resolve_nodes, then call declare_plan.
- On conflicts in the response, read the embedded specs, replan to build against their declared
  interfaces, never against in-flight code, adjust your targets, declare again.
- Edit normally. If the gate denies, follow the message. It will either hand you a spec to replan
  around or tell you to rescope.
- When tests pass and the branch merges, call release.
- Spec template fields. Goal in two sentences. Write targets as node IDs. New or changed
  interfaces as exact signatures. Assumes as node IDs plus the signature you rely on. Out of scope
  in one line.

### 4.5 Seamlessness requirements

- `pip install loom` or `uvx loom`, nothing else.
- `loom serve` starts server plus indexer against a repo path or a list of repos. One process.
- `loom init --server URL` per user. Registers the hook in `.claude/settings.json`, appends the
  snippet to CLAUDE.md, mints an agent token, verifies with a ping.
- `loom index` is automatic on merge via a webhook or a git post-merge hook that init installs.
  Manual command exists as fallback.
- A user who runs init and then just talks to their agent should never touch loom again. That is
  the acceptance bar.

## 5. Build milestones, written for agents to execute

Each milestone is a handoff-able spec. Interfaces are fixed here so two agents can build in
parallel. We dogfood, one of us takes M1 plus M4, the other takes M2 plus M3, specs declared up
front.

- M0, scaffold. Half a day.
  - Deliver: repo layout, pyproject, SQLite schema migration, empty MCP app serving a health tool,
    CI running pytest.
  - Accept: `loom serve` starts, health tool answers over MCP.
- M1, indexer. One day.
  - Deliver: tree-sitter walk of a repo producing nodes and edges per 4.1, canonical naming per
    Serena convention, `loom index` full and incremental (changed files only, by mtime plus hash),
    FalkorDB-derived queries for the chosen language.
  - Accept: indexing the eval repo yields functions and CALLS edges, spot-checked against 20 known
    call sites, re-index of one changed file touches only its nodes.
- M2, server tools. One day.
  - Deliver: all tools in 4.2, atomic declare_plan and rescope, TTL sweeper, conflict rule with
    all three cases, events log.
  - Accept: two simulated agents, scripted, hit declare_plan concurrently on overlapping targets,
    exactly one wins, the loser's response embeds the winner's spec. check answers under 10ms warm.
- M3, hook plus protocol. One day.
  - Deliver: gate.py and locator.py per 4.3, loom init per 4.5, CLAUDE.snippet.md and spec
    template per 4.4.
  - Accept: scripted — pipe PreToolUse JSON into gate.py and assert exit codes plus stderr for all
    four gate cases (in-plan allow, foreign-claim deny with spec, out-of-scope deny, no-plan deny).
- M4, eval plus demo. One to two days.
  - Deliver: harness that runs a task pair in three arms (no coordination, loom, and optionally
    the old glue stack), metrics per grite (wasted work share) plus conflict hunks at merge,
    post-merge test failures, wall clock, tokens. Demo script on the RealWorld repo with the auth
    versus caching pair and one more pair.
  - Accept: one full three-arm run produces a results table from a single command.

Total, four to five focused days solo, about three in parallel.

## 6. Eval design

- Arms. A, two agents, no coordination, worktrees plus plain merge. B, loom. C optional, glue
  stack, to prove the join beats prompting.
- Task pairs, designed to collide. Pair 1, harden authenticate versus cache authenticate results.
  Pair 2, add comment editing versus add comment moderation, both crossing the comment model and
  serializer.
- Metrics. Merge conflict hunks. Post-merge test failures, the semantic conflicts, this is the
  headline. Wall clock to both branches merged and green. Token spend. Wasted-work share.
- The demo clip. Agent B denied at the gate, reading agent A's spec out of the deny message,
  replanning on its own, merging clean. Record it in arm B, first run.

## 7. Scaling, 10 users, multiple repos, one org

Nothing in the core loop changes. What changes is capacity, contention policy, and lifecycle
automation.

- Storage. SQLite WAL genuinely holds 10 agents, but flip to Postgres by config (SQLAlchemy from
  day one, so the flip is a URL). Server stays one stateless process, the transaction semantics
  are identical.
- Tenancy. Every table already keys on repo. Add org above repo. One loom server per org, all
  repos registered with it, `loom serve --repos config.yaml`. Claims are per repo. IDs are hashed
  with repo salt, no collisions.
- Identity. Agent token per user from init, mapped to a user record. Later, map to GitHub
  identities so claims show up as people, not strings.
- Indexing at scale. Per-repo webhook on merge triggers incremental re-index of changed files
  only. M1's incremental path is the whole story, it just runs more often.
- Contention, the real 10-user problem. Three policies. First, claims stay small, symbol not file,
  so most parallel work never touches. Second, waitlists, a denied write can subscribe to a node,
  release notifies the next plan in queue through its next check call. Third, hot nodes (config
  files, barrel files, shared fixtures) get a declared policy, either shared-append mode or
  explicit sequential queue, configured per path pattern.
- Assumes at scale. Read claims are what make 10 users safe. My write against your read warns you
  through your next gate hit instead of silently invalidating your plan. This is the mechanism
  that catches the stale-plan problem without a watcher process.
- Precision tuning. One hop is right for two users. At 10, per-edge-type radius (CALLS one hop,
  IMPORTS zero) and muting of low-signal edges (test files importing everything) keep false
  clashes down. Config, not code.
- Merge lifecycle. On merge webhook, auto-release the plan's claims, re-index, and mark dependents
  of changed nodes. Plans whose assumes intersect the changed set get a stale flag surfaced at
  their next tool call. This is CodePlan's impact rules earning their keep, and it is v2 turned on
  by default at team scale.
- Cross-repo. v2. Import edges across repos via published package names, claims stay per repo, an
  interface change in a shared package broadcasts a warn to plans in dependent repos that assume
  it.
- Visibility. A read-only web page per repo, the graph with live claim badges. One page,
  server-rendered. This is also the sales demo.
- Failure modes. Server down means the hook fails open with a loud warning line (advisory
  philosophy, work continues, coordination degrades). TTL expiry plus heartbeat renewals from
  active sessions handles crashed agents. Events log makes every deny and claim auditable.

## 8. Out of scope, v1

- Multi-language in one repo, live indexing of uncommitted code across machines, CRDT-style live
  edit sync, rename tracking beyond body-hash matching, a smart merge engine, orchestration of any
  kind. The bet stays prevention through planning. If we ever need a clever merger, the gate
  failed and we fix the gate.

## MVP scope cuts (session addendum, gate-2 enforced)

OUT of the 12-hour MVP even where §4-§6 mention them: rename tolerance (v1.5), impact.py, waitlists,
hot-node policies, eval arm C, webhooks (post-merge hook only), web visibility page. M4 shrinks to
harness skeleton + ONE scripted collision demo. IN and mandatory: hook fail-open with ~2s timeout
when the server is unreachable (a hanging PreToolUse hook bricks every edit — worst possible ship).
