"""M3 — BUILD-SPEC §7.2/§7.4: the PreToolUse edit locator. stdlib `ast` ONLY.

Hook input-parsing patterns derived from Serena (https://github.com/oraios/serena), MIT
License, Copyright (c) 2025 Oraios AI. Full license:
third_party/LICENSES/serena-MIT.txt. See THIRD_PARTY_NOTICES.md. Imports are stdlib +
`loom.indexer.naming` (§9.2) — never `loom.server.*`, never `mcp`: tree-sitter and the MCP
SDK live on the server side, so a PreToolUse process pays only the interpreter start.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass

from loom.indexer.naming import norm_path
from loom.indexer.naming import qualname as join_qualname

# The ONE hook-local deny template (§7.4, GATE-2 edit 3) — the server never emits it.
UNSCOPED_TMPL = (
    "loom: replace_in_files across an unscoped path set cannot be claim-checked; "
    "scope it to one file or use symbolic edits."
)
_SYMBOL = frozenset({"replace_symbol_body", "insert_after_symbol", "insert_before_symbol",
                     "rename_symbol", "safe_delete_symbol"})
_SERENA_FILE = frozenset({"create_text_file", "replace_content", "delete_lines",
                          "replace_lines", "insert_at_line"})
_GATEABLE = _SYMBOL | _SERENA_FILE | {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class Located:
    """`action` is one of "pass" | "gate" | "deny_local" (§9.1)."""

    action: str
    path: str = ""
    qualname: str | None = None
    message: str = ""


_PASS = Located("pass")


def _field(tool_input: dict, snake: str):
    """snake_case with a camelCase fallback, per field (§7.1)."""
    head, *rest = snake.split("_")
    return tool_input.get(snake, tool_input.get(head + "".join(w.title() for w in rest)))


def _rel(raw, repo_root: str) -> str | None:
    """Repo-root-relative POSIX path; None when unusable or outside the repo (§7.1)."""
    if not isinstance(raw, str) or not raw:
        return None
    path, root = norm_path(raw), norm_path(repo_root).rstrip("/")
    if not path.startswith("/"):
        return path  # §7.1: a relative tool path is passed through, NEVER cwd-joined
    # Canonicalize both sides so one file has one identity: `realpath` resolves symlinked
    # repo roots (a symlink-spelled repo must gate identically) and collapses `..`, so the
    # wire path is always the indexed spelling and a file truly outside the repo PASSes.
    # `realpath` returns the caller's OWN unicode spelling of each component, so re-run
    # `norm_path` over its output: that is where NFC is applied (see `naming.norm_path`),
    # and it must cover the root too, or an NFD-spelled repo_root and an NFC-spelled payload
    # fail the prefix test and the edit PASSes unchecked (break3 journey-J1).
    path, root = norm_path(os.path.realpath(path)), norm_path(os.path.realpath(root)).rstrip("/")
    return path[len(root) + 1 :] if path.startswith(root + "/") else None


def _abs(rel: str, repo_root: str) -> str:
    return norm_path(repo_root).rstrip("/") + "/" + rel


def collect_symbol_spans(source: str) -> list[tuple[str, int, int]]:
    """`(qualname, start_line, end_line)` per CLAIMABLE definition, in source order (§4).

    Descends `Module` -> `ClassDef` containers only, so a def nested in a function or in a
    block statement is never emitted; a repeated qualname takes Serena's `[i]` suffix.
    """
    if source[:1] == "﻿":
        source = source[1:]        # a caller that read plain utf-8 keeps symbol-level spans
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    spans: list[tuple[str, int, int]] = []
    seen: dict[str, int] = {}

    def walk(body: list, prefix: list[str]) -> None:
        for node in body:
            if not isinstance(node, _DEFS):
                continue
            name = join_qualname(prefix + [node.name])
            nth = seen.get(name, 0)
            seen[name] = nth + 1
            spans.append((f"{name}[{nth}]" if nth else name, node.lineno, node.end_lineno))
            if isinstance(node, ast.ClassDef):
                walk(node.body, prefix + [node.name])

    walk(tree.body, [])
    return spans


def enclosing_qualname(source: str, start_line: int, end_line: int) -> str | None:
    """The narrowest claimable span covering the edited lines; None means file-level."""
    inside = [s for s in collect_symbol_spans(source) if s[1] <= start_line <= end_line <= s[2]]
    return min(inside, key=lambda s: s[2] - s[1])[0] if inside else None


# Files above this size are gated at FILE level without parsing: an AST parse of a 10 MB
# file costs multiple seconds inside a ~2 s hook budget (red-team gate-F5). Coarser claim,
# same protection, bounded latency.
_PARSE_CAP_BYTES = 1_000_000


def _edit(rel: str, repo_root: str, tool_input: dict) -> Located:
    """Edit: find `old_string` on disk, then fall to the narrowest enclosing claimable span."""
    old = _field(tool_input, "old_string")
    try:
        path = _abs(rel, repo_root)
        if os.stat(path).st_size > _PARSE_CAP_BYTES:
            return Located("gate", rel, None)
        # utf-8-sig: a Windows-style BOM decoded as U+FEFF makes ast.parse raise, which
        # silently degraded every edit in the file to FILE-level and denied the symbol's
        # own claim holder (break4). Strips a leading BOM, no-op otherwise.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            source = fh.read()
    except OSError:
        return Located("gate", rel, None)
    idx = source.find(old) if isinstance(old, str) and old else -1
    if idx < 0 or (_field(tool_input, "replace_all") and source.count(old) > 1):
        return Located("gate", rel, None)
    start = source.count("\n", 0, idx) + 1
    return Located("gate", rel, enclosing_qualname(source, start, start + old.count("\n")))


def locate(tool_name: str, tool_input: dict, repo_root: str) -> Located:
    """Classify a payload. Unknown tool / unrecognized keys / path outside the repo -> PASS."""
    name = tool_name.rsplit("__", 1)[-1]  # suffix re-derivation, never `mcp__serena__` (§7.5)
    if name == "replace_in_files":
        if _field(tool_input, "dry_run"):
            return _PASS
        raw = _field(tool_input, "relative_path")
        rel = _rel(raw, repo_root)
        if rel is not None:
            # In-repo single file gates; an in-repo directory/glob stays the unscoped deny.
            return (Located("gate", rel, None) if os.path.isfile(_abs(rel, repo_root))
                    else Located("deny_local", message=UNSCOPED_TMPL))
        # An absolute path _rel rejected is genuinely OUTSIDE the repo -> PASS, identical
        # to the Edit/Write branch (§7.2 — break4). Empty/missing stays the unscoped deny.
        if isinstance(raw, str) and raw and norm_path(raw).startswith("/"):
            return _PASS
        return Located("deny_local", message=UNSCOPED_TMPL)
    if name not in _GATEABLE:
        return _PASS
    key = ("notebook_path" if name == "NotebookEdit"
           else "relative_path" if name in _SYMBOL | _SERENA_FILE else "file_path")
    rel = _rel(_field(tool_input, key), repo_root)
    if rel is None:
        return _PASS
    if name in _SYMBOL:
        raw = _field(tool_input, "name_path") or _field(tool_input, "name_path_pattern")
        qual = raw.lstrip("/") if isinstance(raw, str) else ""
        return Located("gate", rel, qual) if qual else _PASS
    return _edit(rel, repo_root, tool_input) if name == "Edit" else Located("gate", rel, None)
