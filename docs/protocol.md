# The loom protocol

What an agent has to know to work inside a loom-gated repository. This page is current against the
code; where it and `BUILD-SPEC.md` disagree, this page and the code are right.

Two surfaces:

- **MCP tools** at `http://<host>:<port>/mcp` — how an agent declares intent and asks questions.
- **`POST /gate`** — a plain HTTP route the PreToolUse hook calls before every edit. Agents never
  call it; it is documented here because it defines what "blocked" means.

---

## Node refs and the grammar of a target

A **node ref** names one indexed symbol:

```
relative/path/to/file.py::Class/method      a method
relative/path/to/file.py::function          a top-level function
relative/path/to/file.py::Class             a class
relative/path/to/file.py                    the file itself
```

`::` joins the path to the symbol; `/` separates the parts of the symbol name (Serena's
convention). Paths are repo-root-relative and POSIX-normalized.

A **node id** is `n-` followed by eight base-36 characters. It is minted server-side from
`(repo, path, qualname)`, so the same symbol has the same id on every machine and a rename mints a
new one. A **plan id** is `lm-` followed by six base-36 characters.

`resolve_nodes` accepts anything from a bare name to a full ref and answers with node ids. It
resolves through a ladder — exact ref, exact path, path suffix on a `/` boundary, then a guarded
fuzzy tail. The fuzzy rung refuses short or ambiguous queries (fewer than 4 characters, or a tail
shared by more than 3 symbols) and returns suggestions instead of guessing, because a single
accidental match here becomes a claim.

---

## The nine tools

Every tool returns a JSON object. Tools that act on a repository take an optional `repo`; with one
served repository it may be omitted, and with several an unserved name returns
`{"ok": false, "reason": "unknown_repo", "served": [...]}`.

### `health() -> {ok, repo, nodes, active_plans, version}`

Liveness. `nodes` is the indexed node count for the default repo, `active_plans` the count of
plans that have not expired or been released.

### `resolve_nodes(queries: list[str], repo="") -> {ok, resolved}`

`resolved` is one entry per query:

```json
{"query": "authenticate",
 "matches": [{"node_id": "n-...", "ref": "svc.py::AuthService/authenticate",
              "path": "svc.py", "qualname": "AuthService/authenticate", "kind": "Function"}],
 "suggestions": []}
```

More than one match means the query is ambiguous — pick one and re-ask with the full ref. No
matches means `suggestions` carries near misses.

### `declare_plan(agent, title, spec_md, write_targets, assumes=[], branch="", repo="", ttl_s=1800)`

The only way to become allowed to edit. All-or-nothing: either every target is claimed or nothing
is.

`spec_md` must contain these five headings, be at most 60 lines and 8000 characters, and contain no
unfilled template placeholder:

```
## Goal
## Write targets
## New/changed interfaces
## Assumes
## Out of scope
```

The template lives at `src/loom/templates/spec.md` in the loom repository, and `loom init` prints
its installed path. The cap exists because the spec is embedded verbatim in every deny message
another agent receives.

`write_targets` are the symbols you will change. `assumes` are the symbols you depend on but will
not touch; they are claimed in read mode, and another agent writing to one of them warns rather
than blocks.

**Granted:**

```json
{"ok": true, "plan_id": "lm-xxxxxx",
 "expires_ts": 1770000000.0, "expires_iso": "2026-08-19T12:00:00Z",
 "claimed_write": ["n-...", "..."], "claimed_read": ["n-..."],
 "expanded_from": {"n-target": ["n-neighbour"]},
 "warnings": [ /* read/write mismatches — advisory */ ]}
```

`claimed_write` is larger than what you asked for: each write target also claims its **one-hop
CALLS neighbours in both directions**, and `expanded_from` shows which target pulled in which
neighbour. IMPORTS edges are never expanded.

**Refused, conflict:**

```json
{"ok": false, "reason": "conflict",
 "conflicts": [{"kind": "write-write", "node_id": "n-...", "ref": "svc.py::login",
                "owner_agent": "aria", "owner_plan_id": "lm-...", "owner_title": "...",
                "owner_spec_md": "<the full spec>", "owner_expires_ts": 1770000000.0}]}
```

Nothing was claimed. `owner_spec_md` is the whole point: read it, build against the interfaces it
declares, narrow your targets, and declare again.

A claim is judged against its **containment scope** — the symbol's class and file above it, and
everything it contains below it. A claim on a file therefore covers every symbol in that file, and
a claim on a method collides with a claim on its class. Siblings do not collide: two agents may
hold two unrelated functions in the same file, which is the difference between loom and a file
lock.

**Refused, bad spec:**

