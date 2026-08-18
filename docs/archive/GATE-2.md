# GATE-2 — fable freeze gate on BUILD-SPEC.md

Reviewer: freeze gate 2, 2026-08-18.
Scope: `loom/docs/BUILD-SPEC.md` against PLAN-v1.md, GATE-1.md, and the nine extractions.
Method: full read of BUILD-SPEC/PLAN/GATE-1; targeted verification of the load-bearing frozen
artifacts, including **executing** the §3 ID algorithm from the spec text alone and grepping the
spec and extractions for the claims below.

## Overall verdict: **FAIL** — 4 blocking edits, 3 minor edits

The spec is very close: the DDL, ID function, wire contracts, deny templates, and tool shapes are
genuinely frozen, and the M1/M2/M3 whitelists are disjoint. Two coder agents could build
`server/` (M2) from this file alone. They could NOT build `indexer/` (M1) from this file alone —
the "VERBATIM" tree-sitter query strings the spec mandates do not appear anywhere in it, and one
M1 acceptance case contradicts the documented behavior of those very queries. Fix the numbered
edits and this freezes clean.

---

## Per-check verdicts (the question's 1–8)

### 1. DDL verbatim + complete — **PASS**
§2 carries the full literal DDL for all five tables (`nodes`, `edges`, `plans`, `claims`,
`events`) plus every index, all comments resolving type/semantic questions inline
(base36 ID shapes, kind vocabularies, tombstone rule). Connection pragmas are frozen as literal
code (WAL, `busy_timeout=5000`, `synchronous=NORMAL`, Row factory). The transaction law
(`BEGIN IMMEDIATE` around every read-judge-write, no Python locks), the authoritative
active-claim predicate with the LEFT-JOIN orphan rule, and the canonical TTL set
(1800 s / `max(current, now+1800)` / floor 60 / no renew after expiry / lazy sweep at 2×TTL grace)
are each stated exactly once and referenced elsewhere. Nothing for a coder to invent.

### 2. Node-ID function exact — **PASS (executed)**
§3 fully specifies hash (sha256), input (`repo + "\x00" + node_ref(path, qualname)`, UTF-8),
salt boundary (NUL, with the rationale), truncation (`digest()[:5]`), alphabet, encoding algorithm
(divmod base36, left-pad, keep-last-N), and length (8). `LENGTH_TO_BYTES` is consistent with the
plan-ID recipe, and `mint_plan_id`'s entropic loop (lengths 6/7/8 × nonce 0–9, `SELECT 1`, inside
the caller's tx) is frozen. **Verified by execution**: I implemented `encode_base36` /
`beads_hash_id` / `node_id` from the spec text alone; all six golden-vector outputs match
(`bd-vju` … `bd-8r5sr6bm`) and the NUL-salt test (`node_id("a","b/c","") ≠ node_id("a/b","c","")`)
holds. Server-side-only minting and the float-nanoseconds trap are both stated.

### 3. MCP tool success AND conflict shapes frozen — **PASS**
All nine tools (§5.1–5.9) carry literal JSON for success, conflict, and validation returns; the
Conflict object is defined once (with inline `owner_spec_md`) and reused; the conflict rule
(write-write blocks, write-read/read-write warn, shared∧shared never, self-skip) is exact;
registration rules (sync `def`, `-> dict[str, Any]` annotation mandatory, errors-as-data,
`wrong_repo`) are frozen. `renew`'s `{renewed: 0}` typed verdicts and `release`'s owner-only
statuses are complete. One residual ambiguity inside `declare_plan` — see **Edit 4**.

### 4. Hook stdin + templates + fail-open — **PASS with one cross-ref defect (Edit 3)**
§7.1 enumerates every consumed stdin field including the camelCase fallback, the
`name_path_pattern` trap, and the never-cwd-join rule. §7.3's exit table is exact (only 0 and 2;
fail-open = `systemMessage` JSON on stdout + warning line on stderr + exit 0; HTTP timeout frozen
at **1.5 s**, ≈2 s wall, settings `timeout: 5` as discarded backstop). §7.4 freezes
FOREIGN_CLAIM / OUT_OF_SCOPE / NO_PLAN verbatim with the 9000-char cap, comment-strip, truncation
suffix, and the no-override forbidden-substring set tested in both M2 and M3. The fourth template
(unscoped `replace_in_files`) is frozen verbatim too — but in §7.2, while §9.1 claims all four are
"§7.4 verbatim" and homes `UNSCOPED_TMPL` in M2's `claims.py`, which M3's hook may not import.
See **Edit 3**.

