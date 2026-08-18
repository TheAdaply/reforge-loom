# LIVEFIRE — real-session acceptance evidence (2026-08-18)

Environment: Claude Code **2.1.234**, `claude -p` headless sessions, `--permission-mode acceptEdits`,
`--mcp-config .mcp.json --strict-mcp-config`, per-session identity via `LOOM_AGENT`/`LOOM_CONFIG`
(added this session). Rig: fixture pyrepo served by `loom serve` (indexed at boot: 8 files,
32 nodes, 51 edges); separate git clones per agent, mirroring two machines. All transcripts under
the session scratchpad `livefire/` dir; server decisions in the events table quoted below.

## Probe (1 session) — PASS

Fresh session, CLAUDE.md protocol only: `resolve_nodes` → `declare_plan` (template-filled spec;
granted, one-hop expansion claimed 3 nodes) → edit ALLOWED by the live gate → `release` (done).
Proves: hooks fire in `-p` mode; `.mcp.json` (this session's init fix) loads non-interactively;
the protocol is followable end-to-end by a real session with zero human help.

## Arm B (loom): alice(validate authenticate) vs bob(cache authenticate) — PASS

Server event log, verbatim ordering:

```
indexer|indexed|repo: 8 files, 32 nodes, 51 edges, 8 changed
bob  |declared|lm-g0i22g
bob  |denied  |out_of_scope n-dz4vtqb5      <- file-level import drift
bob  |rescoped|lm-g0i22g                    <- protocol step 5, unprompted
alice|denied  |declare conflict             <- THE COLLISION: write-write, bob's spec embedded
bob  |allowed |in_plan n-dz4vtqb5
bob  |allowed |in_plan n-9xnjrbhi
bob  |released|lm-g0i22g
alice|declared|lm-7t5glj                    <- re-declared AFTER release; granted
alice|allowed |in_plan n-9xnjrbhi
alice|released|lm-7t5glj
```

- Bob declared first; his one-hop expansion claimed `authenticate` + call neighbors.
- **Alice's declare was refused with bob's full spec embedded.** Her transcript: read his plan,
  designed her validation to compose with his future cache ("so invalid emails can never be served
  from a cache path"), built a watcher, waited for his release, re-declared, edited, released — all
  autonomously, from the deny message and CLAUDE.md alone.
- Zero simultaneous edits to the shared function; every edit passed through the live gate.
- Bob also exercised drift-rescope live (needed a top-level `import time` → out_of_scope on the
  file node → `rescope` → allowed).

Merge (separate clones, common base): **1 conflict hunk** in `svc.py`, both insertions at method
top. Resolution direction was **pre-declared by alice**; mechanical union (validation lines, then
bob's full cache incl. `import time` and the store-tail) → behavioral test **ALL PASS** (4 invalid
inputs raise; cache dedupes to 1 underlying call). Note: the first union attempt (orchestrator's)
dropped bob's import/tail and failed the test — evidence that human merge-resolution DOES botch
unions, and that the pre-declared ordering is what made the correct resolution obvious.

## Arm A (no loom): same tasks, same base, no coordination

Both sessions edited blind; merge produced **1 conflict hunk** in the same region; naive file-order
union (cache-check first, validation second) **also passed** the behavioral test on this pair —
with the caveat that its ordering safety is accidental (validation is skipped for cache hits; holds
only while nothing ever caches an invalid entry), whereas arm B's ordering was designed.

## Metrics (shipped `loom.eval.metrics`, true merge-base, pyc excluded)

| | armB (loom) | armA (none) |
|---|---|---|
| conflict hunks at merge (svc.py) | 1 | 1 |
| overlap/conflict lines | 6 | 3 |
| wasted_work_share | 0.46 | 0.25 |
| post-merge behavioral test | PASS (designed ordering) | PASS (accidental ordering) |

**Honest reading:** static hunk metrics measure textual adjacency, not coordination, and on this
compose-friendly pair they slightly favor the uncoordinated arm (bob's cache block is simply
bigger). What separated the arms is in the process record: the refusal-with-spec, the autonomous
replan-and-wait, serialized edits, the audit trail, and a merge whose correct resolution was
pre-declared rather than guessed. The task pair that should separate the arms on *outcome* metrics
(an interface change vs a new caller — the founding cross-file example) is the next eval; no
outcome-superiority number is claimed from this run. n=1 per arm; same model both arms.

## Environment facts for reruns

`--dangerously-skip-permissions` was never used (it can bypass hooks); `acceptEdits` only. MCP
approval handled via `--mcp-config` + `--strict-mcp-config` (no interactive trust prompt in -p).
Sessions in the same OS user distinguished via `LOOM_AGENT` env (gate override added + tested).
