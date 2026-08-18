# loom documentation

Start at the repository [README](../README.md) for install and the quickstart. This page says what
every other document is and whether you can trust it as current.

## Read in this order

`README.md` → `docs/architecture.md` → `docs/protocol.md` → `docs/operations.md`. Reach for
`docs/troubleshooting.md` when a `loom doctor` row is red. Everything under `docs/archive/` is
history.

## Current

| Document | Status | What it is |
|---|---|---|
| [architecture.md](architecture.md) | current | The five-minute mental model: one process, one SQLite file, indexer to graph, declare as one transaction, why the hook imports almost nothing. |
| [protocol.md](protocol.md) | current | The agent-facing contract: nine MCP tools with arguments and returns, the `POST /gate` wire, the six decision cases, the TTL law, node-ref grammar. |
| [operations.md](operations.md) | current | Running a server: where the database lives, `--db` with several roots, backups, re-indexing, keeping `serve` alive, host and port choices. |
| [troubleshooting.md](troubleshooting.md) | current | One section per `loom doctor` row, plus four symptoms doctor does not cover, plus the human escape hatch. |
| [FINDINGS.md](FINDINGS.md) | current | The red-team ledger: every confirmed defect with its repro, root cause and fix status; what held under attack; the simplification record. |

## Specification chain

BUILD-SPEC is the base contract and it is **frozen**: it is never edited, and each later
specification amends it. Where they disagree, the newest amendment wins, and where an amendment
disagrees with the code, the code wins.

| Document | Status | What it is |
|---|---|---|
| [BUILD-SPEC.md](BUILD-SPEC.md) | frozen, superseded in part | The implementation contract the MVP was built against: DDL, ID scheme, tool shapes, hook contract, frozen deny templates. §11 DECISIONS-DELTA records every correction. |
| [MULTIREPO-SPEC.md](MULTIREPO-SPEC.md) | current amendment | Serving several repositories from one process. Amends BUILD-SPEC §5 and §9; deltas D1–D6. |
| [ITERATION-2-SPEC.md](ITERATION-2-SPEC.md) | current amendment | Optional shared-token auth, `/state` totals and truncation, dashboard focus mode. Deltas D7–D11. |

The U1/U2/U3 recon fixes (edge resolution, staleness as a verdict, entropy-gated fuzzy resolve)
landed after ITERATION-2-SPEC and are recorded in [CHANGELOG.md](../CHANGELOG.md) and in the module
docstrings that implement them.

## Archive

[archive/](archive/README.md) holds internal build records: the original plan, two review gates,
the build log, the live-fire transcript, and the per-source study notes under
`archive/extractions/`. Kept for provenance, not maintained, and not a guide to using loom.

## Screenshots

`docs/*.png` are README assets, not documentation.