### 5. Milestone briefs — **split verdict**
- **Whitelists disjoint: PASS.** M1 (`indexer/**`, `tests/indexer/**`, `tests/fixtures/pyrepo/**`,
  `third_party/LICENSES/**`), M2 (`server/{app-extend,claims,tools}.py`, `tests/server/**`), and
  M3 (`hook/**`, `cli/main.py`, `templates/**`, `tests/hook/**`, `tests/fixtures/pretooluse/**`)
  share no file. M0 delivers every shared module complete before the parallel phase; M2's
  app.py-extend and M3's cli-fill both build on finished M0 files. The cross-module import matrix
  (§9.2) is explicit, and M3's stub-server rule ("M3 NEVER imports M2 code") plus M0-frozen
  `db.py`/`ids.py`/`naming.py` make server/ and indexer/ genuinely parallel-safe at the interface
  level.
- **Runnable acceptance commands: PASS.** Every brief (M0–M4) carries exact absolute-path
  `uv run --directory ... pytest ...` commands plus the env-gotchas block, honoring the sandbox
  and no-`timeout`-binary constraints.
- **Self-contained: FAIL for M1.** §9.1 mandates the FalkorDB query strings "VERBATIM" and names
  the five constants, but the strings themselves appear nowhere in BUILD-SPEC — they exist only
  in `extractions/falkordb.md` §2.3 (verified: lines 111–143), which the spec's own header
  declares "provenance, not required reading." A compliant M1 coder cannot write
  `queries/python.py`. See **Edit 1**. (Minor same-shape issue: `_closest` ranking for
  `resolve_nodes` suggestions is referenced but defined only in specgate.md — **Edit 5**.)
  Additionally one M1 acceptance case contradicts the frozen queries — see **Edit 2**.

### 6. MVP cuts actually absent — **PASS (grepped)**
`grep -in "impact|waitlist|rename|webhook|sqlalchemy"` over BUILD-SPEC: every hit is either the
§10 cut list itself, a §11 delta explaining the deferral, the `sig_hash`/free-TEXT-kind migration
insurance (explicitly v2-flag-off), or Serena's `rename_symbol` tool name (unrelated). No cut
feature is specified as MVP work anywhere in §5–§9. `LOOM_ARM` in M4 is spec'd-not-exercised and
declared so. Clean.

### 7. 12 h at the line budgets — **PASS with one flagged budget (Edit 6)**
server <700 (db ~60 + ids ~50 + claims ~280 + tools ~130 + app ~90) and hook <180 (gate ~90 +
locator ~70) are realistic. indexer <300 is tight but the spec pre-cut the expensive feature
(assignment-type inference) explicitly to fit. cli <150 is the outlier: `init` alone
(settings.json read-modify-write with idempotent merge, CLAUDE.md marker-append, config.toml
write, /health ping, synthetic-payload gate verification, bypass-note print) plus five more verbs
plus the beads CLI conventions plausibly exceeds 150 non-blank lines. See **Edit 6**.

### 8. tree-sitter API pinned + probe — **PASS**
Pin `>=0.25.2`, API style verified on 0.25.2 AND 0.26.0 (`Query(language, src)` +
`QueryCursor(q).captures/.matches`, never `Language.query()`, multi-capture → `matches()` only),
and M0's acceptance includes the literal 3-line probe command with a STOP-and-report instruction
on failure. Belt and braces as promised.

---

## Numbered required edits

**Blocking (the FAIL):**

1. **Inline the five FalkorDB query strings (M1 is unbuildable without them).** §9.1 requires
   `_QUERY_TOP_LEVEL_FUNC`, `_QUERY_TOP_LEVEL_CLASS`, `_QUERY_CLASS_METHODS`, `_QUERY_IMPORT`,
   `_QUERY_IMPORT_FROM` copied VERBATIM, but their text lives only in `extractions/falkordb.md`
   §2.3 while BUILD-SPEC's header says the extractions are "provenance, not required reading."
   Either paste the five strings (≈30 lines) into §9.1 or the M1 brief, or amend the
   self-containment claim to name `falkordb.md §2.3` as REQUIRED reading for M1 (and state
   whether `_QUERY_TOP_LEVEL_ASSIGN` is in or out — §9.1 currently omits it silently).

