# loom

**Many threads, one fabric, no tangles.** A coordination gate for teams of coding agents (multiple
users, multiple machines, one git repository): every agent must declare a plan — which symbols it
will change and why — before editing. Overlapping plans are refused at declare time with the
owner's spec embedded in the refusal, and a Claude Code PreToolUse hook enforces the claims at edit
time, so the merge conflict never gets written.

One process, one SQLite store: the code graph (tree-sitter indexed, function-level), the plans,
and the claims live behind one server, and check-and-claim is a single `BEGIN IMMEDIATE`
transaction — no race window between checking and claiming.

## Quickstart (two users, two machines)

**Team lead, once — anywhere both machines can reach:**

```bash
uv run --directory <this-dir> loom serve --repo-root /path/to/shared-repo-clone --port 8788
```

Serving indexes the repo (functions, classes, CALLS/IMPORTS/CONTAINS edges) and exposes the MCP
tool surface at `/mcp` plus the hook's fast `POST /gate` endpoint.

**Each user, once, inside their clone of the shared repo:**

```bash
uv run --directory <this-dir> loom init --server http://<host>:8788 --agent <your-name>
```

`init` merges the PreToolUse gate into the repo's `.claude/settings.json` (idempotent, never
overwrites your other hooks), appends the protocol snippet to `CLAUDE.md`, writes
`~/.loom/config.toml`, pings the server, and verifies the gate with a synthetic payload. After
that you never touch loom again — you just talk to your agent.

## The protocol (what agents do)

1. Before any code change: write a one-page spec (`src/loom/templates/spec.md` — goal, write
   targets as node refs, exact new/changed interfaces, assumes, out of scope), resolve targets
   with `resolve_nodes`, call `declare_plan`.
2. On conflict, the response embeds every clashing plan's full spec inline — replan against their
   declared interfaces, adjust targets, declare again.
3. Edit normally. The hook allows in-plan edits silently; a foreign-claim edit is blocked (exit 2)
   with the owner's spec in the message; out-of-scope edits are told to `rescope`.
4. When merged: `release`. Claims also expire on TTL (30 min, renewed implicitly on activity), so
   a crashed agent never freezes the team.
5. If the server is unreachable, the gate **fails open** in ~1.5s with a loud warning — work
   continues, coordination degrades, edits are never bricked.

## Watching the board

```bash
uv run --directory <this-dir> loom ls                 # active claims
uv run --directory <this-dir> loom show lm-xxxxx      # a plan (or n-xxxxx for a node)
uv run --directory <this-dir> loom release lm-xxxxx --agent <name>
uv run --directory <this-dir> loom index --repo-root <repo> --changed   # manual re-index
```

## See it work (60 seconds, no setup)

```bash
uv run --directory <this-dir> python -m loom.eval.harness --demo
```

Boots a real server on a throwaway db, indexes a fixture repo, and scripts the headline sequence:
agent A declares over `AuthService/authenticate` → agent B's overlapping declare is refused with
A's spec embedded → B's edit on A's symbol is blocked by the real gate subprocess (exit 2, spec in
stderr) → B's edit on its own symbol passes silently → A releases and the symbol frees. Every step
carries an inline assert.

## Tests

```bash
uv run --directory <this-dir> pytest tests -q     # 217 tests
```

`tests/server/test_concurrency.py` is the one that matters: two HTTP clients race `declare_plan`
on overlapping targets against a real subprocess server — exactly one wins, the loser gets the
winner's spec.

## Design & provenance

- `docs/PLAN-v1.md` — the original plan; `docs/BUILD-SPEC.md` — the frozen implementation
  contract (DDL, ID scheme, tool shapes, hook contract, deny templates); `docs/extractions/` —
  what was cherry-picked from beads, FalkorDB code-graph, Serena, spec-kit, mcp_agent_mail
  (patterns only), CodePlan, and grite, with licenses; `THIRD_PARTY_NOTICES.md` — credits.
- Claims are **advisory with TTL**, never hard locks; write-write blocks, read/write mismatches
  warn with the owner's spec attached.
- Qualnames follow Serena's convention: `path/to/file.py::Class/method`.

## MVP limits (deliberate)

Python-only indexing; one repo per server; caller-asserted identity (no auth yet); no rename
tracking; no impact analysis beyond one-hop CALLS expansion; the eval's real-codebase three-arm
runs are post-MVP (the harness and metrics ship now).
