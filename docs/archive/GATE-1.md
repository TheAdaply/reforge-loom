# GATE-1 — skeptic review of PLAN-v1 extractions

Reviewer: skeptic gate, 2026-08-18.
Scope: PLAN-v1.md + all 9 files in `docs/extractions/` (now `docs/archive/extractions/`).
Method: full read of every file, plus sampled verification of load-bearing claims against the
actual sources (saved doc fetch, vendor clones, specgate checkout, installed mcp 2.0.0 SDK).

## Overall verdict: **PASS-WITH-FIXES**

All nine extractions are COMPLETE. No file is THIN or WRONG. The fixes below are (1) one
hard-check blemish in agent-mail, and (2) five cross-file contradictions that the harden agent
must resolve to a single decision each — the individual files are internally sound but disagree
with each other on load-bearing conventions.

---

## Hard-check results

| Check | Result | Evidence |
|---|---|---|
| (a) hooks-contract quotes OFFICIAL docs for stdin JSON, exit-2, matcher wiring | **PASS** | Doc is grounded in a saved 273,639-byte fetch of `code.claude.com/docs/en/hooks.md` (`scratchpad/hooks-ref.md`, verified present). Sampled 10 cited line refs against the saved copy — all match: exit-2 headline `:756`, exit-2-overrides-JSON `:775`, stderr-as-denial-reason `:1719`, 10k output cap `:885`, MCP `.*` requirement `:361`, matcher-semantics table `:288-290`, common-input-fields anchor `:708-712`, verbatim stdin example `:733+`, PreToolUse-specific fields `:1526`. `grep -c MultiEdit hooks-ref.md` → 0, confirming CORRECTIONS §5.1. Not recalled knowledge. |
| (b) agent-mail zero verbatim code + rider recorded | **PASS with one named blemish** | Rider recorded correctly: clone `LICENSE` header is literally "MIT License (with OpenAI/Anthropic Rider), Copyright (c) 2026 Jeffrey Emanuel", and the rider's "use" definition names "benchmarking" (verified `LICENSE:36`). Pseudocode is written in loom vocabulary and does not mirror upstream structure — EXCEPT one line: the git argv list `["git","diff","--cached","--name-only","-z","--diff-filter=ACMRDTU"]` in §2.4 is token-identical to upstream `guard.py:277`. A git invocation is a functional command spelling rather than copyable expression, so no code is being relied on here — but the document should say so plainly instead of asserting "zero verbatim" unqualified. See Fix 1. |
| (c) every file has a LICENSE section | **PASS** | All 9 files carry a §1 LICENSE with the restriction-that-matters stated. Spot-verified against sources: agent-mail rider ✓, beads/falkordb/serena/spec-kit plain MIT ✓ (`spec-kit/LICENSE` = "MIT License / Copyright GitHub, Inc."), specgate no-LICENSE-but-ours ✓ (clone root has none), conduit no-license/all-rights-reserved ✓, hooks-contract clone unlicensed (Cargo.toml license field commented out) ✓, papers arXiv nonexclusive-distrib ✓. |
| (d) conduit-verify exact qualnames for both pairs + test-run result | **PASS** | Pair 1: `src/conduit/api/routes/auth.py::login` (verified L131 signature matches file), `src/conduit/core/security.py::decode_jwt_token` (verified L50), `core/middleware.py::TenantMiddleware.dispatch`. Pair 2 as planned (comment model) proven nonexistent (grep verified — no `class Comment` in src) and replaced with a fully-qualnamed Document editing-vs-retention pair. Test run: real output tail quoted, baseline 1039 passed / 4 failed with root-caused deselect list and a canonical eval command; explicit fallback (`gothinkster/django-realworld-example-app`) also given. |
| (e) specgate quotes real MCPServer surface, not FastMCP | **PASS** | Verified against the actual checkout and installed SDK: `specgate/src/specgate/server.py:25` is `from mcp.server import MCPServer`; installed `mcp/server/__init__.py:4,7` re-exports `MCPServer`; `class MCPServer(Generic[LifespanResultT])` at `mcp/server/mcpserver/server.py:147`; no FastMCP in the package. Extraction quotes the ctor, `@mcp.tool()`, `run(transport="streamable-http")`, and the `/mcp` default path with SDK line refs. |

