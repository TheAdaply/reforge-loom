# loom red-team FINDINGS — synthesis for the live-fire go/no-go

Synthesized from the five attacker passes under `scratchpad/redteam/`: `findings-gate.md`,
`findings-claimsx.md`, `findings-indexer.md`, `findings-xm.md`, plus the verification gate's
re-run log `verified.md`. A sixth attacker dir (`firstcontact/`) shipped repro scripts and
captured output but **no** findings file; its captures are folded in below (they corroborate
gate-F1 and claimsx-F2 and add two P3s — provenance tagged `firstcontact`).

**Every finding retains its original attacker ID** (gate-Fn / claimsx-Fn / indexer-Fn / xm-Fn)
so it traces back to `verified.md`'s verdict table. Root-cause groups merge sibling IDs.

**Evidence tier (one tag per finding):**
- `[GATE-VERIFIED]` — independently re-run this pass by the verification gate (all P0/P1 + P2
  crash/hang). Tool output matched the attacker's claim.
- `[AUTHOR-PROVEN]` — captured attacker output on file, not re-run by the gate (the P2/P3 tail).

**Severity bar (stated once, applied uniformly).** The attacker files and `verified.md` capped
everything at P1 on the "advisory tool, fail-open by design" rationale. This synthesis draws a
**P0 line at *silent* coordination-off under documented-ordinary usage** — no deny, no
`systemMessage`, no stderr, indistinguishable from a legitimate allow — because that is exactly
the failure the two-user loop cannot survive and cannot detect. Three findings cross that line.
Loud fail-open (a warning fires) stays P1/P2. Escalations from the attacker filing are noted
inline.

---

## 1. CONFIRMED FINDINGS (ranked P0 to P3)

### P0-1 · Path identity is never canonicalized -> silent gate-off on symlinked repos, unmatchable wire paths, false hard-deny outside the repo
IDs: gate-F1 / gate-F2 / gate-F3 · `[GATE-VERIFIED]` · attacker filed P1, **escalated** (gate-F1's
own note flags the P0 for symlinked users).

- **Repro**: `scratchpad/redteam/gate/d_confirm.py` (D0/D1/D2 symlink), `gate/a_identity.py` A1
  (`src/../src/app.py`), `gate/b_edits.py` B7 (`../outside.py`). Corroborated live by
  `firstcontact/attack2.out` HOOK 5: the standard macOS `/var/folders/...` -> `/private/var/...`
  spelling gives `exit=0 stdout='' stderr=''` — SILENTLY UNCHECKED.
- **Root cause**: `src/loom/hook/locator.py:49-56` `_rel()` compares path strings with
  `norm_path` = `str(PurePosixPath(...))`, which neither resolves symlinks nor collapses `..`.
  A repo under a symlinked parent (or `/tmp`, or a symlinked `~/code`) fails the
  `startswith(root + "/")` prefix test -> `locate()` returns PASS -> **no server call, exit 0, audit
  line `"decision":"allow"` with no path** (gate-F1). `src/../src/app.py` passes the prefix test
  but reaches the wire verbatim, matching no indexed node -> server `new_path` allow (gate-F2).
  `<root>/../outside.py` still starts with `root+"/"`, so a non-repo file is gated and, planless,
  hard-denied — directly contradicting §7.2 "path outside `repo_root` -> PASS" (gate-F3).
- **Minimal fix**: in `_rel`, on the **absolute branch only**, `os.path.realpath()` both `path`
  and `root` once before the prefix comparison. `realpath` resolves symlinks (fixes gate-F1),
  collapses `..` inside the repo (gate-F2), and pushes `<root>/../outside.py` out of the prefix so
  it PASSes (gate-F3) — one change closes all three legs. **Do not** realpath the relative-path
  passthrough (`if not path.startswith("/")`): realpath cwd-joins a relative input, violating §7.1
  "`file_path` is always absolute and authoritative — never `cwd`-joined."
- **Regression test**: `tests/hook/test_locator.py`. The test MUST build an **explicit symlink**
  and drive `_rel`/`locate` through it — `pytest tmp_path` is already realpath'd on macOS, which
  is structurally why the existing suite cannot see gate-F1.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/hook/test_locator.py::test_symlinked_repo_spelling_gates_identically` + `::test_dot_dot_alias_is_normalized_before_the_wire` + `::test_dot_dot_escape_out_of_the_repo_passes`** — gate re-run: D1/D2 symlink flipped exit 0→2 vs a denying server (gate-F1), A1/D3 wire path now `src/app.py` not `src/../src/app.py` (gate-F2), B7/A2 `<root>/../outside.py` flipped exit 2→0 with zero server calls (gate-F3); A4 relative passthrough preserved.

### P0-2 · Claims are not hierarchical — a file-level claim covers none of its symbols (silent two-writer clearance + owner locked out of own file)
IDs: claimsx-F1 / xm-F4 · `[GATE-VERIFIED]` · attacker filed P1, **escalated** (the silent
false-ALLOW leg crosses the P0 bar).

- **Repro**: `scratchpad/redteam/claimsx/repro5.py` (+ `repro1.py` F2, `repro4.py` A);
  `scratchpad/redteam/xm/e2_staleness.py` E2.1/E2.3/E2.4.
- **Root cause**: `src/loom/server/claims.py:168-181` `expand_write_targets` walks `CALLS` only;
  the `CONTAINS` edges are never consulted. `resolve_gate_target`
  (`claims.py:152-165`) maps an edit to the *narrowest* node. So `app.py` (File node) and
  `app.py::login` are unrelated ids. Both error directions fire, both silent on the allow side:
  (a) **false ALLOW** — bob declares the whole file `app.py` over aria's live `app.py::login`
  write claim -> `ok:true, warnings:[]`; both are then cleared to write the same bytes. (b) **false
  DENY** — the file-claim owner is denied `out_of_scope` on every symbol-narrowed Edit inside the
  file it declared. This directly guts app.py `INSTRUCTIONS` step 2 ("whole files are
  `relative/path.ext`") and §11.20 ("declared-targets discipline still covers new files via
  file-level refs"). Not an MVP cut: §10 cuts impact/IMPORTS expansion, never containment; §4 only
  says edits *resolve* to the narrowest span.
- **Minimal fix** (localized to `claims.py`): add a bounded `contains_closure(conn, repo, node_id)`
  that walks `edges.kind='CONTAINS'` upward. (1) In `check_node` (`claims.py:380`) match the
  caller's/foreign write claims against the edited node **plus its CONTAINS ancestors**, so a
  container claim covers a contained edit and a foreign container claim blocks it. (2) In
  `find_conflicts` (`claims.py:184`) expand each wanted node to its CONTAINS closure (ancestors +,
  for File/Class nodes, descendants) so a new file-level declare intersects existing symbol claims
  and vice-versa. This is the intended hierarchy, not the cut IMPORTS/impact radius.
- **Regression test**: `tests/server/test_claims.py` (declare/gate hierarchy, both directions);
  add an end-to-end leg in `tests/server/test_gate_endpoint.py`.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/server/test_claims.py::test_a_file_claim_covers_every_symbol_it_contains` (+5 hierarchy siblings incl. `::test_declaring_a_file_conflicts_with_a_live_claim_on_a_symbol_inside_it`) + `tests/server/test_gate_endpoint.py::test_a_file_level_claim_is_hierarchical_end_to_end`** — verified both directions: file-claim owner now `allow in_plan` on every contained symbol, and a file declare over a live symbol claim is refused `conflict` (xm e2_staleness E2.3 end-to-end on a real boot-index). Note: the original `claimsx/repro5.py`/`repro1.py` F2 fixtures mint nodes with ZERO edges, so they cannot observe the edge-based fix; verification re-ran them as-is (unchanged, fixture infidelity) then via edge-faithful copies (`scratchpad/verify-p0/`) whose only delta is the CONTAINS rows `walk.py:138` always mints — both show the fix.

