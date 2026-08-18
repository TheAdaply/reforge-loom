"""Frozen artifact — BUILD-SPEC §4. Canonical qualname / path string helpers.

Symbol name-path convention derived from Serena (https://github.com/oraios/serena),
MIT License, Copyright (c) 2025 Oraios AI. See THIRD_PARTY_NOTICES.md.

stdlib only. `node_ref` / `split_ref` are re-exported from `loom.server.ids`
(single definition lives there, §9.1) — `ids.py` is stdlib-only, so the hook's
`import loom.indexer.naming` stays inside its PreToolUse budget (§9.0 __init__ law).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from loom.server.ids import node_ref, split_ref

__all__ = ["NAME_SEP", "qualname", "norm_path", "prefix_candidates", "node_ref", "split_ref"]

# Serena's real NAME_PATH_SEP (serena/symbol.py:26). The plan's dotted
# `Class.method` form does not exist in Serena and is dead everywhere in loom.
NAME_SEP = "/"


def qualname(components: Sequence[str]) -> str:
    """Join symbol path components with NAME_SEP: ('Outer', 'method') -> 'Outer/method'.

    Empty / falsy components are dropped; an empty sequence yields '' (the
    file-level node's qualname).
    """
    return NAME_SEP.join(c for c in components if c)


def norm_path(p: str) -> str:
    """Repo-root-relative, POSIX-normalized path (specgate §3.4, verbatim)."""
    return str(PurePosixPath(p.replace("\\", "/")))


def prefix_candidates(name_path: str) -> list[str]:
    """Serena `name_path` resolution candidates, longest-first (§4).

    Leading '/' stripped, then 'A/b/c' -> ['A/b/c', 'A/b', 'A'].
    """
    parts = [c for c in name_path.lstrip("/").split(NAME_SEP) if c]
    return [NAME_SEP.join(parts[:i]) for i in range(len(parts), 0, -1)]
