# Extraction: beads (Go) → loom

**Source clone root:** `/private/tmp/claude-501/-Users-cero-Desktop-PROJECTS-reforge-workspace-re-forge-irl-data-team-collab/6458dacd-1b63-4e60-82c7-dac1ea52eb51/scratchpad/vendor/beads`
All `file:line` provenance below is **relative to that root** (e.g. `internal/idgen/hash.go:55`).

**Go module path:** `github.com/steveyegge/beads` (`go.mod:1`). Backend is Dolt/MySQL-dialect SQL, not SQLite — every SQL excerpt needs dialect translation (noted per item).

Scope of this extraction, per the assignment: (1) hash-based short-ID minting, (2) the claim/status state machine including expiry, (3) CLI verb ergonomics. Everything else in the repo (Dolt federation, molecules/wisps, formulas, trackers) is out of scope and implicitly rejected.

---

## 1. LICENSE

`LICENSE` (repo root), verbatim header:

> MIT License
>
> Copyright (c) 2025 Beads Contributors

Standard unmodified MIT text follows (permission to use, copy, modify, merge, publish, distribute, sublicense, sell; the copyright + permission notice must be included in all copies or substantial portions; AS-IS warranty disclaimer).

**Restriction that matters:** exactly one — the notice-retention clause. Verbatim excerpts and derived code are fine provided loom ships an attribution notice. `THIRD_PARTY_LICENSES` exists at the repo root but concerns beads' own dependencies, not us.

**Action for loom:** add to `loom/THIRD_PARTY_NOTICES.md`:

```
Portions of loom's ID-minting and claim-lease logic are derived from
beads (https://github.com/steveyegge/beads), MIT License,
Copyright (c) 2025 Beads Contributors.
```

Since we are translating Go → Python and restructuring, most of what lands is pattern, but the notice costs nothing and covers the excerpts quoted below.

---

## 2. ADOPT

### 2.1 Hash-based short-ID minting

**IMPORTANT — there are two `GenerateHashID` functions in this repo. Only one is live.**

| Function | File | Status |
|---|---|---|
| `idgen.GenerateHashID` (base36, nonce, length param) | `internal/idgen/hash.go:55` | **LIVE.** Called from `internal/storage/issueops/helpers.go:202`, `internal/storage/domain/issue.go:1632`, `internal/storage/dolt/issues.go:913`, `internal/linear/mapping.go:99` |
| `types.GenerateHashID` (hex, RFC3339Nano, workspaceID) | `internal/types/id_generator.go:29` | **DEAD.** Zero non-test callers. Legacy. Do not port. |

Port the base36 one.

#### 2.1.1 The hash input and encoding (verbatim, `internal/idgen/hash.go:11-85`)

```go
// base36Alphabet is the character set for base36 encoding (0-9, a-z).
const base36Alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"

// EncodeBase36 converts a byte slice to a base36 string of specified length.
func EncodeBase36(data []byte, length int) string {
	num := new(big.Int).SetBytes(data)
	var result strings.Builder
	base := big.NewInt(36)
	zero := big.NewInt(0)
	mod := new(big.Int)

	chars := make([]byte, 0, length)
	for num.Cmp(zero) > 0 {
		num.DivMod(num, base, mod)
		chars = append(chars, base36Alphabet[mod.Int64()])
	}
	for i := len(chars) - 1; i >= 0; i-- {
		result.WriteByte(chars[i])
	}
	str := result.String()
	if len(str) < length {
		str = strings.Repeat("0", length-len(str)) + str   // left-pad zeros
	}
	if len(str) > length {
		str = str[len(str)-length:]                        // keep LEAST-significant digits
	}
	return str
}

func GenerateHashID(prefix, title, description, creator string, timestamp time.Time, length, nonce int) string {
	// Combine inputs into a stable content string; nonce handles hash collisions
	content := fmt.Sprintf("%s|%s|%s|%d|%d", title, description, creator, timestamp.UnixNano(), nonce)

	hash := sha256.Sum256([]byte(content))

	var numBytes int
	switch length {
	case 3: numBytes = 2  // 2 bytes = 16 bits ≈ 3.09 base36 chars
	case 4: numBytes = 3  // 3 bytes = 24 bits ≈ 4.63 base36 chars
	case 5: numBytes = 4  // 4 bytes = 32 bits ≈ 6.18 base36 chars
	case 6: numBytes = 4
	case 7: numBytes = 5  // 5 bytes = 40 bits ≈ 7.73 base36 chars
	case 8: numBytes = 5
	default: numBytes = 3
	}

	shortHash := EncodeBase36(hash[:numBytes], length)
	return fmt.Sprintf("%s-%s", prefix, shortHash)
}
```

Exact spec, restated so a coder never has to open the source:

- Hash input string: `f"{title}|{description}|{creator}|{timestamp_unix_nanos}|{nonce}"`, UTF-8, SHA-256.
- Take the **first** `numBytes` of the 32-byte digest (`hash[:numBytes]`) per the length→bytes table above.
- Interpret those bytes as a **big-endian unsigned integer**, render base36 with alphabet `0123456789abcdefghijklmnopqrstuvwxyz`.
- If shorter than `length`, **left-pad with `0`**. If longer, **truncate from the left** (keep the least-significant digits).
- Final ID: `f"{prefix}-{short}"`.

Note the length-6 and length-8 cases reuse the byte width of 5 and 4 respectively — so the 6-char ID is the 5-char ID with one padded/carried leading digit, and the 8-char is the 7-char likewise. The test vector below makes this visible (`bi3tk` → `8bi3tk`, `r5sr6bm` → `8r5sr6bm`). Preserve it; it is not a bug, it is why "extend the length" is cheap.

#### 2.1.2 Golden test vector — port this verbatim as a pytest (`internal/idgen/hash_test.go:8-30`)

```go
timestamp := time.Date(2024, 1, 2, 3, 4, 5, 6*1_000_000, time.UTC)
prefix, title, description, creator := "bd", "Fix login", "Details", "jira-import"

tests := map[int]string{
	3: "bd-vju",     4: "bd-8d8e",     5: "bd-bi3tk",
	6: "bd-8bi3tk",  7: "bd-r5sr6bm",  8: "bd-8r5sr6bm",
}
// GenerateHashID(prefix, title, description, creator, timestamp, length, 0)
```

Python equivalent input: `datetime(2024, 1, 2, 3, 4, 5, 6000, tzinfo=timezone.utc)`, nanos `1704164645006000000`, nonce 0.

**Verified**: the spec in §2.1.1, implemented in Python exactly as written, reproduces all six strings. Confirmed by running it during this extraction — the port is not guesswork.

