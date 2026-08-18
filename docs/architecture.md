# Architecture

The five-minute mental model. [protocol.md](protocol.md) is the contract; this is how it is built.

## One process, one file

Everything is a single `loom serve` process holding a single SQLite database. That database
contains four things:

| Table | Holds |
|---|---|
| `nodes` | one row per indexed symbol: file, class, function |
| `edges` | `CONTAINS` (file → class → method), `CALLS`, `IMPORTS` |
| `plans` | a declared intent: agent, title, the spec text, status, TTL deadline |
| `claims` | which plan holds which node, in write or read mode |

The graph and the coordination state living in one store is the reason a declare can be a single
transaction. There is no cache, no queue, no second service, and no background thread.

## Four pieces

```
  repo on disk ──▶ indexer ──▶ nodes + edges ─┐
                                              ├──▶ claims judgement ──▶ MCP tools   (agents)
                  plans + claims ─────────────┘                    └──▶ POST /gate  (the hook)
                                                                   └──▶ GET /state  (dashboard)
```

**The indexer** (`src/loom/indexer/`) walks the repository and parses Python with tree-sitter.
Two passes: pass 1 mints node rows per file, pass 2 wipes and rebuilds every `CALLS`/`IMPORTS`
edge for the repository from the whole node set. Pass 2 is unconditional, which is what makes an
incremental index produce byte-identical output to a cold one — pinned by
`tests/indexer/test_incremental.py::test_incremental_equals_cold_index`. The honest price is that
`--changed` saves the database writes, not the parse: about 92% of a cold run.

**The judgement** (`src/loom/server/claims.py`) is the only place that decides anything. Declare,
conflict detection, TTL, and the text of every deny message are here, and so is all the claim SQL.
Everything else is an adapter over it.

**The tool surface** (`src/loom/server/tools.py`, `app.py`) exposes nine MCP tools plus three plain
HTTP routes on the same port: `/health`, `/state`, `/gate`. The server never needs a repository
path to judge — it judges the graph, and every path on the wire is already repo-root-relative.

**The hook** (`src/loom/hook/`) is a separate process that Claude Code runs before every edit. It
reads the tool payload on stdin, works out `(path, qualname)`, POSTs `/gate`, and exits 0 or 2.

## Declare is one transaction

`declare_plan` runs inside a single `BEGIN IMMEDIATE`. The write lock is taken **before** the first
read, so the whole resolve → expand → detect-conflicts → insert-claims cycle is atomic. Two agents
racing on overlapping targets cannot both win: one takes the lock, the other waits and then sees
the first agent's claims. `tests/server/test_concurrency.py` races two real HTTP clients against a
subprocess server to prove it.

The read path is deliberately *not* wrapped. `gate_decision` and `check_node` judge on plain reads,
because expiry is enforced by a `ttl_expires > now` filter in the query rather than by a sweep — a
stale sweep is harmless, and the gate never has to wait for a writer.

## Why the hook imports almost nothing

The PreToolUse hook runs on every single edit, and its whole budget is a couple of seconds.
`loom/hook/**` therefore imports the standard library and `loom.indexer.naming`, and nothing else —
never `loom.server.*`, never `mcp`. Importing the MCP SDK or starlette in that process would spend
the entire budget on interpreter startup before any work happened. This is an invariant no linter
enforces; see [CONTRIBUTING.md](../CONTRIBUTING.md).

The same reasoning shapes the wire: the hook speaks plain `POST /gate`, never the MCP handshake,
even though both are served on the same port.

## Everything fails open

The coordination layer is advisory. If the server is down, slow, misconfigured, or answering
nonsense, the hook prints one loud warning and allows the edit. It exits 0 or 2 and never 1,
because exit 1 is the code Claude Code treats as a hook malfunction to be swallowed silently — a
gate that fails invisibly is worse than one that fails loudly.

The cost is real and worth stating: a network partition means no coordination, and nothing tells
the agents. `loom doctor` is the loud path for a human.

## Identity and multi-repo

One process can serve several repositories. Names are keys: `nodes`, `plans` and `claims` are all
scoped by repository name, so the same `svc.py` in two repositories is two independent symbols.
The first `--repo-root` is the default that every omitted `repo` argument resolves to.

A repository name is a plain string agreed between the server and each checkout — the server mints
it at boot (default: the root directory's basename) and every `loom init` reads it back from
`/health`. Agent identity is whatever the caller says it is; loom does not verify it. See
[SECURITY.md](../SECURITY.md).