2. **Resolve the block-nested-definition gap (M1 acceptance is self-contradictory).** The M1
   brief requires "same-name method twins under `if TYPE_CHECKING` (second gets `[1]`)" in the
   fixture and asserts the EXACT node set — but the frozen `_QUERY_CLASS_METHODS` anchors to
   direct children of the class `body: (block …)` and, per falkordb.md's own verified note,
   "methods inside `if TYPE_CHECKING:` blocks are missed." As written, the `[1]` twin is never
   minted and the test cannot pass. Root gap: the spec never states how definitions nested in
   block statements (`if`/`try`/`with`) inside a module or class body are discovered and
   qualnamed — which also leaves §4's ancestor-chain rule ambiguous for a def under an `if`
   inside a class, and breaks §4's promise that indexer and stdlib-`ast` locator "apply the same
   rule" (the AST visitor will see guarded defs the anchored queries miss). State the rule ONCE
   (e.g. "entity discovery recurses through block statements; only function bodies stop
   descent"), then make the queries/walk and the fixture case consistent with it — or drop the
   twin case from M1 and the `[1]` machinery to "expect zero, locator-side only."

3. **Fix the UNSCOPED_TMPL home and cross-reference.** §9.1 declares four templates
   "(§7.4 verbatim)" but §7.4 contains three; the unscoped `replace_in_files` text is in §7.2.
   Move the verbatim unscoped message into §7.4 (or fix the cross-ref), and state explicitly that
   this one template is emitted HOOK-side (`locator.py`, DENY_LOCAL) and therefore duplicated as
   a literal in M3 — `UNSCOPED_TMPL` in M2's `claims.py` is otherwise dead code the server never
   sends (§6 has no unscoped case), and M3 is forbidden from importing `server.*`.

4. **State which claim bucket expanded neighbors land in.** §5.3 expands write targets one hop
   over CALLS, then claims "all-or-nothing," and the success shape has `claimed_write`,
   `claimed_read`, and `expanded_from` — but never says whether one-hop neighbors are claimed as
   write, claimed as read, or merely conflict-checked and not claimed. PLAN §4.2's "claims
   everything" leans write; the gate consequences differ materially (a write-claimed neighbor is
   editable by the owner per §6 step 3 and BLOCKS foreign declares; a read-claimed one warns and
   is NOT editable). M2's implementation, M2's own tests, and M4's demo choreography ("B
   re-declares against non-overlapping targets") all hinge on this. One sentence in §5.3 fixes it.

**Minor (fix while in there):**

5. **Define the suggestions ranking in-spec.** §5.2's "specgate `_closest` ranking" is defined
   only in `extractions/specgate.md:342`. Either inline the rule (e.g.
   `difflib.get_close_matches` over refs, n=5) or mark specgate.md §2.4 required reading for M2.

6. **Revisit the cli 150-line budget.** `init`'s mandatory feature list (§7.5 merge + idempotency
   + gate verification + CLAUDE.md append + config write) plus six verbs plus the beads CLI
   conventions realistically lands 180–230 non-blank lines. Raise the cli budget (~220) or
   explicitly exclude the frozen §7.5 JSON literal from the count; "cut features not correctness"
   has nothing left to cut here since every init step is load-bearing.

7. **State the CONTAINS edge direction.** §2/§9.1 freeze the kind but never say
   `src=container, dst=contained` (or the reverse). Nothing in MVP consumes CONTAINS beyond M1's
   own tests, so this cannot cause cross-agent divergence today, but a frozen schema should not
   leave an edge direction to convention.

---

## What was verified vs read

Executed: §3 golden vector (all six lengths, from spec text alone — all match) and NUL-salt
distinctness. Grepped: MVP-cut terms across BUILD-SPEC (check 6); query-string constants across
BUILD-SPEC vs falkordb.md (edit 1); `_closest` definition site (edit 5); `UNSCOPED` sites
(edit 3); `statusMessage`/`args` settings fields confirmed grounded in hooks-contract.md. Read in
full: BUILD-SPEC.md, PLAN-v1.md, GATE-1.md; targeted sections of falkordb.md, specgate.md,
hooks-contract.md. Taken on trust (bounded, GATE-1 verified them): the mcp 2.0.0 `MCPServer`/
`custom_route` surface, the conduit baseline numbers, and the hooks-ref line citations.

