# Extraction: mcp_agent_mail → loom

Source clone: `<vendor-clone>/mcp_agent_mail`
Upstream: github.com/Dicklesworthstone/mcp_agent_mail (author Jeffrey Emanuel).
Extraction targets: (1) TTL lease semantics + renewal, (2) advisory-by-design stance,
(3) deny-message format with actionable next step, (4) pre-commit guard script shape.

**Reading rule for coder agents: this document is the only artifact carried forward. Do not open,
vendor, copy, or commit the source clone. Everything you need to implement is restated here.**

---

## 1. LICENSE

File: `LICENSE` (repo root). Header, verbatim as a name:

> **MIT License (with OpenAI/Anthropic Rider)** — Copyright (c) 2026 Jeffrey Emanuel

MIT grant, then an **ADDITIONAL RIDER / RESTRICTION (OpenAI / Anthropic)** that the license says
*controls* over any conflicting clause. Restrictions that matter to us, restated:

1. **"Restricted Parties"** = OpenAI L.L.C.; Anthropic PBC; their Affiliates; and any person or
   entity acting directly or indirectly on behalf of, for the benefit of, or under the direction of
   any of the foregoing (officers, employees, contractors, agents, consultants, service providers,
   representatives).
2. **No rights are granted to any Restricted Party.** Any purported license/sublicense/transfer to
   one is null and void absent written permission from the author.
3. You may not **provide, disclose, distribute, sublicense, host, make available, or otherwise
   permit access** to the Software *or any Derivative Work* to or for a Restricted Party.
4. The rider's definition of **"use" explicitly includes**: copying, modifying, merging,
   publishing, distributing, selling, hosting, deploying, executing, **benchmarking, testing,
   analyzing, indexing**, or incorporating the Software or Derivative Works into any dataset,
   training corpus, **evaluation harness**, or pipeline for machine learning or other automated
   systems.
5. Breach **automatically terminates** all permissions, obliges destruction of copies, and the
   author reserves injunctive relief plus attorneys' fees.
6. Any distribution of the Software or Derivative Works must carry the rider unmodified.

**Operational consequences for loom (binding, not advisory):**

- **Zero verbatim code anywhere in loom, and zero verbatim code anywhere in this document.** Not in
  `server/claims.py`, not in `hook/gate.py`, not in tests, not in comments. Any copied expression
  would make loom a Derivative Work and drag the rider (including the Restricted-Party
  distribution ban) onto our repo.
- **Never vendor, submodule, or commit the clone.** Delete the scratchpad clone after extraction.
  Never place it inside `loom/` or `eval/target-repo`.
- **Never point loom's eval harness at this repo.** Clause 4 names benchmarking/evaluation harnesses
  explicitly. Eval target stays RealWorld/Conduit (PLAN §2, §6).
- What we legitimately keep: **mechanisms, defaults, field names, semantics, sequencing, and file:line
  pointers.** Facts and ideas are not copyrightable expression; the pseudocode below is freshly
  written in loom's own vocabulary (`plan_id`, `node_id`, `claims`) and shares no identifiers or
  code structure with the source.
- Because a Restricted Party is arguably in the chain of authorship for anything an Anthropic model
  writes, keeping loom's implementation clean-room-from-mechanism (never from expression) is also
  the risk-minimal posture. Treat that as settled; do not reopen it mid-build.

Everything in §2 below is therefore marked **patterns only, no verbatim code**.

---

## 2. ADOPT — patterns only, no verbatim code

### 2.1 TTL lease semantics with renewal

**Provenance:** `src/mcp_agent_mail/models.py:117-137` (lease row); `src/mcp_agent_mail/app.py:11328-11341`
(lease-issuing tool signature + defaults); `app.py:11540-11626` (issue path); `app.py:12008-12143`
(renewal tool); `app.py:4221-4300` (expiry sweep); `app.py:3967-4135` (staleness classification);
`src/mcp_agent_mail/config.py:201-203, 546-548, 600-602` (tunables + defaults);
`src/mcp_agent_mail/http.py:1101-1140` (background sweep loop).

**Lease row fields (semantics we take):**

| Field | Type | Semantics worth copying |
|---|---|---|
| scope key (project) | FK, indexed | Every lease keys on the tenant. loom: `repo`. |
| holder (agent) | FK, **nullable** | Deliberately nullable so a lease can outlive its owner row. See orphan rule below. |
| target (path pattern) | str ≤512 | loom's analogue is `node_id`. |
| exclusive | bool, default true | Exclusive vs shared. loom: `mode` in `write` / `read`. |
| reason | str ≤512, default "" | Free-text intent, surfaced to humans and to the deny message. loom already has richer: `plan_id` → `spec_md`. |
| created_ts | UTC | |
| **expires_ts** | UTC, required | The lease clock. Never null — a lease with no expiry is the failure mode this design exists to prevent. |
| **released_ts** | UTC, nullable | **Release is a tombstone, not a delete.** "Active" = `released_ts IS NULL AND expires_ts > now`. |