**Float trap, hit while verifying**: `int(ts.timestamp() * 1e9)` yields `1704164645006000128`, not `...000000`, and every ID comes out wrong. Compute nanos in integer arithmetic:
```python
nanos = int(ts.timestamp()) * 1_000_000_000 + ts.microsecond * 1_000
# or, since Python 3.7:  time.time_ns() for "now"
```
Go's `timestamp.UnixNano()` is exact by construction, so this bug only exists on our side of the port. It is silent — the IDs are still well-formed, just not the ones a Go peer would mint.

#### 2.1.3 The actual uniqueness guarantee: mint-and-check inside the transaction

The hash alone does **not** guarantee cross-machine uniqueness. The guarantee is a collision loop against the shared store, inside the same transaction that inserts. `internal/storage/issueops/helpers.go:176-217`:

```go
func GenerateIssueIDInTable(ctx context.Context, tx DBTX, table, prefix string, issue *types.Issue, actor string) (string, error) {
	// ... counter-mode branch omitted (see REJECT) ...
	baseLength, err := GetAdaptiveIDLengthTx(ctx, tx, table, prefix)
	if err != nil { baseLength = 6 }

	maxLength := 8
	if baseLength > maxLength { baseLength = maxLength }

	for length := baseLength; length <= maxLength; length++ {
		for nonce := 0; nonce < 10; nonce++ {
			candidate := idgen.GenerateHashID(prefix, issue.Title, issue.Description, actor, issue.CreatedAt, length, nonce)

			var count int
			err = tx.QueryRowContext(ctx, fmt.Sprintf(`SELECT COUNT(*) FROM %s WHERE id = ?`, table), candidate).Scan(&count)
			if err != nil { return "", fmt.Errorf("failed to check for ID collision: %w", err) }
			if count == 0 { return candidate, nil }
		}
	}
	return "", fmt.Errorf("failed to generate unique ID after trying lengths %d-%d with 10 nonces each", baseLength, maxLength)
}
```

Nested loop: 10 nonces at the current length, then lengthen by one, up to 8. All reads and the eventual INSERT share one transaction.

#### 2.1.4 Adaptive base length — birthday bound from live row count

`internal/storage/issueops/helpers.go:319-395`:

```go
func GetAdaptiveIDLengthTx(ctx context.Context, tx DBTX, table, prefix string) (int, error) {
	var count int
	err := tx.QueryRowContext(ctx, fmt.Sprintf(`
		SELECT COUNT(*) FROM %s
		WHERE id LIKE CONCAT(?, '-%%')
		  AND INSTR(SUBSTRING(id, LENGTH(?) + 2), '.') = 0
	`, table), prefix, prefix).Scan(&count)
	if err != nil { return 6, err }
	cfg := GetAdaptiveConfigTx(ctx, tx)
	return ComputeAdaptiveLength(count, cfg), nil
}

func DefaultAdaptiveConfig() AdaptiveIDConfig {
	return AdaptiveIDConfig{MaxCollisionProbability: 0.25, MinLength: 3, MaxLength: 8}
}

func ComputeAdaptiveLength(numIssues int, cfg AdaptiveIDConfig) int {
	const base = 36.0
	for length := cfg.MinLength; length <= cfg.MaxLength; length++ {
		totalPossibilities := math.Pow(base, float64(length))
		exponent := -float64(numIssues*numIssues) / (2.0 * totalPossibilities)
		prob := 1.0 - math.Exp(exponent)
		if prob <= cfg.MaxCollisionProbability { return length }
	}
	return cfg.MaxLength
}
```

Birthday approximation `p = 1 - exp(-n² / (2·36^L))`; pick the shortest `L` with `p ≤ 0.25`. Config keys read from a `config(key,value)` table: `max_collision_prob`, `min_hash_length`, `max_hash_length` (`GetAdaptiveConfigTx`, same file). Note the deliberately loose 0.25 threshold — a 25% *expected-somewhere* collision rate is fine precisely because §2.1.3 checks every candidate anyway; the bound only picks the length that keeps the loop cheap.

The COUNT excludes hierarchical children (IDs containing `.` after the prefix) so the population is root IDs only.

**Python port notes (loom `server/ids.py`):**
- base36: `n = int.from_bytes(digest[:num_bytes], "big")`, then repeated `divmod(n, 36)` collecting `ALPHABET[r]`, reverse, `.rjust(length, "0")`, then `s[-length:]`.
- SQLite dialect: `CONCAT(?, '-%')` → `? || '-%'`; `INSTR(SUBSTRING(id, LENGTH(?)+2), '.') = 0` → `instr(substr(id, length(?)+2), '.') = 0`.
- Wrap the whole mint+insert in `BEGIN IMMEDIATE` so the COUNT and the INSERT are one writer-locked unit. Without IMMEDIATE, SQLite starts the transaction as a deferred reader and the write-lock upgrade can fail with `SQLITE_BUSY` after the COUNT already passed — the exact check-then-act race the plan says the single server kills.

#### 2.1.5 Hierarchical child IDs and prefix extraction

`internal/types/id_generator.go:43-101` (this part of the dead file is still worth copying as a convention — it is pure string manipulation, no hashing):

```go
func GenerateChildID(parentID string, childNumber int) string { return fmt.Sprintf("%s.%d", parentID, childNumber) }
// "bd-af78e9a2.1.2" → rootID "bd-af78e9a2", parentID "bd-af78e9a2.1", depth 2
const MaxHierarchyDepth = 3
func ExtractPrefix(id string) string  // everything up to and including the first hyphen; "" if none
```

Useful for loom sub-plans if we ever add them; not needed for MVP.

### 2.2 Claim / status state machine

#### 2.2.1 The real status set (`internal/types/types.go:504-524`)

```go
type Status string
const (
	StatusOpen       Status = "open"
	StatusInProgress Status = "in_progress"
	StatusBlocked    Status = "blocked"
	StatusDeferred   Status = "deferred" // Deliberately put on ice for later
	StatusClosed     Status = "closed"
	StatusPinned     Status = "pinned"   // Persistent bead that stays open indefinitely
	StatusHooked     Status = "hooked"   // Work actively claimed by a worker
)
```

Status *categories* (`internal/types/types.go:563-575`) drive visibility: `active` (shows in `ready` and default `list`), `wip` (hidden from `ready`), `done`, `frozen`, `unspecified`.

The **claimable-from** set is derived, not hardcoded — `open` plus any custom status whose category is `active` (`internal/storage/issueops/claim.go:341-353`). WIP/done/frozen customs are excluded so an `in_progress` row is never silently re-claimable (their GH-3570 anti-steal fix).

**There is no `claimed` status and no `expired` status.** See CORRECTIONS §5.

#### 2.2.2 The claim CAS shape (`internal/storage/issueops/claim.go:36-227`)

This is the single most valuable pattern for `server/claims.py`. Reduced to its skeleton, with all Dolt-specific machinery stripped (see REJECT):

