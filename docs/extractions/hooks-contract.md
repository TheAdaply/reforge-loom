# Extraction: Claude Code hooks contract (loom `hook/gate.py`)

Sources of truth for this document:

1. **Official docs, verified 2026-08-18.** `https://code.claude.com/docs/en/hooks` — fetched as raw
   markdown from `https://code.claude.com/docs/en/hooks.md` (273,639 bytes) and saved locally at
   `/private/tmp/claude-501/-Users-cero-Desktop-PROJECTS-reforge-workspace-re-forge-irl-data-team-collab/6458dacd-1b63-4e60-82c7-dac1ea52eb51/scratchpad/hooks-ref.md`.
   **All `hooks-ref.md:NNN` line numbers below are into that saved copy.** Section anchors are given
   too so they survive a re-fetch. `https://code.claude.com/docs/en/hooks-guide` also exists
   ("Automate actions with hooks") but is a quickstart; every normative statement below comes from
   the reference page.
2. **Clone:** `/private/tmp/claude-501/.../scratchpad/vendor/claude-code-permissions-hook`
   (github.com/kornysietsma/claude-code-permissions-hook, HEAD `ca0dca0`, pushed 2025-12-06).

Do not use the prose summary of the doc that a fetch-and-summarize returns; it silently drops the
`"defer"` permission decision and mis-states the exit-0 stdout rules. Read the raw markdown.

---

## 1. LICENSE

**The vendored repo has no license. Treat it as all-rights-reserved: patterns only, zero verbatim
code.**

Three-way proof:

- No `LICENSE` file exists in the tree. `git ls-files | grep -i lic` in the clone returns nothing;
  `ls` of the repo root shows only `.gitignore Cargo.lock Cargo.toml docs example.toml README.md
  src tests`.
- `Cargo.toml:7` has the license field commented out:
  `# license = "MIT"  # https://spdx.org/licenses/`
- `gh api repos/kornysietsma/claude-code-permissions-hook` returns `{"license": null, "pushed":
  "2025-12-06T19:39:00Z"}`.
