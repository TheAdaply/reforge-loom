"""BUILD-SPEC §9.1 — repo discovery and the two-pass tree-sitter indexer (M1).

INCREMENTAL CAVEAT, ACCEPTED FOR MVP (falkordb C5): `changed_only` re-resolves only files
whose sha256 moved, and `delete_file_nodes` drops every edge touching a re-indexed file,
so inbound CALLS/IMPORTS from UNCHANGED files go stale until those files change. A full
`index_repo(..., changed_only=False)` always restores the complete graph.

§11.11 keeps SQL out of call sites: every statement loom issues against `nodes`/`edges`
lives in the `_put`/`_edge` helpers or in `delete_file_nodes`/`index_repo` directly.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any

from tree_sitter import Parser

from loom.indexer.naming import norm_path, qualname as join_qualname
from loom.indexer.queries.python import LANGUAGE, Q_DEFS, Resolver, matches
from loom.server.db import iso, log_event, now_s
from loom.server.ids import node_id

EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "site-packages", "build", "dist", "__pycache__", "frontend", "alembic", "tests"}

_PARSER = Parser(LANGUAGE)

# §4 block-statement rule: the ONLY node types allowed between a claimable def/class and
# the module root. A def under `if`/`try`/`with`/`for`/`while`/`match` fails at that
# statement's own type; a def inside a function fails at `function_definition`.
_ALLOWED_ANCESTORS = {"block", "class_definition", "decorated_definition"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put(conn: sqlite3.Connection, row: tuple) -> None:
    conn.execute("INSERT INTO nodes (id, repo, path, qualname, kind, body_hash, sig_hash,"
                 " start_line, end_line, updated) VALUES (?,?,?,?,?,?,?,?,?,?)", row)


def _edge(conn: sqlite3.Connection, src: str, dst: str, kind: str) -> None:
    conn.execute("INSERT OR IGNORE INTO edges (src, dst, kind) VALUES (?,?,?)", (src, dst, kind))


def discover_files(repo_root: str) -> list[str]:
    """Repo-root-relative POSIX paths of every indexable ``*.py`` file, sorted."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        out += [norm_path(os.path.relpath(os.path.join(dirpath, f), repo_root))
                for f in filenames if f.endswith(".py")]
    return sorted(out)


def _claimable(node: Any) -> bool:
    """§4: claimable iff every ancestor up to `module` is a class body or decorator wrapper."""
    p = node.parent
    while p is not None and p.type != "module":
        if p.type not in _ALLOWED_ANCESTORS:
            return False
        p = p.parent
    return p is not None


def _entities(root: Any) -> list[tuple[str, str, Any]]:
    """`(qualname, kind, ts_node)` in source order, with §4's `[i]` duplicate counter.

    `Q_DEFS` captures EVERY def/class in the file; `_claimable` — not the query text — is
    the claimability rule, so every candidate is judged by one predicate and intermediate
    classes can no longer be skipped (FINDINGS P1-5/P2-6). Captures are taken via
    `@name`.parent, which unwraps `decorated_definition` to the inner definition for name
    and span (falkordb §2.8).
    """
    found: dict[int, Any] = {}
    for _, caps in matches(Q_DEFS, root):
        for n in caps.get("name", ()):
            found[n.parent.id] = n.parent
    out: list[tuple[str, str, Any]] = []
    assigned: dict[int, str] = {}  # ts node id -> assigned qualname (parents come first)
    counts: dict[str, int] = {}
    for n in sorted((n for n in found.values() if _claimable(n)), key=lambda n: n.start_byte):
        base, p = "", n.parent
        while p is not None:  # nearest already-assigned (i.e. claimable) ancestor
            if p.id in assigned:
                base = assigned[p.id]
                break
            p = p.parent
        q = join_qualname([base, n.child_by_field_name("name").text.decode()])
        seen = counts.get(q, 0)
        counts[q] = seen + 1
        if seen:
            q = f"{q}[{seen}]"  # second identical KEPT def in one file -> Serena `[i]` suffix
        assigned[n.id] = q
        out.append((q, "Class" if n.type == "class_definition" else "Function", n))
    return out