---

## Per-file verdicts

### hooks-contract.md — **COMPLETE**
The strongest file. Every normative statement carries a `hooks-ref.md:NNN` citation into a saved
copy of the official reference; 10 sampled citations all verified accurate. Correctly catches:
MultiEdit removed from the documented surface, settings-timeout output-discard trap (loud warning
must come from gate.py's own client timeout), 10k-char deny cap vs "embed the full spec", exit-1
fails open silently, subagent fan-out. Patterns from the unlicensed Rust clone are restated, not
copied. Gap: none. (Its Serena matcher block conflicts with serena.md — see Fix 4; its deny
transport decision conflicts with serena.md C2 — see Fix 3.)

### agent-mail.md — **COMPLETE** (one blemish, Fix 1)
Rider captured in full including the benchmarking/eval-harness clause and its operational
consequences (never vendor, never eval-target, delete clone). TTL/renewal/sweep semantics,
advisory-grant envelope, deny anatomy, and guard installer discipline all restated with file:line
provenance. Pseudocode is loom-native. Blemish: one token-identical git argv line (check b).
Secondary nit: the composed deny message names `LOOM_BYPASS=1` — contradicts beads' anti-steal
lesson (Fix 5). Guard pseudocode's `len(p) > 2` token filter would drop 1–2-char filenames (Fix 8).

### beads.md — **COMPLETE**
Golden ID test vector ported and *executed* during extraction (float-nanos trap documented from an
actual failure). Correctly identifies the live vs dead `GenerateHashID`, the claim CAS shape,
rows-affected-zero-as-verdict, the wy-yuclk refusal-copy safety lesson, and four factual plan
errors (repo URL steveyegge not gastownhall — verified against `go.mod:1`; the status-set names;
hash-doesn't-prevent-collisions; two of four CLI verbs nonexistent). ADAPT table cleanly splits
deterministic node IDs from entropic plan IDs. Gap: its TTL defaults (1800s/heartbeat-reset)
disagree with agent-mail's (3600s/extend) — see Fix 6.

### falkordb.md — **COMPLETE**
Queries verified by execution against tree-sitter 0.25.2 and 0.26.0 with capture output quoted;
catches the `Language.query()` removal in 0.26 inside upstream's own version pin, the
metaclass= base-class leak, nested-call double attribution (directly load-bearing for one-hop
claim expansion), `matches()` vs `captures()` alignment trap, and the fact that no incremental
path exists upstream (M1 must be costed as original work). MIT verified; copy-permitted status
correctly distinguished from patterns-only sources. Gap: ADAPT §3.4 walk pseudocode builds dotted
`prefix.name` qualnames citing the stale `::Class.method` convention — see Fix 2.

### serena.md — **COMPLETE**
Headline correction is right and verified: `NAME_PATH_SEP = "/"` at `symbol.py:26` (checked in
clone); the plan's `relative/path.py::Class.method` "Serena convention" does not exist in the
source. Full edit-tool matcher list with exact param names including the `name_path_pattern` trap
on `safe_delete_symbol`; discovers `serena/hooks.py` as a second production hook reference; MCP
prefix correctly shown to be user-minted. Gaps: its C2 (JSON-first deny) and ADAPT 5 (suffix-regex
matcher) contradict hooks-contract — see Fixes 3 and 4.

### spec-kit.md — **COMPLETE**
License verified (MIT, GitHub Inc.); `memory/` absence verified in clone. Honest headline: the
five loom spec fields are NOT in spec-kit — what transfers is fill discipline. The delivered
40-line template with the token-tax rationale (spec injected into deny messages) and the
declare-time validator are original, useful work. Gap: template comment line states node IDs as
`relative/path.py::Class.method` — the stale convention serena.md refutes; see Fix 2.

### papers.md — **COMPLETE** (with a provenance caveat)
CodePlan 16-row rules table restated with the three structural lessons (body edits impact nothing
absent escape; D and D′ both needed; relation travels with the affected block); grite's three
metrics kept separate and the plan's "wasted-work share" correctly re-attributed as loom's own
composite; the A′ claims-only arm recommendation is the single most valuable eval finding.
License handling correct for both papers (arXiv nonexclusive ≠ open content; grite code repo
license unread → flagged, not used). Caveat: only file whose claims could not be sampled this
pass (web-only, no saved fetch); risk contained because the doc itself forbids citing grite's
numbers as loom's and the tables land in v2-flag-off code.

### specgate.md — **COMPLETE**
Verified check (e) — see table. Also verified the source is genuinely ours (pyproject authors =
Akash) and unlicensed-but-owned, correctly marked as the only verbatim-copy source. The
`_gate_lock` → `BEGIN IMMEDIATE` supersession, `busy_timeout` requirement, structured-output
annotation trap, uv_build src-layout incompatibility with PLAN §3, and the 10ms-hook-budget risk
(MCP handshake per PreToolUse process) are all real findings with SDK line refs. Gap: §2.4 repeats
the stale "Serena's `::Class.method` convention" line — see Fix 2.

### conduit-verify.md — **COMPLETE**
Verified check (d) — see table. Kills the RealWorld false premise with grep evidence, supplies a
verified substitute pair, pins the non-green baseline as a first-class harness concept
(`baseline_failures` + pre-flight green assertion), and delivers the M1 20-call-site checklist
with adversarial cases (function-local imports, TYPE_CHECKING guards, Depends() default-arg call
sites). License risk (no LICENSE, all-rights-reserved) correctly handled: local clone path, no
submodule, action item to add MIT upstream.

---

## Numbered fix list for the harden agent

Fixes 2–6 are cross-file contradictions: in each, pick the named winner (or overrule with
reasons), then amend every listed site so the extraction set speaks with one voice.

1. **agent-mail §2.4 — state the one shared line accurately (hard-check b blemish).** The
   subprocess argv `["git","diff","--cached","--name-only","-z","--diff-filter=ACMRDTU"]` matches
   upstream `guard.py:277` token for token. Replace the pseudocode with prose ("staged names,
   NUL-separated, filter ACMRDTU") and say plainly in the document that this is the ordinary
   spelling of a git invocation rather than borrowed expression. Do not reword the document's
   summary to make a claim true by rephrasing; describe what is actually shared.

2. **Qualname convention — one canonical spelling (blocks M1/M3).** serena.md C1 proves the
   plan's `relative/path.py::Class.method` is not Serena's convention; Serena's within-file
   separator is `/`. Winner: serena.md — loom canonical form `path/to/file.py::Class/method`
   (`::` joiner is loom's own, `/` inside the symbol part hands straight to Serena's `name_path`).
   Amend the stale dotted-form sites: spec-kit template header comment (§3 THE TEMPLATE),
   specgate.md §2.4 (the "matches Serena's convention" sentence — `collect_qualnames`' dotted
   output must be converted at the naming layer), falkordb.md ADAPT §3.4 (walk pseudocode builds
   `prefix.name`), conduit-verify.md §2.3 qualname tables (e.g. `DocumentParser._resolve_ref` →
   `DocumentParser/_resolve_ref`), and PLAN §2's Serena bullet itself.

3. **Deny transport — exit-2 vs JSON-first (blocks M3 acceptance wording).** hooks-contract §2.5
   (exit 2 + stderr primary; JSON only for fail-open) vs serena.md C2 (JSON `permissionDecision`
   first, exit-2 fallback) amend M3's acceptance test incompatibly. Winner: hooks-contract —
   PLAN §1 commits to exit-2, exit 2 cannot be overridden by a competing hook's `allow`
   (hooks-ref `:775`), and `:1719` shows stderr reaches the model identically to a deny reason.
   Demote serena.md C2 to a noted alternative; keep its `additionalContext` idea as optional
   enrichment, not the primary channel. M3 test asserts exit codes + stderr per hooks-contract.

4. **Serena matcher wiring — hardcoded prefix vs suffix regex.** hooks-contract §2.3 writes
   `"matcher": "mcp__serena__.*"`; serena.md C4 proves the `serena` key is user-minted at
   `claude mcp add` time (and hooks-contract's own §5.6 concedes the plugin-prefix case breaks
   it). Winner: serena.md — matcher `mcp__.*__(replace_symbol_body|insert_after_symbol|...)`
   (full tool list in serena.md §2.3/ADAPT 5) plus in-gate re-derivation from the suffix after
   the last `__`. Amend hooks-contract's settings block and its §3.1.

5. **Escape hatch named in deny copy — safety contradiction.** agent-mail §2.3 rule 6 mandates
   naming `LOOM_BYPASS=1` in the deny message (and the guard pseudocode's "Next:" line names it
   too); beads §2.2.3/C6 (the wy-yuclk production incident) mandates the opposite: a deny message
   must never name an override/force path, and beads ADAPT #15 puts a unit test on it. Winner:
   beads — no bypass named in any agent-facing deny surface; document `LOOM_BYPASS` in human docs
   and `loom init` output only, keep the audited-events requirement. Amend agent-mail §2.3
   (rule 6 + the composed message + guard pseudocode) and keep beads' no-override assertion in
   the M3 gate test.

6. **TTL defaults — two numbers sets.** agent-mail §2.1 adopts 3600s issue / 1800s extend /
   `max(now, expires)+extend` renewal; beads ADAPT #4 adopts 1800s TTL / heartbeat resets to
   `now+TTL` / 2×TTL sweep grace, with implicit renewal on every `check()`. Pick one set and
   record it in both files. Recommendation: TTL 1800s, implicit renew-on-check resetting to
   `now+TTL` (beads' shape — simplest, and check() frequency makes extend-arithmetic moot), keep
   agent-mail's 60s floor and cannot-renew-after-expiry rule, sweep grace 2×TTL.

7. **grite code repo license — keep the tripwire visible.** papers.md correctly bans copying from
   `github.com/neul-labs/grite` until its LICENSE is read. Carry this into the harden pass as an
   explicit backlog item (assign to github-miner) so it does not silently become "assumed MIT".

8. **agent-mail §2.4 guard pseudocode — token-filter heuristic.** `len(p) > 2` (meant to drop
   `--name-status` status tokens) also drops legitimate 1–2-character filenames. Low priority
   (pseudocode, guard is optional/secondary), but annotate it so the implementer parses the
   `-z` name-status format properly instead of length-filtering.

9. **papers.md — add a provenance caveat line.** State in the doc header that the CodePlan table
   and grite Table 1 were restated from web reading without a saved fetch, so a later pass (or
   the v2 impact implementer) should re-verify the 16 rows against the paper before turning
   `LOOM_IMPACT` on. No content change required now.

---

## What was verified vs taken on trust

Verified this pass: hooks-ref.md fetch exists at the stated size and 10 sampled citations match;
mcp_agent_mail LICENSE rider text; upstream guard.py argv overlap; beads go.mod module path and
`GenerateHashID` source; serena `NAME_PATH_SEP`; spec-kit LICENSE + absent `memory/`; code-graph
LICENSE; specgate server.py:25 + installed SDK `MCPServer` surface; conduit `login`/
`decode_jwt_token` signatures at the cited lines and absence of any Comment model.

Taken on trust (bounded risk): papers.md numeric tables (see Fix 9); beads/falkordb/serena deep
file:line refs beyond the sampled ones (sampling found zero misses across 10+ probes, and all
four executed-verification claims — beads test vector, falkordb query outputs on two tree-sitter
versions, conduit test tail — are the kind that fail loudly if fabricated).
