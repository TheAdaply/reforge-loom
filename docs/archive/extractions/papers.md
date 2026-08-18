# Extraction — papers: CodePlan (arXiv:2309.12499) and grite (arXiv:2606.19616)

Scope: (1) CodePlan's edit-classification → affected-relations rules table, documented in full, **lands
in `server/impact.py` as v2, flag OFF in the MVP**; (2) grite's wasted-work metric, definition +
formula + measurement procedure, adapted into a concrete `eval/metrics.py` computation plan over two
agent branches.

Sources are web only (no clone). Provenance below is section/table-level, since arXiv papers have no
`file:line`.

> **Provenance caveat (GATE-1 fix 9).** The CodePlan 16-row rules table (§2.2) and grite Table 1
> (§2.4) were restated from web reading during extraction, with **no saved fetch** of either paper.
> A later pass — at latest, the v2 implementer of `server/impact.py` — must re-verify the 16 rows
> against arXiv:2309.12499 §3 before turning `LOOM_IMPACT` on, and re-verify Table 1 against
> arXiv:2606.19616 before citing its numbers anywhere. Nothing in the loom MVP executes from these
> tables (impact is v2, flag off; the metric definitions in §2.5 are loom's own).

---

## 1. LICENSE

### CodePlan — arXiv:2309.12499
- Title: *CodePlan: Repository-level Coding using LLMs and Planning*.
- Authors: Ramakrishna Bairi, Atharv Sonwane, Aditya Kanade, Vageesh D C, Arun Iyer, Suresh
  Parthasarathy, Sriram Rajamani, B. Ashok, Shashank Shet (Microsoft Research India). Submitted
  2023-09-21.
- License on the abstract page: **arXiv.org perpetual, non-exclusive license**
  (`nonexclusive-distrib/1.0/`). Not CC-BY.
- No code repository is linked from the arXiv abstract page ("Links to Code" is empty).

**Restriction that matters:** the perpetual non-exclusive license grants arXiv the right to
distribute; it grants *us* nothing beyond reading. It is **not** an open-content license. Therefore:

> **Patterns and facts only, no verbatim reproduction of paper prose.** The rules table in §2 below is
> a *restatement* of the paper's classification into our own schema and wording. Factual content
> (which change label implies which relation) is not copyrightable; the paper's sentences are. Do not
> paste paper paragraphs into our repo. Cite as `CodePlan, arXiv:2309.12499, §3 (change may-impact
> analysis)` in a docstring at the top of `server/impact.py`.

### grite — arXiv:2606.19616v1
- Title: *Before the Pull Request: Mining Multi-Agent Coordination*. Presents **grite**, a coordination
  substrate that stores records inside git itself.
- License on the abstract/HTML page: **arXiv.org perpetual non-exclusive license**. Same restriction
  as above — patterns only, no verbatim prose.
- The paper states grite is released as open source at `https://github.com/neul-labs/grite`. The
  paper does **not** state the code license. **Do not vendor or copy any grite code until someone
  opens that repo and reads its LICENSE file.** Everything in this document is derived from the paper
  text, so it is safe to implement from.

---

## 2. ADOPT

### 2.1 CodePlan — dependency-graph relation set (v2 prerequisite)

Provenance: CodePlan §3, dependency graph `D = (N, E)`.

Nodes are: import statements, methods, classes, field declarations, and statements carrying external
dependencies. Edge labels come in inverse pairs:

| Relation (forward / inverse) | Meaning |
|---|---|
| `ParentOf` / `ChildOf` | syntactic containment (our `CONTAINS`) |
| `Construct` / `ConstructedBy` | block participates in constructing an object of a class |
| `Imports` / `ImportedBy` | our `IMPORTS` |
| `BaseClassOf` / `DerivedClassOf` | inheritance |
| `Overrides` / `OverriddenBy` | method override |
| `Calls` / `CalledBy` | our `CALLS` (we only store the forward edge; inverse is a reversed lookup) |
| `Instantiates` / `InstantiatedBy` | object instantiation |
| `Uses` / `UsedBy` | field use |

Our PLAN §4.1 `edges.kind ∈ {CALLS, IMPORTS, CONTAINS}` is a strict subset. See CORRECTIONS §5.1.

### 2.2 CodePlan — atomic change labels → dependency-graph update → may-impact rules

Provenance: CodePlan §3, the atomic-change table (one row per change label; columns: label, how the
dependency graph is updated, which blocks are marked as affected).

Notation, restated:
- `D` = dependency graph **before** the edit, `D'` = graph **after** the edit.
- `Rel(G, X, r)` = the set of blocks reachable from block `X` over relation `r` in graph `G`. Each such
  block is queued as *affected*, tagged with the relation that caused it (the relation is passed to
  the LLM as the reason for revisiting that block).
- `M` = the edited method, `C` = its class, `F` = the field, `I` = the import.
- `Nil` = no blocks affected.

| Label | Change | Dependency-graph update | May-impact (affected blocks) |
|---|---|---|---|
| **MMB** | Modify method **body** | recompute edges of the statements inside the body | `Nil` — **unless** the change modifies an *escaping* object (state visible outside the method), in which case `Rel(D, M, CalledBy)` |
| **MMS** | Modify method **signature** | recompute edges on the method node | `Rel(D, M, CalledBy)`, `Rel(D, M, Overrides)`, `Rel(D, M, OverriddenBy)`, `Rel(D', M, Overrides)`, `Rel(D', M, OverriddenBy)` |
| **MF** | Modify a **field** in a class | recompute edges on the field node | `Rel(D, F, UsedBy)`, `Rel(D, C, ConstructedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **MC** | Modify **class declaration** | recompute edges on the class node | `Rel(D, C, InstantiatedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)`, `Rel(D', C, BaseClassOf)`, `Rel(D', C, DerivedClassOf)` |
| **MCC** | Modify **constructor signature** | no graph change | `Rel(D, C, InstantiatedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **MI** | Modify **import/using** | recompute edges on the import node | `Rel(D, I, ImportedBy)` |
| **AM** | **Add** method | add node + edges; if it overrides an inherited method, redirect existing `Calls`/`CalledBy` to the new method | `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)`, `Rel(D', M, CalledBy)` |
| **AF** | **Add** field | add node + edges | `Rel(D, C, ConstructedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **AC** | **Add** class | add node + edges | `Nil` |
| **ACC** | **Add** constructor | add node + edges | `Rel(D, C, InstantiatedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **AI** | **Add** import | add node + edges | `Nil` |
| **DM** | **Delete** method | remove node + incident edges; redirect `Calls`/`CalledBy` (to the inherited method it was overriding) | `Rel(D, M, CalledBy)`, `Rel(D, M, Overrides)`, `Rel(D, M, OverriddenBy)` |
| **DF** | **Delete** field | remove node + incident edges | `Rel(D, F, UsedBy)`, `Rel(D, C, ConstructedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **DC** | **Delete** class | remove node + incident edges | `Rel(D, C, InstantiatedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **DCC** | **Delete** constructor | remove incident edges on the class | `Rel(D, C, InstantiatedBy)`, `Rel(D, C, BaseClassOf)`, `Rel(D, C, DerivedClassOf)` |
| **DI** | **Delete** import | remove node + incident edges | `Rel(D, I, ImportedBy)` |

Three structural lessons, and these are the whole reason we take the table:

1. **A pure body edit impacts nothing.** Only escaping-state changes propagate over `CalledBy`. This is
   the sharpest correction to naive "expand one hop over CALLS" (see CORRECTIONS §5.2).
2. **Signature/declaration edits need both `D` and `D'`.** `MMS` and `MC` query the *post-edit* graph
   too, because a changed signature can newly override (or stop overriding) something. Impact analysis
   cannot run against a single current snapshot.
3. **The relation label travels with the affected block.** CodePlan hands the LLM "you are affected via
   `CalledBy`", not just "you are affected". Our stale-flag message must carry the same: *plan P is
   stale because node X it assumes was changed via `MMS`; you are reached over `CalledBy`.*

### 2.3 CodePlan — adaptive plan/execute loop (pattern, restated)

Provenance: CodePlan §4, `AdaptivePlanAndExecute`. Restated as our merge-time marker (no LLM in loop):

```
# server/impact.py, v2, flag off in MVP
def propagate(repo, changed_blocks, D_old, D_new) -> dict[plan_id, list[Reason]]:
    frontier = deque(changed_blocks)          # blocks merged in this push
    seen = set()
    hits = defaultdict(list)
    while frontier:
        block, label = frontier.popleft()      # label from classify_change()
        for (affected, rel) in may_impact(label, block, D_old, D_new):   # the table above
            if (affected, rel) in seen: continue
            seen.add((affected, rel))
            for plan in plans_assuming(affected) | plans_writing(affected):
                hits[plan.id].append(Reason(affected, rel, label))
            # CodePlan re-enqueues because the LLM then edits the affected block.
            # We do NOT edit, so we do NOT re-enqueue -> our propagation is depth-1 per merge.
    return hits
```

Differences from CodePlan are deliberate and listed in ADAPT.

### 2.4 grite — the wasted-work metric family

Provenance: grite, evaluation section + Table 1.

grite does **not** define one "wasted-work" number. It defines three, restated exactly:

- **Duplicate-work rate** = *completions of an already-completed task ÷ total completions* — the
  fraction of finishing work that re-does a teammate's task. Detection: the substrate's event log
  carries a `duplicate` field, set when a `state_changed → closed` event arrives for a task that is
  already closed.
- **Conflicting edits** — a **count**, not a rate: cross-actor last-writer-wins overwrites, detected by
  CRDT instrumentation when two actors overwrite each other's value for the same field. grite's
  scalar merge rule is LWW keyed on the total order `(timestamp, actor, event_id)`; sets merge
  commutatively.
- **Goodput** = *distinct tasks completed per round*.

Plus one failure-mode counter worth stealing: **lock starvation** = a run of denied lease acquisitions
(leases are TTL-bounded, stored under `refs/grite/locks`).

**Measurement procedure (restated):**

- Work units are an *abstract pool of independent items*, explicitly **not real source files**, chosen
  so contention is controllable exactly. Pool size is fixed across runs.
- Agent-count sweep `N ∈ {2, 4, 8, 16, 32}`, multiple seeded runs per cell. Contention rises with `N`
  because the pool is fixed.
- Per round, each agent: select a candidate task → in the lease arms, try to acquire its lease and
  back off on denial → "work" it by emitting real grite events (`select`, `issue_updated`,
  `state_changed → closed`). A completion is counted at the transition to `closed`.
- Three arms: **no-coord** (free pick, nothing prevents duplicates), **locks-only** (exclusive advisory
  leases taken before work), **locks+state** (leases *plus* shared completion state).

**Table 1 headline, at N = 32:**

| Arm | Duplicate-work rate | Conflicting edits | Goodput |
|---|---|---|---|
| No coordination | 0.78 | 410 | 2.33 |
| Locks only | 0.64 | 138 | 3.84 |
| Locks + shared state | 0.00 | 48 | 8.00 |

The load-bearing finding for loom: **leases alone only take duplicate work 0.78 → 0.64.** Shared
completion/plan state is what takes it to 0. loom's `declare_plan` + embedded `spec_md` is the
"+state" half; a claim TTL alone is the "locks-only" half. Our eval must separate them or we cannot
claim the effect.

### 2.5 `eval/metrics.py` — concrete computation plan over two agent branches

grite's units are abstract; ours are git hunks. Restated adaptation, implementable as-is:

**Inputs:** `repo_path`, `base` (merge-base commit), `branch_a`, `branch_b`, `task_id`, `arm`.

**Work unit.** A *hunk*: one contiguous change region from `git diff --unified=0 <base> <branch> --`
per file. Two counters per hunk: `n_lines = added + deleted`, and the hunk itself (count 1). Report
both; lines are the primary denominator because hunk boundaries are noisy.

```python
# eval/metrics.py
@dataclass(frozen=True)
class Hunk:
    path: str
    base_start: int; base_len: int      # range in the merge-base file (old side)
    new_start: int;  new_len: int
    added: tuple[str, ...]              # normalized added lines
    deleted: tuple[str, ...]
    @property
    def lines(self) -> int: return len(self.added) + len(self.deleted)
```

Normalization before comparison (kills false negatives from formatting): strip trailing whitespace,
collapse internal runs of spaces, drop blank lines and comment-only lines, do **not** lowercase, do
**not** strip leading indentation (indentation is semantic in Python).

**Step 1 — total work.**
`total_lines = sum(h.lines for h in A) + sum(h.lines for h in B)`, `total_hunks = len(A) + len(B)`.
This is the denominator. Also record `files_touched_a`, `files_touched_b`, `|files_a ∩ files_b|`.

**Step 2 — duplicated work** (grite's duplicate-work rate, ported).
Pair hunks `a ∈ A`, `b ∈ B` with `a.path == b.path`. Compute
`J(a,b) = |set(a.added) ∩ set(b.added)| / |set(a.added) ∪ set(b.added)|` over normalized added lines
(if both have empty `added`, fall back to the same Jaccard over `deleted`). Mark the pair **duplicate**
when `J ≥ 0.8` **and** the pair is a mutual best match (greedy maximum-weight matching over the pair
graph, one-to-one, so a single hunk cannot be charged twice).
`dup_lines = Σ min(a.lines, b.lines)` over matched pairs — charge only the *redundant* copy, not both
sides, mirroring grite counting *the extra completion* rather than the whole task.
`duplicate_work_rate = dup_lines / total_lines`.
Threshold `0.8` is a config constant `DUP_JACCARD`; log the matched pairs so a human can eyeball them
(a run whose duplicate matches are all `import` lines is a false positive and must be visible).

**Step 3 — conflicting work.** Two independent numbers, both reported:
- `merge_conflict_files` / `merge_conflict_hunks` — authoritative. Run `git merge-tree --write-tree
  --name-only <branch_a> <branch_b>` (git ≥ 2.38). **Exit status: `0` = clean, `1` = conflicts,
  `≥2` = error** — never treat `≥2` as a conflict count; raise. MVP counts *conflicted files* (the
  `--name-only` list) and reports `merge_conflict_hunks = None`; v2 refines to per-hunk counts by
  reading the conflicted stage blobs from the written tree and counting `<<<<<<<` marker regions.
  This is the number PLAN §6 calls "merge conflict hunks". No working tree is dirtied.
- `overlap_lines` — grite's "conflicting edits" analog, computable even when git's 3-way merge happens
  to succeed textually. **Bucket rule: every hunk is charged at most once, and duplicate beats
  conflict** — hunks already matched in Step 2 are removed from both sides before this step runs.
  Over the remaining hunks, per file, build overlap pairs on **base-side ranges**
  (`a.base_start < b.base_start + b.base_len and b.base_start < a.base_start + a.base_len`), then
  group those pairs into **connected components** (interval clusters). Per cluster:
  `cluster_cost = max(Σ lines of its A-side hunks, Σ lines of its B-side hunks)`;
  `conflict_lines = Σ cluster_cost`. Charging per cluster, not per pair, is required — per-pair
  charging double-counts a hunk that overlaps several opposing hunks and can push
  `wasted_work_share` above 1. We do not know which side loses the merge, so we charge the larger
  side: a deliberate, documented upper bound.

**Step 4 — the composite.** loom's own headline number, ours, not grite's:
```
wasted_work_share = (dup_lines + conflict_lines) / total_lines      # in [0, 1]
useful_lines      = total_lines - dup_lines - conflict_lines
```
Guard `total_lines == 0` → return `None`, never `0.0` (an arm where both agents produced nothing is
not a perfect arm). Because dup and conflict buckets are disjoint by construction (Step 3 bucket
rule) and each charges at most one side of a pairing, `dup_lines + conflict_lines ≤ total_lines`
holds — assert it, and assert `0 ≤ wasted_work_share ≤ 1`.

**Step 5 — goodput analog.** `distinct_tasks_green` = number of task-pair items whose post-merge test
suite passes, ÷ wall-clock minutes to "both branches merged and green". Also emit
`post_merge_test_failures` (PLAN §6 headline) and `tokens_total`.

**Step 6 — starvation analog.** From loom's `events` table: `deny_count`, and
`max_consecutive_denies` per agent (grite's "run of denied acquisitions"). An arm that scores 0 wasted
work by blocking one agent into starvation must be visibly caught.

**Output contract.** One JSON row per (task_pair, arm, seed):
```json
{"task": "auth-vs-cache", "arm": "loom", "seed": 3,
 "total_lines": 412, "total_hunks": 27,
 "dup_lines": 0, "duplicate_work_rate": 0.0,
 "merge_conflict_hunks": 0, "overlap_lines": 18, "conflict_lines": 18,
 "wasted_work_share": 0.0437, "useful_lines": 394,
 "post_merge_test_failures": 0, "wall_clock_s": 1840, "tokens_total": 512000,
 "deny_count": 3, "max_consecutive_denies": 1}
```
`eval/harness` writes rows; a `results_table()` renders the markdown table PLAN M4 requires from a
single command. Seeds matter: grite averages multiple seeded runs per cell, and with N=2 the variance
across two LLM agents will be larger than grite's — report median and min/max over ≥3 seeds, never a
single run.

**Pure-function boundary (testable without agents).** `compute_metrics(hunks_a, hunks_b) -> dict` takes
parsed hunks, not a repo. Unit tests build synthetic hunk lists: identical hunks → rate 1.0;
disjoint files → 0.0; overlapping-but-different ranges → conflict only; one hunk duplicated by two
opposing hunks → matching stays one-to-one.

---

## 3. ADAPT

| Source behavior | What we change | Why |
|---|---|---|
| CodePlan re-enqueues each affected block, edits it with an LLM, then re-classifies and propagates transitively until fixpoint | loom's `impact.py` propagates **depth-1 per merge** and only *marks* plans stale; no LLM, no editing | We are a coordination server, not an autofixer. Transitive closure over `CalledBy` in a real repo marks half the codebase; the value is telling the *human/agent owner* of an assuming plan, and one hop from the merged change is what their `assumes` list actually referenced. |
| CodePlan classifies changes by diffing the LLM's old and new code fragment | We classify from the **merge diff** with tree-sitter: for each changed node, compare pre/post `qualname` set, `signature_hash`, and `body_hash` | We already store `body_hash` (PLAN §4.1). Add a `sig_hash` column and the label falls out: node absent→present = `AM`/`AC`; present→absent = `DM`/`DC`; `sig_hash` changed = `MMS`/`MC`; only `body_hash` changed = `MMB`. |
| CodePlan's `MMB` escaping-object test is a static analysis of whether mutated state escapes the method | MVP-adjacent approximation: `MMB` propagates over `CalledBy` **only if** the body diff touches a `return` statement, a `global`/`nonlocal`, an assignment to `self.*` / a module-level name, or a `yield` | We are not writing an escape analysis. This over-approximates in the cheap direction and is one tree-sitter query. Config knob `IMPACT_MMB_ESCAPE_HEURISTIC`, default on when impact is on. |
| CodePlan needs `Overrides`, `BaseClassOf`, `UsedBy`, `InstantiatedBy`, `ConstructedBy` edges | v2 indexer adds `EXTENDS` (class → base class) and `USES` (block → field/attribute); `Overrides` is *derived*, not stored (method `C.m` overrides `B.m` iff `EXTENDS(C,B)` and both define `m`); `InstantiatedBy`/`ConstructedBy` collapse into `CALLS` against the class node in Python (`Foo()` is a call) | Keeps the MVP's three edge kinds untouched and adds exactly two. Deriving `Overrides` avoids an edge type whose maintenance on re-index is the buggiest part of CodePlan's update column. |
| CodePlan queries both `D` and `D'` | `impact.py` receives `(nodes_before, edges_before)` snapshot for the touched paths only, captured by `loom index` *before* it writes the new rows | Full graph versioning is out of scope. `MMS`/`MC` are the only labels needing `D'`, and both are satisfied by a per-file before-snapshot. |
| grite's work units are abstract pool items with a `duplicate` event field | Our units are git hunks and duplication is inferred by Jaccard similarity | We have no oracle telling us "these two agents did the same task"; grite's simulator does. Our estimate is noisy, so we log every matched pair and report a threshold sweep (`J ∈ {0.7, 0.8, 0.9}`) in the appendix rather than one magic number. |
| grite's "conflicting edits" = CRDT LWW overwrite count | Split into `merge_conflict_hunks` (git-authoritative) + `overlap_lines` (base-range overlap) | We have no CRDT; git's 3-way merge is our arbiter, and base-range overlap catches the semantically-conflicting-but-textually-clean case that PLAN §6 correctly calls the headline. |
| grite sweeps `N ∈ {2,…,32}` over a synthetic pool | We run N = 2 on real task pairs, ≥3 seeds | Real LLM agents on RealWorld cost money and wall clock. But see CORRECTIONS §5.5 — we must not quote grite's N=32 numbers as our expectation. |
| grite's arms: no-coord / locks-only / locks+state | loom's arms: **A** = no coordination, **A′** = claims-only (TTL leases, conflict responses stripped of `spec_md`), **B** = full loom (claims + embedded specs) | This is the single most valuable thing grite gives us. A′ costs ~10 lines (a flag that blanks `spec_md` in the conflict response) and it is the arm that isolates *the specs*, which is loom's actual thesis. Without A′, arm B's win is attributable to plain locking. |
| grite stores state in `refs/grite/wal` / `refs/grite/locks`, LWW on `(timestamp, actor, event_id)` | loom stays SQLite/WAL per PLAN §1 | Rejected as storage — see REJECT. |

---

## 4. REJECT

- **CodePlan's whole planning loop (`AdaptivePlanAndExecute`) as a runtime component.** PLAN §2 only
  claims the rules table, correctly. Do not import the oracle/LLM-repair loop, the `GatherContext`
  prompt assembly, or the `Pending/Completed` plan-graph executor. loom prevents conflicts before
  edits; CodePlan repairs a repo after one. Adopting the loop would make loom an autofixer and
  contradict PLAN §8 ("if we ever need a clever merger, the gate failed").
- **CodePlan-style impact expansion inside `declare_plan` (the MVP hot path).** The rules table is a
  *merge-time* marker (PLAN §7 "merge lifecycle"), not a claim-time expander. `check()` has a sub-10ms
  budget; the table's `D`/`D'` queries do not fit and would over-claim (see §5.2).
- **`MCC`/`ACC`/`DCC` (constructor rows) and `MF`/`AF`/`DF` (field rows) as separate labels in v2 for
  Python.** In Python a constructor is `__init__`, i.e. an ordinary method → fold `*CC` into `*M`.
  Instance fields are assignments inside `__init__`, not declarations → folding `MF` into `MMB` of
  `__init__` (with the escape heuristic firing on `self.*`) is correct and removes six rows. Keep all
  16 rows documented here for the day we index C#/TypeScript, but implement 10.
- **CodePlan's C#-specific `Construct`/`ConstructedBy` relation.** No Python analog worth an edge type.
- **grite's git-refs storage substrate (`refs/grite/wal`, `refs/grite/locks`), its CRDT, and its
  LWW-on-`(timestamp, actor, event_id)` merge rule.** PLAN §1 locks one SQLite store behind one
  process precisely so check-and-claim is a single transaction. A git-ref CRDT is eventually
  consistent — it *detects* concurrent overwrites after the fact instead of preventing them, which is
  the failure loom exists to remove. Reject the substrate; keep only the metric.
- **grite's absolute numbers (0.78 → 0.00, goodput 2.33 → 8.00) as anything we cite about loom.** They
  are from a synthetic simulator at N=32. Quoting them next to our N=2 results would be
  misrepresentation. Cite them only as motivation, always labelled "grite, simulated, N=32".
- **`race-to-close` as a metric.** grite lists it as a failure mode but never formally defines it in
  the paper. Do not implement a metric we cannot define; `max_consecutive_denies` (starvation) is
  defined and is enough.
- **grite's code from `github.com/neul-labs/grite`.** Not read, license unknown. No copying until
  someone reads that LICENSE file.

---

## 5. CORRECTIONS to PLAN-v1.md

**5.1 §4.1 edge schema is too narrow for the v2 impact rules, and the constraint style matters now.**
The rules table needs inheritance and field-use relations. Concretely: add nothing to the MVP, but
declare `edges.kind` as a plain `TEXT` column with **no `CHECK` constraint** (or a `CHECK` that already
includes `EXTENDS`, `USES`) so that turning impact on in v2 is an insert, not a table migration under
a live claims database. PLAN §4.1 as written invites a `CHECK (kind IN ('CALLS','IMPORTS','CONTAINS'))`
that will cost a migration.

**5.2 §4.2's "expands write_targets by one hop over CALLS and IMPORTS" over-claims, and CodePlan
proves it.** CodePlan's `MMB` row says a method-body change impacts **nothing** unless mutated state
escapes. Most agent edits are body edits. Blanket one-hop `CALLS` expansion will therefore claim
callers that cannot possibly be affected, producing false denials — the exact contention problem PLAN
§7 defers to "per-edge-type radius". Recommendation, cheap and MVP-safe: keep one-hop expansion for
the *declared* targets (a plan is a statement of intent, not a diff, so over-claiming at declare time
is defensible), but state explicitly in §4.2 that the expansion is intent-based, **not** CodePlan
impact analysis, and that `IMPORTS` expansion radius should be 0 by default (PLAN §7 already reaches
this conclusion for 10 users; it is right at 2 users too — a one-hop `IMPORTS` expansion on a Python
package `__init__.py` claims the world).

**5.3 §7 "merge lifecycle" understates what impact needs: two graph snapshots, not one.** `MMS` and
`MC` query `D'` as well as `D`. PLAN §4.1 stores only the current graph. Fix: `loom index` must retain
a pre-update snapshot of the affected files' node rows (`id, qualname, kind, body_hash, sig_hash`) for
the duration of the re-index transaction and hand it to `impact.propagate()`. Also add a `sig_hash`
column alongside `body_hash` in §4.1 — without it, `MMB` and `MMS` are indistinguishable and the whole
table collapses to its most expensive row. **This is a one-column MVP change worth making now**, since
adding a column later means a migration on a live claims DB.

**5.4 §2's grite line ("the wasted-work metric, share of duplicated or conflicting work") describes a
metric the paper does not contain.** grite defines a *duplicate-work rate* (a ratio), a *conflicting
edits* count (an integer), and *goodput* — three separate quantities. The combined "share of
duplicated or conflicting work" in §2, and "Wasted-work share" in §6, are **our composite** and must
be defined by us (done in §2.5 Step 4). Attribute it as "loom's composite, after grite's components" —
never as "grite's metric".

**5.5 §6's expected effect size cannot come from grite.** grite's 78% → 0% is N=32 agents against a
fixed pool, i.e. maximal contention. loom's eval is N=2 on two designed-to-collide task pairs. At N=2
the no-coordination duplicate-work rate will be far lower than 0.78 and possibly near 0, with the
signal living almost entirely in `conflict_lines` and `post_merge_test_failures`. §6 should say so, or
the first run will read as a failure.

**5.6 §6 arms are missing the arm that actually tests loom's thesis.** grite's locks-only arm moved
duplicate work only 0.78 → 0.64; locks+state took it to 0.00. Translated: TTL claims alone are not the
win — shared plan state is. PLAN §6 arm C (the old glue stack) is optional and expensive; a
**claims-only arm (A′)** is nearly free (blank `spec_md` in the conflict response and in the deny
message) and is the only arm that separates "we added locks" from "we added specs". Recommend
promoting A′ over C. This does not change the MVP scope addendum — A′ is a flag on arm B's code path,
not new machinery.

**5.7 §5 M4 / §2 `eval/metrics.py` needs a git version floor.** `git merge-tree --write-tree` (the
non-destructive conflict counter, no worktree needed) requires **git ≥ 2.38**. Record it in the eval
README and assert it at harness start; the fallback (`git merge` in a scratch worktree, then
`--abort`) is what the MVP-cut "harness skeleton" gets if the floor is not met.

**5.8 §2 treats grite as paper-only; it is also a codebase.** The paper releases grite at
`https://github.com/neul-labs/grite`. That makes it a candidate cherry-pick source for the *lease*
semantics (TTL leases under `refs/grite/locks`, back-off on denial) alongside mcp_agent_mail — but its
license is unstated in the paper, so it stays out of the manifest until someone reads its LICENSE.
Flag for the github-miner track, not for the coder.

**5.9 §2/§4 confirmation (not a correction).** PLAN's claim that CodePlan contains an
"edit-classification to affected-relation rules table" is **accurate** — the table exists, is reproduced
in restated form in §2.2 above, and is complete at 16 atomic change labels. `server/impact.py`, v2,
flag off in MVP, stands as written.

---

### Implementation checklist for the coder (no need to open the papers)

1. MVP now: `edges.kind` = free TEXT (no narrow CHECK); add `nodes.sig_hash` next to `body_hash`.
2. MVP now: `eval/metrics.py` per §2.5 — `Hunk`, `parse_hunks(repo, base, branch)`,
   `compute_metrics(hunks_a, hunks_b) -> dict`, pure and unit-testable; git ≥ 2.38 assert.
3. MVP now: arm flag `LOOM_ARM ∈ {none, claims_only, full}` that blanks `spec_md` in conflict/deny
   payloads for `claims_only`.
4. v2, behind `LOOM_IMPACT=1`, default off: `server/impact.py` implementing the 10 Python-relevant
   rows of the §2.2 table, depth-1, with the `MMB` escape heuristic, consuming a pre-index snapshot.
5. Never: CodePlan's LLM repair loop; grite's git-ref CRDT storage; verbatim text from either paper.
