# Extraction: Serena (github.com/oraios/serena)

Source clone: `/private/tmp/claude-501/-Users-cero-Desktop-PROJECTS-reforge-workspace-re-forge-irl-data-team-collab/6458dacd-1b63-4e60-82c7-dac1ea52eb51/scratchpad/vendor/serena`
Version in clone: `serena-agent` 1.7.1.dev0 (`pyproject.toml:6-7`).
All `file:line` references below are relative to `<clone>/`.

---

## 1. LICENSE

**MIT License, Copyright (c) 2025 Oraios AI** — `LICENSE:1-3`, corroborated by `pyproject.toml:68-69`:

```toml
[project.license]
text = "MIT"
```

Full grant text (`LICENSE:5-9`):

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software...

**Restrictions that matter:** exactly one — attribution. `LICENSE:11-12`:

> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

**Consequences for loom:**

- Verbatim excerpts and derived code are permitted. Unlike `mcp_agent_mail` (MIT + OpenAI/Anthropic rider, patterns-only per PLAN-v1 header), **no "patterns only" restriction applies to Serena.** We may copy code.
- If we copy a *substantial portion* (more than the small snippets in §2), add `loom/NOTICES.md` with the Oraios AI copyright line. For the snippet-scale adoption in §2, a `# derived from serena (MIT, (c) 2025 Oraios AI)` comment at the top of `indexer/naming.py` and `hook/gate.py` discharges the obligation.
- **No separate license inside `src/solidlsp/` or `src/interprompt/`** — `find src/solidlsp src/interprompt -iname "*license*"` returns nothing, and no per-file copyright headers exist. The single root MIT covers the whole tree. (We take nothing from solidlsp anyway — see §4.)

---

## 2. ADOPT

### 2.1 The canonical symbol identifier — it is a **pair**, and the separator is `/`

This is the single most important finding, and it contradicts PLAN-v1 §2 (see §5).

**Separator constant** — `src/serena/symbol.py:26`:

```python
NAME_PATH_SEP = "/"
```

**Construction of the within-file symbol path** — `src/serena/symbol.py:347-359`:

```python
    def get_name_path(self) -> str:
        """
        Get the name path of the symbol, e.g. "class/method/inner_function" or
        "class/method[1]" (overloaded method with identifying index).
        """
        name_path = NAME_PATH_SEP.join(reversed([str(x) for x in self.iter_name_path_components_reversed()]))
        return name_path

    def iter_name_path_components_reversed(self) -> Iterator[NamePathComponent]:
        yield NamePathComponent(self.name, self.overload_idx)
        for ancestor in self.iter_ancestors(up_to_symbol_kind=SymbolKind.File):
            yield NamePathComponent(ancestor.name, ancestor.overload_idx)
```

Note `up_to_symbol_kind=SymbolKind.File`: ancestry stops **before** the file node, so the file is never part of the name path string.

**Component rendering, including the overload index** — `src/serena/symbol.py:131-141`:

```python
class NamePathComponent:
    def __init__(self, name: str, overload_idx: int | None = None) -> None:
        self.name = name
        self.overload_idx = overload_idx

    def __repr__(self) -> str:
        if self.overload_idx is not None:
            return f"{self.name}[{self.overload_idx}]"
        else:
            return self.name
```

**The convention, stated by the source itself** — `src/serena/symbol.py:145-157` (`NamePathMatcher` class docstring; the identical text is repeated verbatim as agent-facing documentation in `src/serena/tools/symbol_tools.py:162-172`):

```
    A name path is a path in the symbol tree *within a source file*.
    For example, the method `my_method` defined in class `MyClass` would have the name path `MyClass/my_method`.
    If a symbol is overloaded (e.g., in Java), a 0-based index is appended (e.g. "MyClass/my_method[0]") to
    uniquely identify it.

    A matching pattern can be:
     * a simple name (e.g. "method"), which will match any symbol with that name
     * a relative path like "class/method", which will match any symbol with that name path suffix
     * an absolute name path "/class/method" (absolute name path), which requires an exact match of the full name path within the source file.
    Append an index `[i]` to match a specific overload only, e.g. "MyClass/my_method[1]".
```

**The file half of the identity is a separate field.** `src/serena/symbol.py:29-47`:

```python
@dataclass
class LanguageServerSymbolLocation:
    """
    Represents the (start) location of a symbol identifier, which, within Serena, uniquely identifies the symbol.
    """

    relative_path: str | None
    line: int | None
    column: int | None
```

