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
- 2026-08-19/20 Break-cycle-4 + backlog closure + REAL two-laptop live fire. (a) BC3-1 origin
  authority model landed (§11.38): claims carry origin target|expanded; expansion-acquired
  container claims authorize/contend on their own node only; rescope-naming promotes; fuzzer
  runs the one-writer law unconditionally. (b) 13-agent break-cycle-4: 11 findings, 8
  adversarially confirmed, 0 killed, ALL fixed (§11.40–44: origin visible on every read
  surface, SQLITE_BUSY as data, BOM hook fix, loom never indexes its own db, self-held
  warning, dead-code removals). (c) BC3-2 chunked indexing (§11.45) — the whole-rebuild write
  lock is gone. (d) BC3-3/4/6 closed (§11.46: `error` wire case, init symlink guard,
  executable dashboard test). 360 tests. (e) LIVE FIRE, two physical machines, two humans:
  macOS hosted `loom serve` (token) at 192.168.1.2:8790; a Linux laptop cloned from GitHub,
  self-verified (360 tests), initialized as `linuxbob`, and both users pasted the same task
  into Claude Code simultaneously. Timeline: 20:48:37 linuxbob declared lm-i8nccb → 20:48:49
  its edit allowed in_plan → 20:51:49 akash declare REFUSED conflict (spec embedded) →
  20:52:15 akash tried the edit anyway, hook denied foreign_claim exit 2. Zero uncoordinated
  writes; the CALLS-swept `helper` claim rendered as `expanded` in `loom ls`, get_plan and the
  dashboard tooltip exactly as §11.40 specifies. Screenshot: dashboard-two-laptops.png.
  Operational note proven live: the dashboard reads OPEN servers only (§5a "for now"), so the
  demo server was restarted tokenless — now a P1 readiness item (see PRODUCTION-READINESS.md).
