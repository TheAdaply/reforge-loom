# Extraction: GitHub spec-kit → `loom/templates/spec.md`

**Source clone**: `<vendor-clone>/spec-kit`
(upstream `github.com/github/spec-kit`)
**Commit**: `13344409786a29f631c24ee49e9f307e7b588465`, Mon 2026-08-17 18:32:12 -0500
**Plan reference**: PLAN-v1.md §2 (cherry-pick manifest), §4.4 (protocol / spec template fields)
**Scope of this extraction**: the spec template(s) only. Everything below is file:line against the
clone above.

Template inventory found (there is **no `memory/` directory** in this clone):

| Path | Role |
|---|---|
| `templates/spec-template.md` | 132 lines. The default spec template. This is the one PLAN §2 means. |
| `templates/plan-template.md` | 113 lines. Downstream technical plan. |
| `templates/tasks-template.md` | Task breakdown with `[P]` parallel markers. |
| `templates/checklist-template.md`, `templates/constitution-template.md` | Not relevant to us. |
| `templates/commands/specify.md` | The prompt that *fills* spec-template.md. The real value is here, not in the template. |
| `presets/lean/commands/speckit.specify.md` | 23 lines. Upstream's own "just the prompt, just the artifact" minimal mode. Directly validates our trim. |
| `presets/self-test/templates/spec-template.md`, `presets/scaffold/templates/spec-template.md` | Test/scaffold fixtures. Ignore. |

`templates/spec-template.md` is the default: `templates/commands/specify.md:96` resolves the active
`spec-template` "through the Spec Kit preset/template resolution stack", and the `lean` preset
(`presets/lean/preset.yml:15-45`) overrides **commands only**, no templates — so with no preset
installed, `templates/spec-template.md` is what gets copied to `spec.md`
(`templates/commands/specify.md:97`).

---

## 1. LICENSE

`LICENSE` (repo root), lines 1-3:

```
MIT License

Copyright GitHub, Inc.
```

Standard unmodified MIT. The only obligation (`LICENSE:12-13`):