1. **Read the pre-image inside the transaction** — `oldIssue, err := GetIssueInTx(ctx, tx, id)` (line 47). Everything after judges against this snapshot.
2. **Judge claimability in application code, not in SQL** (line 91):
   ```go
   assigneeOK := oldIssue.Assignee == "" || actorMatches(oldIssue.Assignee, actor) || slices.Contains(pools, oldIssue.Assignee)
   ```
   Their stated reason (line 80-90): a spelling-sensitive string comparison embedded in SQL is fragile; keep identity logic in one place in Go. For loom: the same argument applies to plan-ID/agent-ID comparisons.
3. **Conditional UPDATE, skipped entirely when the pre-check already lost** (lines 108-136):
   ```sql
   UPDATE issues
   SET assignee = ?, status = 'in_progress', updated_at = ?, started_at = ?, row_lock = ?
   WHERE id = ? AND row_lock = ? AND status IN (<claimable statuses>)
   ```
   `started_at` is only set on the *first* transition to in_progress; a re-claim preserves the original start time (comment at line 104).
4. **`rowsAffected == 0` is the verdict, not an error** — re-read the coordination columns to disambiguate (lines 138-141, and the shared reader at 235-243):
   ```go
   func readClaimStateInTx(ctx, tx, issueTable, id) (string, types.Status, error) {
       // SELECT assignee, status FROM <table> WHERE id = ?
   }
   ```
5. **Idempotent re-claim by the same actor is a success, not a conflict** (lines 143-149) — explicitly to support agent retry after a transient failure:
   ```go
   if actorMatches(assignee, actor) && currentStatus == types.StatusInProgress {
       return &ClaimResult{OldIssue: oldIssue, IsWisp: isWisp}, nil
   }
   ```
6. **The refusal is typed and carries the losing state** (lines 189-194):
   ```go
   return nil, &publicops.ClaimConflictError{
       IssueID:  id,
       Assignee: assignee,
       Status:   currentStatus,
       Err:      refusal,
   }
   ```
   The caller reads `.Assignee` / `.Status` as fields — no message parsing. `claim.go` (repo root, the public shim) documents at lines 10-30 that the older string-parsing path (`ParseClaimConflict`, root `claim.go:52-61`, keying on exported message fragments with `strings.LastIndex`) is legacy precisely because it is "a deliberately string-coupled shim."
   **Take the lesson, not the shim:** loom's `declare_plan` conflict response must be structured data (owner agent, plan id, title, spec_md), never prose the caller has to parse. The plan already says this; beads is the evidence for why.
7. **Grant the lease only after the CAS wins** (lines 201-205), then record an event (215-224).

#### 2.2.3 Refusal copy — the wy-yuclk lesson. Copy this into `hook/gate.py`.

`internal/storage/issueops/claim.go:173-184`, verbatim comment + message:

```go
case currentStatus == types.StatusOpen:
	// Do not name a release command here — not `bd unclaim`, not
	// `bd unclaim --force`. Refusal copy that names one gets
	// pattern-matched by batch agents into an unclaim+claim
	// steamroller of live claims (wy-yuclk). Point at the holder;
	// bd reclaim is safe to name because it only recovers claims
	// whose lease has already expired.
	refusal = fmt.Errorf("%w: already assigned to %q — coordinate with the holder; if their claim is abandoned (crashed agent), lease expiry will surface it for bd reclaim", storage.ErrAlreadyClaimed, assignee)
```

And the same rule in the release path, `internal/storage/issueops/unclaim.go:60-63`:

```go
return fmt.Errorf("%w: %s is held by %s; coordinate with the holder — pass --force only if their claim is abandoned (crashed agent, expired lease)",
	storage.ErrNotOwner, id, oldIssue.Assignee)
```

**This is a hard-won production finding about LLM agents and it directly contradicts naive deny-message design.** An agent reading a deny message will pattern-match any escape hatch named in it and use it. loom's gate deny message must therefore name only actions that are *safe when spammed*: `rescope`, "read the owner's spec and build against its interfaces", "wait for TTL expiry". It must **not** name a force-release or claim-steal command, even as a caveat. PLAN §4.3's deny strings already comply — this section is the justification, and the tripwire for anyone tempted to add "or run `loom release --force`" later.

#### 2.2.4 Leases, TTL, heartbeat, expiry (`internal/storage/issueops/lease.go`)

Constants and the contract (`lease.go:19-43`):

```go
// DefaultLeaseTTL is how long a fresh claim stays valid without a heartbeat.
// A worker is expected to call HeartbeatIssueInTx well within this window ...
// A worker that dies stops heartbeating, its lease_expires_at goes stale, and
// bd reclaim reverts the issue to ready. Tunable per-claim via WithLeaseTTL.
const DefaultLeaseTTL = 5 * time.Minute
```

Lease table columns (from `UpsertLeaseInTx`, `lease.go:170-186`): `leases(issue_id PK, holder, granted_at, lease_expires_at, heartbeat_at, granted_node)`.

```go
// INVARIANT: a leases row exists if and only if its issue is a live claim
// (in_progress with the row's holder as assignee) on this node. Every path
// that ends or transfers a claim — close, unclaim, reclaim, delete, a generic
// update that changes status/assignee ... — must delete the lease row.
func UpsertLeaseInTx(ctx, tx, id, holder string, now time.Time, ttl time.Duration) error {
	// INSERT INTO leases (issue_id, holder, granted_at, lease_expires_at, heartbeat_at, granted_node)
	// VALUES (?, ?, ?, ?, ?, ?)  ON DUPLICATE KEY UPDATE  <all of the above> = VALUES(...)
	// args: id, holder, now, now.Add(ttl), now, NodeID(ctx)
}
```

**Heartbeat = renewal** (`lease.go:288-360`). The whole write is one UPDATE scoped to the holder. Verbatim (`lease.go:353-357`):

```sql
UPDATE leases SET lease_expires_at = ?, heartbeat_at = ?,
    granted_node = IF(COALESCE(granted_node, '') = '', ?, granted_node)
WHERE issue_id = ? AND holder = ?
-- args: now.Add(leaseTTL(ctx)), now, NodeID(ctx), id, actor
```