Indexes worth mirroring: `(scope, released_ts, expires_ts)` for the active-lease scan, and
`(scope, holder, released_ts)` for "what do I hold".

**Defaults and floors — upstream's numbers, for the record.** *(GATE-1 fix 6: loom does NOT adopt
these. The canonical loom TTL set — recorded identically in beads.md ADAPT #4 — is: **TTL 1800 s at
declare; renewal is implicit on every `check()` and explicit via `renew()`, both resetting
`ttl_expires = max(current, now + 1800)` so a renewal never shortens; floor 60 s (warn at declare,
clamp at renew); an expired or non-active plan cannot be renewed (`{renewed: 0}` — re-declare); the
read filter `status='active' AND ttl_expires > now` is authoritative everywhere; the lazy sweep
that flips `status='expired'` is bookkeeping only and runs with a 2×TTL (3600 s) grace.** Upstream's
3600/1800 issue/extend split is rejected: loom's hook hits `check()` on every edit, so
extend-arithmetic is moot.)*

| Knob | Source default | Note |
|---|---|---|
| Lease TTL at issue | **3600 s** | Tool parameter default. |
| Renewal extension | **1800 s** | Renewal tool parameter default. |
| TTL floor | **60 s** | *Warned* (not rejected) at issue; *clamped* upward at renewal. |
| Background expiry sweep interval | **60 s** | Env-overridable. |
| Holder-inactivity threshold | **1800 s** | Used by the heavier staleness pass (we mostly reject this; see §4). |
| Activity grace window | **900 s** | Same. |
| Stale-agent retirement sweep | **3600 s** interval, **86400 s** threshold, enabled by default | Retires dead identities. |

**Renewal semantics (the important part):**

- New expiry = `max(now, current_expires_ts) + extend_seconds`. Extending from the *later* of the two
  means a renewal that arrives early never shortens a lease, and a renewal that arrives late does not
  silently backdate.
- The renewal query filters on `released_ts IS NULL AND expires_ts > now`. **An already-expired lease
  cannot be renewed.** Expiry is final; the holder must re-acquire. This is what makes expiry a real
  liveness guarantee rather than a suggestion.
- Renewal is scoped: renew all of my active leases, or restrict by id list / target list. Empty-list
  arguments are an explicit error (distinct from omitted = "all"), because `[]` meaning "everything"
  is a foot-gun.
- Renewal runs inside a single immediate-write transaction so the new expiry is visible to other
  connections before the call returns.

**Re-acquiring a lease you already hold (idempotent issue):** when the same holder re-requests the
same target, the existing row is *updated in place* — exclusivity and reason refreshed, expiry set to
`max(requested_expiry, current_expiry)` — instead of inserting a duplicate. The response marks that
entry with a `reused` flag so callers (e.g. an auto-release wrapper) can avoid releasing a lease the
caller held before the call.