```
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

**What this means for us, explicitly:**

- **Verbatim copying is permitted**, including modification, sublicensing, and redistribution.
  There is **no patterns-only restriction here** — unlike `mcp_agent_mail` (PLAN-v1.md:4-5, "MIT +
  OpenAI/Anthropic rider — patterns only, zero verbatim code"). Do **not** carry that caution over
  to spec-kit.
- `pyproject.toml` and `CITATION.cff` add no further terms. `SECURITY.md`/`CODE_OF_CONDUCT.md` are
  contributor-facing only, not use restrictions.
- **Practical action**: our `loom/templates/spec.md` as delivered in §3 below is an original
  rewrite — it shares no substantial portion of spec-kit text (section names differ, all five
  fields differ, none of the placeholder prose is reused). Attribution is therefore not legally
  required for the template itself. Still add a one-line credit in `loom/templates/spec.md`'s HTML
  comment and in `NOTICES.md`:
  `Spec discipline inspired by github/spec-kit (MIT, Copyright GitHub, Inc.).`
  If a coder agent later copies any spec-kit text verbatim (e.g. lifts the FR-### example block),
  the full MIT notice must go into `loom/licenses/spec-kit-MIT.txt`.

---

## 2. ADOPT

The five *fields* of loom's spec are already fixed by PLAN-v1.md §4.4 and are **not** taken from
spec-kit — spec-kit's template contains none of them. What we adopt from spec-kit is **filling
discipline**: five conventions that make an LLM-authored spec machine-checkable rather than prose.
Each is cited with provenance and excerpt.

### A2.1 — Mandatory-section markers on the heading itself

`templates/spec-template.md:11`, `:81`, `:106`:

```markdown
## User Scenarios & Testing *(mandatory)*
## Requirements *(mandatory)*
## Success Criteria *(mandatory)*
```

Adopt: every loom spec heading carries `*(mandatory)*`. A section may not be deleted; an empty one
must say `none`. This is what makes the template validatable by a 20-line linter (see ADAPT
A3.5) — a missing heading is a hard error, not a judgement call.

### A2.2 — Bracketed placeholder + inline worked example, in the same line

`templates/spec-template.md:90-94`:

```markdown
- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
```

`templates/spec-template.md:115-118`:

```markdown
- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
```

Adopt: the `[what goes here, e.g. "<concrete example>"]` shape. The example is *inside* the
bracket, so a spec that still contains `[` is trivially detectable as unfilled, and the agent never
has to guess the intended granularity. We use this for every field in §3.

### A2.3 — ACTION REQUIRED comment blocks that survive into the copied file

`templates/spec-template.md:72-76`:

```markdown
<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->
```

Same pattern at `:83-86`, `:108-111`, `:122-126`. Adopt the mechanism (HTML comments that the agent
reads and the reader never sees), but see ADAPT A3.4 — we compress four blocks to one.

### A2.4 — Testability + implementation-agnosticism as explicit fill rules

`templates/commands/specify.md:131-138` (the numbered execution flow the agent follows):

```
    5. Generate Functional Requirements
       Each requirement must be testable
       Use reasonable defaults for unspecified details (document assumptions in Assumptions section)
    6. Define Success Criteria
       Create measurable, technology-agnostic outcomes
       ...
       Each criterion must be verifiable without implementation details
```

Reinforced in the lean preset, `presets/lean/commands/speckit.specify.md:20-23`:

```markdown
3. Create a specification from the user input and store it in `<feature_directory>/spec.md`.
   - Overview, functional requirements, user scenarios, success criteria
   - Every requirement must be testable
   - Make informed defaults for unspecified details
```

Adopt both halves: **(a) every stated interface must be testable** — for loom that means an exact
signature, not a description; **(b) make informed defaults instead of stopping**, and record the
default. Note that upstream's own minimal mode keeps exactly these two rules and drops everything
else — that is our precedent for how aggressively to trim.

### A2.5 — Assumptions as a first-class mandatory section (the ancestor of loom `Assumes`)

`templates/spec-template.md:120-131`:

```markdown
## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- [Assumption about target users, e.g., "Users have stable internet connectivity"]
- [Assumption about scope boundaries, e.g., "Mobile support is out of scope for v1"]
- [Assumption about data/environment, e.g., "Existing authentication system will be reused"]
- [Dependency on existing system/service, e.g., "Requires access to the existing user profile API"]
```

This is the single most load-bearing borrow. spec-kit already establishes that "what I relied on
but did not build" is a *required, enumerated* section rather than something implicit. loom takes
that principle and makes it executable: the same list becomes `assumes[]` in `declare_plan`
(PLAN-v1.md:112) and is registered as **read claims** (PLAN-v1.md:101), which is what powers the
write-vs-read and read-vs-write conflict cases in PLAN-v1.md:125.

Note the scope-boundary bullet at `:129` ("Mobile support is out of scope for v1") — loom promotes
that from a bullet buried in Assumptions to its own top-level `Out of scope` field.

---

## 3. ADAPT

### A3.0 — The governing constraint spec-kit does not have

spec-kit's spec is written **for a human to read once and for one agent to plan from**. loom's spec
is written **to be injected verbatim into a second agent's context, repeatedly**:

- `declare_plan` conflict responses "embed each clashing plan's full `spec_md` inline"
  (PLAN-v1.md:114-116).
- `check` returns `deny(owner_plan, spec_md)` (PLAN-v1.md:118).
- The hook's deny message emits `"Its spec follows."` followed by the whole spec
  (PLAN-v1.md:133-135).

So **spec length is a per-deny token tax paid by another agent, on every collision**. A 132-line
spec-kit spec injected into a deny message is a context bomb. That, not aesthetics, is why the
template is one page. Target: **under 40 lines, under ~400 tokens.** This constraint should be
stated in the template itself so agents do not grow it.

### A3.1 — Field mapping: what becomes what

| spec-kit | loom | Why the change |
|---|---|---|
| `## User Scenarios & Testing` + 3 prioritized user stories with Given/When/Then (`spec-template.md:11-69`) | **Goal** (2 sentences) | The reader is a peer agent deciding "does your work invalidate mine?" It needs intent in one glance, not journeys. Full stories are unaffordable at inject-time. |
| `### Key Entities` (`spec-template.md:101-104`) | **Write targets** (node IDs) | spec-kit names entities in prose; loom names them as canonical node IDs from `resolve_nodes` (PLAN-v1.md:109-110), because these strings are the claim keys, not documentation. |
| `### Functional Requirements` FR-### (`spec-template.md:88-94`) | **New/changed interfaces** (exact signatures) | "System MUST validate email addresses" is unbuildable-against. A blocked agent needs to code against the *declared interface* without reading in-flight code (PLAN-v1.md:145-146), so the field must carry a compilable signature. |
| `## Assumptions` (`spec-template.md:120-131`) | **Assumes** (node ID + relied-on signature) | Same idea, machine-checkable. Each entry becomes a read claim. |
| Scope bullet inside Assumptions (`spec-template.md:129`) | **Out of scope** (1 line) | Promoted to top level. It is what tells the other agent it is safe to take that ground — it *prevents* a conflict, so it must be visible. |
| `## Success Criteria` SC-### (`spec-template.md:106-118`) | *dropped* | See REJECT A4.6. |

### A3.2 — Placeholders become canonical-ID-shaped

spec-kit placeholders are English (`[Entity 1]`). Ours must show the exact syntax the server
accepts — loom's canonical form `relative/path.py::Class/method` (GATE-1 fix 2: `::` is loom's own
joiner; `/` inside the symbol part is Serena's real `NAME_PATH_SEP`, so the symbol half hands
straight to Serena's `name_path`; the plan's `Class.method` dotted form does not exist in Serena) —
so the agent produces a resolvable string on the first try and `resolve_nodes` is a confirmation,
not a repair.