and every symbolic edit tool takes the two halves as **two separate parameters** (`symbol_tools.py:590-593`, `623-626`, `649-652`, `675-678`, `699-702`). There is no place in the repo where a path and a name path are concatenated into one identifier string; `to_dict` (`symbol.py:450-465`) emits `name_path` and `relative_path` as distinct keys.

**Absolute vs. suffix matching** — `src/serena/symbol.py:181-193`:

```python
    def __init__(self, name_path_pattern: str, substring_matching: bool) -> None:
        assert name_path_pattern, "name_path must not be empty"
        self._expr = name_path_pattern
        self._substring_matching = substring_matching
        self._is_absolute_pattern = name_path_pattern.startswith(NAME_PATH_SEP)
        self._components = [
            self.PatternComponent.from_string(x) for x in name_path_pattern.lstrip(NAME_PATH_SEP).rstrip(NAME_PATH_SEP).split(NAME_PATH_SEP)
        ]
```

Matching is done **right-to-left over reversed components**, and an absolute pattern additionally asserts the symbol has no further ancestors — `src/serena/symbol.py:198-215`:

```python
    def matches_reversed_components(self, components_reversed: Iterator[NamePathComponent]) -> bool:
        for i, pattern_component in enumerate(reversed(self._components)):
            try:
                symbol_component = next(components_reversed)
            except StopIteration:
                return False
            use_substring_matching = self._substring_matching and (i == 0)
            if not pattern_component.matches(symbol_component, use_substring_matching):
                return False
        if self._is_absolute_pattern:
            # ensure that there are no more components in the symbol
            try:
                next(components_reversed)
                return False
            except StopIteration:
                pass
        return True
```

**Overload-index parsing from a pattern string** — `src/serena/symbol.py:159-169`:

```python
        @classmethod
        def from_string(cls, component_str: str) -> Self:
            overload_idx = None
            if component_str.endswith("]") and "[" in component_str:
                bracket_idx = component_str.rfind("[")
                index_part = component_str[bracket_idx + 1 : -1]
                if index_part.isdigit():
                    component_str = component_str[:bracket_idx]
                    overload_idx = int(index_part)
            return cls(name=component_str, overload_idx=overload_idx)
```

**What `indexer/naming.py` adopts** (see §3 for the loom-specific wire format):

- `NAME_PATH_SEP = "/"` and the reversed-ancestry join.
- Overload suffix `[i]`, rendered only when an index exists.
- Leading `/` = absolute (whole-file-rooted) qualname; no leading `/` = suffix pattern.
- Right-to-left suffix matching for the fuzzy input side of `resolve_nodes`.
- Storing path and qualname as two columns, never one string.

### 2.2 Tool-name derivation (how the exact strings in §2.3 were proved)

`src/serena/tools/tools_base.py:192-199`:

```python
    @classmethod
    def get_name_from_cls(cls) -> str:
        name = cls.__name__
        if name.endswith("Tool"):
            name = name[:-4]
        # convert to snake_case
        name = "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")
        return name
```

Registration keys the registry on exactly this string — `src/serena/tools/tools_base.py:594-606`:

```python
        for cls in iter_subclasses(Tool, inclusion_predicate=inclusion_predicate):
            if not any(cls.__module__.startswith(pkg) for pkg in tool_packages):
                continue
            is_optional = issubclass(cls, ToolMarkerOptional)
            is_beta = issubclass(cls, ToolMarkerBeta)
            name = cls.get_name_from_cls()
            if name in self._tool_dict:
                raise ValueError(f"Duplicate tool name found: {name}. Tool classes must have unique names.")
```

and the MCP layer registers under that name with **no prefix added by Serena** — `src/serena/mcp.py:52-60, 114-118`:

```python
class SerenaFastMCPTool(FastMCPTool):
    def __init__(self, tool: Tool, openai_tool_compatible: bool, structured_output: bool | None):
        func_name = tool.get_name()
        ...
        super().__init__(
            fn=execute_fn,
            name=func_name,
```

Two classes do not end in `Tool` (`SafeDeleteSymbol`, `JetBrainsInlineSymbol`); the suffix strip is a no-op for them and the snake_case result is still correct.

### 2.3 Serena EDIT tool names — the hook matcher list

Serena's edit capability is declared by marker mixins — `src/serena/tools/tools_base.py:88-113`:

