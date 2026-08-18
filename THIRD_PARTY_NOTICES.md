# Third-party notices

loom is Apache-2.0 (see `LICENSE` and `NOTICE`). This file lists the three projects whose **code**
loom derives from, and reproduces the license each one requires us to carry. Projects that
influenced loom's *design* but contributed no code are listed in `CREDITS.md`; nothing there
imposes an obligation, and nothing here may be dropped.

---

## beads — MIT

Portions of loom's ID-minting and claim-lease logic are derived from
beads (https://github.com/steveyegge/beads), MIT License,
Copyright (c) 2025 Beads Contributors.
Full license: `third_party/LICENSES/beads-MIT.txt`

In loom: `src/loom/server/ids.py` (base-36 encoding of the ID suffix) and the TTL-lease shape in
`src/loom/server/claims.py`.

## FalkorDB/code-graph — MIT

Portions of loom's indexer (tree-sitter capture queries and static resolver design) are derived
from FalkorDB/code-graph (https://github.com/FalkorDB/code-graph), MIT License,
Copyright (c) 2024 FalkorDB.
Full license: `third_party/LICENSES/falkordb-code-graph.txt`

In loom: `src/loom/indexer/queries/python.py` and the two-pass walk in
`src/loom/indexer/walk.py`.

## Serena — MIT

Symbol name-path convention and hook input-parsing patterns derived from
Serena (https://github.com/oraios/serena), MIT License,
Copyright (c) 2025 Oraios AI.
Full license: `third_party/LICENSES/serena-MIT.txt`

In loom: `src/loom/indexer/naming.py` (the `Class/method` name path) and
`src/loom/hook/locator.py` (tool-payload field names).