### A3.3 — Two extra fields spec-kit has no equivalent for

`plan_id` and `agent` are written *back into* the spec header after `declare_plan` returns. spec-kit
carries `**Feature Branch**` / `**Created**` / `**Status**` (`spec-template.md:3-7`) for human
filing; ours carries identity so an embedded spec in a deny message is self-describing — the
blocked agent can `get_plan(plan_id)` or renegotiate without a lookup. Keep `Status` in spirit but
let the server own it (`plans.status`: active/done/expired/superseded, PLAN-v1.md:99-100); the
template shows it read-only.

### A3.4 — One comment block, not five

spec-kit has four separate `ACTION REQUIRED` blocks plus a long prioritization block
(`spec-template.md:13-24`) — roughly 25 lines of instructions in a 132-line file. At our size that
ratio is fatal. Compress to a single 5-line header comment. Comments are stripped before the spec
is embedded in a deny payload (`spec_md` is stored as authored; the *hook* strips `<!-- -->` blocks
before printing to stderr — cheap regex, do it in `hook/gate.py`).

### A3.5 — Add a validator spec-kit does not have

spec-kit relies on the agent's diligence. We can do better because our fields are typed. Enforce in
`server/plans.py` at `declare_plan` time, reject with a fixable message:

- all five headings present (A2.1);
- no `[` placeholder or `TODO` survives anywhere in `spec_md`;
- every entry under **Write targets** and **Assumes** resolves via `resolve_nodes`;
- the set of write-target IDs in the prose equals `write_targets[]` in the call — the spec and the
  claim cannot disagree, because the spec is what the *other* agent is shown;
- **New/changed interfaces** is non-empty or literally `none`;
- **Goal** is ≤ 2 sentences; whole `spec_md` ≤ 60 lines (hard cap, cheap guard on the token tax).

---

### THE TEMPLATE — ready to use, drop at `loom/templates/spec.md`

