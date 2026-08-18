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

graft (https://github.com/NanoNets/Graft, MIT) informed the incremental-index
discipline as a PATTERN ONLY — per-file content-hash memo, whole-node-set edge resolution,
and a cold-equals-incremental equality test (`src/graph/build.ts`, `test/graph-incremental.test.ts`).
No code from it is included; loom's implementation is `src/loom/indexer/walk.py`.

graphiti (https://github.com/getzep/graphiti, Apache-2.0) informed the low-information gate
on fuzzy symbol resolution as a PATTERN ONLY — gate the fuzzy path, never the exact ones,
and escalate on ambiguity instead of guessing
(`graphiti_core/utils/maintenance/dedup_helpers.py`). No code from it is included; loom's
implementation is `claims._fuzzy_tail` and uses no entropy arithmetic.

graphify (https://github.com/Graphify-Labs/graphify, Apache-2.0) informed index staleness as
a soft VERDICT rather than an error or a block (`graphify/cli.py`, "soften, never block") as
a PATTERN ONLY. No code from it is included; loom's implementation is `app.index_age`, and
it is reported on `/state` only — never on the `/gate` wire.
