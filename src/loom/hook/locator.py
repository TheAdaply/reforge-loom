"""M3 — BUILD-SPEC §7.2/§7.4: the PreToolUse edit locator. stdlib `ast` ONLY.

Hook input-parsing patterns derived from Serena (https://github.com/oraios/serena), MIT
License, Copyright (c) 2025 Oraios AI. See THIRD_PARTY_NOTICES.md. Imports are stdlib +
`loom.indexer.naming` (§9.2) — never `loom.server.*`, never `mcp`: tree-sitter and the MCP
SDK live on the server side, so a PreToolUse process pays only the interpreter start.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass

from loom.indexer.naming import norm_path, qualname as join_qualname

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
    path, root = os.path.realpath(path), os.path.realpath(root).rstrip("/")
    return path[len(root) + 1 :] if path.startswith(root + "/") else None


def _abs(rel: str, repo_root: str) -> str:
    return norm_path(repo_root).rstrip("/") + "/" + rel


def collect_symbol_spans(source: str) -> list[tuple[str, int, int]]:
    """`(qualname, start_line, end_line)` per CLAIMABLE definition, in source order (§4).

    Descends `Module` -> `ClassDef` containers only, so a def nested in a function or in a
    block statement is never emitted; a repeated qualname takes Serena's `[i]` suffix.
    """
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


def _edit(rel: str, repo_root: str, tool_input: dict) -> Located:
    """Edit: find `old_string` on disk, then fall to the narrowest enclosing claimable span."""
    old = _field(tool_input, "old_string")
    try:
        with open(_abs(rel, repo_root), encoding="utf-8", errors="replace") as fh:
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
        rel = _rel(_field(tool_input, "relative_path"), repo_root)
        if rel and os.path.isfile(_abs(rel, repo_root)):
            return Located("gate", rel, None)
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