```markdown
<!--
  loom spec. ONE PAGE, HARD CAP 60 LINES. This file is injected verbatim into other agents'
  deny messages and conflict responses, so every line you add is a tax they pay on every clash.
  Fill EVERY [bracket] and delete none of the five *(mandatory)* headings; write `none` if empty.
  Node IDs are canonical: `relative/path.py::Class/method` (files: `relative/path.ext`).
  Run resolve_nodes on every ID BEFORE declare_plan. Spec discipline inspired by
  github/spec-kit (MIT, Copyright GitHub, Inc.).
-->

# Spec: [short imperative title, e.g. "Cache authenticate() results"]

**Agent**: [your agent id]  **Plan**: [plan_id, written back after declare_plan]  **Repo/branch**: [repo] / [branch]

## Goal *(mandatory)*

[Two sentences. Sentence 1: what changes and why, e.g. "Add a 60s TTL cache in front of
authenticate() so repeated logins skip the bcrypt round." Sentence 2: the observable outcome,
e.g. "Auth-heavy endpoints drop from ~120ms to <5ms on cache hit; behaviour is unchanged on miss."]

## Write targets *(mandatory)*

[Canonical node IDs you will EDIT. One per line. Must equal write_targets[] in declare_plan.]

- [src/auth/service.py::AuthService/authenticate]
- [src/auth/cache.py  — file-level ID for a new or non-code file]

## New/changed interfaces *(mandatory)*

[EXACT signatures other agents may build against. Include the full signature and return type;
mark each ADDED / CHANGED / UNCHANGED-BUT-LOAD-BEARING. Write `none` if you change no interface.
A blocked agent codes against THIS, never against your in-flight source.]

- CHANGED `AuthService.authenticate(self, email: str, password: str, *, use_cache: bool = True) -> AuthResult`
  (was `(self, email: str, password: str) -> AuthResult`; return type and raised exceptions unchanged)
- ADDED `AuthCache.get(key: str) -> AuthResult | None`
- ADDED `AuthCache.put(key: str, value: AuthResult, ttl_s: int = 60) -> None`

## Assumes *(mandatory)*

[Canonical node IDs you RELY ON but will NOT edit, each with the exact signature you rely on.
These become read claims — if someone changes them, you get warned. Write `none` if nothing.]

- [src/auth/models.py::AuthResult] — relies on `AuthResult(user_id: str, token: str, expires_at: datetime)` being a frozen dataclass
- [src/auth/hashing.py::verify_password] — relies on `verify_password(plain: str, hashed: str) -> bool`
<!-- symbol IDs use `/` inside the symbol part, e.g. src/auth/service.py::AuthService/authenticate -->


## Out of scope *(mandatory)*

[One line. Name the adjacent ground you are NOT taking, so a peer can claim it safely, e.g.
"Session storage, token refresh, and the password-reset flow are untouched."]
```

---

## 4. REJECT

PLAN-v1.md §2 says "Take: spec template structure, trimmed to one page." Everything below is in
spec-kit and must **not** come across. Each rejection is argued from loom's mechanism, not taste.

**A4.1 — The multi-artifact pipeline.** `templates/plan-template.md:49-57` mandates
`plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/`, `tasks.md` per feature.
Reject all of it. loom stores one blob, `plans.spec_md` (PLAN-v1.md:99), and the whole conflict UX
depends on there being exactly one document to embed. A pipeline of six artifacts means a deny
message either truncates or explodes. One file, no directory convention.

**A4.2 — The Constitution Check gate.** `templates/plan-template.md:39-43`:

```markdown
## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*
```

plus `templates/constitution-template.md` and the whole `constitution-sync` preset. Reject. loom
already has a gate, and it is executable: the PreToolUse hook with exit 2 (PLAN-v1.md:24-26,
§4.3). A second, advisory, prose-evaluated gate adds ceremony with no enforcement and competes
with the real one for the agent's attention.

**A4.3 — `[NEEDS CLARIFICATION]` markers.** `templates/spec-template.md:96-99` and the rule at
`templates/commands/specify.md:122-129` ("LIMIT: Maximum 3 markers total"). Reject the marker
mechanism outright; keep only the "make informed guesses" half (adopted in A2.4). Mechanism
argument: `declare_plan` is atomic and claims immediately (PLAN-v1.md:112-116). A spec containing
unresolved questions must not be declared, because (a) it would take exclusive write claims on
nodes whose treatment is undecided, and (b) the unresolved marker would be shipped verbatim into
another agent's deny message, where nobody can answer it. The validator (A3.5) rejects `[` in
`spec_md` precisely to make this unrepresentable. If genuinely blocked: ask the human before
declaring, or declare a narrower target set and `rescope` later (PLAN-v1.md:119-120).

**A4.4 — `[P]` parallel markers and P1/P2/P3 story priorities.**
`templates/tasks-template.md:18` (`**[P]**: Can run in parallel (different files, no
dependencies)`), `:190-194`, and the prioritized-story block at `templates/spec-template.md:13-24`,
`:26`, `:41`, `:55`. Reject. spec-kit derives parallelism from a human-annotated guess about file
disjointness; loom derives it from the graph — write claims expanded one hop over CALLS/IMPORTS,
intersected in a single transaction (PLAN-v1.md:112-116). Our answer is computed and enforced;
theirs is asserted and unenforced. Importing `[P]` would give agents a second, weaker,
contradictory notion of "safe to run in parallel" — the exact confusion loom exists to end. Note
also that `[P]`'s definition is *file*-granular, while loom claims at *symbol* granularity
(PLAN-v1.md:28), so the annotation would be wrong at our resolution.