**Orphan rule (`models.py:126-131`, upstream issue #161) — take this:** the holder FK is nullable so
that deleting an agent record does not leave an undiscoverable lease pinning a target forever. The
sweep uses an **outer** join on holder so orphaned leases are still found and auto-released, and it
tags them with a distinguishable reason (`holder never set` vs `holder row deleted`). *loom
equivalent:* a `claims` row whose `plan_id` no longer resolves to a live `plans` row must still be
sweepable — do not rely on an inner join anywhere in the sweep or the claim would be immortal.

**Sweep architecture (two-tier):**

1. **Lazy sweep:** every lease-touching call (issue, renew, list, and the message-write enforcement
   path) first expires everything past its TTL for that scope, then proceeds. Called at
   `app.py:11434`, `app.py:12084`, `app.py:5528`, `app.py:13746`.
2. **Background loop:** a startup-registered worker walks the distinct scopes present in the lease
   table, sweeps each, logs `scopes_scanned` / `released` structured events, sleeps the configured
   interval, repeats. Every scope sweep is wrapped so one bad scope cannot kill the loop.

Expiry is executed as a set-based `UPDATE ... SET released_ts = now WHERE released_ts IS NULL AND
expires_ts < now`, inside an **immediate** (write-locking) transaction so the release is visible to
concurrent acquirers on other connections — upstream issues #129/#130 were exactly the phantom
conflict / duplicate-holder bugs caused by reading on a stale snapshot.

**Fresh loom pseudocode (ours, written from the mechanism):**

```python
# server/claims.py — constants per the canonical TTL set (GATE-1 fix 6)
CLAIM_TTL_S      = 1800   # lease at declare_plan AND the reset target on every renew/check
TTL_FLOOR_S      = 60     # warn at declare, clamp at renew
SWEEP_GRACE_S    = 2 * CLAIM_TTL_S   # status-flip bookkeeping grace; read filter is authoritative

def sweep_expired(conn, repo, now):
    """Tombstone, never delete. Returns rows affected (feeds the events log)."""
    with immediate_tx(conn):                       # BEGIN IMMEDIATE — see 2.4
        rows = conn.execute(
            "SELECT plan_id FROM plans "
            "WHERE repo=? AND status='active' AND ttl_expires < ?", (repo, now)).fetchall()
        conn.execute(
            "UPDATE plans SET status='expired', updated=? "
            "WHERE repo=? AND status='active' AND ttl_expires < ?", (now, repo, now))
        # claims are keyed to plan_id; a non-active plan holds nothing.
        for (plan_id,) in rows:
            log_event(conn, actor="sweeper", action="plan_expired", detail=plan_id)
    return [r[0] for r in rows]

def renew(conn, plan_id, ttl_s=CLAIM_TTL_S, now=None):
    ttl_s = max(TTL_FLOOR_S, int(ttl_s))
    with immediate_tx(conn):
        row = conn.execute(
            "SELECT ttl_expires FROM plans "
            "WHERE id=? AND status='active' AND ttl_expires > ?", (plan_id, now)).fetchone()
        if row is None:
            return {"renewed": 0, "reason": "expired_or_inactive_cannot_renew"}
        new_exp = max(row[0], now + ttl_s)        # reset to now+TTL; never shorten (GATE-1 fix 6)
        conn.execute("UPDATE plans SET ttl_expires=?, updated=? WHERE id=?",
                     (new_exp, now, plan_id))
    return {"renewed": 1, "old_expires": row[0], "new_expires": new_exp}
```

Call `sweep_expired(repo)` at the top of `declare_plan`, `rescope`, `check`, and `list_claims`
(lazy tier); the background tier is optional for MVP (see §3).

### 2.2 Advisory by design — grant alongside conflicts

**Provenance:** `app.py:11541-11545` (grant-anyway comment + branch), `app.py:11588-11607`
(conflict collection then grant regardless), `app.py:11627-11636` (advisory-only warning), the tool
return shape at `app.py:11637`; docstring stance `app.py:11342-11367`; `README.md:1670` and
`README.md:2234` state it as policy; `guard.py:262-264` and `guard.py:466-468` show the *other* half.

The stance, restated:

- A lease request **never fails**. Overlapping active exclusive leases held by *other* holders are
  detected and reported, and the lease is still issued. The response is a three-part envelope:
  `{granted: [...], conflicts: [...], warnings: [...]}`. `granted` entries carry
  `{id, target, exclusive, reason, expires_ts, reused}`; `conflicts` entries carry
  `{target, holders: [{agent, pattern, exclusive, expires_ts}]}`.
- Conflict detection short-circuits on three cases before doing any pattern work: the lease is
  already released; the lease is held by *me*; **both** sides are non-exclusive (shared ∧ shared
  never conflicts). Only exclusive-vs-anything can conflict.
- The server tells the caller, in-band, **where its enforcement actually is**: the response carries a
  warning entry led by a stable machine-readable code (upstream's is spelled
  `enforcement_off_for_code_paths`), followed by a count of how many requested targets fall outside
  server-enforced territory and a pointer to the pre-commit guard as the authoritative gate — so a
  wrapper tool can detect advisory-only mode without parsing prose. *Adopt this principle:* the
  coordination API must state its own enforcement level in its response, never leave it implicit.
- The design splits cleanly: **advisory at claim time, enforcing at edit/commit time.** The guard's
  mode default is `block`, not `warn` (`guard.py:262`). This is exactly loom's shape — advisory TTL
  claims in the server, teeth in the PreToolUse hook — and confirms PLAN §1 rather than contradicting it.
- Crash safety comes from TTL + renewal, not from lock release. Nothing in the system requires a
  well-behaved shutdown.

### 2.3 Deny message with an actionable next step

Three deny surfaces exist upstream; all three carry the same anatomy. Take the anatomy.

**(a) Structured error envelope** — `app.py:321-336`. Shape:
`{"error": {"type": <SCREAMING_SNAKE code>, "message": <one sentence>, "recoverable": <bool>,
"data": {<machine-readable context>}}}`. `recoverable` is the field that tells an agent whether to
replan or abort. Take all four keys.

**(b) Next step as a ready-to-run call** — `app.py:10274-10296`. The strongest pattern in the repo:
on a recoverable error, `data` includes a **`suggested_tool_calls`** array of
`{"tool": <name>, "arguments": {...}}` objects, pre-filled with everything the server already knows
and `<placeholder>` tokens for what it does not. The agent's next action is a copy-paste, not an
inference. Also present: an empty-input error (`app.py:11392-11401`) whose message contains a
concrete worked example of a valid input.

**(c) Domain conflict payload** — `app.py:5602-5610`: the same envelope with a domain-specific type
code (upstream's reads `FILE_RESERVATION_CONFLICT`), a one-line message saying that active conflicting
leases prevented the write, and a `conflicts` array. Each conflict names the contested surface, the **holder**, the pattern that matched, exclusivity, and
`expires_ts`. The expiry timestamp is the actionable bit: it tells the blocked agent *when* the block
lifts, so "wait" becomes a costed option rather than a guess.

**(d) Human/CLI deny** — `cli.py:4524-4536` and `guard.py:404-411`: a red headline stating that
exclusive conflicts were detected, then one bullet per conflict giving the offending path, the pattern
it matched, and the holding agent, **capped at 10** to keep stderr readable, then a dimmed hints line
that names both escape hatches as literal env-var settings (mode-var set to warn for advisory,
bypass-var set to 1 for emergencies) alongside the instruction to resolve or release before
proceeding.

**loom's deny message, composed from that anatomy (fresh text, ours):**

```
loom: BLOCKED — server/auth.py::authenticate is claimed by agent "aria"
      under plan lm-4f2a "harden authenticate", expires 2026-08-18T14:20:07Z (in 22m).

Its spec:
  <spec_md of the owning plan, inline — no second fetch>

Next step (pick one):
  1. Build against the declared interface above; do not edit the symbol.
  2. loom.rescope(plan_id="lm-91cc", add_assumes=["server/auth.py::authenticate"])
  3. Wait for expiry (a crashed agent's claim surfaces on its own via TTL).
```

*(GATE-1 fix 5: an earlier draft of this message named `LOOM_BYPASS=1` as an escape hatch.
Removed — beads' wy-yuclk production incident (beads.md §2.2.3/C6) proves agents pattern-match any
override named in a deny message and steamroll live claims. `LOOM_BYPASS` is documented in human
docs and `loom init` output only, never in any agent-facing deny surface; every use is still
audited to `events`.)*

Rules to enforce in `hook/gate.py`:

1. Exit 2, message on **stderr**, per the Claude Code hook contract (PLAN §4.3).
2. Name the owner, the plan id, the plan title, **and the expiry instant**.
3. Embed the owning `spec_md` inline. Never make the blocked agent issue a second call to learn why.
4. End with a numbered, executable next step — at least one of them a literal loom tool call with
   arguments filled in.
5. Cap enumerated conflicts at 10 lines; append `(+N more)`.
6. **Never name an escape hatch, override, or force-release path in any agent-facing deny surface**
   (GATE-1 fix 5, overruling upstream's hints-line habit: beads.md §2.2.3/C6, the wy-yuclk
   incident). `LOOM_BYPASS` lives in human docs and `loom init` output only; every use of it is
   still written to `events`, and M3's gate test asserts the deny text names no override.
7. Mirror the same information in the machine envelope: `{"error": {"type": "CLAIM_CONFLICT",
   "message": ..., "recoverable": true, "data": {"node_id", "owner_agent", "owner_plan_id",
   "owner_title", "owner_spec_md", "expires_ts", "suggested_tool_calls": [...]}}}`.

### 2.4 Pre-commit guard script SHAPE

**Provenance:** `guard.py:222-433` (guard body renderer), `guard.py:23-208` (chain-runner),
`guard.py:667-722` (installer), `guard.py:439+` (pre-push twin), `cli.py:3754,3784` (install/uninstall
verbs), `scripts/test_guard.sh` (its test harness).

**Installer shape (`install_guard`):**

1. Resolve the hooks dir properly — honor `core.hooksPath`, then `git rev-parse --git-dir`/hooks,
   only then fall back to `.git/hooks`. (Worktrees and husky repos break the naive path.)
2. Never overwrite a user's hook. Install a **chain-runner** at `.git/hooks/pre-commit`; if a
   pre-existing, non-ours hook is there, rename it to `pre-commit.orig` first (idempotency check:
   look for our marker comment in the file before renaming).
3. Drop our actual check as a numbered plugin in `.git/hooks/hooks.d/pre-commit/50-<name>.py`,
   chmod 0755.
4. The chain-runner runs `hooks.d/pre-commit/*` in lexical order, then the preserved `.orig` last,
   and exits non-zero on the first failure — forwarding git's own argv (and, for pre-push, one
   read-once stdin buffer replayed to each child).
5. Uninstall is a first-class CLI verb, and it restores `.orig`.

**Guard body pipeline (the ordered stages — this is the pattern to reimplement):**

| # | Stage | Detail worth keeping |
|---|---|---|
| 1 | Shebang + self-identifying marker comment | Enables idempotent reinstall detection. |
| 2 | **Feature gate** | If the coordination feature flags are off → `exit 0` immediately. A guard nobody enabled must cost nothing. |
| 3 | **Emergency bypass** | `<BYPASS>=1` → write one line to stderr saying the bypass fired, `exit 0`. Loud, never silent. |
| 4 | **Mode** | `<MODE>` env, default **`block`**; `warn`/`advisory`/`adv` → advisory. |
| 5 | **Identity required** | No agent identity in env → stderr + `exit 1`. Refusing to run unidentified beats running blind. |
| 6 | **Collect staged paths** | staged names, NUL-separated, filtered to added/copied/modified/renamed/deleted/type-changed/unmerged (`git diff --cached --diff-filter=ACMRDTU --name-only -z` — flag order is ours; the invocation is a functional git command spelling, not copied expression), plus a second `--name-status -M -z` pass to expand renames into *both* old and new paths. Missing the old side of a rename is a real hole. |
| 7 | Early out | No staged paths → `exit 0`. |
| 8 | **Load foreign active leases once** | Read every lease record; skip: non-exclusive, held by me, released, expired. Parse ISO-8601 with `Z`→`+00:00` normalization and treat a naive timestamp as UTC; **an unparseable expiry is treated as not-expired** (fail closed on garbage). De-dupe by lease id. |
| 9 | **Compile patterns once, prefilter with a union** | Compile each pattern a single time (not per-path), build one union matcher, test each path against the union first, and only run per-pattern attribution on the ones that hit. O(n+m) instead of O(n×m). Honor `git config core.ignorecase` by case-folding both sides. |
| 10 | **Report** | Headline line, then `- <path> matches <pattern> (holder: <name>)` per conflict, capped at 10, all to **stderr**. |
| 11 | **Exit** | advisory → `exit 0`; block → `exit 1`. |

Every parse/IO step is individually exception-wrapped so a malformed record degrades the guard's
coverage rather than blocking the commit outright.

**Fresh loom pseudocode (ours):**

```python
# hook/guard.py  — rendered into .git/hooks/hooks.d/pre-commit/50-loom.py by `loom init`
import os, subprocess, sys

MARKER = "# loom pre-commit guard"
if os.environ.get("LOOM_ENABLED", "0").lower() not in {"1", "true", "yes"}:
    sys.exit(0)
if os.environ.get("LOOM_BYPASS", "0").lower() in {"1", "true", "yes"}:
    sys.stderr.write("[loom] bypass active via LOOM_BYPASS=1 (audited)\n"); sys.exit(0)

mode      = (os.environ.get("LOOM_GUARD_MODE") or "block").strip().lower()
advisory  = mode in {"warn", "advisory"}
agent     = os.environ.get("LOOM_AGENT")
if not agent:
    sys.stderr.write("[loom] LOOM_AGENT is required; run `loom init`.\n"); sys.exit(1)

def staged_paths():
    out, seen = [], set()
    a = subprocess.run(["git","diff","--cached","--diff-filter=ACMRDTU",
                        "--name-only","-z"], capture_output=True, check=True)
    b = subprocess.run(["git","diff","--cached","--name-status","-M","-z"],
                       capture_output=True, check=True)
    for chunk in (a.stdout, b.stdout):
        for p in chunk.decode("utf-8", "ignore").split("\0"):
            # NOTE (GATE-1 fix 8): `len(p) > 2` is a crude heuristic to drop the
            # status tokens (`M`, `R100`, ...) that the -z name-status stream
            # interleaves with paths — but it also drops legitimate 1–2-char
            # filenames. The implementer must parse the -z name-status record
            # format properly (status token, then 1 path, or 2 paths for R/C)
            # instead of length-filtering.
            if p and len(p) > 2 and p not in seen:
                seen.add(p); out.append(p)
    return out                                       # rename => old AND new both present

paths = staged_paths()
if not paths:
    sys.exit(0)

conflicts = loom_client.check_paths(agent, paths, timeout_s=2.0, on_timeout=[])  # fail open
if not conflicts:
    sys.exit(0)

sys.stderr.write("loom: staged files are claimed by other plans\n")
for c in conflicts[:10]:
    sys.stderr.write(f"  - {c.path} -> {c.node_id} (plan {c.plan_id}, {c.agent}, "
                     f"expires {c.expires_ts})\n")
if len(conflicts) > 10:
    sys.stderr.write(f"  (+{len(conflicts)-10} more)\n")
sys.stderr.write("Next: `loom rescope <your-plan> --add-assumes ...`, or wait for expiry.\n")
# GATE-1 fix 5: the Next line must never name LOOM_BYPASS or any force/override path.
sys.exit(0 if advisory else 1)
```

### 2.5 Bonus adopt — the check-then-act transaction lesson

`app.py:11475-11480` and `app.py:12092-12094` both run the entire read → conflict-check → write cycle
inside a **single immediate (write-locking) SQLite transaction**, with comments citing upstream bugs
#129 (duplicate exclusive holders) and #130 (phantom conflicts after a release). A deferred
transaction takes its snapshot on first *read*, so two concurrent acquirers both see "free" and both
write.

This is independent third-party confirmation of specgate's check-then-act lock lesson (PLAN §0 note c)
and it is the load-bearing requirement for `declare_plan` atomicity (PLAN §4.2). Concretely, in loom:
open the transaction with `BEGIN IMMEDIATE` before the first `SELECT` in `declare_plan`, `rescope`,
`renew`, and `sweep_expired`. `check` may read on a deferred/read transaction. Set
`PRAGMA busy_timeout` (a few seconds) so contenders queue instead of erroring with SQLITE_BUSY.

---

## 3. ADAPT — what we change, and why

1. **Grant-anyway → claim-all-or-nothing for write-write.** Upstream always grants and reports
   conflicts alongside. loom's `declare_plan` (PLAN §4.2) is atomic: on a write-write clash it claims
   *nothing* and returns `{conflicts}` with the winner's `spec_md` embedded. Why: upstream's unit is a
   file glob whose owner might legitimately be doing something orthogonal; loom's unit is a symbol,
   where two writers is definitionally a semantic conflict. We keep the *advisory philosophy* (TTL
   expiry, renewal, no hard OS locks, fail-open when the server is down) and drop *grant-anyway* for
   the exclusive case only. Read-vs-write and write-vs-read stay grant-with-warning, exactly the
   upstream shape.
2. **Target unit: symbol, not path glob.** Everything about pattern compilation, union prefilters,
   gitignore/wildmatch semantics, symmetric glob overlap, and `core.ignorecase` folding collapses to a
   primary-key equality test on `node_id`. Keep only the O(n+m) *instinct* (one query, set
   intersection in SQL) — drop the machinery. File-granularity fallback for non-code files (PLAN §1)
   uses exact normalized-path equality, not globs.
3. **Staleness = TTL only.** Upstream layers a second staleness notion on top of TTL: agent
   `last_active_ts` beyond 1800 s, cross-checked against recent mail activity, filesystem mtimes, and
   a git rev-walk over the reservation's pathspec, with a 900 s grace and a `stale_reasons` list. That
   is a lot of machinery (and a thread-offloaded git walk per lease) to answer "is this agent alive".
   loom answers it with `ttl_expires` plus explicit `renew()` heartbeats from active sessions
   (PLAN §7). Drop the activity probes; keep the *idea* of a human-readable reason string on the
   expiry event.
4. **Sweep: lazy-only for MVP.** Implement the lazy tier (sweep at the top of `check`, `declare_plan`,
   `rescope`, `list_claims`). The 60 s background loop is a v1.1 addition — it matters when nobody is
   calling, which for a 12-hour MVP demo is never. Keep the interval constant in config so turning it
   on is a `loom serve` flag, not a refactor.
5. **Guard reads the server, not local files — with a hard timeout.** Upstream's guard reads JSON
   artifacts from a local git-backed archive: fully offline, zero network dependency, but the artifacts
   can be stale relative to the DB. loom has a single SQLite store behind one process, so both the
   PreToolUse hook and the pre-commit guard query the server. **Tradeoff we accept and must mitigate:**
   a hanging server bricks every edit. Mandatory per the MVP addendum — 2 s timeout, fail open, one
   loud stderr line (`loom: server unreachable, coordination degraded — edit allowed`). Upstream's
   design does not need this; ours does.
6. **Pre-commit guard is optional and secondary.** Upstream's guard is *the* authoritative gate
   because MCP tool calls are voluntary. loom's authoritative gate is the PreToolUse hook, which fires
   before the write happens — strictly better, since it prevents the edit rather than the commit.
   Keep `hook/guard.py` as the belt-and-braces catch for edits made outside Claude Code
   (PLAN §3 already marks it optional).
7. **Chain-runner: POSIX only, ~40 lines.** Adopt the `.orig` preservation + `hooks.d/NN-name`
   ordering + marker-comment idempotency. Drop the Windows shims (`.cmd`/`.ps1`), the shebang-dispatch
   fallbacks, and the husky-v9 argv0 special case. Same idea applies to `loom init` writing
   `.claude/settings.json`: **merge into the existing hooks array, never overwrite**, and make reruns
   idempotent by matching on our command string.
8. **Naming.** Upstream calls them reservations/leases with a holder agent. loom's owner is a *plan*,
   not an agent (`claims(node_id, plan_id, mode)` per PLAN §4.1), and the TTL lives on the plan row
   (`plans.ttl_expires`), so one renewal covers all of a plan's claims. Do not port a per-claim TTL
   column.
9. **Defaults, tuned for our loop (canonical TTL set, GATE-1 fix 6 — same text in beads.md ADAPT
   #4).** Claim TTL **1800 s**; keep the 60 s floor. Because loom's hook calls `check` on *every*
   edit, renewal is **implicit**: every `check()` from the plan's own agent resets
   `ttl_expires = max(current, now + 1800)`; `renew(plan_id)` stays as the explicit escape hatch for
   long think-time gaps. Cannot-renew-after-expiry holds (re-declare). Free heartbeat, no extra tool
   call, and it makes "crashed agent's claim expires" the *only* way a claim dies unowned.
10. **Escape hatch is audited.** Upstream's bypass writes one stderr line. loom writes an `events`
    row (`actor`, `action="bypass"`, `detail=node_id`) so the audit trail in PLAN §4.1 stays complete
    and the eval harness can count bypasses as a failure metric.

---

## 4. REJECT — mentioned or implied by the plan, but not taken

- **The mail/inbox system itself** (messages, threads, recipients, ack/read receipts, ack TTL and
  escalation, importance, attachments, unified inbox). loom's channel is the deny message plus the
  embedded spec. Agent-to-agent messaging is a whole second product and PLAN §0 explicitly drops the
  runtime dependency on Agent Mail.
- **Git-backed JSON artifacts as coordination state.** Upstream writes a JSON file per reservation
  into a git archive and the guard reads *those*, with a compensating-delete dance when the archive
  write fails after the DB commit (upstream #180). Two stores, two-phase commit, drift. PLAN §1 locks
  us to one SQLite store; do not reintroduce a file mirror. If we want human-auditable artifacts, the
  `events` table plus a read-only render is the v2 answer.
- **Glob / pathspec machinery**: gitignore-style pattern compilation, LRU-cached specs, symmetric
  cross-matching for overlap, union PathSpecs, `core.ignorecase` folding, virtual-namespace schemes
  (`tool://`, `resource://`, `service://`), suspicious-pattern detection. All are consequences of
  path-glob granularity, which we replaced with symbol IDs.
- **Activity-based staleness** (mtime scans, git rev walks per lease, mail-activity signals,
  `stale_reasons` vocabulary, agent auto-retirement sweeps). See ADAPT 3.
- **Build slots** (coarse per-project advisory locking with its own acquire/renew/release trio).
  loom's contention answer is symbol granularity plus, at v2, waitlists and hot-node policies
  (PLAN §7) — which the MVP addendum already cuts.
- **Window identities / session bindings / agent links / contact handshakes.** loom's identity is one
  agent token per user minted by `loom init` (PLAN §4.5, §7). Cross-project link approval flows are
  out of scope.
- **Pre-push guard twin.** Doubles the surface area for near-zero marginal catch once the PreToolUse
  hook exists. If we ever want it, it is the same body with a different path source.
- **Server-side enforcement of claims on API writes** (upstream blocks message writes when an archive
  path is reserved). loom's server stays advisory by design; enforcement lives in the hook. Keeping
  that boundary sharp is what makes fail-open coherent.
- **Windows/husky chain-runner support**, rich-console tool logging panels, and the LLM-assisted
  helpers. Not MVP.

---

## 5. CORRECTIONS to PLAN-v1.md

1. **§2 attribution and license — confirmed, and the plan understates it.** The clone's `LICENSE` is
   titled "MIT License (with OpenAI/Anthropic Rider)", © 2026 Jeffrey Emanuel, repo
   github.com/Dicklesworthstone/mcp_agent_mail. The plan's header note (a) is right that this is
   patterns-only. What it does not say: the rider's "use" definition **explicitly names benchmarking,
   testing, analyzing, indexing, and evaluation harnesses**. Add a hard constraint to §6 Eval design:
   *the eval harness must never target, import, or measure this repo.* Also add to §2: the clone is
   never vendored or committed, and is deleted after extraction.

2. **§2 "the guard script shape" needs to be split in two.** The plan lands this source's guard
   contribution in `hook/gate.py` (message format) and `server/claims.py` (TTL sweeper). But the guard
   proper is a *pre-commit* artifact, and its most valuable half is the **installer** discipline
   (hooksPath resolution, `.orig` preservation, `hooks.d/NN-name` chaining, idempotent reinstall,
   first-class uninstall). That belongs to the `loom init` writer in §4.5 — which currently says only
   "Registers the hook in `.claude/settings.json`" with no word about what happens when a hook is
   already registered. §4.5 must state: merge, never overwrite; idempotent on rerun; `loom uninit`
   restores prior state.

3. **§4.1 claims table is missing the tombstone and the orphan rule.** `claims(node_id, plan_id, mode)`
   has nowhere to record a release and no defense against a claim whose plan row vanished. Amend:
   `claims(node_id, plan_id, mode, created, released)` with active = `released IS NULL` AND the owning
   plan `status='active'` AND `ttl_expires > now`; release sets `released`, never deletes (the audit
   trail in §4.1 depends on it). And the sweep must find claims by **left** join to plans so an
   orphaned claim is releasable rather than immortal (upstream #161).

4. **§4.2 `renew(plan_id)` is underspecified.** Fix the contract (canonical TTL set, GATE-1 fix 6):
   new expiry = `max(ttl_expires, now + 1800)`, floor 60 s (clamped up), and **an already-expired or
   non-active plan cannot be renewed — it returns `{renewed: 0}` and the agent must re-declare.**
   Without the "cannot renew after expiry" rule, TTL stops being a liveness guarantee. Default claim
   TTL at `declare_plan`: **1800 s**, implicitly renewed by every `check()` from the owning agent.

5. **§4.2 / §2 "TTL sweeper" implies a background thread; it should be lazy-first.** Upstream's own
   primary mechanism is a lazy sweep at the top of every lease-touching call, with the 60 s background
   loop as a backstop. State the lazy sweep as the MVP requirement (§4.2, on `check` / `declare_plan` /
   `rescope` / `list_claims`) and move the periodic worker to the same v1.1 bucket as the other §7
   items. This also removes any need for a scheduler in M2.

6. **§4.3 deny message is missing the expiry instant and a machine-readable envelope.** The specified
   stderr (`claimed by <agent> under plan <id>: <title>. Its spec follows.`) omits *when the claim
   expires*, which is the single most actionable field — it converts "blocked" into "blocked for 22
   more minutes", making waiting a costed choice. Add `expires <ISO8601> (in <N>m)`. Also add: the
   hook emits the same facts as a structured payload (`{error: {type, message, recoverable, data:
   {..., suggested_tool_calls}}}`) so tooling need not scrape stderr. Upstream's `recoverable` flag
   and pre-filled `suggested_tool_calls` array are the concrete precedent.

7. **§7 "Server down means the hook fails open with a loud warning line" needs the same treatment at
   commit time.** The MVP addendum fixes the 2 s timeout for the PreToolUse hook only. If we ship
   `hook/guard.py`, it inherits the identical requirement — plus the loud-bypass rule: an emergency
   bypass must print that it fired *and* write an `events` row. Silence here would let a bypassed
   commit look identical to a clean one in the eval.

8. **§1's "advisory, never hard locks" is not in tension with §4.2's atomic all-or-nothing — say so.**
   A reader who studies this source will find the opposite convention (always grant, report conflicts
   alongside) and may "fix" our design toward it. Add one clarifying sentence to §1: *advisory* means
   TTL-bounded, renewable, fail-open, and never an OS-level lock — it does not mean `declare_plan`
   grants overlapping write claims. The source itself validates the split: its claim API is advisory
   while its commit guard defaults to `block`.

9. **§0 note (c) gains a second, independent witness.** The check-then-act lock lesson is not just a
   specgate finding; this source hit the same class of bug twice in production (duplicate exclusive
   holders; phantom conflicts after release) and fixed both by wrapping the whole read-check-write
   cycle in an immediate write transaction. Promote it from a lesson to a stated requirement in §4.2:
   `declare_plan`, `rescope`, `renew`, and the sweeper each run inside one `BEGIN IMMEDIATE`
   transaction, with a `busy_timeout` pragma set so contenders queue rather than error.