The `granted_node` clause is a backfill-never-overwrite of the replica identity and is **rejected** for loom (REJECT #2). What loom implements is the two-column form:

```sql
UPDATE plans SET ttl_expires = ? WHERE id = ? AND agent = ? AND status = 'active'
```

and the doc contract (`lease.go:288-296`):

> Only the current holder may heartbeat — a heartbeat from anyone else, or on an issue whose lease is gone (closed, unclaimed, reclaimed ...), affects no rows and returns `storage.ErrNotClaimable` / `ErrAlreadyClaimed` **so the caller learns its lease is gone**.

That is the exact semantics loom's `renew(plan_id)` needs: rows-affected-zero → typed "your plan is no longer active, re-declare". Not silence, not success.

**Expiry is a reaper, not a status.** `ReclaimExpiredLeasesInTx` (`lease.go:624-760`):

```go
// Revert in_progress issues whose lease has gone stale back to ready: the lease
// row is deleted, then status → open, assignee cleared, started_at cleared ...
// An issue is stale when its lease row's lease_expires_at is strictly before cutoff.
// Callers pass cutoff = now - graceWindow (the supervisor uses graceWindow = 2×TTL)
// so only leases that expired a safe margin ago — i.e. workers that are almost
// certainly dead — are reclaimed.
```

The three-step shape, all in one transaction:

```sql
-- 1. Snapshot the stale set (so the reaper can report exactly what it reverted)
SELECT l.issue_id, COALESCE(i.assignee,''), COALESCE(l.granted_node,'')
FROM leases l JOIN issues i ON i.id = l.issue_id
WHERE i.status = 'in_progress' AND l.lease_expires_at < ?     -- ? = cutoff

-- 2. Per row, DELETE that RE-CHECKS the expiry predicate
DELETE FROM leases WHERE issue_id = ? AND lease_expires_at < ?
--    rows==0  →  "rescued by a concurrent heartbeat — leave it be"  (continue)

-- 3. Only if the DELETE matched, revert the issue, re-checking status
UPDATE issues SET status = 'open', assignee = NULL, started_at = NULL, updated_at = ?, ...
```

The **snapshot → per-row re-check → skip-on-zero** structure is the transferable part: it makes the sweeper safe against a renewal that lands mid-sweep without holding a long lock, and it yields an exact "here is what I reclaimed" report. Copy it wholesale into loom's TTL sweeper.

Full lifecycle, as beads actually implements it:

```
                 claim (CAS: status IN claimable AND pre-image unchanged)
   open ─────────────────────────────────────────────────► in_progress
     ▲                                                       │  + leases row (expires = now+TTL)
     │                                                       │
     │  unclaim  (owner only, or --force)                    │  heartbeat: expires = now+TTL
     ├───────────────────────────────────────────────────────┤  (holder-scoped; 0 rows ⇒ lease gone)
     │                                                       │
     │  reclaim  (lease_expires_at < now-grace, grace=2×TTL)  │
     └───────────────────────────────────────────────────────┤
                                                             │  close  (alias: done)
                                                             ▼
                                                          closed  ── reopen ──► open
```

`unclaim` (`internal/storage/issueops/unclaim.go:34-110`) mirrors the claim CAS exactly: pre-checks (cannot unclaim `closed`; must have an assignee; owner-only unless `force`), conditional `UPDATE ... SET assignee='', status='open', started_at=NULL WHERE id=? AND status IN ('open','in_progress') AND <pre-image token unchanged>`, `rowsAffected==0` → re-read and disambiguate, then a shared tail `finishUnclaimInTx` that deletes the lease and records an `unclaimed` event (lines 117-134). Note that `force` widens *who* may release, but does **not** exempt the pre-image check (comment at line 76-78) — a good invariant for loom's `release(plan_id)`.

There is also a **conditional release**: `UnclaimIssueIfAssigneeInTx` (`unclaim.go:153-228`) releases only while the issue is still held by `expectedAssignee`, returning `ErrAssigneeMismatch` naming the current holder otherwise. loom wants this shape for merge-webhook auto-release: release my plan's claims only if they are still mine.

#### 2.2.5 Claim-the-first-ready, atomically (`internal/storage/issueops/claim.go:248-301`)

```go
func ClaimReadyIssueInTx(ctx, tx, filter types.WorkFilter, actor string) (*types.Issue, error) {
	claimFilter := filter
	claimFilter.Status = types.StatusOpen
	claimFilter.Unassigned = true
	claimFilter.Limit = 0; claimFilter.MaxRows = 0   // scan unbounded — see below

	readyIssues, err := GetReadyWorkInTx(ctx, tx, claimFilter)
	for _, issue := range readyIssues {
		if _, err := ClaimIssueInTx(ctx, tx, issue.ID, actor); err != nil {
			if errors.Is(err, storage.ErrAlreadyClaimed) || errors.Is(err, storage.ErrNotClaimable) { continue }
			return nil, err
		}
		return GetIssueInTx(ctx, tx, issue.ID)
	}
	return nil, nil
}
```

Two things worth stealing: the readiness scan and the claim are in **one transaction**, and a row that loses to a racing agent is **skipped, not fatal** — the loop walks on. The long comment at lines 260-281 is a warning: they had to explicitly clear row-cap settings here, because a global "max rows" guard sized for bulk listing made `claim` spuriously report "nothing to claim". Generalized lesson for loom: a pagination/limit knob applied uniformly to reads will silently corrupt a claim path that walks past unclaimable candidates. Keep the claim scan uncapped.

### 2.3 CLI verb ergonomics

Everything here is cobra-based (`cmd/bd/`). The transferable material is flag/output *conventions*, not the framework.

**Verbs that exist** (see CORRECTIONS §5 for what does not):

| loom's plan wanted | beads actually has | provenance |
|---|---|---|
| `ls` | `bd list` (no `ls` alias) | `cmd/bd/list.go:153-156` |
| `show` | `bd show [id...] [--id=<id>...] [--current]`, alias `view` | `cmd/bd/show.go:17-23` |
| `claim` | `bd ready --claim` and `bd update <id> --claim` | `cmd/bd/ready.go:718`, `cmd/bd/update.go:987` |
| `done` | `bd close [id...]`, **alias `done`** | `cmd/bd/close.go:22-26` |
| (release) | `bd unclaim <id> [--force]` | `cmd/bd/unclaim.go` |
| (expiry) | `bd reclaim [--older-than D] [scope flags]` | `cmd/bd/reclaim.go:16-19` |
| (renew) | `bd heartbeat` | referenced `lease.go:20`, `cmd/bd/reclaim.go:22-24` |

**Adopt these ergonomics:**

1. **`--claim` as a flag on the query verb, not a separate verb.** `bd ready --claim --json` = "find the first ready item matching my filters and take it, atomically." One round trip, no TOCTOU. `readyCmd.Flags().Bool("claim", false, "Atomically claim the first ready issue matching the filters")` (`cmd/bd/ready.go:718`). loom equivalent: `loom ls --claim`, or more likely the MCP tool doing declare-and-claim in one call, which PLAN §4.2 already specifies.

2. **Mutually-exclusive combinations rejected loudly**, not silently ignored (`cmd/bd/ready.go:92, 100, 107`):
   ```go
   if claimReady { return HandleErrorRespectJSON("--claim cannot be combined with --gated") }
   // ... likewise --mol, --explain
   ```

3. **JSON envelopes are stable, named objects — not bare arrays — when there is more than one thing to report.**
   - `bd reclaim --json` (`cmd/bd/reclaim.go:129-135`):
     ```go
     outputJSON(map[string]interface{}{
         "reclaimed": reclaimed,   // [{id, previous_owner}, ...]
         "count":     len(reclaimed),
         // Whether any scope filter was in effect, so a supervisor auditing
         // its own reclaim log can tell a scoped sweep from a global one.
         "scoped":    scoped,
     })
     ```
     That `scoped` field is the pattern worth copying: **echo back the effective scope of a destructive operation** so an automated caller can audit its own log.
   - `bd close --json` (`cmd/bd/close.go:334-345`): bare array of closed issues normally; `{"closed": [...], "claimed": {...}}` when `--claim-next` also fired.

4. **Empty scope flags are a hard error, never a wildcard** (`cmd/bd/reclaim.go:170-197`) — the single best small idea in the CLI:
   ```go
   // The hard error is the point. `bd reclaim --label "$LANE"` with LANE unset
   // parses to an empty slice, which would otherwise be indistinguishable from
   // "no --label at all" — i.e. a supervisor's scoped sweep silently degrading
   // into a global one that reaps every stale lease in a federated database. A
   // scope flag that resolves to nothing is operator error, not a wildcard.
   return nil, fmt.Errorf("--%s was given no usable value (an empty scope flag would reclaim everything; drop the flag to sweep globally)", name)
   ```
   Implemented via `cmd.Flags().Changed(name)` to distinguish "flag absent" from "flag present but empty". Apply to every loom CLI flag that narrows a destructive set (`loom release --plan "$PLAN"`).

5. **Missing-ID fallback is interactive-only** (`cmd/bd/close.go:29-49`):
   ```go
   // If no issue ID is provided, closes the last touched issue ... This fallback
   // only applies in interactive sessions (stdin is a terminal); in scripts and
   // agent sessions a missing ID is an error, so a command built from an empty
   // variable cannot silently close an unrelated issue.
   // Set BD_LAST_TOUCHED_FALLBACK=1 to allow the fallback anywhere, or =0 to disable it.
   ```
   Validated in `Args:` — *before* `PersistentPreRunE` can open the store or run migrations, so a bad invocation costs nothing.

6. **Hidden aliases for agent ergonomics** (`cmd/bd/close.go:394-400`): `--resolution` (Jira convention), `-m/--message` (git convention), `--comment` all alias `--reason`, all `MarkHidden`. An agent trained on other tools guesses one of them and it just works, without bloating `--help`. Cheap; copy it.

7. **Positional flag mapping for batch verbs** (`cmd/bd/close.go:37-40`): "provide one `--reason` for all IDs or repeat `--reason` once per ID. Reasons map positionally: the first `--reason` applies to the first ID ... regardless of where the flags appear in the command line."

8. **Partial-ID resolution with a typed ambiguity error** (`internal/utils/id_parser.go`). This is the ergonomic payoff of short hash IDs — you type `a3f8`, not `bd-a3f8e9`.
   ```go
   var ErrAmbiguousID = errors.New("ambiguous issue ID")
   // ...
   sort.Strings(matches)   // deterministic candidate list across storage impls
   if len(matches) > 1 {
       return "", fmt.Errorf("%w: %q matches %d issues: %v\nUse more characters to disambiguate",
           ErrAmbiguousID, input, len(matches), matches)
   }
   ```
   Resolution order (`id_parser.go:52-228`): exact ID → normalize (add configured prefix if bare) → exact on normalized → SQL-filtered substring search → **leading-prefix match only**:
   ```go
   } else if strings.HasPrefix(issueHash, hashPart) {
       // Leading-prefix abbreviation (documented UX, e.g. "a3f8" -> "a3f8e9...").
       // HasPrefix rather than Contains: reject interior-substring matches
       // like "kt8" inside "j0kt8" (GH#4234).
   ```
   Also worth noting (`id_parser.go:121-140`): they push the substring filter into SQL, not into Python/Go memory — "On large databases (23k+ issues ...), loading all issues took 60+ seconds; with SQL filtering it's near-instant" — and use a narrow ID-only projection for the loop. loom's `resolve_nodes` should do the same: `SELECT id FROM nodes WHERE ...`, never hydrate rows to throw them away.

9. **Output shapes.** Three text modes plus JSON, chosen by mode not by a per-command flag.
   - Default compact row (`cmd/bd/list_format.go:238-278`):
     ```
     <status-icon> <pin?><ID> [P<n>] [<type>] @<assignee> [labels] - <Title> (blocked by: X, blocks: Y)
     ```
     with one nice touch: an `open` issue that has open blockers renders with the **blocked** icon (line 244-247) — the displayed state is the effective state, not the stored one.
   - `--long` (`list_format.go:85-130`): `ID [P n] [type] status` / indented title / `Assignee:` / `Description:` / `Labels:` / `Metadata: N keys`.
   - **Agent mode** (`list_format.go:136-146`, selected by `ui.IsAgentMode()`): ultra-compact `ID: Title (parent: X, blocked by: Y, blocks: Z)`, one line per issue, no color, no box drawing. Selection (`internal/ui/styles.go:87-96`):
     ```go
     func IsAgentMode() bool {
         if os.Getenv("BD_AGENT_MODE") == "1" { return true }
         if os.Getenv("CLAUDE_CODE") != "" { return true }   // auto-detect Claude Code
         return false
     }
     ```
     **Auto-detecting the agent environment and switching to a token-cheap format is directly applicable to loom** — every loom CLI call happens inside Claude Code by construction.
   - Truncation is announced on **stderr**, only when stderr is a terminal (`cmd/bd/list_output.go:14-23`): "Showing N issues; more results matched but were hidden by --limit." Never let an agent mistake a truncated page for a complete set — but never pollute a piped stdout with it either.
   - `--brief` drops big text fields, and the omission is **marked in the row** (`IsLitePartial`), so the renderer prints `Description: (omitted by --brief)` rather than leaving it indistinguishable from empty (`list_format.go:107-116`, flag help at `cmd/bd/list.go:449-455`).

10. **Exit codes.** Errors go through `HandleErrorRespectJSON(...)` (so a `--json` caller gets JSON, not a bare stderr line); a "nothing matched / nothing done" outcome exits 1 silently via `SilentExit()` → `&exitError{Code: 1}` (`cmd/bd/errors.go:119-121`), used at `cmd/bd/close.go:374` when every attempted close was a no-op. Commands set `SilenceUsage: true, SilenceErrors: true` so a runtime failure never dumps a usage wall at an agent.

### 2.4 `discovered-from` lineage — ADOPT, minimally

PLAN §2 names this. It is a **dependency edge type**, not a special mechanism: `types.DepDiscoveredFrom` = `"discovered-from"` (`internal/types`), one of `blocks | tracks | related | parent-child | discovered-from | until | caused-by | validates | relates-to | supersedes` (`cmd/bd/dep.go:1405`). Key properties:

- **Non-blocking**: "parent-child, related, discovered-from, etc. do not block" (`cmd/bd/dep.go:1230`) — it never gates readiness, it is pure provenance.
- Created inline at issue creation: `bd create --deps 'discovered-from:bd-15'` (`cmd/bd/create.go:872`).
- A discovered-from parent's `source_repo` is **inherited** by the child (`cmd/bd/create.go:560`, `cmd/bd/create_deps.go:228-242`).
- Rendered distinctly: label `"DISCOVERED FROM"`, glyph `◊`, phrase "was discovered from" (`cmd/bd/dep_relation.go:38`); green dashed edge in the DOT export (`cmd/bd/list_output.go:81-84`).

**For loom:** add a nullable `plans.discovered_from` column (or an `edges` row of kind `DISCOVERED_FROM`) recording the plan a rescope-triggering discovery came from. Non-blocking, informational, one column. It is the drift audit trail PLAN §2 asks for, and it costs a column and a JOIN — no state machine involvement. Do **not** build the full 10-type dependency taxonomy.

---

## 3. ADAPT

| # | beads does | loom does instead | why |
|---|---|---|---|
| 1 | ID hash input = `title\|description\|creator\|unix_nanos\|nonce` — deliberately unique per creation event | **Node IDs**: `sha256(f"{repo}\0{qualname}")`, base36, no timestamp / creator / nonce. **Plan IDs**: keep beads' full recipe (`title\|spec_md\|agent\|unix_nanos\|nonce`). | Two different jobs. Two machines indexing the same commit must **agree** on a node ID (deterministic, content-addressed by identity, not by creation event). Two machines declaring plans must **not collide** (needs the entropy). PLAN §7 already wants "IDs hashed with repo salt" — the repo string is that salt, and NUL-separating the fields prevents `repo="a", qualname="b/c"` colliding with `repo="a/b", qualname="c"`, which beads' `\|` separator does not guard against. |
| 2 | Adaptive length recomputed per mint by `SELECT COUNT(*)` (`helpers.go:319`) | Fix node-ID length at **8** (36⁸ ≈ 2.8e12; birthday p ≈ 0.25 at ~1.2M nodes) and skip the count query. Keep the adaptive formula for plan IDs, or just fix those at 6 too. | Node minting happens once per symbol per index run — thousands per re-index. A COUNT query per mint is the wrong shape for a bulk path. beads mints one ID per user action, so the query is free for them. Keep `ComputeAdaptiveLength` in `ids.py` as a helper with a unit test, but call it from a config path, not the hot loop. |
| 3 | Collision loop: 10 nonces × lengths 3→8, each candidate a separate `SELECT COUNT(*)` | For **nodes**: no collision loop. Deterministic ID + `INSERT ... ON CONFLICT(id) DO UPDATE` upsert; if two distinct qualnames ever hash-collide, that is a bug we want to see, so add a `UNIQUE(repo, qualname)` constraint and let the insert raise. For **plans**: keep the loop, but collapse it — `SELECT 1 FROM plans WHERE id = ?` (existence, not count), and 5 nonces is plenty. | A collision loop on a *deterministic* ID would silently mint a second node for the same symbol — the exact failure mode the plan's claim model cannot tolerate. |
| 4 | Lease TTL 5 min, reaper grace 2×TTL, heartbeat by explicit `bd heartbeat` invocation | **This row is the canonical loom TTL set (GATE-1 fix 6; same text in agent-mail.md §2.1/ADAPT 9):** TTL **1800 s (30 min)** at declare; renewal **implicit** — every `check()` from the owning agent resets `ttl_expires = max(current, now + 1800)` (never shortens) — with `renew(plan_id)` as the explicit escape hatch; floor 60 s; **cannot renew after expiry** (`{renewed: 0}` → re-declare); the read filter `status='active' AND ttl_expires > now` is authoritative everywhere; the lazy status-flip sweep is bookkeeping with grace 2×TTL = 3600 s. | beads' worker is a script that can heartbeat on a timer. loom's "worker" is a Claude Code session whose only reliable liveness signal is that it is still editing files — and it hits `check()` on every single edit. Free heartbeat, zero protocol surface. 30 min because an agent can plausibly spend 20 minutes reading before its next edit; 5 would thrash. Grace is safe here (unlike beads, expiry is terminal bookkeeping, not a destructive revert — the read filter already stops honoring the claim at expiry). |
| 5 | Lease lives in a separate `leases` table, deliberately node-local and excluded from replication | One `plans` table with `ttl_expires` inline, exactly as PLAN §4.1 already specifies. No separate table. | The split exists purely because Dolt commits every issues-table write and they refused to mint a commit per heartbeat (`lease.go:151-157`). SQLite has no such cost. One table, one row, one UPDATE. |
| 6 | Reaper is a manual/cron CLI verb (`bd reclaim`) run by a supervisor | In-process sweeper on a timer inside `loom serve`, plus a lazy sweep at the top of `declare_plan` / `check` so an expired claim can never block even if the timer is wedged. | PLAN §4.5's "a user who runs init should never touch loom again" forbids a supervisor cron. The lazy sweep is cheap: `UPDATE plans SET status='expired' WHERE status='active' AND ttl_expires < ?` before the conflict query, in the same transaction. |
| 7 | Reaper reverts `in_progress → open` and **deletes** the lease row | Transition `plans.status: active → expired` and **delete the claim rows** (`claims` is keyed by `plan_id`). Keep the plan row. | The plan row carries `spec_md`, which is the thing a later conflict wants to show. Deleting it destroys the audit trail and the demo data (`events` log, PLAN §4.1). Beads deletes the lease because the lease has no content; our plan does. |
| 8 | Statuses: 7 built-ins + configurable custom statuses with categories | Exactly the four in PLAN §4.1 — `active, done, expired, superseded` — hardcoded as a Python `enum.StrEnum`. No custom statuses, no category system. | The category machinery exists to let workspaces model draft→ready→in_progress lifecycles. loom has one lifecycle. Adding configurability here buys nothing and costs a config table plus a resolution query on the `check()` hot path (PLAN §4.2 targets sub-10ms). |
| 9 | Claimability derived per-transaction from config (`ClaimableSourceStatusesInTx`, `claim.go:341`) | Claimable set is the literal constant `{PlanStatus.ACTIVE}`. | Same reason as #8. |
| 10 | Conflict detail recovered by parsing error message fragments (`ParseClaimConflict`, root `claim.go`) — kept only for API compat | Structured only. The MCP tool returns `{"conflicts": [{"plan_id", "agent", "title", "spec_md", "nodes": [...], "kind": "write-write"\|"write-read"\|"read-write"}]}`. Never a string to be parsed. | beads' own doc comment calls the string path "a deliberately string-coupled shim" and says the typed error means "errors.As recovers them without parsing anything." We start where they ended up. |
| 11 | `bd list` / `bd show` with ~60 filter flags | `loom ls [--repo R] [--agent A] [--json]` and `loom show <plan-id-or-node-id>`. Five flags total. | PLAN §3 budgets 150 lines for the whole CLI. Adopt the *conventions* (§2.3 items 2-10), not the surface area. |
| 12 | `bd ready --claim` (claim the first matching item) | No equivalent verb. loom's claim is `declare_plan`, driven by the MCP tool from inside the agent's reasoning, not by a CLI poll. | loom claims a *set* of nodes derived from an authored spec; there is no queue of pre-existing work to pull from. The pattern that does transfer is the atomicity (§2.2.5): the readiness scan and the claim share one transaction. |
| 13 | Identity comparison via `actorMatches` / `canonicalActor` (cross-layer spelling reconciliation) | Exact string match on an agent token minted by `loom init` (PLAN §4.5). | The spelling problem is a Gas Town artifact — the same identity spelled differently by different layers. We mint the identity ourselves in one place, so there is exactly one spelling. Do not port `actorMatches`. |
| 14 | MySQL/Dolt SQL: `CONCAT`, `INSTR`, `SUBSTRING`, `ON DUPLICATE KEY UPDATE`, `IF()` | SQLite: `\|\|`, `instr`, `substr`, `ON CONFLICT(...) DO UPDATE SET`, `CASE WHEN`. Transactions opened `BEGIN IMMEDIATE`. WAL mode per PLAN §4.1. | Direct dialect translation. The `BEGIN IMMEDIATE` point is not cosmetic — see §2.1.4. |
| 15 | Anti-steal on refusal copy is per-message, ad hoc | One `deny_message()` function in `hook/gate.py`, with a unit test asserting the output contains **no** force/override command name. | §2.2.3's lesson deserves a tripwire, not a convention. The plan already has four gate cases scripted in M3's acceptance criteria — add this assertion to that same test. |

---

## 4. REJECT

Everything in this section is beads machinery that exists to survive a **Dolt-backed, multi-replica, cell-merging** store. loom is one SQLite database behind one process (PLAN §1), where transactions serialize and there is exactly one writer. **That single fact rejects items 1-6.** Porting any of them would be cargo-culting a fix for a race we do not have, at a cost the plan's 500-700 line server budget cannot absorb.

1. **`row_lock` / `RowVersion` reminting** (`lease.go:45-120`). A random non-zero int64 rewritten by every status/ownership-mutating write, so that concurrent writers collide on a shared cell. Their own justification: *"Dolt has no real row locking and merges concurrent commits cell-by-cell, so two transactions that touch DIFFERENT cells of the same issue row ... merge silently instead of conflicting."* SQLite does not cell-merge. It also drags in a build-time enforcement test (`TestAllIssueRowWritesStampRowLock`) and a two-directional invariant ("status writes MUST remint, aux-marker writes MUST NOT") that is a documented trap. **Reject entirely.** Our CAS predicate is `WHERE plan_id = ? AND status = 'active'` inside `BEGIN IMMEDIATE`, which is sufficient and obvious.

2. **`granted_node` replica guard and `--any-replica`** (`lease.go:600-760`, `cmd/bd/reclaim.go:40-68`). Machinery for "a lease is only enforceable on the replica that granted it," plus the invariant the guard *cannot* enforce (`grace window > sync interval`), plus `reportForeignSkips` diagnostics, plus a documented permanent-strand failure mode when a node is renamed. loom has one server. There is no second replica and no sync interval. **Reject.**

3. **The separate node-local `leases` table.** See ADAPT #5. The split is a Dolt commit-cost optimization. **Reject; one column on `plans`.**

4. **Wisp routing** (`WispTableRouting`, `IsActiveWispInTx`, threaded through every claim/unclaim path). Ephemeral issues live in parallel tables and are never leased. loom has one kind of plan. Every `issueTable, _, eventTable, _ := WispTableRouting(isWisp)` line in the excerpts above collapses to a literal table name. **Reject.**

5. **`actorMatches` / `canonicalActor` spelling canonicalization.** See ADAPT #13. **Reject.**

6. **Claim pools** (`claim.pools` config, `ParseClaimPools`, `ClaimPoolAliasesInTx`, `claim.go:303-331`). Pre-assigning work to a group pseudo-assignee that any member may claim. This is a dispatcher pattern for a work queue; loom has no queue and no dispatcher. **Reject.**

7. **Counter mode** (`IsCounterModeTx` / `NextCounterIDTx` / `SeedCounterFromExistingIssuesTx`, `helpers.go:218-315`). Sequential `prefix-1, prefix-2` IDs as an alternative to hashes, with counter seeding and a re-increment-after-seed retry. It exists for users who want Jira-looking IDs. **Reject — it is the exact thing hash IDs are for.** PLAN §2 takes beads for "hash-based short IDs so two users never mint colliding IDs"; a shared counter reintroduces the coordination point.

8. **`internal/types/id_generator.go` in its entirety as a minting path** (hex, RFC3339Nano, workspaceID, "caller extracts hash[:6] then [:7] then [:8]"). Dead code with zero non-test callers. Its docstring's collision table is *also* wrong for our purposes — it quotes hex probabilities against a base36 implementation. Anyone skimming for "the hash function" will land here first; do not. (Its `GenerateChildID` / `ParseHierarchicalID` string helpers are the exception — see ADOPT §2.1.5.)

9. **The `ParseClaimConflict` string shim** (repo root `claim.go:32-88`): package-init-derived message markers, `strings.LastIndex` to survive `%w` wrapping, `ContainsAny(tail, " \t\r\n(")` bounding to reject garbage tokens. Beautiful defensive code for a problem we should never create. **Reject the shim; adopt the typed error it was replaced by.**

10. **The custom-status / status-category system** (`types.go:563-575`, `ResolveCustomStatusesDetailedInTx`, `GetCustomStatusesTx`). See ADAPT #8. **Reject.**

11. **The full dependency-type taxonomy** (10 types, `dep add/remove/tree/cycles`, cycle detection, blocked-state denormalization with its own consistency tests). PLAN §2 takes only `discovered-from` from this area; take it as one nullable column (§2.4) and reject the rest. **Note this is not just scope — `blocked_state.go` exists to keep a denormalized `is_blocked` marker consistent, and it is the source of one of the two `row_lock` exemption cases.** Importing the taxonomy imports that complexity.

12. **`SweepInTx`** (`internal/storage/issueops/sweep.go`). Despite the name, this is **not** the TTL expiry sweeper — it is `bd purge` / `bd prune`, deleting closed issues, with reference-protection scanning of descriptions/notes/comments for citations of deletion candidates. Unrelated to claims. Named here only because "sweeper" is the word PLAN §4.2 uses and a coder grepping for it will land in the wrong file. **The expiry path is `ReclaimExpiredLeasesInTx` in `lease.go`.**

13. **Dolt versioning choreography** (`commitPendingIfEmbedded`, `doltAutoCommitParams`, autocommit tests, `bd compact`). Not applicable. **Reject.**

14. **The proxied-server dual code path** (`*_proxied_server.go` — roughly half of `cmd/bd/`). Every command is implemented twice, once direct and once over a proxy, and the repo carries parity tests plus a documented `AMBIGUITIES.md` entry (A-blk-1) where the two paths behave *differently* on a decoration failure. loom has one path: MCP over streamable-http. **Reject, and treat it as a warning against ever adding a second.**

---

## 5. CORRECTIONS to PLAN-v1.md

**C1 — §2 repo URL is wrong.** The plan says `github.com/gastownhall/beads`. The clone's module path is `github.com/steveyegge/beads` (`go.mod:1`), and every internal import confirms it, e.g. `claim.go:7`:

```go
import "github.com/steveyegge/beads/internal/storage"
```

`gastownhall/beads` does appear in the source — as a *reference to a different repo's PR*: "analysis absorbed from gastownhall/beads#4682, Julian Knutsen" (`lease.go:73`), and internal issue IDs use `bd-`/`wy-`/`ga-` prefixes suggesting several related trackers. Fix the attribution line to `github.com/steveyegge/beads` — this matters because the MIT notice we ship names the project.

**C2 — §2 "the claim state machine (open, claimed, done, expired)" is wrong on three of four names.** There is no `claimed` status and no `expired` status in beads. Actual (`internal/types/types.go:508-516`): `open, in_progress, blocked, deferred, closed, pinned, hooked`. A claim sets `status = 'in_progress'` and `assignee = <actor>`; "done" is `closed`. **Expiry is not a status at all** — it is a fact about a row in a separate `leases` table (`lease_expires_at < cutoff`), acted on by a reaper that transitions the issue `in_progress → open` and deletes the lease (§2.2.4).

This matters for loom's design, not just the wording. PLAN §4.1 already gets it right for plans (`status in active, done, expired, superseded`) — but note that beads deliberately does **not** persist an expired state, because for them expiry means "return to the pool". loom's `expired` is a *terminal* state on a plan whose spec we want to keep. Those are different semantics and the sweeper must not be written from the beads shape by reflex: we set `status='expired'` and delete the *claims*, we do not revert anything (ADAPT #7).

**C3 — §2 "hash-based short IDs so two users never mint colliding IDs" overstates what the hash does.** The hash does not prevent collisions; it makes them *rare and detectable*. Collision-freedom comes from the mint-time `SELECT COUNT(*) ... WHERE id = ?` loop inside the inserting transaction (§2.1.3), backed by an adaptive length chosen so that expected collision probability stays ≤ 25% (§2.1.4) — a threshold that is only tolerable *because* the check exists.

Consequence for loom, and it is a good one: PLAN §1's "one server, one store, check and claim become one transaction" is precisely what makes this guarantee exact rather than probabilistic. Two agents on two machines both minting through the same server serialize on the same transaction. **State this explicitly in the spec** so nobody later "optimizes" by minting IDs client-side in the hook — that would silently reintroduce the collision the plan claims to have eliminated.

**C4 — §2 "the CLI verb ergonomics (ls, show, claim, done)" — two of those four verbs do not exist.**
- `ls`: does not exist. `listCmd` is `Use: "list"` with **no** `Aliases` field (`cmd/bd/list.go:153-156`).
- `claim`: does not exist as a verb. Claiming is a **flag**: `bd ready --claim` (`cmd/bd/ready.go:718`) or `bd update <id> --claim` (`cmd/bd/update.go:987`). Releasing is `bd unclaim`; expiry recovery is `bd reclaim`.
- `show`: exists, alias `view` (`cmd/bd/show.go:19`).
- `done`: exists **as an alias of `close`** (`cmd/bd/close.go:23-25`: `Use: "close [id...]", Aliases: []string{"done"}`).

The flag-not-verb shape for claim is the more interesting finding (§2.3 item 1) and should be recorded as such rather than silently corrected to a verb list. If loom wants `loom ls`, that is our choice, not an inheritance.

**C5 — §4.1 `claims(node_id, plan_id, mode)` has no expiry column, and §4.2's `renew(plan_id)` has no defined failure mode.** Beads' contract is explicit and we should copy it: a renewal that matches zero rows returns a **typed error meaning "your lease is gone"**, not success and not silence (`lease.go:288-296`). Add to the spec: `renew(plan_id)` returns `{"ok": false, "reason": "expired"|"released"|"unknown_plan"}` when the UPDATE affects zero rows, and the protocol snippet (§4.4) must tell the agent to re-declare on that response. Without this, a crashed-then-resumed session silently believes it still holds claims it lost.

**C6 — §4.3's deny messages are right, and §4.4 must be constrained to keep them right.** Beads paid for this (`claim.go:173-184`, quoted in §2.2.3): naming a force-release command in a refusal causes batch agents to pattern-match it into "an unclaim+claim steamroller of live claims." The plan's four deny strings currently name only `rescope` and "build against declared interfaces" — safe. **Add an explicit constraint to §4.3**: *the deny message must never name a command that releases or overrides another agent's claim, including in a caveat clause.* And add the assertion to M3's scripted gate test (ADAPT #15). This is the one finding from beads that is a genuine safety property rather than an ergonomic preference.

**C7 — §7 "IDs are hashed with repo salt, no collisions" needs the separator specified.** Beads joins hash inputs with a bare `|` (`hash.go:58`), which is ambiguous under concatenation. With `repo` and `qualname` both free-form and both able to contain almost anything, use a NUL separator: `sha256(f"{repo}\0{qualname}".encode())`. One character, closes the ambiguity. (Beads gets away with `|` because a `nonce` and nanosecond timestamp are also in the mix; our node IDs have neither — see ADAPT #1.)

**C8 — no correction, but a confirmation worth recording.** PLAN §4.2 targets "sub-10ms" for `check()`. Beads' partial-ID resolver has the relevant war story (`id_parser.go:121-135`): loading all rows to filter in application code took **60+ seconds on 23k issues**; pushing the filter into SQL with a narrow ID-only projection made it "near-instant." loom's `resolve_nodes` and `check` must both be single indexed SQL queries with narrow projections — `SELECT id FROM nodes WHERE repo=? AND qualname=?`, indexed on `(repo, qualname)` — never a fetch-then-filter. Add the index to the §4.1 schema; it is not currently listed.
