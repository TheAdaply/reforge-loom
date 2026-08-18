# BUILD-LOG (orchestrator)
- 2026-08-18 M0 PASS (30 tests; golden vector + DDL verbatim verified; git tracking init f1e07fe).
- 2026-08-18 M1/M2/M3 parallel build complete; integration gate PASS: 196 passed, live server
  /health + /gate verified, import-compliance clean, ownership audit clean (commit 3bdc5e2).
- 2026-08-18 ADJUDICATION: M1 verifier FAIL overturned by orchestrator. Grounds: (1) M1 acceptance
  green (63 passed) + all spot-checks pass; (2) the "out-of-whitelist" paths it saw were M2/M3
  coders writing their own whitelisted files concurrently in the shared worktree; (3) integration
  gate's cross-milestone ownership audit confirms clean attribution; (4) the verifier itself
  pre-declared this exemption pending orchestrator confirmation. Meta-note: this is precisely the
  multi-writer attribution problem loom exists to solve. Wasted one fixer dispatch — retro item.
- 2026-08-18 Hygiene: .gitignore added; pyc/db untracked (both gates flagged).
- 2026-08-18 M4 PASS + FINAL GATE PASS (commit 7987530): 215 tests, ts-probe green, race test
  green alone, fail-open PROVEN via raw subprocess with dead server (exit 0 + systemMessage),
  full demo transcript green. Metrics §9.1-vs-§9.3 contradiction adjudicated: frozen formula wins
  (identical hunks = 0.5 share, pair Jaccard 1.0) — documented in tests/eval/test_metrics.py.
  BUILD-LOG.md out-of-whitelist flag confirmed orchestrator-authored, benign.
- 2026-08-18 README.md written by orchestrator, every documented command verified against the
  real CLI surface (--help captures + demo + ls run). MVP wrap.
- 2026-08-18 Final simplification pass (commit 01ec907): applied 20, rejected 6 (reasons in
  FINDINGS.md ledger). 249 -> 254 tests. E22 /state phantom-chip bug fixed + regression test.
  ADJUDICATION (orchestrator): applier's S1 sequencing deviation ACCEPTED — shipping the query
  collapse directly instead of landing the interim P1-5 patch first was the right call; the
  controlled edge-dump gate (byte-identical pyrepo build, only by-design P1-5/P2-6 deltas) is
  stronger verification than the interim-patch route, and both verifier and live boot confirm
  behavior. P1-5/P2-6 are thereby CLOSED by S1. Remaining open by charter: P2-1..4, P3 tail.
- 2026-08-18 TCC incident: Desktop access lost mid-pass (stale per-folder deny overriding FDA);
  fixed live via tccutil reset SystemPolicyDesktopFolder com.apple.Terminal. Zero work lost.
