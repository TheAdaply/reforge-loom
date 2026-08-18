"""BUILD-SPEC §9.1 — frozen tree-sitter capture queries + the static call/import resolver."""
# Portions derived from FalkorDB/code-graph (api/analyzers/python/).
# Copyright (c) 2024 FalkorDB. MIT License. See third_party/LICENSES/falkordb-code-graph.txt

from __future__ import annotations

from typing import Any

import tree_sitter_python
from tree_sitter import Language, Query, QueryCursor

from loom.indexer.naming import NAME_SEP

# EVERY def/class in the file, unanchored and undecorated-agnostic. The ancestor chain is
# NOT expressed here: `walk._claimable` is §4's single authority on what may be claimed, and
# it already walks parents to the module root. Three module-anchored queries used to restate
# that rule in tree-sitter syntax, and the two statements of it disagreed — an intermediate
# class (`B` in `A->B->C`) whose body holds only another class matched none of them, so it
# was skipped and `C` was minted as `A/C` while the hook's `ast` locator said `A/B/C`
# (FINDINGS P1-5, and its method-less single-level form P2-6). One capture, one rule.
#
# `@name`.parent is the def/class node in both the plain and the decorated case, because the
# capture is on the `name:` FIELD — a `decorated_definition` wrapper is never the parent of
# the name identifier, so the span stays the definition's own (falkordb §2.8).
_QUERY_DEFS = """
(function_definition name: (identifier) @name)
(class_definition name: (identifier) @name)
"""

# Plain ``import x`` / ``import x.y`` / ``import x as y`` / ``import x.y as z``.
_QUERY_IMPORT = """
(import_statement) @stmt
"""

# ``from x import y`` / ``from x import y as z`` / ``from . import y`` / ``from .x import y``.
_QUERY_IMPORT_FROM = """
(import_from_statement) @stmt
"""

# falkordb §2.4: a call is ATTRIBUTED to its enclosing entity, never to the file root. The
# cursor still runs once at the root and each site is bucketed by the innermost claimable
# span containing it — same attribution, and no site can be counted twice.
_QUERY_CALL = "(call) @reference.call"

LANGUAGE = Language(tree_sitter_python.language())
Q_DEFS, Q_IMPORT, Q_IMPORT_FROM, Q_CALL = (
    Query(LANGUAGE, s)
    for s in (_QUERY_DEFS, _QUERY_IMPORT, _QUERY_IMPORT_FROM, _QUERY_CALL)
)


def captures(q: Query, node: Any) -> dict[str, list]:
    """Run a SINGLE-capture query (imports, calls). Multi-capture MUST use `matches`."""
    return QueryCursor(q).captures(node)


def matches(q: Query, node: Any) -> list[tuple[int, dict]]:
    """Run a query match-wise — mandatory for multi-capture queries (falkordb §2.9)."""
    return QueryCursor(q).matches(node)


def _txt(node: Any) -> str:
    return node.text.decode("utf-8", "replace")


def dotted_parts(node: Any) -> list[str] | None:
    """`f` -> ['f'], `a.b.f` -> ['a','b','f']; None for anything else (never guessed)."""
    if node is None or node.type not in ("identifier", "attribute"):
        return None
    if node.type == "identifier":
        return [_txt(node)]
    base = dotted_parts(node.child_by_field_name("object"))
    attr = node.child_by_field_name("attribute")
    return None if base is None or attr is None else base + [_txt(attr)]