### P0-3 · A second `loom init` clobbers the one global `~/.loom/config.toml` -> silent coordination-off in the first repo
ID: xm-F1 · `[GATE-VERIFIED]` · attacker filed P1, **escalated** (post-clobber the first repo's
gate returns exit 0 with empty stdout AND stderr — not even the loud fail-open path).

- **Repro**: `scratchpad/redteam/xm/e1_identity.py` (E5 step 1/2/3), output `xm/e1-out.txt`.
- **Root cause**: `src/loom/cli/main.py:198-200` `cmd_init` writes `~/.loom/config.toml` with
  `open(..., "w")` — ONE global slot holding `server_url`/`repo`/`repo_root`. A human who runs
  `loom init` for a second repo overwrites the first; every edit in repo #1 is thereafter checked
  against repo #2's server, which answers `allow`/`unindexed` because it does not serve that salt
  (`server/app.py:96-99`). §10 cuts "multi-repo *serve*", not "a human owns two repos."
- **Minimal fix**: key config to the repo. `cmd_init` writes a per-repo config
  (e.g. `~/.loom/config.<repo>.toml`, or `<repo_root>/.loom/config.toml`) and registers the hook
  with that path in `HOOK_ENTRY` so `gate.py:load_config` reads it via the existing `LOOM_CONFIG`
  env override (`gate.py:33`). Each repo's PreToolUse hook then loads its own config; the global
  slot stops being a single point of clobber. (Contained: `HOOK_ENTRY` already carries per-hook
  fields and `load_config` already honors `LOOM_CONFIG`.)
- **Regression test**: `tests/hook/test_cli.py` — `cmd_init` twice for two repo roots, assert each
  repo's registered hook resolves its own `repo`/`server_url`.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/hook/test_cli.py::test_init_for_a_second_repo_does_not_disarm_the_first` + `tests/hook/test_gate.py::test_load_config_prefers_the_per_repo_file_over_the_global_one`** — xm e1_identity E5 step 3 re-run: after the second `loom init` (gamma), bob's edit in the first repo is again exit 2 with alice's claim message (was exit 0, empty stdout AND stderr); `cmd_init` now writes `<repo_root>/.claude/loom.toml` and the gate walks up from the edited file to it before falling back to `~/.loom/config.toml`.

### P1-1 · `check()` silently ALLOWS any `node` string it cannot parse as a path — including a bare symbol name over a live foreign claim
ID: claimsx-F2 · `[GATE-VERIFIED]` · corroborated by `firstcontact/attack1.out` ATTACK 7
(`check(node='authenticate')` and `'AuthService/authenticate'` -> `allow=True case=new_path` while
aria holds the write claim).

- **Repro**: `scratchpad/redteam/claimsx/repro1.py` section F1.
- **Root cause**: `src/loom/server/tools.py:81-86` — `check` treats `node` as an id only when it
  starts `n-` AND exists; otherwise it hands the whole string to `gate_decision` as a **path**,
  where `resolve_gate_target` finds "no node rows for path" -> §6 `new_path` allow. A bare qualname
  (`login`, `AuthService/authenticate`) is resolvable by `resolve_nodes`/§5.2's ladder, so the
  natural agent call `check(agent, "login")` is told "clear to edit" over a live claim. The hook is
  unaffected (it always sends a real path); damage is confined to the agent-facing tool the
  protocol tells agents to trust before editing.
- **Minimal fix**: in `check`, when `node` is not an `n-` id, route it through
  `claims.resolve_query` (the §5.2 ladder) first; a unique hit -> `check_node` on that id; ambiguous
  or unresolvable -> an explicit verdict (`{"allow": false, "case": "unresolved", ...}`), never the
  path-based `new_path` allow. Only fall to `gate_decision`-as-path when the string is an actual
  ref (`path::qual`).
- **Regression test**: `tests/server/test_tools.py`.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/server/test_tools.py::test_check_resolves_a_bare_symbol_name_instead_of_allowing_it_as_a_new_path` + `::test_check_denies_needs_resolution_when_it_cannot_pin_one_node`** — claimsx repro1 F1 re-run: `check(bob, "login")` over aria's live claim now `{'allow': False, 'case': 'foreign_claim'}` (was `allow True, new_path`); an unresolvable/ambiguous string (incl. a bogus `n-` id) now answers `needs_resolution` with candidates instead of the path-ladder allow.

### P1-2 · `loom index` cannot be told the repo salt -> re-indexing after `serve --repo NAME` writes a second, invisible graph
ID: xm-F3 · `[GATE-VERIFIED]`.

- **Repro**: `scratchpad/redteam/xm/e1_identity.py` section E1b, output `xm/e1-out.txt`.
- **Root cause**: `src/loom/cli/main.py:106` `cmd_index` hardcodes `_repo_of(repo_root)` (the
  basename), and the `index` verb (`main.py:301-303`) declares no `--repo` flag — while `serve`
  does (`main.py:295`, honored at `main.py:86`). A team that pinned a stable salt with
  `loom serve --repo teamrepo` (exactly §11.19's remedy for two clones with different basenames)
  has its only re-index path write nodes under the BASENAME salt into the same db; the served graph
  stays permanently stale and new symbols never become claimable for the served salt (they resolve
  to the File node — feeding P0-2).
- **Minimal fix**: add `("--repo", {"default": ""})` to the `index` verb's flag list
  (`main.py:301-303`) and change `cmd_index` to `args.repo or _repo_of(repo_root)`, mirroring
  `cmd_serve` (`main.py:86`).
- **Regression test**: `tests/hook/test_cli.py` (the CLI test home) — `loom index --repo X` writes
  under salt X.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/hook/test_cli.py::test_index_honours_an_explicit_repo_salt`** — xm e1 E1b re-run + the now-possible invocation: `loom index --repo teamrepo --repo-root <alpha> --db <dbC> --changed` reports `{"repo": "teamrepo", ... "nodes": 5}` and writes under the SERVED salt (`[('alpha',5),('teamrepo',5)]`); the post-boot symbol `added_after_boot` is now a real Function node `n-8ox6xnz7` under `teamrepo` (gate resolves the symbol, no longer the File node). The un-flagged invocation still defaults to the basename, unchanged.