```python
class ToolMarkerCanEdit(ToolMarker):
    """
    Marker class for all tools that can perform editing operations on files.
    """
...
class ToolMarkerSymbolicEdit(ToolMarkerCanEdit):
    """
    Marker class for tools that perform symbolic edit operations.
    """
```

`EditingToolWithDiagnostics(Tool, ToolMarkerCanEdit)` is at `tools_base.py:466`.

Below, every name is the exact registered MCP tool-name string, with the exact `apply()` parameter names — the hook reads these keys out of the PreToolUse `tool_input` JSON, so the param names are load-bearing.

#### Group A — symbolic edits, carry `(name_path, relative_path)`. **Primary matcher targets.**

| tool name | params | class / provenance |
|---|---|---|
| `replace_symbol_body` | `name_path`, `relative_path`, `body` | `ReplaceSymbolBodyTool(EditingToolWithDiagnostics)` — `symbol_tools.py:585-593` |
| `insert_after_symbol` | `name_path`, `relative_path`, `body` | `InsertAfterSymbolTool(EditingToolWithDiagnostics)` — `symbol_tools.py:618-626` |
| `insert_before_symbol` | `name_path`, `relative_path`, `body` | `InsertBeforeSymbolTool(EditingToolWithDiagnostics)` — `symbol_tools.py:644-652` |
| `rename_symbol` | `name_path`, `relative_path`, `new_name` | `RenameSymbolTool(Tool, ToolMarkerSymbolicEdit)` — `symbol_tools.py:670-678` |
| `safe_delete_symbol` | **`name_path_pattern`**, `relative_path` | `SafeDeleteSymbol(Tool, ToolMarkerSymbolicEdit)` — `symbol_tools.py:698-703` |

**Trap:** `safe_delete_symbol` uses `name_path_pattern`, *not* `name_path`. A locator that reads only `tool_input["name_path"]` silently sees `None` on every delete and fails open. Read `tool_input.get("name_path") or tool_input.get("name_path_pattern")`.

`rename_symbol` is also repo-wide in effect (`symbol_tools.py:678-681`: "Renames the symbol ... throughout the entire codebase") even though it names one `relative_path` — treat it as a write against the target node **and** all inbound-`CALLS` neighbours.

#### Group B — file edits, carry `relative_path` only. **Map to file-level or enclosing-symbol ID.**

| tool name | params | provenance | default-enabled? |
|---|---|---|---|
| `create_text_file` | `relative_path`, `content` | `file_tools.py:58-63` | yes |
| `replace_content` | `relative_path`, `needle`, `repl`, `mode`, `allow_multiple_occurrences` | `file_tools.py:173-183` | yes |
| `delete_lines` | `relative_path`, `start_line`, `end_line` | `file_tools.py:451-461` | **no** (`ToolMarkerOptional`) |
| `replace_lines` | `relative_path`, `start_line`, `end_line`, `content` | `file_tools.py:477-488` | **no** (`ToolMarkerOptional`) |
| `insert_at_line` | `relative_path`, `line`, `content` | `file_tools.py:511-520` | **no** (`ToolMarkerOptional`) |

`delete_lines` / `replace_lines` / `insert_at_line` give **0-based line indices** (docstrings, `file_tools.py:468-469`, `495-497`, `518`). Claude Code's own `Edit` tool gives no line numbers at all. Our `locator.py` must accept both shapes.

#### Group C — multi-file edit, no single target. **Needs special handling.**

`replace_in_files` — `ReplaceInFilesTool(EditingToolWithDiagnostics)`, `file_tools.py:218-235`:

```python
    def apply(
        self,
        needle: str,
        repl: str,
        mode: Literal["literal", "regex"],
        relative_path: str = "",
        paths_include_glob: str = "",
        paths_exclude_glob: str = "",
        dry_run: bool = False,
        occurrence_ids: list[str] | None = None,
        expected_count: int = -1,
        max_answer_chars: int = -1,
    ) -> str:
```

`relative_path` **defaults to `""`** (whole repo) and may name a directory. The locator cannot resolve this to one symbol. Handling: if `dry_run` is true → allow (read-only preview); else if `relative_path` names a single file → file-level check; else → deny with `replace_in_files across an unscoped path set cannot be claim-checked; scope it to one file or use symbolic edits`.

#### Group D — JetBrains variants (`ToolMarkerSymbolicEdit` + `ToolMarkerOptional` + `ToolMarkerBeta`)

