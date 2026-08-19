"""Frozen artifact — BUILD-SPEC §4. Canonical qualname / path string helpers.

Symbol name-path convention derived from Serena (https://github.com/oraios/serena),
MIT License, Copyright (c) 2025 Oraios AI. Full license:
third_party/LICENSES/serena-MIT.txt. See THIRD_PARTY_NOTICES.md.

stdlib only. `node_ref` / `split_ref` are re-exported from `loom.server.ids`
(single definition lives there, §9.1) — `ids.py` is stdlib-only, so the hook's
`import loom.indexer.naming` stays inside its PreToolUse budget (§9.0 __init__ law).
"""

from __future__ import annotations

import unicodedata
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
    """Repo-root-relative, POSIX- and NFC-normalized path (specgate §3.4 — loom's own MVP).

    NFC is the second half of P0-1's "one file, one identity" (break3 journey-J1). `café.py`
    has two byte spellings — decomposed (`cafe` + U+0301, what a macOS zip, a Finder copy or
    any non-precomposing tool leaves on disk) and composed (U+00E9, what a keyboard, a fresh
    clone, `git ls-files` and every LLM emit). APFS opens both as ONE file; loom compared
    STRINGS, so the indexer keyed the graph on whichever spelling it walked and a gate call
    in the other spelling resolved nothing -> `new_path` -> ALLOW, straight over a live
    foreign write claim, and the edit landed. One form, chosen on BOTH sides — here, which
    keys the graph, and in `hook/locator._rel`, which produces the wire path — closes it.
    NFC is the composed form git and the web platform already normalize to.
    """
    return unicodedata.normalize("NFC", str(PurePosixPath(p.replace("\\", "/"))))


def prefix_candidates(name_path: str) -> list[str]:
    """Serena `name_path` resolution candidates, longest-first (§4).

    Leading '/' stripped, then 'A/b/c' -> ['A/b/c', 'A/b', 'A'].
    """
    parts = [c for c in name_path.lstrip("/").split(NAME_SEP) if c]
    return [NAME_SEP.join(parts[:i]) for i in range(len(parts), 0, -1)]
