# Changelog

Notable changes to loom. The format follows [Keep a Changelog](https://keepachangelog.com/); this
project does not yet make release tags, so entries are grouped by the build pass that produced
them.

## Unreleased

### Fixed

- **Declare-time conflict detection was file-granular, not function-granular.** The containment
  closure used one mixed up-and-down walk, which pivoted through the file node and pulled every
  sibling function into the conflict question. Two agents could not work in one file, and the
  demo's headline sequence failed. Conflict scope is now the union of the two single-direction
  closures, which is the same question the edit-time gate already asked.
- `python -m loom.eval.harness --demo` runs green again, and `tests/eval/test_demo.py` now runs it
  so it cannot break unnoticed.

### Added

- `docs/protocol.md`, `docs/architecture.md`, `docs/operations.md`, `docs/troubleshooting.md`,
  `docs/README.md`, and an Install section in the README — the first documentation of the MCP tool
  surface, the `/gate` wire contract and the optional shared token outside a frozen spec.
- `CONTRIBUTING.md`, `SECURITY.md`, `CLAUDE.md`, `CREDITS.md`, this file, and a CI workflow.
- Full MIT license texts for beads and Serena under `third_party/LICENSES/`, and `license-files`
  in `pyproject.toml` so notices travel into built artifacts.
- `loom --version`.

### Changed

- Internal build records moved to `docs/archive/`. `THIRD_PARTY_NOTICES.md` is now the three
  code-derived sources with their licenses; design influences that contributed no code moved to
  `CREDITS.md`.
- `uv.lock` is committed, and `mcp` is bounded to `>=2.0.0,<3`.

## Iteration 2 + recon fixes — 2026-08-19

### Added

- **U1** — edge resolution now makes an incremental index produce the same graph as a cold one,
  pinned by an equality test.
- **U2** — index staleness is reported as a verdict on `/state`, in the dashboard, and as a
  `loom doctor` WARN. Never on the `/gate` wire.
- **U3** — fuzzy symbol resolution is gated on information: short or ambiguous tails refuse and
  return suggestions instead of guessing. Exact and path-suffix matching are untouched.

## Iteration 2 — 2026-08-18

### Added

- Opt-in shared-token auth on `/gate`, `/state` and `/mcp`; `/health` stays open and advertises the
  mode.
- `/state` reports totals and a per-axis truncation flag instead of silently capping.
- Dashboard focus mode: past ~12 files the fabric draws the threads carrying live claims
  plus the biggest remaining ones, and says so in the header.
- Path-suffix resolution on a `/` boundary, so `auth.py::login` resolves on a deep tree.

### Fixed

- `loom init` now gitignores the per-user `.claude/loom.toml`. Found live: a teammate's `git add -A`
  committed their identity file, and pulling it re-identified everyone.

## Multi-repo — 2026-08-18

### Added

- One server, one database, several repositories. `--repo-root` is repeatable and each root may be
  named; graph, plans and claims are keyed by repository name.
- `loom doctor`: ten checks over one checkout, including a real gate round-trip.
- A repo switcher in the dashboard when more than one repository is served.

## MVP — 2026-08-18

### Added

- The whole thing: tree-sitter index, plans and claims in SQLite, nine MCP tools, the PreToolUse
  gate, the dashboard, the CLI, and the evaluation harness.