### P1-3 · Indexer bare-name CALLS fallback mints edges between unrelated files -> false `foreign_claim` deny
ID: indexer-F1 · `[GATE-VERIFIED]` · **demo-safe** (0 occurrences on conduit — see WHAT HELD).

- **Repro**: `scratchpad/redteam/indexer/build_and_index.py` + `coordination_effects.py` TEST 1.
- **Root cause**: `src/loom/indexer/queries/python.py:225-226` `Resolver._target` ends in a
  **whole-repo** bare-name fallback with no import check — `hits = [p for p,d in self.defs.items()
  if name in d]`. A call to a name that is not a claimable node in the caller (most importantly a
  function nested inside another function, which §4 does not mint) binds to whatever single
  module-level def in the repo shares the name. §5.3 claims one-hop CALLS neighbours as **write**,
  so one bogus edge transfers ownership of an unrelated file's function into an unrelated plan and
  hard-denies the second agent.
- **Minimal fix**: gate the fallback on relevance — resolve only when the single defining file is
  actually reachable from `rel` (present in `rel`'s import table `self.imports[rel]`, i.e. the name
  was imported), otherwise return `None`. Under-claim is the safe direction (§9.1 freezes
  uniqueness, not relevance).
- **Regression test**: `tests/indexer/test_queries.py`.

### P1-4 · A repo module whose name shadows stdlib/third-party captures every call to that module
ID: indexer-F2 · `[GATE-VERIFIED]` · **demo-safe** (conduit has no such shadow).

- **Repro**: `scratchpad/redteam/indexer/build_and_index.py` + `coordination_effects.py` TEST 3.
- **Root cause**: `src/loom/indexer/queries/python.py:180-181` `_module_path` falls back to
  `self.suffix` (a dotted-suffix index over in-repo files) with no check that a plain `import x`
  actually resolves in-repo. `import logging` in a repo containing `shadow/logging.py` binds to
  that file; every `logging.getLogger()` call mints a false `CALLS`/`IMPORTS` edge, so declaring a
  plan on `shadow/logging.py::getLogger` silently also claims the unrelated caller `c.py::boot`.
  Shadowing `logging.py`/`config.py`/`types.py` is common in app repos.
- **Minimal fix**: the `suffix` fallback must not apply to a plain absolute `import x`
  (distinguishable at the call site: `index_file` records plain imports as `(name, None, 0)`,
  `symbol is None`). For a bare `import x`, resolve only via an exact `self.modules` hit (a
  top-level `x.py`/`x/__init__.py` from the repo root); reserve `suffix` for `from`-imports.
- **Regression test**: `tests/indexer/test_queries.py`.

### P1-5 · Classes nested >=2 deep get a wrong qualname; the locator disagrees -> owner denied on their own claimed code
ID: indexer-F3 · `[GATE-VERIFIED]` · **demo-safe** (conduit has 0 nested classes).

- **Repro**: `scratchpad/redteam/indexer/robustness.py` + `nested_class_mismatch.py`.
- **Root cause**: `src/loom/indexer/walk.py:69-100` `_entities`. Nested classes are only discovered
  as a side effect of `_QUERY_CLASS_METHODS` firing for a class whose body *directly* contains a
  function. An intermediate class (`B` in `A->B->C`, body is only another class) is never captured,
  so the "nearest already-assigned ancestor" walk (`walk.py:87-92`) skips it and mints `A/C` where
  the source says `A/B/C`. The hook locator (`locator.py:collect_symbol_spans`, stdlib `ast`) spans
  `A/B/C/z` correctly, so declaring the only ref loom offers (`A/C/z`) then editing -> gate sees
  `A/B/C/z` -> `deny out_of_scope`. Breaks §4's frozen "indexer and locator apply the same rule."
- **Minimal fix**: make every `class_definition` enter `_entities`' candidate set so the `[i]`/
  ancestor walk sees intermediate classes. Add ONE unanchored class capture (e.g.
  `(class_definition name: (identifier) @name) @def`) to the query tuple driving `_entities`
  (`walk.py:78`); `_claimable` (`walk.py:59-66`) already filters block-nested/function-nested
  defs, so the ancestor predicate stays the single authority. Also fixes indexer-F4 below. **Keep
  this strictly separate from the query-collapse simplification candidate (§4) — that is deferred.**
- **Regression test**: `tests/indexer/test_walk.py` (qualname == `A/B/C`), plus an
  indexer-vs-locator agreement assertion in `tests/hook/test_locator.py`.
- **STATUS: FIXED by the final simplification pass, regression
  `tests/indexer/test_walk.py::test_intermediate_and_method_less_nested_classes_are_claimable`
  + `::test_indexer_and_locator_agree_on_nested_qualnames`** — fixed NOT by the one-capture
  patch above but by the §4 candidate-1 query collapse (S1) that supersedes it, which landed
  under an id-independent before/after dump gate; see "Final simplification pass". The
  agreement assertion lives in `test_walk.py` (it needs the indexed `graph` fixture) and
  imports `locator.collect_symbol_spans` rather than sitting in `test_locator.py`.
  `nested_class_mismatch.py` re-run: both sides now say `A/B/C/z`, and the owner's edit on
  their own claimed code is `allow in_plan` (was `deny out_of_scope`).

### P2-1 · No total wall-clock deadline — a slow-drip server stalls every edit for as long as it likes
ID: gate-F4 · `[GATE-VERIFIED]`.