`jet_brains_move`, `jet_brains_safe_delete`, `jet_brains_inline_symbol`, `jet_brains_rename` — `jetbrains_tools.py:145, 195, 235, 559`. Note the snake_case transform splits the capital B: it is `jet_brains_*`, **not** `jetbrains_*`. Off by default; see §4.

#### Full registry name list (for negative matching — everything not in A–D is not an edit)

`activate_project, create_text_file, delete_lines, delete_memory, edit_memory, execute_shell_command, find_declaration, find_file, find_implementations, find_referencing_symbols, find_symbol, get_current_config, get_diagnostics_for_file, get_diagnostics_for_symbol, get_symbols_overview, initial_instructions, insert_after_symbol, insert_at_line, insert_before_symbol, list_dir, list_memories, list_queryable_projects, onboarding, open_dashboard, query_project, read_file, read_memory, remove_project, rename_memory, rename_symbol, replace_content, replace_in_files, replace_lines, replace_symbol_body, restart_language_server, safe_delete_symbol, search_for_pattern, serena_info, write_memory`
(plus the `jet_brains_*` set: `jet_brains_debug, jet_brains_find_declaration, jet_brains_find_implementations, jet_brains_find_referencing_symbols, jet_brains_find_symbol, jet_brains_get_symbols_overview, jet_brains_inline_symbol, jet_brains_list_inspections, jet_brains_move, jet_brains_rename, jet_brains_run_inspections, jet_brains_safe_delete, jet_brains_type_hierarchy`.)

**Dead names — never match these.** `tools_base.py:583-591`:

```python
    _deleted_tools: list[str] = [
        "think_about_collected_information",
        "prepare_for_new_conversation",
        "summarize_changes",
        "think_about_whether_you_are_done",
        "switch_modes",
        "check_onboarding_performed",
    ]
```

### 2.4 The MCP tool-name prefix and matcher wiring

Serena adds no prefix (`mcp.py:116` above); the `mcp__<serverKey>__<tool_name>` prefix is minted by Claude Code from the **user's** server key. Serena's docs assume the key `serena` — `docs/02-usage/030_clients.md:133`:

```shell
claude mcp add --scope user serena -- serena start-mcp-server --context claude-code --project-from-cwd
```

and their own settings block uses a **glob matcher on that prefix** — `docs/02-usage/030_clients.md:166-188`:

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "",
                "hooks": [
                    { "type": "command", "command": "serena-hooks remind --client=claude-code" }
                ]
            },
            {
                "matcher": "mcp__serena__*",
                "hooks": [
                    { "type": "command", "command": "serena-hooks auto-approve --client=claude-code" }
                ]
            }
        ]
    }
}
```

Two adoptable facts: the matcher accepts a pattern (`""` = all tools; `mcp__serena__*` = prefix glob), and hook commands are registered as a console-script entry point (`pyproject.toml:66`: `serena-hooks = "serena.hooks:hook_commands"`) so the settings.json command is a bare binary name, not a path to a `.py` file. `loom init` should do the same: install a `loom-hook` console script and write `"command": "loom-hook gate"`.

### 2.5 Serena ships its own PreToolUse hook — copy its I/O contract (`src/serena/hooks.py`, 634 lines)

This is a direct precedent for `hook/gate.py`'s input parsing and message discipline. (Its JSON deny
transport was demoted by GATE-1 fix 3 — see ADAPT 6 and C2; loom denies via exit 2 + stderr.)

**Robust input parsing across clients** — `hooks.py:30-44, 68-83`:

```python
        raw = sys.stdin.read()
        input_data = json.loads(raw, strict=False)
        ...
        session_id = input_data.get("session_id") or input_data.get("sessionId")
```

```python
        _tool_name = self._input_data.get("tool_name") or self._input_data.get("toolName", "") or ""
        _tool_name = str(_tool_name).lower().strip()
        if not _tool_name:
            raise ValueError("Tool name is required in the hook input data")
        self._tool_name = _tool_name
        raw_tool_input = self._input_data.get("tool_input") or self._input_data.get("toolInput")
        # TODO: some agents, like copilot CLI, can send a string as value for raw_tool_input
        self._tool_input: dict | None = raw_tool_input if isinstance(raw_tool_input, dict) else None

        raw_permission_mode = self._input_data.get("permission_mode") or self._input_data.get("permissionMode") or ""
        self._permission_mode = str(raw_permission_mode).strip()