- The repo's own `README.md` (last section) says, misleadingly: `## License` / `See LICENSE file
  for details.` — a file that does not exist.

Author: `authors = ["Korny Sietsma <korny@sietsma.com>"]` (`Cargo.toml:5`).

**Restriction that matters:** absent a license grant, no copying, adaptation, or redistribution of
the Rust source is permitted. It is also Rust and we are writing Python, so the question is moot in
practice — but the rule is binding on comments, doc text, and the `docs/*.md` files too. Every
ADOPT item sourced from the clone below is therefore a **from-scratch restatement of the PATTERN,
with file:line provenance and no verbatim code.**

Quotations from Anthropic's documentation are fine and are used freely below (they are the contract
we must implement against).

---

## 2. ADOPT

### 2.1 PreToolUse stdin JSON — exact fields (docs, verbatim)

`hooks-ref.md:708–752` (anchor `#common-input-fields`). Common fields, quoted:

| Field | Doc text (verbatim) |
| :-- | :-- |
| `session_id` | "Current session identifier" |
| `prompt_id` | "UUID identifying the user prompt currently being processed… Absent until the first user input. Requires Claude Code v2.1.196 or later" |
| `transcript_path` | "Path to conversation JSON." |
| `cwd` | "Current working directory when the hook is invoked" |
| `permission_mode` | "Current permission mode: `\"default\"`, `\"plan\"`, `\"acceptEdits\"`, `\"auto\"`, `\"dontAsk\"`, or `\"bypassPermissions\"`… **Not all events receive this field.**" |
| `effort` | "Object with a `level` field… `\"low\"`, `\"medium\"`, `\"high\"`, `\"xhigh\"`, or `\"max\"`" |
| `hook_event_name` | "Name of the event that fired" |
| `agent_id` | "Unique identifier for the subagent. **Present only when the hook fires inside a subagent call.**" |
| `agent_type` | "Agent name (for example, `\"Explore\"` or `\"security-reviewer\"`)." |

PreToolUse-specific (`hooks-ref.md:1526`), verbatim: *"In addition to the common input fields,
PreToolUse hooks receive `tool_name`, `tool_input`, and `tool_use_id`."*

Verbatim stdin example (`hooks-ref.md:733–750`):

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "default",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "npm test",
    "description": "Run test suite",
    "timeout": 120000,
    "run_in_background": false
  },
  "tool_use_id": "toolu_01ABC123..."
}
```

**`tool_input` shape for the edit tools** (`hooks-ref.md:1583–1601`), verbatim tables:

`Write` — "Creates or overwrites a file."

| Field | Type | Example | Description |
| :-- | :-- | :-- | :-- |
| `file_path` | string | `"/path/to/file.txt"` | Absolute path to the file to write |
| `content` | string | `"file content"` | Content to write to the file |

`Edit` — "Replaces a string in an existing file."

| Field | Type | Example | Description |
| :-- | :-- | :-- | :-- |
| `file_path` | string | `"/path/to/file.txt"` | Absolute path to the file to edit |
| `old_string` | string | `"original text"` | Text to find and replace |
| `new_string` | string | `"replacement text"` | Replacement text |
| `replace_all` | boolean | `false` | Whether to replace all occurrences |

**`MultiEdit` does not appear anywhere in the current reference.** `grep -n "MultiEdit"
hooks-ref.md` → zero hits across all 273KB. See CORRECTIONS §5.1.

Path guarantees, verbatim (`hooks-ref.md:1528–1533`) — load-bearing for `locator.py`:

> For the file tools `Write`, `Edit`, and `Read`, `tool_input.file_path` is always absolute:
> * Claude Code expands `~` and relative paths before hooks run, so a hook that matches on paths
>   can't be bypassed via `~` or a relative spelling of the same path
> * On Windows, the path arrives with backslash separators, even when your hook runs under Git Bash
>   where `$PWD` looks like `/c/project`
> * A comparison written with forward slashes, such as a `/src/` check, never matches a backslash
>   path, and the tool call proceeds as if the hook had nothing to block
> * Normalize separators before comparing: … or `file_path.replace("\\", "/")` in Python, then
>   match a path segment such as `/src/` rather than anchoring with `^`, since the path is absolute

**Adopt:** `locator.py` takes `file_path` as absolute and authoritative — never `os.getcwd()`,
never `cwd + file_path`. Normalize `\\` → `/` before any repo-relative computation, then compute
`repo_relative = abspath.relative_to(repo_root)` for the Serena-style qualname.

**Adopt (Edit → range):** `Edit` gives **no line numbers**, only `old_string`. `locator.py` must
compute the byte offset itself: `idx = source.find(tool_input["old_string"])`, convert to a
line/byte range, then ask tree-sitter for the enclosing `function_definition` / `class_definition`
node. If `old_string` is absent from the file (stale edit) or occurs multiple times with
`replace_all: true`, fall back to **file-level claim granularity** rather than guessing. `Write`
targets a whole file → always file-level, plus (if the file already exists) the set of symbols
whose bodies change; MVP: file-level for `Write`.

### 2.2 Exit-code contract (docs, verbatim)

Headline, `hooks-ref.md:756` (anchor `#exit-code-output`):

> The exit code from your hook command tells Claude Code whether the action should proceed, be
> blocked, or be ignored. The exit code doesn't act alone. Claude Code reads JSON output fields
> from stdout on every exit code, not just 0… **Exit 2's block is the one outcome JSON can't
> override.**

Exit 0 (`hooks-ref.md:762–771`):

> Exit 0 means success… For most events, stdout is written to the debug log but not shown in the
> transcript. … Whether Claude Code reads your stdout as JSON output or as plain text depends on
> its first character, ignoring leading whitespace: **Starts with `{`**: Claude Code parses it as
> JSON… **Starts with anything else**: Claude Code treats it as plain text…
> **Stderr from a hook that exits 0 goes to the debug log only, never the transcript, and Claude
> never sees it.**

Exit 2 (`hooks-ref.md:775–779`):

> Exit 2 means a blocking error. On events that can block, **exit 2 blocks whether or not you print
> JSON: even a JSON `permissionDecision` of `"allow"` can't override it.** …
> **The blocking message is the reason from your JSON's blocking decision when it makes one, and
> your stderr text otherwise.** What the block does varies by event: `PreToolUse` blocks the tool
> call…

Per-event table (`hooks-ref.md:828`): `| PreToolUse | Yes | Blocks the tool call |`

And, decisively for loom (`hooks-ref.md:1719`):

> **A hook that blocks by exiting 2 routes the same way as `"deny"`: Claude sees the stderr message
> as the denial reason.**

Other codes — the fail-open path (`hooks-ref.md:805` and the warning box at `:812`):

> With stdout that Claude Code treats as plain text, or with empty stdout, it's a non-blocking
> error for most hook events: the action proceeds, and the transcript shows a `<hook name> hook
> error` notice followed by the first line of stderr, prefixed with `Failed with non-blocking
> status code:`.
>
> For most hook events, **exit code 2 is the only exit code that blocks through the code alone.
> Without valid JSON on stdout, Claude Code treats exit code 1 as a non-blocking error and proceeds
> with the action**, even though 1 is the conventional Unix failure code. If your hook is meant to
> enforce a policy, use `exit 2`.

And the silent-gate trap (`hooks-ref.md:809`):

> When the script path doesn't exist or isn't executable, the shell exits with a code like 127 and
> you see the same notice with the interpreter's message… **When you set up a policy hook, watch
> for this notice on its first run: a mistyped path in `settings.json` leaves the gate silently
> disabled.**

**Adopt — loom's exit map for `hook/gate.py`:**

| Situation | gate.py does | Why |
| :-- | :-- | :-- |
| server says allow | `exit 0`, no stdout | normal permission flow proceeds |
| foreign claim / out-of-scope / no plan | write message to **stderr**, `exit 2` | blocks; Claude sees stderr as the denial reason (`:1719`) |
| server unreachable / our own HTTP timeout / any internal exception | print `{"systemMessage": "..."}` to stdout, `exit 0` | fail-open **with a user-visible warning**; see §3.2 |
| never | `exit 1` / uncaught traceback | would fail open but render a scary "hook error" notice and no useful message |

`loom init` must verify the gate after writing settings (run it once with a synthetic payload) —
`:809` is exactly the failure mode that makes the gate silently absent.

### 2.3 `settings.json` wiring (docs, verbatim structure)

Nesting rule (`hooks-ref.md:235–239`): "1. Choose a hook event… 2. Add a matcher group… 3. Define
one or more hook handlers".

Matcher semantics table, verbatim (`hooks-ref.md:286–292`):

| Matcher value | Evaluated as | Example |
| :-- | :-- | :-- |
| `"*"`, `""`, or omitted | Match all | fires on every occurrence of the event |
| Only letters, digits, `_`, `-`, spaces, `,`, and `\|` | Exact string, or list of exact strings separated by `\|` or `,` with optional surrounding whitespace | `Bash` matches only the Bash tool; `Edit\|Write` and `Edit, Write` each match either tool exactly |
| Contains any other character | JavaScript regular expression, unanchored | `^Notebook` matches any tool whose name starts with `Notebook`; `mcp__memory__.*` matches every tool from the `memory` server |

> A matcher on the regular-expression path is tested with JavaScript's `RegExp.prototype.test`,
> which succeeds on a match anywhere in the value. `Edit.*` matches both `Edit` and `NotebookEdit`;
> wrap the pattern in `^` and `$`, as in `^Edit$`, when you need a whole-string match.

MCP matching (`hooks-ref.md:361`), the Serena trap, verbatim:

> To match every tool from a server, append `.*` to the server prefix. **The `.*` is required: a
> matcher like `mcp__memory` or `mcp__brave-search` contains only exact-match characters, so it is
> compared as an exact string and matches no tool.**

**Adopt — exactly what `loom init` writes into `.claude/settings.json`** (project scope; see
`hooks-ref.md:251–259` for the location/scope table — `.claude/settings.json` is "Single project /
Yes, can be committed to the repo"):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/loom-gate",
            "args": [],
            "timeout": 5,
            "statusMessage": "loom: checking claims"
          }
        ]
      },
      {
        "matcher": "mcp__.*__(replace_symbol_body|insert_after_symbol|insert_before_symbol|rename_symbol|safe_delete_symbol|create_text_file|replace_content|replace_in_files|delete_lines|replace_lines|insert_at_line)",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/loom-gate",
            "args": [],
            "timeout": 5,
            "statusMessage": "loom: checking claims"
          }
        ]
      }
    ]
  }
}
```

*(GATE-1 fix 4: an earlier draft of this block used `"matcher": "mcp__serena__.*"`. serena.md C4
proves the `serena` key is user-minted at `claude mcp add` time — and this document's own §5.6
concedes the plugin-prefix case (`mcp__plugin_<plugin>_<server>__<tool>`) breaks a hardcoded server
prefix. The matcher is therefore the suffix regex above (tool list from serena.md §2.3/ADAPT 5),
and gate.py re-derives the classification from the tool-name suffix after the last `__`.)*

Notes, each doc-backed:

- `Edit|Write|MultiEdit|NotebookEdit` stays on the **exact-string list** path (all chars are in the
  letters/`|` set, `:289`), so it is four exact names, not a regex. `MultiEdit` no longer exists in
  the current reference but costs nothing and keeps older Claude Code versions covered.
  `NotebookEdit` **does** exist (`:292` names it).
- The MCP matcher is a **suffix regex on tool names**, not a server prefix (GATE-1 fix 4; serena.md
  C4 — the server key is user-minted, so `mcp__serena__.*` matches nothing when the user registered
  the server under another key or via a plugin). Note the `:361` trap still applies to anyone
  writing a prefix matcher: a bare `mcp__serena` is exact-match characters only and matches no
  tool. It is a separate matcher group because the two groups match disjoint sets; both point at
  the same script. All matching hooks run in parallel and a handler defined in more than one
  settings file "runs once" (`:410`).
- `${CLAUDE_PROJECT_DIR}` = "the project root" (`:585`). "Prefer exec form for any hook that
  references a path placeholder" (`:589`) — exec form is what `"args": []` selects (`:449`,
  `:460`): `command` is resolved as an executable on PATH and spawned directly, no shell, no
  quoting hazards. Write `loom-gate` as an executable shim with a `#!` line rather than
  `"command": "python3 /path/gate.py"`.
- Hooks merge across settings levels rather than replacing (`:275`), so appending loom's block is
  safe next to a user's existing hooks. `loom init` must merge into the existing
  `hooks.PreToolUse` array, not overwrite it.
- Optional narrowing: the handler-level `if` field takes one permission rule, e.g.
  `"Edit(**/*.py)"` (`:421`, `:428`). Do **not** use it for the gate — `:440` says "the `if` filter
  is best-effort" and "fails open… when the Bash command can't be parsed". Filter inside gate.py.

