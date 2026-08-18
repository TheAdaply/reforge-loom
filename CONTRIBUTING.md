# Contributing to loom

## Setup

Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), git.

```bash
git clone <this-repo-url> loom
cd loom
uv sync
uv run pytest tests -q
uv run ruff check src tests
```

`uv.lock` is committed. Use `uv sync --frozen` if you want to reproduce CI exactly.

The end-to-end demo is the fastest way to see whether a change broke anything real:

```bash
uv run python -m loom.eval.harness --demo
```

## Module map

| Directory | What lives there |
|---|---|
| `src/loom/server/` | the database, the claim judgement, the nine MCP tools, the HTTP routes |
| `src/loom/indexer/` | repository walk, tree-sitter queries, the qualname helpers |
| `src/loom/hook/` | the PreToolUse gate process and the tool-payload locator |
| `src/loom/cli/` | `serve` · `init` · `doctor` · `index` · `ls` · `show` · `release` |
| `src/loom/eval/` | the demo harness and the metrics formulas |
| `src/loom/templates/` | files loom writes into *other* repositories |

[docs/architecture.md](docs/architecture.md) explains how they fit together. The README's
"Reading the code" table is the recommended order for a first pass.

## The four invariants

None of these is enforced by a linter, and each one has cost somebody an afternoon.

1. **`src/loom/hook/**` imports the standard library and `loom.indexer.naming`, nothing else.**
   Never `loom.server.*`, never `mcp`. This process runs before every single edit and its whole
   budget goes on interpreter startup; importing the MCP SDK there makes every edit slow.
2. **`loom-gate` exits 0 or 2, never 1, and every failure path fails open.** Exit 1 is treated as a
   hook malfunction and swallowed, which turns a broken gate into an invisible one. If you add a
   code path that can raise, it must end in an allow and a warning.
3. **Storage SQL lives in `server/db.py` and `server/claims.py`.** The indexer, the dashboard reads
   and the CLI admin verbs carry their own queries by design — that is the whole list. Adding claim
   SQL anywhere else breaks the "one place decides" property the concurrency test depends on.
4. **No deny message names an escape hatch.** No text an agent can see may contain "force",
   "bypass", "override" or "unclaim". `tests/hook/test_gate.py` asserts this. The human escape
   hatch is documented in `docs/troubleshooting.md` and nowhere an agent reads.

Every `__init__.py` is empty except `loom/__init__.py`, which holds `__version__`. That is
deliberate — see invariant 1.

## Adding a language

The indexer is Python-only today. The seam is `src/loom/indexer/queries/`: one module per language
holding the tree-sitter capture queries and the static call/import resolver. To add one:

1. Add `queries/<lang>.py` with the same shape as `queries/python.py` — capture queries for the
   node kinds loom mints (File, Class, Function) and a resolver for calls and imports.
2. Teach `discover_files` in `walk.py` about the file extensions.
3. Add a fixture repository under `tests/fixtures/` and mirror `tests/indexer/test_queries.py`.
4. The cold-equals-incremental test must pass for the new language too; it is the property that
   keeps the graph honest.

Qualnames must follow the existing convention (`path.py::Class/method`) or gate resolution breaks.

## Pull requests

- `pytest tests -q` green, run in a real checkout. Tests that shell out to `loom` or `loom-gate`
  resolve to the installed console scripts, so a copied tree silently skips ten of them.
- `ruff check src tests` clean.
- New behaviour comes with a test that fails without the change.
- Behaviour changes to a frozen surface — the deny templates, the `/gate` wire keys, the DDL,
  `EXCLUDE_DIRS` — need a numbered entry in `docs/BUILD-SPEC.md` §11 DECISIONS-DELTA saying what
  changed and why. Do not edit the frozen text itself.
- Docs are part of the change. If you alter a command, a count, or a check, update the README and
  the page under `docs/` that describes it in the same commit.
- Keep lines to 100 characters. Write docstrings that say *why*; the code already says what.

## Reporting a bug

Open an issue with the output of `loom doctor` and `loom --version`. Those two make almost every
report actionable. Security issues: see [SECURITY.md](SECURITY.md) — please do not open a public
issue.