```

Adopt: snake_case-or-camelCase fallback on every field, `json.loads(..., strict=False)`, and the `isinstance(raw_tool_input, dict)` guard — a non-dict `tool_input` (Copilot CLI sends a patch string) must not crash the gate.

**The structured deny — richer than exit 2** — `hooks.py:85-107`:

```python
    @dataclass
    class OutputData:
        permission_decision: Literal["deny", "allow"]
        permission_decision_reason: str
        additional_context: str = ""

        def to_json_string(self, client: HookClient) -> str:
            if client == HookClient.GROK:
                grok_output: dict[str, str] = {"decision": self.permission_decision}
                if self.permission_decision == "deny":
                    grok_output["reason"] = self.permission_decision_reason
                return json.dumps(grok_output)

            hook_output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": self.permission_decision,
                    "permissionDecisionReason": self.permission_decision_reason,
                }
            }
            if client != HookClient.CODEX:
                hook_output["hookSpecificOutput"]["additionalContext"] = self.additional_context
            return json.dumps(hook_output)
```

The two-field split maps exactly onto loom's deny: `permissionDecisionReason` = the one-line `claimed by <agent> under plan <id>: <title>`; `additionalContext` = the owner's full `spec_md`. That is strictly better than PLAN-v1's "exit 2 with the spec appended to stderr", because `additionalContext` is fed to the model as context rather than as an error string.

**Deny message shape — always end with the recovery move.** `hooks.py:488-497`:

```python
    def _build_grep_deny(self) -> "PreToolUseHook.OutputData":
        return self.OutputData(
            permission_decision="deny",
            permission_decision_reason="Too many consecutive grep calls without using symbolic tools. "
            "You can continue using grep now if needed, the counter was reset.",
            additional_context=(
                "You were using many grep calls recently. Consider using Serena's symbolic "
                "mcp tools instead for more code-centric search. You can continue using grep now if needed, the counter was reset."
            ),
        )
```

Every deny states the rule *and* the next action, in both fields. Adopt that discipline verbatim for `hook/gate.py`'s four cases.

**Silence is the neutral outcome.** `hooks.py:573-582`:

```python
    def execute(self) -> None:
        # only emit a decision when both the tool and the mode match; stay silent otherwise
        if not self.is_serena_symbolic_tool() or not self.is_auto_approve_mode():
            return
```

Emitting nothing (exit 0, empty stdout) leaves the client's default flow untouched. This is exactly loom's fail-open path: on server timeout, print the warning to stderr and emit no decision.

**Permission-mode awareness** — `hooks.py:566, 569-570`:

```python
    _AUTO_APPROVE_MODES: frozenset[str] = frozenset({"acceptEdits", "auto"})

    def is_auto_approve_mode(self) -> bool:
        return self._permission_mode in self._AUTO_APPROVE_MODES
```

Useful fact for us: Claude Code's `acceptEdits` / `auto` blanket approvals do **not** cover MCP tool calls, and `bypassPermissions` short-circuits before the hook. Loom's gate must therefore not assume it sees every edit under `bypassPermissions`.

**Session-scoped state directory** — `hooks.py:41-43`:

```python
        self.session_persistence_dir = os.path.join(serena_home_dir, "hook_data", self._session_id)