### 2.4 Timeout field + default (docs, verbatim)

`hooks-ref.md:422`, common hook-handler fields:

> `timeout` — no (not required) — **Seconds** before canceling. Defaults: **600 for `command`,
> `http`, and `mcp_tool`**; 30 for `prompt`; 60 for `agent`. `UserPromptSubmit` lowers the
> `command`, `http`, and `mcp_tool` default to 30, and `MessageDisplay` lowers it to 10.

Timeout behavior, verbatim (`hooks-ref.md:817–819`):

> A `command`, `http`, or `mcp_tool` hook that reaches its `timeout` is canceled: **Claude Code
> discards the hook's output, and the hook renders no decision.** … A timed-out `command`, `http`,
> or `mcp_tool` hook **doesn't block the tool call. The call continues through the normal
> permission flow, so don't count on a stalled hook to act as a gate.**

**Adopt:** set `"timeout": 5` explicitly. The 600 s default is catastrophic for loom — a hung
server would freeze every edit for ten minutes. But the settings `timeout` is only a **backstop**:
because a timed-out hook's output is *discarded*, the plan's mandated "loud warning line" cannot
come from that path. gate.py therefore enforces its **own** ~1.5–2 s HTTP client timeout and exits
0 with a `systemMessage` (§3.2).

**Unit trap:** hook-handler `timeout` is in **seconds** (`:422`); `Bash`'s `tool_input.timeout` is
in **milliseconds** (`:1559`). Same word, different unit, one config file apart.

