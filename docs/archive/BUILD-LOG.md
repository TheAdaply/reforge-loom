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
- 2026-08-18 Iteration 2 complete (a055e8a + 9969a77): opt-in token auth (18/18 live-fire,
  ambient-canary suite run), /state totals+truncated, fabric focus mode (verified by executing
  the page JS on real payloads). 298 tests. Mid-iteration: an accidental SendMessage-resume fork
  of the server coder was detected by the fork itself (clean hold, zero clobber) — the loom
  founding failure mode, lived inside loom's own build; fork's findings folded into spec §5a.
- 2026-08-18 Orchestrator tryout fix: resolve_query gains '/'-boundary PATH-suffix matching —
  agents are taught `path.py::qualname` and on deep trees (conduit) that form resolved to zero.
  Live-verified: auth.py::login -> src/conduit/api/routes/auth.py::login; declare via suffix refs
  granted (lm-udebf0). +3 deep-path regression tests incl. negative non-boundary case. 301 tests.
  Focus-mode before/after screenshots committed (docs/dashboard-conduit-{before,focus}.png).
- 2026-08-18 TWO-USER SIMULATION (unscripted, current build): bare origin + two clones, per-clone
  identities via .claude/loom.toml discovery (no env vars), two concurrent `claude -p` sessions.
  Event trace: alice declared/edited/pushed f665a1a/released; bob's colliding declare DENIED with
  her spec embedded; bob read it, designed his cache to sit BEHIND her guard, waited; his eager
  post-release edit was ALSO denied (no_plan) until he re-declared; then declared/edited/pushed
  fd7d034/released. Origin: two stacked commits, ZERO merge conflicts, composed behavior verified
  by bob's own smoke test. Product finding fixed same-hour (ace3cc0): init now gitignores the
  per-user loom.toml — alice's `git add -A` had committed her identity file and bob had to stash
  around it. 302 tests. Screenshot: docs/dashboard-two-users.png.
- 2026-08-19 Prior-art survey of neighbouring tools (graft, graphiti, graphify). Outcome that
  reached the code: each is a **pattern influence only** — no upstream code was taken, and the
  U1/U2/U3 fixes below were re-implemented from loom's own model. What loom took from where is
  recorded publicly in `THIRD_PARTY_NOTICES.md` and `CREDITS.md`; the survey notes themselves are
  not published.
- 2026-08-19 Recon fixes landed (2a5332b, 316 tests): U1 cold≡incremental edge resolution — the
  edge-decay false-ALLOW caveat is FIXED and its docstring deleted (identity test pins it
  forever); U2 staleness-is-a-verdict (/state index_age + dashboard note + doctor WARN); U3
  entropy-gated fuzzy resolve (short/ambiguous tails refuse with suggestions; exact and
  '/'-boundary rungs untouched). Patterns re-implemented, license-clean, THIRD_PARTY_NOTICES
  updated. Follow-up (non-blocking): per-item extraction stubs for graft/graphiti.