```

with a `SessionEnd` cleanup command (`hooks.py:540-543`). Adopt for caching the agent's active `plan_id` between gate invocations so the fast path avoids a round trip.

---

## 3. ADAPT

1. **Wire format for a loom node id.** Serena keeps `(relative_path, name_path)` as two fields. Loom's schema (PLAN-v1 §4.1) already has `nodes(id, repo, path, qualname, ...)` — two columns — so keep them split in storage. For the single-string form agents type into `resolve_nodes` and that appears in deny messages, adopt Serena's separator inside the symbol part and pick **one** joiner between path and symbol. Recommendation: `path/to/file.py::Class/method`. `::` cannot appear in a POSIX path or a Python/TS identifier, and preserving `/` inside the symbol part keeps a straight `str.split("::")[1]` handoff to and from Serena's `name_path` parameter with zero translation. Document this as loom's convention, not Serena's.
   - `indexer/naming.py` exposes `qualname(components) -> str` (join with `/`, `[i]` suffix on overloads) and `node_ref(path, qualname) -> str` (join with `::`), plus the inverse `split_ref`.
2. **Ancestry stop condition.** Serena stops at `SymbolKind.File`. Our tree-sitter walk has no file node; stop at the tree root and never prepend the module name — otherwise the qualname stops matching what `find_symbol` would accept.
3. **Overload indices.** Serena needs `[i]` because Java/C++ overload. Python and TS (the MVP languages) do not, except for `@overload` stubs and same-name defs in different branches. Implement `[i]` in `naming.py` (it costs ~4 lines and makes the ID collision-free by construction), but expect it to be absent from every ID in the MVP demo.
4. **Matching direction.** Serena matches suffix-first (right-to-left) because agents type short names. `resolve_nodes` should do the same over the `qualname` column: try exact, then suffix on `/` boundaries, then substring on the last component only — mirroring `use_substring_matching = self._substring_matching and (i == 0)` (`symbol.py:205`). Ambiguity must return all candidates, never guess.
5. **Matcher regex, not a fixed prefix.** The `mcp__serena__` prefix in Serena's docs is a *convention* — it comes from whatever key the user passed to `claude mcp add`. `loom init` must not hardcode it. Write two `PreToolUse` blocks into `.claude/settings.json`:
   - `"matcher": "Edit|Write|MultiEdit|NotebookEdit"`
   - `"matcher": "mcp__.*__(replace_symbol_body|insert_after_symbol|insert_before_symbol|rename_symbol|safe_delete_symbol|create_text_file|replace_content|replace_in_files|delete_lines|replace_lines|insert_at_line)"`
   Then, defensively, `gate.py` re-derives the classification from the tool-name **suffix after the last `__`**, so a user who registered the server as `serenamcp` or `code` is still gated.
6. **Deny transport — OVERRULED by GATE-1 fix 3; exit-2 + stderr is primary.** This item originally recommended JSON-first (`hookSpecificOutput` deny on stdout, Serena's contract). The gate ruled for hooks-contract §2.5: PLAN §1 commits to exit-2; exit 2 cannot be overridden by a competing hook's `allow` (hooks-ref `:775`); and `:1719` shows stderr reaches the model identically to a deny reason. loom's gate denies with **exit 2 + stderr only**. The JSON deny stays a noted alternative; the `additionalContext` idea survives only as optional enrichment, never the primary channel. M3's acceptance test asserts exit codes + stderr per hooks-contract. Still valid from this item: do not lowercase our own tool name before regex-matching (Serena lowercases at `hooks.py:71`; Claude Code's built-ins are `Edit`/`Write`, so lowercasing would break a naive `Edit` comparison — either lowercase both sides or neither).
7. **Param extraction table for `locator.py`** — drive it from a dict, not an if-chain:
   ```
   symbol edits: (name_path | name_path_pattern, relative_path)  -> node ref directly, no tree-sitter needed
   file edits:   (relative_path, [start_line|line])              -> tree-sitter enclosing-symbol lookup
   CC builtins:  (file_path, old_string)                          -> locate old_string offset, then enclosing symbol
   replace_in_files                                               -> see Group C rule in §2.3
   ```
   The symbol-edit branch is the cheap one: when Serena is present, loom gets a canonical symbol ID for free and never parses the file.
8. **Fail-open budget.** Serena's hook does blocking disk I/O (pickle load/save) with no timeout. Loom's gate hits the network, so the ~2s timeout and fail-open from the PLAN's MVP addendum stays mandatory; adopt Serena's "emit nothing" as the fail-open representation.

---

## 4. REJECT

1. **`relative/path.py::Class.method` as "Serena's convention"** (PLAN-v1 §2, Serena bullet). It is not Serena's convention and does not exist anywhere in the source. Adopting it verbatim would defeat the stated rationale ("so our node IDs match what LSP tooling produces later") — see §5.
2. **Any dependency on `solidlsp` / a real language server.** Serena's symbol tree comes from LSP `documentSymbol` responses (`symbol.py:12-20`, `UnifiedSymbolInformation`). PLAN-v1 §1 fixes tree-sitter as loom's indexer. Take the *naming string format* only; do not take `LanguageServerSymbol`, `LanguageServerManager`, `ls_manager.py`, or `code_editor.py`. LSP-per-repo startup latency alone would blow the sub-10ms `check` budget.
3. **`execute_shell_command` in the matcher.** It is `ToolMarkerCanEdit` (`cmd_tools.py:11`) but carries only `cmd`/`command` — no path, no symbol. There is nothing to check a claim against. Gating it would mean denying every test run. Leave it ungated in v1 and note the hole in the README (an agent can bypass loom via `sed -i`; so can it via `git checkout`, and the answer to both is the pre-commit `guard.py`, not the PreToolUse gate).
4. **The four memory tools** — `write_memory`, `delete_memory`, `rename_memory`, `edit_memory` (`memory_tools.py:9, 62, 74, 94`). All are `ToolMarkerCanEdit`, so a naive "match everything that can edit" rule catches them, but they write `.serena/memories/`, not repo source. Matching them would deny an agent's own scratch notes for no benefit.
5. **The `jet_brains_*` edit tools** in the default matcher. They are `ToolMarkerOptional` *and* `ToolMarkerBeta` (`jetbrains_tools.py:145` etc.), so they are off unless a user explicitly enables them, and they require a running JetBrains IDE plugin. Ship the names in a config list (`hook.extra_edit_tools`) rather than the default regex.
6. **`is_serena_symbolic_tool()`'s substring heuristic** — `hooks.py:109-112`:
   ```python
       return "serena" in self._tool_name and not any(
           substring in self._tool_name for substring in self._NON_SYMBOLIC_SERENA_TOOL_NAME_SUBSTRINGS
       )
   ```
   with `_NON_SYMBOLIC_SERENA_TOOL_NAME_SUBSTRINGS = {"pattern", "read", "diagnostics", "memory", "onboarding", "config", "list_file", "find_file", "shell", "dashboard", "restart_language_server"}` (`hooks.py:52-66`). It is a deny-by-exclusion list that requires the literal string `serena` in the tool name and silently misclassifies anything new. For a *reminder* hook a false positive costs a nudge; for loom's *gate* it costs a wrongly-allowed edit. Use the explicit allowlist in §2.3 instead.
7. **`PreToolUseRemindAboutSymbolicToolsHook`'s whole apparatus** — the pickled `ToolUseCounter`, the burst thresholds, `_GREP_SHELL_COMMANDS`, `_READ_SHELL_COMMANDS`, `_CODE_FILE_EXTENSIONS`, the read/grep classification cascade (`hooks.py:115-538`, ~420 of the file's 634 lines). It solves agent-drift-toward-grep, an unrelated problem. Loom's gate is stateless per call except for the cached `plan_id`.
8. **The `ToolRegistry` / `ToolMarker` / `iter_subclasses` machinery** (`tools_base.py:142-560, 582-690`). Loom exposes ~9 MCP tools from one module; a subclass-scanning registry with duplicate-name detection and optional/beta tiers is overhead against the 500–700 line server budget.
9. **Serena as a runtime dependency.** Confirmed consistent with PLAN-v1 §1 ("Serena is optional and never load-bearing") — nothing found here changes that. It is purely a tool-name compatibility surface.

---

## 5. CORRECTIONS to PLAN-v1.md

**C1 — PLAN-v1 line 56-58 is wrong on both halves. This is the headline correction.**

> - Serena (github.com/oraios/serena)
>   - Take: the canonical symbol ID convention, `relative/path.py::Class.method`, so our node IDs match what LSP tooling produces later.

Two independent errors:

- **(a) Serena never produces a joined single-string identifier.** Symbol identity is the *pair* `(relative_path, name_path)`, passed as two separate parameters by every symbolic edit tool (`symbol_tools.py:590-593, 623-626, 649-652, 675-678, 699-702`) and emitted as two separate keys by `to_dict` (`symbol.py:450-465`). The docstring that calls a location "uniquely identifies the symbol" is on `LanguageServerSymbolLocation`, whose fields are `relative_path`, `line`, `column` (`symbol.py:29-47`) — a path plus a position, not a path-plus-qualname string. There is no `::` anywhere in Serena's identifier scheme.
- **(b) The within-file separator is `/`, not `.`.** `NAME_PATH_SEP = "/"` (`symbol.py:26`). The method `my_method` in class `MyClass` is `MyClass/my_method`, not `MyClass.my_method` (`symbol.py:147-148`). Overloads append `[i]`; a leading `/` means "absolute within the file".

Because both halves are wrong, an `indexer/naming.py` built literally to the plan's spec would produce IDs that **cannot** be handed to `find_symbol` / `replace_symbol_body`, which is the plan's own stated reason for the borrow. Replace the plan text with:

> - Serena (github.com/oraios/serena, MIT)
>   - Take: the within-file name-path convention — `/` separator (`NAME_PATH_SEP`), `Class/method/inner`, `[i]` overload suffix, leading `/` for absolute — and the split `(relative_path, name_path)` identity. Loom joins the two halves as `path/to/file.py::Class/method` for display and agent input; storage keeps them in the `path` and `qualname` columns. Also take the PreToolUse hook I/O contract from `serena/hooks.py`.
>   - Lands in: `indexer/naming.py`, `hook/gate.py`.

**C2 — DEMOTED to a noted alternative (GATE-1 fix 3: hooks-contract wins; exit-2 + stderr stays primary).** Original claim: PLAN §4.3's exit-2 contract is stale as the *primary* mechanism, because Serena's shipping hook uses the JSON contract instead — `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"|"allow", "permissionDecisionReason": str, "additionalContext": str}}` on stdout with exit 0 (`hooks.py:85-107`). The gate overruled it: exit 2 cannot be overridden by a competing hook's `allow` (hooks-ref `:775`) — the stronger enforcement posture and the whole point of the gate — and `:1719` shows stderr reaches the model identically to a deny reason, so the JSON route buys no model-visible benefit for a deny. PLAN §4.3 and M3's acceptance ("assert exit codes plus stderr for all four gate cases") stand as written. The `additionalContext` idea remains available as optional enrichment on non-deny outputs only; the JSON deny is recorded here as the road-not-taken, with `hooks.py:85-107` as its production precedent if a future client ignores exit codes.

