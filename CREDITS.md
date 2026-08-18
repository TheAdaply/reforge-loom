# Credits

Projects that shaped how loom works. **No code from any of them is in loom.** Each entry names the
idea we took and the loom file that implements our own version of it, so the claim is checkable.

Projects loom does derive code from are listed separately, with their licenses, in
`THIRD_PARTY_NOTICES.md`.

- **github/spec-kit** (https://github.com/github/spec-kit) — fill discipline for a one-page spec:
  every field answerable, no field optional. loom's five spec fields are its own.
  In loom: `src/loom/templates/spec.md`.

- **mcp_agent_mail** (https://github.com/Dicklesworthstone/mcp_agent_mail) — TTL leases instead of
  locks, and deny messages that carry the other agent's intent rather than a refusal.
  In loom: `src/loom/server/claims.py`, `src/loom/hook/gate.py`.

- **graft** (https://github.com/NanoNets/Graft) — incremental indexing kept honest by a
  cold-equals-incremental equality test, with a per-file content hash as the memo key.
  In loom: `src/loom/indexer/walk.py`, `tests/indexer/test_incremental.py`.

- **graphiti** (https://github.com/getzep/graphiti) — gate the *fuzzy* resolution path only, never
  the exact ones, and escalate on ambiguity instead of guessing.
  In loom: `claims._fuzzy_tail`, which uses no entropy arithmetic.

- **graphify** (https://github.com/Graphify-Labs/graphify) — index staleness is a soft verdict, not
  an error and not a block.
  In loom: `app.index_age`, reported on `/state` only and never on the `/gate` wire.

`specgate` in source comments refers to loom's own earlier unpublished MVP by the same author, not
to a third-party project.