- **Repro**: `scratchpad/redteam/gate/c_failure.py` C3 (1 byte/s body): `exit=0 wall=89.75s`.
- **Root cause**: `src/loom/hook/gate.py:43-49` `call_gate` uses `urllib.request.urlopen(timeout=
  timeout_s)` — a per-socket-operation timeout, not a wall deadline. A server that ACKs then
  dribbles the body keeps the gate alive indefinitely; §7.3 promises ~2 s total. (The settings
  `"timeout": 5` backstop kills the process but discards the `systemMessage`, §11.15 — so the loud
  fail-open channel never fires.)
- **Minimal fix**: wrap the `call_gate` round trip in a hard wall bound — a monotonic
  start/elapsed check or `signal.alarm(2)` on the main thread — that trips the fail-open path,
  **not** another socket timeout. Fail open loudly on trip.
- **Regression test**: `tests/hook/test_gate.py`.
- **STATUS: FIXED `490c642`** — `src/loom/hook/gate.py:174-186` runs the round trip on a daemon
  thread behind a 2.5 s wall deadline that trips the loud fail-open path.

### P2-2 · A 10 MB source file costs ~5.9 s in the hook (past the ~2 s budget and the 5 s backstop) — silent degradation
ID: gate-F5 · `[GATE-VERIFIED]`.

- **Repro**: `gate/d_confirm.py` D4 (10.6 MB -> `exit=2 wall=5.77s`); `gate/c_failure.py` C4
  (5.4 MB -> `exit=0 wall=2.96s`).
- **Root cause**: `src/loom/hook/locator.py:97-109` `_edit` reads the whole file and
  `ast.parse`s it on every Edit with no size guard. 5.4 MB already blows the budget; 10.6 MB
  exceeds the 5 s backstop, so the harness kills the hook and the edit proceeds with no
  `systemMessage`.
- **Minimal fix**: an `os.path.getsize` guard in `_edit` — above a threshold (e.g. 1-2 MB), skip
  the parse and return a file-level `GATE(rel, None)`. Generated/vendored files this size are
  ordinary and file-level gating is the safe degrade.
- **Regression test**: `tests/hook/test_locator.py`.
- **STATUS: FIXED `490c642`** — `src/loom/hook/locator.py:101-112`, `_PARSE_CAP_BYTES = 1_000_000`;
  past the cap the locator skips the parse and degrades to a file-level target.

### P2-3 · `POST /gate` returns HTTP 500 on malformed bodies / non-string qualname (§6: "always HTTP 200")
ID: claimsx-F3 · `[GATE-VERIFIED]`.

- **Repro**: `scratchpad/redteam/claimsx/repro3.py` (real subprocess server): empty body / not-json
  / list / int-qualname / list-qualname all -> HTTP 500.
- **Root cause**: `src/loom/server/app.py:94` `gate_route` does `await request.json()` with no
  guard, and passes `body.get("qualname")` (`app.py:101`) unchecked into `resolve_gate_target` ->
  `prefix_candidates`, which explodes on a non-string. Per §7.3 a non-200 is a hook fail-open, so
  any client drift or truncated request silently no-ops the gate while the server is healthy —
  indistinguishable from "server down."
- **Minimal fix**: in `gate_route`, `try/except` the JSON parse (empty/garbage -> `body = {}`) and
  coerce `qualname` to `str`-or-`None` (`qualname = q if isinstance(q, str) else None`) before the
  call. Always return the five-key 200 body.
- **Regression test**: `tests/server/test_gate_endpoint.py`.
- **STATUS: FIXED** — `gate_route` (`src/loom/server/app.py`) guards the JSON parse, treats a
  non-object body as absent, and coerces a non-string `qualname` to `None`. Regression:
  `tests/server/test_multirepo.py::test_a_malformed_gate_body_still_gets_the_five_frozen_keys`.

### P2-4 · The gate "fast read path" needs the SQLite WRITE lock — a concurrent `BEGIN IMMEDIATE` blocks it past 1.5 s, then raises
ID: claimsx-F4 · `[GATE-VERIFIED]`.

- **Repro**: `scratchpad/redteam/claimsx/repro2.py`: foreign 2 s tx -> gate `2016 ms` (fail-open);
  6 s tx -> gate `5388 ms` then `sqlite3.OperationalError: database is locked`.
- **Root cause**: `src/loom/server/claims.py` — `_decide` (`:371-377`) writes an `events` row on
  EVERY decision and `check_node` (`:389-391`) fires the implicit-renew UPDATE, both in autocommit.
  The path §2 calls a "plain deferred read" is a writer, so it queues on `busy_timeout=5000` behind
  any `declare/rescope/release/sweep` and, past it, raises -> HTTP 500 -> P2-3's fail-open. Trigger is
  a *slow* writer (declare whose `suggestions()` scans a big repo, a large sweep, a slept laptop).
- **Minimal fix**: make the gate read path lock-tolerant — the `events` insert and implicit-renew
  are best-effort bookkeeping, so wrap them to skip-on-`OperationalError` (or defer them) rather
  than let a lock turn a decision into a raise. The *judgement* is a pure read under the
  `ttl_expires > now` filter and needs no write lock.
- **Regression test**: `tests/server/test_concurrency.py`.

### P2-5 · `LOOM_BYPASS=0`, `=false`, `=no` all ENABLE the bypass (silent dead gate)
ID: gate-#6 · `[AUTHOR-PROVEN]` (`gate/c_failure.py` C5).

- **Root cause**: `src/loom/hook/gate.py:107` `os.environ.get("LOOM_BYPASS")` is a truthiness test
  on the string — every non-empty spelling, including the ones a human writes to mean "off",
  disables the gate for that shell; the stderr note is invisible at exit 0 (§11.15).
- **Minimal fix**: parse the value — bypass only when `os.environ.get("LOOM_BYPASS","").strip().
  lower() in {"1","true","yes","on"}`.
- **Regression test**: `tests/hook/test_gate.py`.
- **STATUS: FIXED 2026-08-18 commit pending, regression `tests/hook/test_gate.py::test_loom_bypass_off_spellings_do_not_disable_the_gate` + `::test_loom_bypass_on_spellings_still_bypass`** — gate c_failure C5 re-run: `LOOM_BYPASS=1` bypasses (server_calls=0, audit `bypass`); `0`/`false`/`no` now gate normally (server_calls=1, audit `allow`/`in_plan`). Applied on-set is `{"1","true"}` (narrower than the suggested `{"1","true","yes","on"}`): `yes`/`on` fail CLOSED — the gate runs — which is the safe direction.