**C3 — PLAN-v1 §4.3's "the Serena edit tools if present" is unspecified; §2's implied list is incomplete.** The plan never enumerates them. The authoritative list is §2.3 above: five symbolic (`replace_symbol_body`, `insert_after_symbol`, `insert_before_symbol`, `rename_symbol`, `safe_delete_symbol`) + five file-level (`create_text_file`, `replace_content`, `delete_lines`, `replace_lines`, `insert_at_line`) + `replace_in_files` (unscopable) + four `jet_brains_*` (optional/beta). The plan's shorthand "replace_symbol_body, insert_after_symbol, etc." undercounts by 10 tools and misses that `safe_delete_symbol` uses a *different parameter name* (`name_path_pattern`).

**C4 — The `mcp__serena__` prefix must not be hardcoded, contra the natural reading of §4.3/§4.5.** Serena registers tools with bare names (`mcp.py:116`); the prefix is minted from the user's `claude mcp add <key>` (`docs/02-usage/030_clients.md:133`). `loom init` must write a suffix-matching regex (§3.5). Serena's own settings block uses a glob (`"matcher": "mcp__serena__*"`, `030_clients.md:180`) and only works because their docs dictate the key.

**C5 — PLAN-v1 §2 header note about licensing does not apply here.** The header's "patterns only, zero verbatim code" restriction is scoped to `mcp_agent_mail`. Serena is plain MIT with an attribution-only condition (`LICENSE:11-12`, `pyproject.toml:68-69`), and `src/solidlsp` / `src/interprompt` carry no additional license. Verbatim excerpts from Serena are legally fine with an attribution line; the reasons to rewrite (§4) are technical, not legal.