```json
{"ok": false, "reason": "validation",
 "validation_errors": ["missing heading: ## Assumes"],
 "unresolved": [{"query": "handel_login", "suggestions": ["auth.py::handle_login"]}]}
```

### `check(agent, node, repo="") -> allow/deny`

"May I edit this right now?" Same judgement as the edit-time gate, without editing anything.

```json
{"allow": true,  "case": "in_plan", "plan_id": "lm-..."}
{"allow": false, "case": "foreign_claim", "message": "loom: BLOCKED — ...",
 "owner": { /* conflict object */ }, "node_id": "n-..."}
```

An ambiguous `node` comes back as a resolution request rather than a decision.

### `rescope(plan_id, add_targets=[], add_assumes=[])`

Widen an active plan **before** touching new ground. Same shapes, same atomicity and the same
one-hop expansion as `declare_plan`; existing claims are untouched, and success renews the TTL.
`{"ok": false, "reason": "not_active"}` means the plan expired or was released — declare a new one.

### `get_plan(plan_id) -> {ok, plan}`

The full spec and current claim refs of any plan, yours or not. This is how you read the plan a
deny message named. `{"ok": false, "reason": "unknown_plan"}` if there is no such plan.

### `list_claims(repo="") -> {ok, claims}`

Every active claim in the repository with its owner, plan, mode and expiry. Runs a TTL sweep first,
so the answer is current.

### `renew(plan_id) -> {renewed, ...}`

Extend an active plan's TTL to `max(current, now + 1800s)`.

```json
{"renewed": 1, "expires_ts": 1770000000.0, "expires_iso": "..."}
{"renewed": 0, "reason": "expired"}          // also: "released", "unknown_plan"
```

`renewed: 0` is a verdict, not a warning. Declare again; do not keep editing.

### `release(plan_id, agent, status="done")`

Owner-only. Frees every claim the plan holds and closes it.

```json
{"ok": true, "released_claims": 5, "plan_status": "done"}
{"ok": false, "reason": "not_owner"}         // also: "unknown_plan", "not_active"
```

`status` may be `done` or `superseded`.

---

## The TTL law

- A claim's default lifetime is **1800 seconds**. `ttl_s` below the 60-second floor is clamped up.
- Any successful `check`, gate decision or `rescope` on a plan renews it implicitly to
  `max(current, now + 1800s)`.
- **An expired plan is never renewed.** Once past its deadline it is gone; declare a new one.
- Expiry is enforced by the read filter, so a plan stops protecting anything the instant it
  expires, whether or not a sweep has run. The lazy sweep tombstones rows an hour later.

A crashed agent therefore frees its claims by itself, which is why claims are advisory leases and
not locks.

---

## `POST /gate` — the edit-time wire

Called by `loom-gate`, the PreToolUse hook, once per edit. **Request:**

```json
{"agent": "aria", "repo": "api", "path": "src/svc.py", "qualname": "AuthService/authenticate",
 "tool_name": "Edit"}
```

`qualname` may be `null` for a whole-file edit. **Response — always HTTP 200, always exactly these
five keys:**

```json
{"decision": "allow" | "deny", "case": "<one of the six below>",
 "message": "<text shown to the agent on deny, else empty>",
 "node_id": "n-..." | null, "plan_id": "lm-..." | null}
```

The hook maps `deny` to exit code 2 with `message` on stderr, and everything else to exit 0 in
silence.

### The six cases

| `case` | Decision | What it means |
|---|---|---|
| `in_plan` | allow | The edit is inside a live claim of yours. Silent. |
| `foreign_claim` | **deny** | Someone else holds a write claim on this symbol or on a container of it. The message carries their whole spec. |
| `out_of_scope` | **deny** | You have an active plan, but this symbol is not in it. The message names the exact `rescope` call to make. |
| `no_plan` | **deny** | You have no active plan at all. Write a spec, resolve targets, declare. |
| `new_path` | allow | The file exists in the repository but has no indexed nodes — a new file. Creating files is never gated. |
| `unindexed` | allow | This repository has no graph at all, or is not served by this server. loom has nothing to judge with, so it does not pretend to judge. |

`unindexed` is the silent-failure case worth knowing: a checkout whose configured repo name is not
one the server serves will be allowed forever and look perfectly healthy. `loom doctor`'s
"repo match" row is the check that catches it.

### Fail-open

If the server is unreachable, slow, or answers anything other than a well-formed 200, the hook
**allows the edit** and prints one loud warning. It has a 1.5-second socket timeout and a
2.5-second hard wall deadline, so no server can stall an edit by dribbling a response. Coordination
degrades; work never stops.

No deny message ever names a way around a claim. There is a human escape hatch and it is documented
for humans only, in [troubleshooting.md](troubleshooting.md).