---

# GATE-2 RE-CHECK — after revision 1

Reviewer: freeze gate 2 (fable), 2026-08-18, second pass.
Scope: the revised `BUILD-SPEC.md` (harden pass, 1153 lines) re-checked against PLAN-v1.md,
GATE-1.md, and the extractions. Method: full re-read of BUILD-SPEC + PLAN + GATE-1; **executed**
verification of the two artifacts revision 1 changed most (the §3 ID algorithm re-run from spec
text alone; the five newly-inlined §9.1 query strings compiled with real tree-sitter 0.26.0 and
behavior-tested against the M1 brief's adversarial cases); re-grep of MVP-cut terms; verbatim diff
of the inlined queries against `extractions/falkordb.md` §2.3.

## Overall verdict: **PASS** — 0 blocking edits, 3 advisory notes

All seven round-1 edits are folded in, each traceable in the spec text and none merely cosmetic.
Two coder agents can now build `server/` (M2) and `indexer/` (M1) in parallel from this file plus
their briefs without ever talking.

## Disposition of the round-1 edits

| # | Round-1 edit | Status in revision 1 |
|---|---|---|
| 1 | Inline the five query strings | **FIXED, verified by execution.** §9.1 now carries all five VERBATIM (diffed against falkordb.md §2.3 — identical), with the NOTICE header, `.py`-constants rationale, and `_QUERY_TOP_LEVEL_ASSIGN` explicitly declared OUT with the reason. All five compile via `Query(language, src)` on tree-sitter 0.26.0. |
| 2 | Block-nested-definition gap | **FIXED, verified by execution.** §4 now states the block-statement discovery rule ONCE (ancestor predicate; both engines; discarded defs fall to the narrowest claimable span). The M1 fixture case was inverted to match: guarded method **asserted absent**; the `[1]` twin case moved to top-level module twins, which the queries DO capture. Executed against a fixture: guarded method not even query-matched; both twins matched; decorated `@staticmethod` matched; guarded/function-local imports still captured (per the imports-never-ancestor-filtered rule the M1 IMPORTS case depends on). Self-consistent. |
| 3 | UNSCOPED_TMPL home | **FIXED.** §7.4 now carries the UNSCOPED text verbatim as the fourth template, explicitly hook-local in `locator.py` (M3); §9.1's `claims.py` entry now lists only the three server-side templates; §9.1's `locator.py` entry declares `UNSCOPED_TMPL: str`; M3's forbidden-substring test covers it. No dead code, no forbidden import. |
| 4 | Expanded-neighbor claim bucket | **FIXED.** §5.3 states neighbors are claimed as **WRITE** (land in `claimed_write`, provenance in `expanded_from`, owner-editable per §6 step 3, block foreign write declares; only explicit assumes land in `claimed_read`). M2's tests and M4's demo choreography ("non-overlapping AFTER one-hop expansion — pick disjoint call components") were both updated consistently. |
| 5 | Suggestions ranking | **FIXED.** §5.2 now inlines the full rule (candidate pool, tail split regex, sort key, top-5) — self-contained, implementable without specgate.md. |
| 6 | cli 150-line budget | **FIXED.** Budget raised to 220 (§1 + §11.24), with the delta recorded and a "do not fix this back to 150" guard. |
| 7 | CONTAINS direction | **FIXED.** Frozen in the DDL comment itself: src=container, dst=contained, plus CALLS and IMPORTS directions — better than asked. |

## Per-check verdicts (re-run)

1. **DDL verbatim + complete — PASS.** Unchanged from round 1 except the edge-direction comment
   (edit 7). Still complete: five tables, all indexes, pragmas, transaction law, active-claim
   predicate, canonical TTL set.
2. **Node-ID function exact — PASS (re-executed).** Re-implemented §3 from the revised text alone;
   all six golden-vector outputs match (`bd-vju` … `bd-8r5sr6bm`); NUL-salt distinctness holds.
3. **MCP tool shapes frozen — PASS.** The round-1 residual (edit 4) is resolved; all nine tools
   still carry literal success/conflict/validation JSON; Conflict object single-sourced.
4. **Hook stdin + 4 templates + fail-open — PASS.** The template-home defect (edit 3) is resolved;
   §7.4 now genuinely contains all four verbatim; exit contract (0/2 only), 1.5 s client timeout
   ≈2 s wall, dual-channel fail-open, and the no-override substring set all stand.
5. **Milestone briefs — PASS.** Whitelists still disjoint (M1/M2/M3 share no file; M0 sequential
   predecessor). Acceptance commands runnable, absolute-path, env-gotcha-compliant. M1 is now
   self-contained: the queries live in the spec, the fixture cases are consistent with the frozen
   §4 rule (verified by execution), and falkordb.md is genuinely provenance-only again.
6. **MVP cuts absent — PASS (re-grepped).** `impact|waitlist|rename|webhook|sqlalchemy|force|
   bypass|override` over the revised spec: every hit is the §10 cut list, a §11 delta, migration
   insurance, the no-override defense itself, or Serena's `rename_symbol` tool name. No cut
   feature is specified as MVP work.
7. **12 h at the line budgets — PASS.** cli now 220 (edit 6 accepted); server <700 / indexer <300
   / hook <180 unchanged and realistic given the pre-cut resolver features.
8. **tree-sitter pinned + probe — PASS (independently re-verified).** Pin `>=0.25.2`, M0's 3-line
   probe with STOP-on-failure; this pass compiled and ran the actual frozen queries on 0.26.0 —
   the pinned API surface (`Query(lang, src)` + `QueryCursor.matches`) is real and sufficient.

## Blocking edits: **none.**

## Advisory notes (non-blocking; fix at leisure, none requires re-gating)

1. **§4 ancestor-predicate wording vs the CST.** "Every node between it and the module root is a
   `class_definition` (or its `decorated_definition` wrapper)" — in the tree-sitter CST a `block`
   container node (the class body) ALWAYS intervenes, so a hyper-literal implementation keeps
   nothing. Non-blocking because the M1 fixture assertions triangulate exactly one behavior
   (`Big/sm` present, guarded absent — verified by execution: only skip-`block`-containers,
   disqualify-on-block-*statements* passes both), and the locator's `ast` side has no block nodes
   at all. Suggested reword: "skip `block` container nodes; disqualify on any block statement
   (`if`/`try`/`with`/`for`/`while`/`match`) or function body."
2. **Methodless nested class.** §4 declares classes-nested-in-classes claimable, but query-driven
   discovery only sees a nested class via `_QUERY_CLASS_METHODS`, which requires ≥1 direct method
   — `class Outer: class Inner: x = 1` is never minted. No fixture pins it, M2 cannot import
   `indexer.walk`, and locator/indexer divergence degrades gracefully to the enclosing class via
   longest-prefix — hence non-blocking. One sentence in §4 or §9.1 (minted or not?) closes it.
3. **Stale aside.** §4's "Expect zero of these in the demo" (the `[i]` twins) is now false: the M4
   demo indexes a copy of `tests/fixtures/pyrepo`, which the M1 brief deliberately seeds with
   top-level twins, so `f[1]` will exist in the demo index. Purely cosmetic — the demo
   choreography never touches those symbols.

## What was executed vs read this pass

Executed: §3 golden vector (6/6 match) + NUL-salt test, from the revised spec text alone; all five
§9.1 query strings compiled on tree-sitter 0.26.0 and run against a fixture covering module twins,
`if TYPE_CHECKING:`-guarded method, decorated `async def`, `@staticmethod`, nested closure,
class-body call, five import forms — every M1-brief assertion about query behavior confirmed.
Grepped: MVP-cut + escape-hatch terms; UNSCOPED/template homes; forbidden substrings against all
four frozen templates (clean). Diffed: inlined queries vs falkordb.md §2.3 (verbatim). Read in
full: BUILD-SPEC.md (revised), PLAN-v1.md, GATE-1.md. Taken on trust (unchanged from round 1,
GATE-1 verified them): mcp 2.0.0 `MCPServer`/`custom_route` surface, conduit baseline numbers,
hooks-ref line citations.