**C6 — §4.5 hook installation shape.** The plan says `loom init` "registers the hook in `.claude/settings.json`". Serena's precedent (`pyproject.toml:66`, `030_clients.md:166-199`) is to install a console-script entry point and register the bare command name, plus a `SessionStart` hook for context injection and a `SessionEnd` hook for state cleanup. Loom should mirror this: `loom-hook` entry point, `"command": "loom-hook gate"`, and consider a `SessionStart` hook that injects the CLAUDE.snippet protocol as `additionalContext` (`hooks.py:604-617`) rather than relying only on appending to CLAUDE.md — which is a cheap fix for the same agent-drift problem Serena documents at `030_clients.md:150-153`.

**C7 — new, unplanned finding worth a line in §2: Serena ships a full PreToolUse hook (`src/serena/hooks.py`, 634 lines).** The plan's hook bullet cites only "Claude Code hooks docs plus kornysietsma/claude-code-permissions-hook". `serena/hooks.py` is a second, MIT-licensed, production reference for the same file we are writing, and it contributes the client-agnostic input parsing (`tool_name`/`toolName`, `tool_input`/`toolInput`, non-dict `tool_input` guard — `hooks.py:68-83`), the JSON deny contract, the emit-nothing neutral outcome, and the permission-mode facts in §2.5. Add it to the cherry-pick manifest.