### 2.5 JSON decisions vs plain exit codes — both work; here is the difference

Verbatim (`hooks-ref.md:1708`):

> `PreToolUse` hooks can control whether a tool call proceeds. Unlike other hooks that use a
> top-level `decision` field, PreToolUse returns its decision inside a `hookSpecificOutput` object.
> This gives it richer control: four outcomes (allow, deny, ask, or defer) plus the ability to
> modify tool input before execution.

Field table, verbatim (`hooks-ref.md:1712–1715`):

| Field | Doc text |
| :-- | :-- |
| `permissionDecision` | "`\"allow\"` skips the permission prompt, except for the actions no mode auto-approves and for `AskUserQuestion` and `ExitPlanMode`, which need `updatedInput` paired with it. `\"deny\"` prevents the tool call. `\"ask\"` prompts the user to confirm. `\"defer\"` exits gracefully so the tool can be resumed later. **Deny and ask rules are still evaluated regardless of what the hook returns**" |
| `permissionDecisionReason` | "**For `\"allow\"` and `\"ask\"`, shown to the user but not Claude. For `\"deny\"`, shown to Claude.** For `\"defer\"`, ignored" |
| `updatedInput` | "Modifies the tool's input parameters before execution. Replaces the entire input object, so include unchanged fields alongside modified ones." |
| `additionalContext` | "String added to Claude's context alongside the tool result. Ignored when `permissionDecision` is `\"defer\"`." |

> When multiple PreToolUse hooks return different decisions, precedence is `deny` > `defer` >
> `ask` > `allow`. (`:1717`)

Canonical shape (`hooks-ref.md:1725–1737`):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "My reason here",
    "updatedInput": { "field_to_modify": "new value" },
    "additionalContext": "Current environment: production. Proceed with caution."
  }
}
```

Deprecation note, verbatim (`hooks-ref.md:1746`):

> PreToolUse previously used top-level `decision` and `reason` fields, but these are deprecated for
> this event. Use `hookSpecificOutput.permissionDecision` and
> `hookSpecificOutput.permissionDecisionReason` instead. **The deprecated values `"approve"` and
> `"block"` map to `"allow"` and `"deny"` respectively.**

Mixing rule (`hooks-ref.md:879`): *"Choose one approach per hook: either use exit codes alone for
signaling, or exit 0 and print JSON for structured control."*

**Adopt for loom (decision, with reasoning):** use **exit 2 + stderr for denials**, JSON only for
the fail-open warning. Both routes are equivalent for the model (`:1719` — "Claude sees the stderr
message as the denial reason"), and PLAN §1 already commits to exit-2. Exit 2 also cannot be
overridden by a competing hook returning `allow` (`:775`), which is the stronger enforcement
posture and the whole point of the gate. A JSON `deny` would additionally be subject to
"`permissionDecision` … deny and ask rules are still evaluated regardless" plumbing and to precedence
merging with other hooks.

**Hard cap — this bites loom directly** (`hooks-ref.md:885`), verbatim:

> Hook output strings, including `additionalContext`, `systemMessage`, and plain stdout, are capped
> at **10,000 characters**. Output that exceeds this limit is saved to a file and replaced with a
> preview and file path.

loom's deny message embeds the owning plan's **full `spec_md`**. **Budget: truncate the assembled
stderr message to 9,000 characters**, preserving the header (`claimed by <agent> under plan <id>:
<title>`) and trimming the spec body from the end with a `[spec truncated — call get_plan(<id>) for
the full text]` marker. Never let the spec push the actionable header out.

**Phrasing rule for the deny message** (`hooks-ref.md`, `#add-context-for-claude`, ~`:977`):