### P2-6 · A class nested in a class with no methods of its own is never minted as a node
ID: indexer-F4 · `[AUTHOR-PROVEN]` (`indexer/robustness.py`) · **demo-safe**. Same root cause as
P1-5 (`walk.py:69-100`), single-level form (`class Meta:`/`class Config:`). §4 lists nested classes
as claimable; `resolve_nodes("Meta")` returns nothing so a spec naming it fails `declare_plan` as
`unresolved`. **Fixed by the same P1-5 change** (unanchored class capture). Regression test:
`tests/indexer/test_walk.py`.
**STATUS: FIXED with P1-5 by the final simplification pass (S1)**, regression
`tests/indexer/test_walk.py::test_intermediate_and_method_less_nested_classes_are_claimable` —
re-indexing `redteam/indexer/repoA` adds exactly one node, `nested.py::Outer/Meta`, plus its
CONTAINS edge, and nothing else moves.

### P3 tail (`[AUTHOR-PROVEN]`; captured output on file, not gate-re-run)

| ID | Defect | Root cause (file:line) | Minimal fix | Reg. test |
|---|---|---|---|---|
| indexer-F5 | Deleting a claimed symbol voids the claim on every read surface, then resurrects it on re-create | `walk.py:103-115` `delete_file_nodes` drops nodes, leaves claim rows; `active_claims`/`get_plan`/`check_node` INNER JOIN `nodes` | On re-index, tombstone (set `released`) the orphaned claims instead of leaving them join-invisible; or emit a rename/delete event | `tests/indexer/test_incremental.py` |
| indexer-F6 | `[i]` positional suffix re-targets a live claim when a same-name def is inserted above | `walk.py:94-98` `[i]` by source order; node ids derived from qualname only | On re-index, reconcile `[i]` claims by stored `body_hash`/`sig_hash` (already columns, never consulted) before re-minting | `tests/indexer/test_walk.py` |
| indexer-F7 | Re-exports through `__init__.py` break call resolution -> silent under-claim | `python.py:200-206` `_entry` misses `from .mod import x` re-exports | Follow one hop through `__init__.py`'s own import table when resolving `from pkg import x` | `tests/indexer/test_queries.py` |
| claimsx-F5 | `declare_plan(write_targets=[])` mints a zero-claim plan; later denies misroute `no_plan`->`out_of_scope` | `claims.py:251` declare has no empty-target guard | Reject empty `write_targets` as `validation` (or route the zero-claim deny back to `no_plan`) | `tests/server/test_claims.py` |
| claimsx-F6 | Implicit renew-on-check renews only the matched plan, not all the caller's active plans (§5.4 vs §6) | `claims.py:389-391` UPDATE keys on `mine["pid"]` only | Renew all the agent's active plans in-repo, per §5.4 — or reconcile the spec to §6 | `tests/server/test_claims.py` |
| claimsx-F7 | `check` wrong_repo response omits the `allow` key (§5.4 shape) | `tools.py:78-79` returns `_WRONG_REPO` (`{"ok":false,...}`) with no `allow` | Return `{"allow": false, "case": "wrong_repo", ...}` from `check` | `tests/server/test_tools.py` |
| firstcontact-A | Multibyte spec deny exceeds the 10k hook-output **bytes** with no truncation marker (cap is char-based) | `claims.py:28,71-79` `MAX_DENY_CHARS=9000` measured in chars; CJK spec -> 23.8 kB deny (`firstcontact/attack3.out`) | Cap `compose_foreign_claim` on `len(msg.encode("utf-8"))`, not chars. Harm depends on whether Claude Code's cap is bytes; demo-safe (conduit specs are ASCII) | `tests/server/test_claims.py` |
| firstcontact-B | INSTRUCTIONS step 1 points agents at `templates/spec.md`, unreachable mid-session (repo-root: False, cwd: False) | `server/app.py:31` snippet text; template ships in-package only | Have `loom init` copy `spec.md` into the repo (e.g. `.loom/spec.md`) and reference that path in the snippet | `tests/hook/test_cli.py` |