def delete_file_nodes(conn: sqlite3.Connection, repo: str, rel_path: str) -> None:
    """Drop a file's nodes and EVERY edge touching them (falkordb §2.12).

    Claims on the deleted nodes are left as tombstoned orphans — §2's LEFT JOIN predicate
    judges them dead. Claim rows are never deleted here.
    """
    ids = [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE repo=? AND path=?", (repo, rel_path))]
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM edges WHERE src IN ({marks}) OR dst IN ({marks})", ids + ids)
    conn.execute("DELETE FROM nodes WHERE repo=? AND path=?", (repo, rel_path))
    log_event(conn, "indexer", "indexed", f"deleted {rel_path} ({len(ids)} nodes)")


def _index_tree(conn, repo: str, rel: str, src: bytes, tree: Any) -> list[tuple[str, str, int, int]]:
    """Re-mint one file's File/Class/Function nodes + CONTAINS edges. Idempotent."""
    delete_file_nodes(conn, repo, rel)
    now, file_id = iso(now_s()), node_id(repo, rel, "")
    _put(conn, (file_id, repo, rel, "", "File", _sha(src), "", 1, src.count(b"\n") + 1, now))
    owners: dict[int, str] = {}
    ents: list[tuple[str, str, int, int]] = []
    for q, kind, n in _entities(tree.root_node):
        nid = node_id(repo, rel, q)
        body = n.child_by_field_name("body")
        head = src[n.start_byte:(body.start_byte if body is not None else n.end_byte)]
        _put(conn, (nid, repo, rel, q, kind, _sha(src[n.start_byte:n.end_byte]), _sha(head),
                    n.start_point[0] + 1, n.end_point[0] + 1, now))
        owner, p = file_id, n.parent
        while p is not None:
            if p.id in owners:
                owner = owners[p.id]
                break
            p = p.parent
        owners[n.id] = nid
        _edge(conn, owner, nid, "CONTAINS")  # src = container (File|Class), dst = contained
        ents.append((q, kind, n.start_byte, n.end_byte))
    return ents


def index_repo(conn, repo: str, repo_root: str, changed_only: bool = False) -> dict[str, Any]:
    """Two-pass index of a repo. Returns `{files, nodes, edges, changed}` (repo totals)."""
    files = discover_files(repo_root)
    known = {r["path"]: r["body_hash"] for r in
             conn.execute("SELECT path, body_hash FROM nodes WHERE repo=? AND qualname=''", (repo,))}
    sources: dict[str, bytes] = {}
    for rel in files:
        with open(os.path.join(repo_root, *rel.split("/")), "rb") as fh:
            src = fh.read()
        if not (changed_only and known.get(rel) == _sha(src)):
            sources[rel] = src
    changed = sorted(sources)
    for rel in sorted(set(known) - set(files)):
        delete_file_nodes(conn, repo, rel)
        changed.append(rel)
    # Pass 1: nodes + CONTAINS for changed files; def/module tables for the WHOLE repo.
    res, trees = Resolver(), {}
    for rel in sorted(sources):
        trees[rel] = _PARSER.parse(sources[rel])
        ents = _index_tree(conn, repo, rel, sources[rel], trees[rel])
        res.add_known(rel, {q: k for q, k, _, _ in ents}, [(s, e, q) for q, _, s, e in ents])
        res.index_file(rel, trees[rel])
    for rel in files:
        if rel not in trees:  # unchanged: defs come from the DB, never a re-parse
            res.add_known(rel, {r["qualname"]: r["kind"] for r in conn.execute(
                "SELECT qualname, kind FROM nodes WHERE repo=? AND path=? AND qualname<>''", (repo, rel))})
    # Pass 2: File->File IMPORTS (src = importer), then bucketed CALLS (src = caller).
    for rel in sorted(trees):
        for dst in res.resolve_imports(rel):
            _edge(conn, node_id(repo, rel, ""), node_id(repo, dst, ""), "IMPORTS")
        for sq, dp, dq in res.resolve_calls(rel, trees[rel]):
            _edge(conn, node_id(repo, rel, sq), node_id(repo, dp, dq), "CALLS")
    n = conn.execute("SELECT COUNT(*) c FROM nodes WHERE repo=?", (repo,)).fetchone()["c"]
    e = conn.execute("SELECT COUNT(*) c FROM edges WHERE src IN"
                     " (SELECT id FROM nodes WHERE repo=?)", (repo,)).fetchone()["c"]
    log_event(conn, "indexer", "indexed",
              f"{repo}: {len(files)} files, {n} nodes, {e} edges, {len(changed)} changed")
    return {"files": len(files), "nodes": n, "edges": e, "changed": changed}