> Write the text as factual statements rather than imperative system instructions. … Text framed as
> out-of-band system commands can trigger Claude's prompt-injection defenses, which causes Claude to
> surface the text to you instead of treating it as context.

So the gate message must read as a report + the plan text, not as `SYSTEM: YOU MUST…`. PLAN §4.3's
wording ("Build against its declared interfaces or rescope.") is fine; keep it declarative.

### 2.6 Patterns taken from the permissions-hook clone (patterns only, no verbatim code)

*(Restatements. No Rust is reproduced; the repo is unlicensed — see §1.)*

**P1 — three-state decision, deny evaluated first.**
Provenance: `src/lib.rs:71–86` (`process_hook_input_with_config`), `src/auditing.rs:15–21`
(`enum Decision { Allow, Deny, Passthrough }`), and the README flowchart.
Pattern: the hook resolves to one of **deny / allow / passthrough**; deny rules are evaluated
before allow rules; **no match at all produces no output and no decision**, leaving the tool to the
normal permission flow.
loom restatement: `gate.py` returns `DENY` (exit 2 + stderr), `PASS` (exit 0, silent — our default
for allow, since we do not want to auto-approve anything the user's own permission rules would
prompt on), and `FAIL_OPEN` (exit 0 + `systemMessage`). Never emit `permissionDecision: "allow"` —
that would *suppress* the user's normal permission prompts as a side effect of a coordination
check.

**P2 — the check is a pure function of (config, input); IO lives at the edges.**
Provenance: `src/lib.rs:62–86` — a `process_hook_input_with_config(config, input) -> HookResult`
core with the doc comment "This is the core logic that can be tested without stdin/stdout", wrapped
by `src/main.rs:39–75` which owns stdin/stdout/exit.
loom restatement: `gate.py` exposes `decide(payload: dict, cfg) -> Decision` with no IO; `main()`
does `json.load(sys.stdin)` → `decide` → write stderr/stdout → `sys.exit(code)`. This is what makes
M3's acceptance criterion ("pipe PreToolUse JSON into gate.py and assert exit codes plus stderr for
all four gate cases") cheap.

**P3 — JSON fixture files piped into the hook as the test suite.**
Provenance: `tests/README.md` fixture table + `tests/{read_allowed,read_path_traversal,bash_allowed,bash_injection,unknown_tool}.json`,
each a ~10-line PreToolUse payload; `tests/integration_test.rs` drives them.
loom restatement: create `tests/fixtures/pretooluse/{in_plan_allow,foreign_claim_deny,out_of_scope_deny,no_plan_deny,server_down_failopen,unknown_tool_pass}.json`
and a pytest that runs `gate.py` as a subprocess with the fixture on stdin, asserting `(exit_code,
stderr_substring)`. That *is* M3's acceptance test. Include a fixture with `agent_id`/`agent_type`
present (subagent case) and one with `permission_mode` absent.