Also noted (P3, `[AUTHOR-PROVEN]`, gate-#7): a `LOOM_BYPASS` use never reaches the server
`events` table (returns before any HTTP call) and its local audit line is stripped to
`{ts, decision, case}` — teammates never learn an agent bypassed. Fix: post a `bypass` event
before returning, or accept it as local-only and downgrade §7.4's wording. Reg. test:
`tests/hook/test_gate.py`.

---

## 2. UNPROVEN SUSPICIONS (worth a later look; max 5)

1. **Incremental-index edge decay** (indexer-susp-1). `changed_only=True` drops inbound
   CALLS/IMPORTS from unchanged files (`walk.py:3-6` documented caveat); a server that only ever
   re-indexes incrementally monotonically loses expansion edges, so false ALLOWs accumulate until a
   full `index_repo(changed_only=False)` runs. Not driven end-to-end.
2. **tree-sitter/ast disagreement on unparseable files** (indexer-susp-2). Tree-sitter recovers
   from syntax errors and mints nodes while the hook's `ast` locator returns `[]` -> file-level; an
   agent holding a function-level claim in a temporarily broken file would be denied
   `out_of_scope` on their own code. Plausible from code; not run.
3. **`EXCLUDE_DIRS` swallows legitimate packages** (indexer-susp-3). `walk.py:26` matches any dir
   *named* `tests`/`build`/`frontend`/`alembic` at any depth, so a real package `src/app/build/` is
   invisible to the graph and every edit there gates as `new_path` (allow) — an unclaimable hole.
4. **Locator relative-path passthrough vs graph identity** (xm-susp-3). `locator._rel` returns a
   relative tool path unchanged without checking it against `repo_root`; an agent whose cwd is a
   subdirectory could gate a path that means something else in the graph (false allow via
   `new_path`). Not reproduced. (Interacts with P0-1's fix — verify together.)
5. **Cross-repo node-id collision** (xm-susp-1). `check_node`'s foreign-claim query filters
   `p.agent<>?` but not `p.repo=?` (`claims.py:393-395`); with two salts in one db (see P1-2) an
   8-char base36 node-id collision would leak a deny across repos. Needs a crafted collision.

---

## 3. WHAT HELD — attacks that failed (stop re-attacking these)

- **Core two-user serialization.** `BEGIN IMMEDIATE` + WAL + `busy_timeout` hold under real
  process-level racing: 8 concurrent `declare_plan` on one node -> exactly 1 granted, 7 refused;
  20 racing rescopes -> 1 winner, `live_write_claims_on_node=1`; `PRAGMA integrity_check` ok; no
  double-grant, no corruption (xm/e3, claimsx/repro4 B).
- **Hook exit contract.** ~30 hostile payloads all landed on exit 0 or 2, never 1, never a
  traceback (empty stdin, `not json`, `[1,2,3]`, non-string tool fields, directory `file_path`).
- **Server failure fail-open** is correct and loud: HTTP 500, non-JSON body, 3 s delay (TimeoutError
  at wall 1.55 s), missing config — all exit 0 with `systemMessage` on stdout + WARNING on stderr.
- **Edit classification** matched §7.2 exactly (imports/module-scope/empty-old_string/multi-match
  replace_all -> file-level; single-match -> symbol-level); a two-method-spanning edit degrades
  upward to the enclosing class (safe direction), never null.
- **TTL law.** Expiry mid-flight stops honoring at the instant of expiry; no resurrection (renew
  carries `AND ttl_expires > now`); 2xTTL sweep grace never grants authority; orphan-claim LEFT
  JOIN judges dead; release-then-redeclare works (claimsx/repro1 F5-F7).
- **Clock skew** cannot touch TTL — no client timestamp reaches the server; every `now` is
  server-side (xm/e2).
- **Repo salt across two clones with different basenames** works as designed — `loom init` pulls
  the salt from `GET /health` and the minted id matches the server row (§11.19; xm/e1).
- **Indexer precision on the demo repo.** conduit (78 files, 527 nodes, 1116 edges): **0** dangling
  edges, **0** `[i]` nodes, **0** cross-file CALLS without a matching IMPORTS, **0** nested classes
  — the P1-3/P1-4/P1-5 fallbacks do not misfire on the eval target. 5 hand-verified CALLS edges and
  3 hand-verified absences all correct. Malformed/latin-1/zero-byte files index without exception.
- **Name-only over-claim through a variable did NOT happen** (`obj = A() if f else B(); obj.m()`
  mints no edge); `getattr` dispatch, star imports, conditionally-defined funcs, var-aliased funcs,
  decorators that rename — all handled or safely dropped.
- **Spec-conformant, NOT defects** (do not "fix" against frozen spec text; candidate spec changes
  only):
  - **xm-F2 (MISREAD-SPEC)** — a repo-salt mismatch degrading to `unindexed` allow with silent
    stdout is exactly §6 step 2 + §7.3 row 1. The advisory posture is the frozen contract. (The
    *mechanism* is real and it is how P0-1/P0-3 become silent, but the behavior itself is spec'd.)
  - **claimsx-F8** — `rescope`/`renew` carry no `agent`, so any agent can widen/extend another's
    plan. Signatures are frozen (§5.5/§5.8) and identity is caller-asserted in MVP (§5, §11). Works
    as spec'd; candidate hardening for v1.1.
  - **gate-#8** — a local STDIN error reported as "coordination server unreachable." §7.3 mandates
    that single string for ANY exception. Paper cut; candidate copy change.
  - **indexer-susp (rename -> orphaned claim)** is **superseded by CONFIRMED indexer-F5** above — no
    longer a suspicion.

---

## 4. SIMPLIFICATION CANDIDATES (identify-only — NO edits; deferred until after live-fire by orchestrator order)

> **EXECUTED.** This section is the identification pass and is kept verbatim as the record of
> what was proposed. What actually shipped — including which of these were rejected and why —
> is in "Final simplification pass" at the end of this document. Candidate 1 shipped as S1,
> candidate 3 had already shipped as `cli.main._index`, and candidate 2 is now S5 (the
> `state` dict it describes had since shrunk to `{"conn","repo"}`, and S3 removed it entirely).

1. **Collapse the three anchored tree-sitter capture queries into one unanchored def/class capture
   governed by `_claimable`.** `python.py:14-34` defines `_QUERY_TOP_LEVEL_FUNC` /
   `_QUERY_TOP_LEVEL_CLASS` / `_QUERY_CLASS_METHODS`, all module-anchored, but `walk.py:59-66`
   `_claimable` is *already* the real §4 authority (it filters by ancestor chain). The anchoring in
   the queries duplicates that rule and is precisely what drops intermediate classes (P1-5/P2-6).
   Replacing them with one `(function_definition)`/`(class_definition)` unanchored pair + the
   existing `_claimable` filter both simplifies and fixes P1-5/P2-6. **Est. -20 to -30 lines.
   Risk: MEDIUM** (indexer core; requires a full `index_repo(changed_only=False)` re-run of the
   indexer suite + a conduit node-count diff). Note: the P1-5 fix (add one capture) must ship on its
   own first; this is the larger cleanup that supersedes it.
2. **Drop the dead `repo_root` / `db_path` entries from the server `state` dict.**
   `app.py:84` passes `{"conn","repo","repo_root","db_path"}` to `tools.register`, but `register`
   (`tools.py:30-32`) reads only `state["conn"]` and `state["repo"]`; `repo_root`/`db_path` are
   never consumed anywhere downstream. **Est. -2 to -4 lines. Risk: LOW** (pure dead-parameter
   removal; grep-confirmed no reader).
3. **Unify the `cmd_serve` boot-index and `cmd_index` bodies behind one helper.** `main.py:81-97`
   and `main.py:100-108` both do `init_db -> connect -> index_repo -> close -> print(stats)` with only
   the salt source and `changed_only` differing. A shared `_index(db, repo, repo_root, changed)`
   helper removes the duplicated connect/close/stats dance (and is the natural home for the P1-2
   `--repo` plumbing). **Est. -6 to -10 lines. Risk: LOW** (both call sites already local-db only;
   behavior-preserving).

Runners-up (not top 3): fold `cmd_release`'s hand-rolled release SQL (`main.py:269-290`) into a
call to `claims.release` (both implement §5.9; ~-12 lines, MEDIUM risk — CLI carries its own tx);
and `MAX_DENY_CHARS` (`claims.py:28`) vs the §11.18 10k figure is an over-conservative constant, not
dead code — leave until firstcontact-A's byte/char cap is settled.

---

## Final simplification pass

Run against `b640b46` (249 tests green, clean tree), applying §4's candidates plus the
staleness/duplication tail found while re-reading the tree. **Rule: the full suite
(`pytest tests -q`) had to stay green after EVERY candidate**, with a cumulative
`accepted.patch` checkpoint after each — `app.py` is touched by seven of them, so a
per-file revert would have destroyed earlier accepted work. Final: **254 passed**
(249 baseline + 5 new regression tests). `src/` moves **+90 / -93** across 7 files — a net
shrink despite the added explanatory comments, because the deleted code outweighs them;
`tests/` gains **+109 / -5** across 5 files, almost all of it the new regression coverage.

### APPLIED (20 candidates, 19 rows — A1/A2 share one)

| # | Candidate | What changed | Why it is safe |
|---|---|---|---|
| S1 | tree-sitter query collapse | `_QUERY_TOP_LEVEL_FUNC` + `_QUERY_TOP_LEVEL_CLASS` + `_QUERY_CLASS_METHODS` -> one unanchored `Q_DEFS`; `_entities` loses its three-query/two-capture-key loop | See the dedicated gate below — **also closes P1-5 and P2-6** |
| S2 | `INSTRUCTIONS` duplication (= C11) | the §8.2 protocol text is now READ from `templates/CLAUDE.snippet.md` instead of being a second inline copy; added `_template()`, which the dashboard route reuses | the two texts were byte-identical; same file `loom init` already reads at runtime, so no new packaging requirement |
| S3 | `register(mcp, state)` | -> `register(mcp, connection, served)`; the `state` dict had exactly two readers | one call site; the dict was pure indirection |
| S4 | dead `walk.index_file` | deleted | zero callers, zero test imports (`Resolver.index_file` is a different method) |
| S5 | unused `repo_root` | dropped from `build_server`/`serve`; `--repo-root` stays as the default source for `repo` and `db_path` | grep-confirmed unread; 4 call sites updated incl. 2 tests |
| S6 | `_chunks` `or [[]]` + dead guard | both removed | both callers already guarantee a non-empty list (`while frontier`, `if not wanted: return []`) |
| S7 | double `_arm()` | dropped the second application inside `compose_foreign_claim` | `_conflict` mints every owner dict and arms there; `test_claims_only_arm_blanks_the_spec` still passes through that path |
| S8 | `/state` ordered dedupe | hand-rolled `seen` dict -> `list(dict.fromkeys(agents))` | identical ordered-dedupe semantics |
| B7 | `app.py` module docstring | listed only `/health` and `/gate`; now names all four plain-HTTP routes | doc only |
| B8 | `build_server` docstring | now says it also mounts the routes, and names the closed-over state | doc only |
| B9 | CLAUDE.snippet marker | marker claimed "edits here are overwritten on re-init"; `_append_snippet` has never overwritten anything — it appends only when the marker is absent. Text corrected, and the idempotency check moved from an exact-first-line match to the `SNIPPET_MARKER = "<!-- loom protocol v1"` **stable prefix** | the naive text-only fix regresses idempotency: a repo carrying the OLD first line would get a SECOND protocol block on every re-init. Regression: `test_cli.py::test_init_does_not_re_append_over_an_older_marker_wording` + `::test_the_shipped_marker_starts_with_the_stable_prefix` |
| B10 | duplicated repo-salt rule | comments only, both sides | `server.app` importing `cli._repo_of` would invert the §9.2 dependency direction, and `app` is spawned as `python -m loom.server.app` with no CLI |
| C13 | `_ref` duplication | pointer comment; code kept | `cli._ref` takes a ROW and tolerates the NULLs that `ls`/`show`'s LEFT JOINs produce for claims orphaned by a re-index (indexer-F5); `ids.node_ref(None, None)` would print "None" |
| E22 | **real bug** — `/state` grew a phantom `loom` agent chip | new `SYSTEM_ACTORS = ("indexer", "loom")` filters the agent-chip list in the state route | `sweep` logs its `expired` rows as actor `loom`; the chip filter excluded only `indexer`, so the first TTL sweep put a permanent teammate named "loom" on the dashboard. Fixed in the ROUTE, not the event actor — the audit trail must keep saying who expired the plan, and the gate feed still shows the row. Regression: `test_dashboard.py::test_a_ttl_sweep_does_not_grow_a_system_actor_chip` (asserts the chip is gone AND the event is still on the feed) |
| A1/A2 | README `init` | it also registers the server in `.mcp.json`, and writes a per-repo `.claude/loom.toml` (global config is the fallback, post P0-3) | doc only |
| A4 | README "Watching the board" | the one-page dashboard at `/` was missing entirely | doc only |
| A5 | README fail-open timing | "~1.5s" was the socket timeout only; there is now also a 2.5s hard wall deadline (gate-F4) | doc only |
| A6 | README provenance | `docs/FINDINGS.md` was unlisted | doc only |
| A3 | README test count | 217 -> **254**, taken from the final run | doc only |

### S1's extra gate (id-INDEPENDENT dumps + red-team re-run)

Node ids are a hash of `(repo, path, qualname)`, so a qualname change moves the id — the
diffs below are over `(path, qualname, kind)` and over edges joined to `path::qualname`,
never ids. The control is a **pre-S1 package copy** (`git show HEAD:` for the two changed
files) run over the same repos, so incremental-index staleness in the stored red-team
`.db` artifacts cannot be mistaken for an S1 effect.

- `tests/fixtures/pyrepo` (full index): **byte-identical** before/after — 32 nodes, 51
  edges, zero drift. Its nested class `AuthService/Session` has a method, so the old
  anchored queries already found it.
- `redteam/indexer/repoB` (P1-5 repro): `deep.py::A/C` + `A/C/z` -> `A/B` + `A/B/C` +
  `A/B/C/z`, with the CONTAINS chain following. **By design**: the source is
  `class A: class B: class C: def z`, so the old `A/C` named nothing that exists.
- `redteam/indexer/repoA` (P2-6 repro): **+1** node `nested.py::Outer/Meta` (a nested class
  with no methods of its own) and its one CONTAINS edge. **By design.**
- **No CALLS or IMPORTS edge changed anywhere**, and no call site was re-attributed.
- Two edges (`c.py IMPORTS shadow/logging.py`, `c.py::boot CALLS shadow/logging.py::getLogger`)
  appear when diffing against the stored `a.db` but are present in BOTH arms of the
  controlled full-index comparison — they are the documented incremental caveat
  (`coordination_effects.py` rewrites `shadow/logging.py` and re-indexes `changed_only=True`,
  which drops inbound edges from unchanged files), **not** an S1 effect. This is
  unproven-suspicion 1 reproducing, and it is unchanged by this pass.
- `nested_class_mismatch.py` re-run: locator and indexer now return the **same** list
  `['A', 'A/B', 'A/B/C', 'A/B/C/z']`, and the owner's edit on their own claimed code is
  `allow in_plan` — it was `deny out_of_scope`.
- `robustness.py` re-run: malformed / latin-1 / zero-byte files still index without
  exception; the `[i]` duplicate counter (`twinclass.py::A`, `A[1]`) is unchanged.

Because S1 closes two findings, it ships with their named regression tests:
`test_walk.py::test_intermediate_and_method_less_nested_classes_are_claimable` and
`::test_indexer_and_locator_agree_on_nested_qualnames`. **P1-5 and P2-6 above are therefore
FIXED by this pass** — §4 candidate 1 said the one-capture P1-5 patch should land first and
be superseded by this collapse; the collapse landed directly, under the gate above.

### REJECTED / NOT APPLIED (6 ledger rows)

| # | Candidate | Verdict |
|---|---|---|
| S9 | `cmd_release` -> `claims.release` | **REJECTED** — the CLI module docstring documents the SQL duplication as deliberate, to keep cross-module imports inside §9.2's allowance. Reversing a documented architecture decision is an orchestrator call, not an applier's. |
| S10a/b | spec-mandated dead code in `eval/metrics.py` | **SKIPPED** — the candidate text itself requires orchestrator sign-off. |
| C12 | test server-boot fixture consolidation | **DEFERRED** — largest test-side churn of any candidate, no user-visible value, and it would multiply the subprocess-server surface in a finishing pass. |
| D14-D21 | BUILD-SPEC stale-side items | **REPORT ONLY** by reviewer order (the spec is frozen). |
| E23 | annotate the unused `repo_root` | **SUPERSEDED** by S5, which removes the parameter instead of documenting it. |
| E24 | `counts.nodes/edges` report post-LIMIT values | **SKIPPED** — reviewer's bar was "fix if ever surfaced"; it has not surfaced. |

**Not touched, by standing order**: §7.4 deny templates, the §5/§6 wire shapes, and the DDL.
At the close of the simplification pass P2-1..P2-4 and the P3 tail were all open — that pass was
chartered to fix E22 and nothing else. **Since then P2-1, P2-2 and P2-3 have been fixed**; each
carries its own STATUS line above, with the commit or the regression test that pins it. **P2-4
remains open**, as does the P3 tail.

**On the repro paths.** Every `scratchpad/...` path in this document names a file from a red-team
session; those scripts are not published, and the durable artifact of each finding is the named
regression test, not the script that first found it. Read the STATUS line's test to see the
behaviour a finding pins.

---

## Council backlog

Deferred items from the 2026-08-19 ten-dimension quality-council pass. The council's APPLY-NOW set
landed in the "docs+quality" commit; everything below was tagged BACKLOG in its verification record
(internal, not published — same rule as the repro scripts above). Each needs a decision or
non-trivial churn; none is a regression. IDs are the council's.

- **V9(b) — make the spec template reachable from a session.** Copy it to
  `<repo_root>/.claude/loom-spec.md` at `loom init`, or add a `spec_template()` MCP tool, then
  reword `NO_PLAN_TMPL` and the CLAUDE snippet. The template wording is §7.4-frozen and asserted
  as a wire string in `tests/hook/test_gate.py`, so this needs an (a)-vs-(b) decision plus a
  DECISIONS delta.
- **V10 (code half) — configurable `EXCLUDE_DIRS`.** Split the set, drop `tests`/`frontend`/
  `alembic` from the defaults, thread a repeatable `--exclude` flag. Behaviour change (walk cost,
  claim semantics on test files); needs its own tests and a delta. The docs half (README MVP
  limits + troubleshooting) has landed.
- **I23 — dashboard on tokened servers.** `?token=` + sessionStorage or a 401 prompt; today
  `--token` leaves the board on "reconnecting".
- **I24 — doctor UX.** A fourth `SKIP` status (dependent rows currently read as extra failures),
  a one-line verdict, `check_*` extraction.
- **I26 — `docs/RECON-FIXES.md`.** U1/U2/U3 are load-bearing in 13 `src/` sites and defined only
  in a five-line changelog entry.
- **I27 — `docs/DESIGN-NOTES.md`.** The ~20 rationales worth keeping from the archived
  extraction files.
- **I28 — test-rig dedup.** One `live_server` conftest fixture replacing the copied
  `free_port`/`wait_for_port`/boot-teardown rigs across the server tests.
- **I29 — one scope rule, one home.** Hoist a shared `claim_scope(mode='declare'|'enforce')` so
  declare and the gate cite one closure rule; move `check`'s resolution ladder from `tools.py`
  into `claims.check_ref`.
- **I30 — claims/gate hardening.** TTL ceiling clamp (constant choice + §7.4 delta); gate-audit
  rotation; `decide()` returns its record (kills the `_REC` race at the wall deadline); retire the
  fuzzy rung for full `path::qualname` inputs; `suggestions()` ranking in SQL instead of a
  full-table scan.
- **I31 — MCP `health` multi-repo parity.** `repo` param + `repos` list; wire delta D12.
- **I32 — doctor staleness freshness.** `?fresh=1` bypass of the 5s index-age cache for the
  one-shot command.
- **I33 — BUILD-SPEC navigability.** `§`-prefix the headings so `grep '§5.3'` lands on the
  definition, add a §0 TOC, split the milestone briefs out (frozen-doc edit + delta).
- **I34 — readability refactors.** `_owner_query()` builder for the fragment-concatenation sites;
  `Intake` NamedTuple; named resolution `Rung`s; `_epoch` → `db.from_iso`; `fabricSVG` three-way
  split.
- **I35 — demo packaging.** Move the demo out of the wheel or guard its fixture path; a
  `loom-demo` console script; drop the one-flag argparse.
- **I12 (remainder) — re-shoot `docs/dashboard-conduit-focus.png`** against
  `tests/fixtures/pyrepo`; the current capture renders a real repository's symbol layout and is
  embedded nowhere.
- **Release-process decision (V2/V3 rider) — fresh-history publish.** The scrubbed personal
  paths/identifiers and the archived internal records remain readable at commits at or before
  `41dd16e`, and the pre-publish commits — this pass's included — carry session-URL trailers no
  file edit can remove. A squash to one initial public commit, or `git filter-repo`, is the only
  remediation; maintainer's call before any public flip.

---
_Provenance: `scratchpad/redteam/{findings-gate,findings-claimsx,findings-indexer,findings-xm}.md`,
`verified.md`, and `firstcontact/attack{1,2,3}.out`. Spec references are to
`loom/docs/BUILD-SPEC.md`. The red-team synthesis above modified no file under `loom/src` or
`loom/tests`; the final simplification pass, recorded in its own section, did._