class Resolver:
    """Static call/import resolver (falkordb §2.7/2.8 scope).

    Frozen precision rules: methods are excluded from the bare-name fallback (only
    module-level qualnames are candidates); ambiguity is DROPPED; wildcard imports are
    ignored; relative imports climb from the file's own directory, which makes the
    ``__init__.py`` off-by-one a no-op; ``Foo()`` resolves to the class node. CUT to fit
    the size budget: no assignment-type inference, so ``x = Foo(); x.m()`` is unresolved.
    """

    def __init__(self) -> None:
        self.defs: dict[str, dict[str, str]] = {}
        self.spans: dict[str, list[tuple[int, int, str]]] = {}
        self.modules: dict[str, str] = {}
        self.suffix: dict[str, set[str]] = {}
        self.imports: dict[str, dict[str, tuple[str, str | None, int]]] = {}

    def add_known(self, rel_path: str, defs: dict[str, str], spans: list | None = None) -> None:
        """Register a file's claimable defs (qualname -> kind), spans, and module names.

        `spans` (start_byte, end_byte, qualname) is supplied only for files parsed this
        run. walk.py owns the ONE implementation of §4's granularity / `[i]` rules.
        """
        self.defs[rel_path] = dict(defs)
        if spans is not None:
            self.spans[rel_path] = sorted(spans)
        if not rel_path.endswith(".py") or rel_path == "__init__.py":
            return
        stem = rel_path[:-3]
        parts = (stem[: -len("/__init__")] if stem.endswith("/__init__") else stem).split("/")
        self.modules[".".join(parts)] = rel_path
        for i in range(1, len(parts)):
            self.suffix.setdefault(".".join(parts[i:]), set()).add(rel_path)

    def index_file(self, rel_path: str, tree: Any) -> None:
        """Pass 1: this file's import table (defs arrive via `add_known`)."""
        tbl = self.imports.setdefault(rel_path, {})
        for q, plain in ((Q_IMPORT, True), (Q_IMPORT_FROM, False)):
            for st in captures(q, tree.root_node).get("stmt", []):
                mod, level = ("", 0) if plain else self._module_of(st)
                # `wildcard_import` is not a `name` field child, so `import *` is ignored.
                for n in st.children_by_field_name("name"):
                    alias, name = self._alias_name(n)
                    if name:
                        tbl[alias] = (name, None, 0) if plain else (mod, name, level)

    def resolve_calls(self, rel_path: str, tree: Any) -> list[tuple[str, str, str]]:
        """Pass 2: one `(src_qualname, dst_path, dst_qualname)` per RESOLVED call site."""
        out: list[tuple[str, str, str]] = []
        for call in captures(Q_CALL, tree.root_node).get("reference.call", []):
            parts = dotted_parts(call.child_by_field_name("function"))
            if not parts:
                continue
            src = self._owner(rel_path, call.start_byte)
            tgt = self._target(rel_path, src, parts)
            if tgt is not None:
                out.append((src, tgt[0], tgt[1]))
        return out

    def resolve_imports(self, rel_path: str) -> list[str]:
        """Every in-repo file this file imports (the IMPORTS edge destinations)."""
        out: set[str] = set()
        for dotted, symbol, level in self.imports.get(rel_path, {}).values():
            # `from pkg import mod` imports both the package and the submodule file.
            for cand in [dotted] + ([f"{dotted}.{symbol}" if dotted else symbol] if symbol else []):
                p = self._module_path(rel_path, cand, level) if (cand or level) else None
                if p is not None and p != rel_path:
                    out.add(p)
        return sorted(out)

    # -- internals ---------------------------------------------------------------
    @staticmethod
    def _alias_name(n: Any) -> tuple[str, str]:
        if n.type == "aliased_import":
            return _txt(n.child_by_field_name("alias")), _txt(n.child_by_field_name("name"))
        return (_txt(n), _txt(n)) if n.type == "dotted_name" else ("", "")

    @staticmethod
    def _module_of(st: Any) -> tuple[str, int]:
        m = st.child_by_field_name("module_name")
        if m is None or m.type != "relative_import":
            return (_txt(m), 0) if m is not None else ("", 0)
        kids = {c.type: c for c in m.children}
        pre, dot = kids.get("import_prefix"), kids.get("dotted_name")
        return (_txt(dot) if dot is not None else ""), (len(_txt(pre)) if pre is not None else 1)

    def _module_path(self, rel_path: str, dotted: str, level: int) -> str | None:
        if level:
            parts = rel_path.rsplit("/", 1)[0].split("/") if "/" in rel_path else []
            if level - 1 > len(parts):
                return None
            segs = parts[: len(parts) - (level - 1)] + (dotted.split(".") if dotted else [])
            joined = "/".join(segs)
            return next((c for c in (f"{joined}.py", f"{joined}/__init__.py") if c in self.defs), None)
        if dotted in self.modules:
            return self.modules[dotted]
        cands = self.suffix.get(dotted)
        return next(iter(cands)) if cands and len(cands) == 1 else None

    def _owner(self, rel_path: str, pos: int) -> str:
        """Innermost claimable span containing `pos`; '' = the File node (§4 bucketing)."""
        best = ""
        for start, end, q in self.spans.get(rel_path, ()):
            if start > pos:
                break
            if pos < end:
                best = q
        return best

    def _pick(self, path: str | None, parts: list[str]) -> tuple[str, str] | None:
        d = self.defs.get(path or "")
        if not parts or d is None:
            return None
        q = NAME_SEP.join(parts)
        return (path, q) if q in d else None  # type: ignore[return-value]

    def _entry(self, rel: str, ent: tuple, rest: list[str]) -> tuple[str, str] | None:
        dotted, symbol, level = ent
        path = self._module_path(rel, dotted, level)
        if symbol is None:
            return self._pick(path, rest)
        return self._pick(path, [symbol] + rest) or self._pick(
            self._module_path(rel, f"{dotted}.{symbol}" if dotted else symbol, level), rest)

    def _target(self, rel: str, src_qual: str, parts: list[str]) -> tuple[str, str] | None:
        tbl = self.imports.get(rel, {})
        if len(parts) > 1:
            for i in range(len(parts) - 1, 0, -1):
                ent = tbl.get(".".join(parts[:i]))
                if ent is not None:
                    return self._entry(rel, ent, parts[i:])
            if parts[0] == "self" and NAME_SEP in src_qual:
                return self._pick(rel, src_qual.rsplit(NAME_SEP, 1)[0].split(NAME_SEP) + parts[1:])
            return self._pick(rel, parts) if parts[0] in self.defs.get(rel, {}) else None
        name = parts[0]
        if name in self.defs.get(rel, {}):
            return (rel, name)
        if name in tbl:
            return self._entry(rel, tbl[name], [])
        # Bare-name fallback: a bare name never denotes a method, so only module-level
        # qualnames match; resolve only when exactly one file defines it.
        hits = [p for p, d in self.defs.items() if name in d]
        return (hits[0], name) if len(hits) == 1 else None