**P4 — audit every decision to a JSONL file, appended under an exclusive advisory file lock, with
long strings truncated.**
Provenance: `src/auditing.rs:87–117` — open with create+append, take an exclusive `flock`, write one
JSON line, unlock; `src/auditing.rs:24` `MAX_STRING_LEN = 256` with a recursive JSON string
truncator at `:39–62`; audit levels `off | matched | all` at `:72–76` where `matched` logs only
non-passthrough decisions.
loom restatement: gate.py appends one JSON object per line to `~/.loom/gate-audit.jsonl` —
`{ts, session_id, agent_id, tool_name, file_path, node_id, decision, plan_id, reason}` — using
`fcntl.flock(fd, LOCK_EX)` around the append, and recursively truncating any string over 256 chars
(a `Write` payload's `content` is unbounded and will otherwise blow up the log). Default level
`matched`. This log is the local mirror of the server's `events` table and is what you read when a
demo run misbehaves and the server is not the suspect.

**P5 — extract fields defensively by name from an opaque `tool_input`.**
Provenance: `src/hook_io.rs:15` types `tool_input` as an opaque JSON value, and `:49–54`
(`extract_field`) does get → as_str → Option, returning `None` for both a missing key and a
non-string value.
loom restatement: `payload.get("tool_input", {}).get("file_path")` with `isinstance(x, str)`
guarding; never index. Different tools carry the path under different keys — see §3.3.

---

## 3. ADAPT

### 3.1 Matcher set, adapted

PLAN §4.3 says "matcher on Edit, Write, MultiEdit, and the Serena edit tools if present". Adapt to
the wiring in §2.3: `Edit|Write|MultiEdit|NotebookEdit` (MultiEdit retained defensively only) plus a
**second matcher group** carrying the suffix regex
`mcp__.*__(replace_symbol_body|...|insert_at_line)` (full list in §2.3; GATE-1 fix 4 — a hardcoded
`mcp__serena__` prefix breaks whenever the user minted a different server key or Serena arrives as a
plugin, per serena.md C4 and §5.6 below). Reason for two groups: matcher values are per-group, and
mixing the regex into the same string would push the whole matcher onto the regex path (`:290`),
where `Edit` unanchored would then also match `NotebookEdit`, `mcp__x__Edit`, and anything else
containing "Edit". Two groups keeps the exact-match group exact.

### 3.2 Fail-open, adapted from "just let it time out" to an explicit path

PLAN's MVP addendum mandates "hook fail-open with ~2s timeout when the server is unreachable". The
doc forces a specific shape:

- Settings `"timeout": 5` is a backstop only; a timeout **discards output** (`:817`) so no warning
  would ever reach the user through it.
- gate.py sets its own client timeout (`httpx.Client(timeout=1.5)` or
  `urllib.request.urlopen(..., timeout=1.5)`) on the `check` call.
- On `ConnectionError` / timeout / non-200 / malformed response / any unexpected exception, gate.py
  prints exactly one JSON object to stdout and exits 0:

```json
{"systemMessage": "loom: coordination server unreachable — edit allowed, claims NOT checked"}
```

  `systemMessage` is "Warning message shown to the user" (`:898`). It is a **universal** field, so
  it needs no `hookSpecificOutput` wrapper and carries no decision, which is exactly right: we want
  "no decision", not `allow`.
- Wrap `main()` in a bare `try/except BaseException` that funnels to the same fail-open branch.
  An uncaught traceback exits 1, which also fails open (`:812`) but renders the ugly
  `Failed with non-blocking status code:` notice with no useful text.

Restated as an invariant for the implementer: **gate.py has exactly two exit codes, 0 and 2.**

### 3.3 `locator.py` input mapping, adapted per tool

| `tool_name` | path key | range info available | loom granularity |
| :-- | :-- | :-- | :-- |
| `Edit` | `tool_input.file_path` | `old_string` only — find its offset in the file | symbol (enclosing def), file-level on ambiguity |
| `Write` | `tool_input.file_path` | whole file (`content`) | file-level (MVP) |
| `MultiEdit` (legacy) | `tool_input.file_path` | `edits[].old_string` | union of enclosing symbols |
| `NotebookEdit` | **`tool_input.notebook_path`** | `cell_id` | file-level |
| `mcp__serena__*` | server-defined (`relative_path` / `name_path`) | symbol name directly | symbol; if keys are unrecognized → PASS, do not guess |

The `NotebookEdit` key difference is the concrete reason P5's defensive extraction matters: a
`file_path`-only locator silently no-ops on notebooks. MVP may legitimately treat notebooks as out
of scope (see REJECT §4.4) — but then say so and PASS, do not crash.

### 3.4 The deny messages, adapted to the 10k cap

PLAN §4.3's three strings, kept verbatim in intent, with the assembly rule from §2.5:

1. Foreign claim → `claimed by <agent> under plan <id>: <title>. Its spec follows. Build against
   its declared interfaces or rescope.` + `\n\n` + `spec_md`, truncated so total stderr ≤ 9,000
   chars.
2. Unclaimed but outside my plan → `outside your declared plan. Call rescope first.` (+ the node id
   we resolved, so the agent can pass it straight to `rescope`).
3. No active plan → `declare a plan before editing.` (+ the path to `templates/spec.md`).

Prefix every line with `loom: ` so the source of the block is unambiguous in the transcript.

### 3.5 Config discovery, adapted

The clone takes `--config <path>` (`src/main.rs:28–31`) and fails hard if the file is missing
(`main` returns `Result` → exit 1). Adapt: gate.py reads `~/.loom/config.toml` (server URL, agent
token, repo root) with **no CLI args**, and a missing/broken config takes the fail-open branch with
`systemMessage: "loom: not initialized — run loom init"`. Rationale: a hook must never hard-fail on
a config problem, and exec-form `args: []` keeps the settings entry stable across config moves.

### 3.6 Don't build the loom gate as an HTTP hook (but know it exists)

`hooks-ref.md:497–534` documents `"type": "http"` handlers that POST the hook payload to a URL and
read the decision from the response body — tempting, since loom already runs a server. Reject for
v1: locator.py needs the **local working-tree file** to map an edit to a symbol, and the server may
be on another machine. Keep the command hook. Revisit only if the locator moves client-side of a
richer payload. Note also `:873`: "**HTTP hooks can't signal a blocking error through status codes
alone.** To block a tool call… return a 2xx response with a JSON body containing the appropriate
decision fields."

---

## 4. REJECT

### 4.1 Reject: `permissionDecision: "allow"` on the happy path
Returning `allow` "skips the permission prompt" (`:1712`). A coordination gate that silently
auto-approves every in-plan edit would strip the user's own `Edit` permission prompts and deny
rules' UX. loom's allow is **silence** (exit 0, no stdout). PLAN never asked for `allow`; this is a
guardrail against an implementer "improving" it.

### 4.2 Reject: `"defer"`, `updatedInput`, `ask`
`defer` is honored "only in non-interactive mode with the `-p` flag. In interactive sessions it
logs a warning and ignores the hook result" (`:1751`) — useless for the demo. `updatedInput`
"replaces the entire input object" (`:1714`) — rewriting a teammate's edit is exactly the "clever
merger" PLAN §8 rules out. `ask` puts a human in the loop on every collision, which destroys the
autonomous-replan demo in PLAN §6.

### 4.3 Reject: the clone's `suppressOutput` field
`src/hook_io.rs:22–24` sets `"suppressOutput": true` on every response. Current docs, `:897`:
"**Has no effect**: Claude Code accepts the field but doesn't act on it. A successful hook's stdout
is never shown in the transcript and is recorded in the debug log." Do not carry it over.

### 4.4 Reject: the clone's `matcher: "*"` catch-all wiring
The clone's README installs itself on `"matcher": "*"`, i.e. every tool call. For loom that means
running a Python process and an HTTP round trip on every `Read`, `Grep`, `Glob`, and `Bash` — the
`check` budget in PLAN §4.2 is sub-10 ms and process startup alone dwarfs it. Match only the edit
tools.

### 4.5 Reject: the clone's strict input struct
`src/hook_io.rs:9–16` declares `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
`tool_name`, `tool_input` as **required non-optional** fields; serde fails the whole parse if one is
absent, and `main` then exits 1. The docs explicitly warn that `permission_mode` is "Not all events
receive this field" (`:718`) and that `prompt_id` is "Absent until the first user input… Requires
Claude Code v2.1.196 or later" (`:715`). Parse leniently: read `tool_name` and `tool_input` with
`.get()`, treat everything else as optional telemetry.

### 4.6 Reject: the clone's TOML rule-engine, regex allow/deny model, and `validate` subcommand
`src/config.rs`, `src/matcher.rs`, `example.toml`. loom's decision comes from the server's claim
table, not from local path regexes. Importing a second policy language would give us two places to
be wrong. (Also: `src/matcher.rs`'s `check_rule` has arms only for `Read|Write|Edit|Glob`, `Bash`,
`Task`, and a `_ => {}` catch-all — so its own `MultiEdit` rules would never fire. A rules DSL with
a silent hole is precisely the thing not to copy.)

### 4.7 Reject: relying on stderr from a non-2 exit to communicate anything
`:771` — stderr on exit 0 "goes to the debug log only, never the transcript, and Claude never sees
it." `:805` — on other codes only "the first line of stderr" is surfaced, in an error notice. The
only two channels loom may use are **stderr with exit 2** and **JSON on stdout**.

### 4.8 Reject (MVP): guarding non-tool file access
`:1515`, verbatim: "PreToolUse runs only when Claude calls a tool. **Files you reference with `@` in
your prompt are added without any tool call**… so no PreToolUse hook fires for them". PreToolUse
also "doesn't fire for `EndConversation`". Relevant honesty for the eval writeup: loom's gate covers
tool-mediated edits, which is all edits, but not all *reads*. Nothing to build; do not claim
otherwise in the demo.

---

## 5. CORRECTIONS to PLAN-v1.md

**5.1 — `MultiEdit` is gone from the documented tool surface. (PLAN §4.3, §5/M3)**
`grep -n "MultiEdit" hooks-ref.md` returns **zero hits in the entire 273KB reference**. The
authoritative PreToolUse tool list (`:1512`) reads, verbatim: "Matches on tool name: `Bash`,
`PowerShell`, `Edit`, `Write`, `Read`, `Glob`, `Grep`, `Agent`, `WebFetch`, `WebSearch`,
`AskUserQuestion`, `ExitPlanMode`, and any MCP tool names." The per-tool `tool_input` tables
(`:1551–1704`) cover Bash, PowerShell, Write, Edit, Read, Glob, Grep, WebFetch, WebSearch, Agent,
AskUserQuestion, ExitPlanMode — no MultiEdit. Keep `MultiEdit` in the matcher string (free
insurance for older clients) but **do not write a MultiEdit acceptance test as a required M3 case**,
and do not assume an `edits[]` array will ever arrive. Note that the list at `:1512` is not
exhaustive either — `NotebookEdit` is absent from it yet is named as a real tool at `:292`.

**5.2 — the plan's PreToolUse timeout number needs to be two numbers, not one. (PLAN MVP addendum)**
"hook fail-open with ~2s timeout" is under-specified against the doc. The settings `timeout` is in
**seconds with a default of 600** (`:422`), and a hook that hits it has its **output discarded and
renders no decision** (`:817`) — so a 2 s settings timeout gives you fail-open but *no warning
line*, and the plan explicitly wants a loud one. Correct form: settings `"timeout": 5` **plus** a
1.5 s client-side HTTP timeout inside gate.py that exits 0 with a `systemMessage`. See §3.2.

**5.3 — the deny message is length-capped; the plan's "embed the full spec" is not free. (PLAN
§4.2, §4.3)**
`:885`: hook output strings "are capped at 10,000 characters. Output that exceeds this limit is
saved to a file and replaced with a preview and file path." PLAN §4.2 says a conflict response
"embeds each clashing plan's full spec_md inline, so the fetch step is free" and §4.3 pushes the
spec through the gate. That holds for the **MCP tool response** (not hook output) but **not** for
the hook's stderr. Add the 9,000-char truncation rule (§2.5) to `hook/gate.py`'s spec, and keep
`templates/spec.md` genuinely one page — a spec over ~8 KB defeats the pull-through, which is the
demo's centrepiece.

**5.4 — the plan under-specifies how `Edit` maps to a range. (PLAN §4.3 "the edited range")**
There is no range. `Edit`'s `tool_input` is exactly `{file_path, old_string, new_string,
replace_all}` (`:1596–1601`) — no line numbers, no offsets. `locator.py` must locate `old_string`
in the on-disk file itself and handle not-found and multiple-match cases. This is a real chunk of
M3's work that the plan's one-liner hides; budget for it and for the file-level fallback.

**5.5 — `.claude/settings.json` must be merged, not written. (PLAN §4.5 `loom init`)**
`:275`: "Hook entries merge across settings levels rather than replacing each other". That is
across *levels*; **within** a single file, `hooks.PreToolUse` is one array and `loom init`
overwriting it would silently delete a user's existing hooks. Spec `loom init` to read-modify-write,
keyed on the loom command string for idempotency (re-running init must not append a duplicate).
Also add the post-write verification run — `:809`: "a mistyped path in `settings.json` leaves the
gate silently disabled."

**5.6 — the Serena matcher needs `.*`. (PLAN §1 "if a user has it, the hook also matches its edit
tools")**
`:361`: a bare `mcp__serena` "contains only exact-match characters, so it is compared as an exact
string and **matches no tool**." The optional-Serena support is a silent no-op unless written
`mcp__serena__.*`. Also `:369`: if Serena arrives as a *plugin*-bundled MCP server, its tools are
named `mcp__plugin_<plugin-name>_<server-name>__<tool>` and the bare-server matcher never fires —
another reason Serena stays "optional and never load-bearing" per PLAN §1.

**5.7 — the gate fires inside subagents too, and the plan's identity model must account for it.
(PLAN §4.3, §7 "Identity")**
`:265`: "Hooks from settings files, managed policy settings, and plugins **also run inside
subagents**… the input carries the `agent_id` and `agent_type` common input fields". So a single
loom "agent" (one Claude Code session, one token) can hit `check` from N concurrent subagents, all
editing under the same plan. That is fine for the claim model (the plan owns the claims, not the
process) but it means: (a) the audit log must record `agent_id`/`session_id` or the events log
misattributes work; (b) `check` throughput at 10 users is not 10 callers, it is 10 × fanout. The
sub-10 ms warm target in PLAN §4.2/M2 should be measured against that.

**5.8 — one plan-adjacent doc fact worth recording: exit 1 is not a block.**
`:812`, verbatim: "Without valid JSON on stdout, Claude Code treats exit code 1 as a non-blocking
error and proceeds with the action, even though 1 is the conventional Unix failure code. If your
hook is meant to enforce a policy, use `exit 2`." PLAN §1 already says exit 2, but any Python
`sys.exit("message")` or uncaught exception yields 1, i.e. a **silently open gate**. Make "gate.py
exits only 0 or 2" an explicit M3 test.

**5.9 — vendor doc staleness, for the record.**
The clone's `docs/tool-input-schemas.md` (358 lines, Dec 2025) documents `MultiEdit`, `LS`,
`BashOutput`, `KillShell`, and the subagent tool as **`Task`**; the current reference names the
subagent tool **`Agent`** (`:1654`) and has no `Task`, `LS`, or `MultiEdit`. Its own header admits
it: "As of December 2025, Anthropic does not publish official documentation for tool_input schemas.
The information below is compiled from [community sources]… Schemas may change between Claude Code
versions. Always test against actual hook inputs." Anthropic now **does** publish them (`:1549`
onward), so `docs/tool-input-schemas.md` in the clone is superseded — **do not cite it**, cite the
reference. The drift itself is the argument for a defensive matcher and lenient parsing.
