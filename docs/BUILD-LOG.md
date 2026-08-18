- 2026-08-19 Council + bench closure fixes (orchestrator, post-workflows): W2 second half —
  CALLS-expansion-swept CONTAINER nodes now scope UP-ONLY in conflict judgement (explicit
  container targets still down-close); sibling functions no longer refuse each other via a
  shared class/file (twice-confirmed repro now a regression test, plus the explicit-file-target
  counter-test). B1 — ANALYZE after every index run (kills the judge-reproduced 2,402x
  declare_plan cliff at django-tests scale). B2 — every file gets a File node (file-level claims
  and gating now work for ANY language; symbols remain Python-only; >2MB files skipped,
  documented). W5 — tests/ (+frontend/alembic) no longer excluded from indexing: test trees are
  gated like all source. 331 tests. Bench numbers in research/benchmarks.md are pre-these-fixes
  where flagged; claim-count and language rows superseded by this commit's behavior.
