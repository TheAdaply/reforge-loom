# loom — agent instructions

You are working on loom itself, not on a repository loom is gating.

## Run

```bash
uv run --directory <repo-root> pytest tests -q                        # all tests
uv run --directory <repo-root> python -m loom.eval.harness --demo     # the end-to-end demo
uv run --directory <repo-root> ruff check src tests                   # lint
```

`uv run --directory` changes the working directory. Any command that takes a path to *another*
repository needs it spelled absolutely, or `--repo-root "$PWD"` evaluated before the prefix.

## Read in this order

`docs/README.md` (index) → `README.md` → `docs/architecture.md` → `docs/protocol.md` →
`docs/BUILD-SPEC.md` §1–§8.

`docs/BUILD-SPEC.md` is **frozen and historical**: never edit its contract text. Corrections go in
its §11 DECISIONS-DELTA, or in the newest amendment document (`docs/MULTIREPO-SPEC.md`,
`docs/ITERATION-2-SPEC.md`). Where an amendment and the code disagree, the code is right and the
document needs a delta entry.

## Laws no linter enforces

Breaking one of these is a defect, not a style disagreement.

- `src/loom/hook/**` imports the standard library and `loom.indexer.naming` — **never**
  `loom.server.*`, **never** `mcp`. The PreToolUse budget is spent on interpreter startup.
- Every `__init__.py` is empty, except `loom/__init__.py` which holds `__version__` only.
- `loom-gate` exits 0 or 2. Never 1. Every failure path fails **open**.
- All storage SQL lives in `src/loom/server/db.py` and `src/loom/server/claims.py`. The indexer,
  the dashboard reads and the CLI admin verbs carry their own by design; nothing else does.
- No deny message ever names force, bypass, override, or unclaim. There is a test for this.

## Conventions

- `§N` with no document name means `docs/BUILD-SPEC.md`. Amendments are always named
  (`MULTIREPO-SPEC §2`, `ITERATION-2-SPEC §3`). `D1`–`D11` are amendment deltas, `U1`–`U3` the
  recon fixes, `P<n>-<n>` findings in `docs/FINDINGS.md`.
- A node ref is `relative/path.py::Class/method`; a file-level ref is `relative/path.ext`.
- Line length is 100. `ruff` config is in `pyproject.toml`.

## Before you claim done

`pytest tests -q` green, and — if you touched the hook, the CLI or the server routes —
`loom doctor` green in a wired checkout. Tests that shell out to `loom` or `loom-gate` resolve to
the **installed** console scripts, so run the suite in a real checkout, never a copied tree.