**A4.5 — The command-harness machinery.** `templates/commands/specify.md:21-54` (extension hooks
via `.specify/extensions.yml`), `:80-111` (feature directory numbering — `sequential` vs
`timestamp`, `.specify/init-options.json`, `.specify/feature.json`, deprecation warnings), `:62-73`
(short-name generation), `:74-78` (branch creation). Reject entirely. This is spec-kit's product
surface. loom's equivalent is `loom init` (PLAN-v1.md:157-160) plus `declare_plan`; the plan
identity is a server-minted `plan_id` (PLAN-v1.md:112), so there is no directory or branch naming
scheme to design. Do not create `specs/NNN-name/` directories.

**A4.6 — `## Success Criteria` / SC-### and its "technology-agnostic" rule.**
`templates/spec-template.md:106-118`, `templates/commands/specify.md:136-138`. Reject for loom's
spec. Two reasons: (i) success criteria are for the spec's *author* and their reviewer, and nothing
in loom's conflict path reads them — pure token tax in every deny message; (ii) the
"technology-agnostic, no implementation details" rule is actively **backwards** for us. Our spec's
job is to publish exact signatures another agent compiles against (PLAN-v1.md:145-146,
§4.4 "New or changed interfaces as exact signatures"). Implementation-level interface detail is the
product. Acceptance criteria for loom's *own* build live in PLAN-v1.md §5 milestones and in
`eval/`, not in the per-plan spec.

**A4.7 — `## Edge Cases`.** `templates/spec-template.md:71-79`. Reject. Legitimate for a product
spec, irrelevant to a claim negotiation; a peer agent does not need your boundary conditions to
decide whether your write breaks their read.

**A4.8 — `## Complexity Tracking` table.** `templates/plan-template.md:106-113`. Reject; it exists
only to justify Constitution Check violations, which we rejected in A4.2.

**A4.9 — `### Source Code (repository root)` option trees.** `templates/plan-template.md:59-104`
(Option 1 single project / Option 2 web app / Option 3 mobile+API). Reject. loom's layout is fixed
by PLAN-v1.md §3, and layout is not per-plan information.

---

## 5. CORRECTIONS

Nothing in spec-kit invalidates PLAN-v1.md. Three precision notes:

**C5.1 — PLAN-v1.md:62-65 wording is slightly optimistic.** It reads:

> - GitHub spec-kit (github.com/github/spec-kit)
>   - Take: spec template structure, trimmed to one page. Goal, targets, interfaces, assumes, out of
>     scope.

This implies the five fields derive from spec-kit's template. They do not — `templates/spec-template.md`
contains **none** of Goal / Write targets / New-changed interfaces / Assumes / Out-of-scope as
sections. Its sections are User Scenarios & Testing, Edge Cases, Requirements, Key Entities, Success
Criteria, Assumptions. The five fields are loom-original (PLAN-v1.md §4.4), derived from the claim
model. What spec-kit actually contributes is **fill discipline** (§2 above: mandatory markers,
bracket-plus-example placeholders, testability rule, assumptions-as-required-section). Suggested
edit to §2:
`Take: section discipline — *(mandatory)* markers, bracket+example placeholders, the
"every requirement testable / informed defaults" fill rule, and assumptions as a required section.
Our five fields are our own.`
This matters operationally: a coder agent reading the current wording might go looking for a field
mapping that isn't there, or worse, import spec-kit's sections wholesale.

**C5.2 — The task brief's "templates/ dir or memory/" is stale for this commit.** There is no
`memory/` directory at `13344409`. The constitution now lives at
`templates/constitution-template.md`. spec-kit itself is internally stale here:
`templates/commands/specify.md:115` still instructs "**IF EXISTS**: Load `/memory/constitution.md`"
— a dangling reference in upstream. Harmless for us (we reject the constitution mechanism entirely,
A4.2), but worth recording so nobody hunts for the directory.

**C5.3 — PLAN-v1.md:257-262 (MVP scope cuts) does not mention the spec template; confirm it is
IN.** It must be: PLAN-v1.md:186-187 puts "CLAUDE.snippet.md and spec template per 4.4" inside M3
deliverables, and the M3 acceptance test asserts the foreign-claim deny path prints a spec
(PLAN-v1.md:188-189). The template is a ~40-line file with no dependencies — cost is near zero and
the headline demo (PLAN-v1.md:208-209, agent B reading agent A's spec out of the deny message) is
unreachable without it. No change to the plan needed; just do not let it slip in triage.
