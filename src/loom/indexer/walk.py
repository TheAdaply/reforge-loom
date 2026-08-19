"""BUILD-SPEC §9.1 — repo discovery and the two-pass tree-sitter indexer (M1).

COLD ≡ INCREMENTAL, BY CONSTRUCTION (U1). `changed_only` decides which files get their
NODE ROWS re-minted; it decides nothing about edges. Pass 2 wipes this repo's CALLS and
IMPORTS and re-resolves them from the whole, up-to-date node set every run, so
`index_repo(changed_only=True)` and a cold `index_repo(changed_only=False)` over the same
tree produce the SAME graph — pinned by
`tests/indexer/test_incremental.py::test_incremental_equals_cold_index`.

Inspired by graft (MIT) `src/graph/build.ts:211-284` + `test/graph-incremental.test.ts`:
memoize the per-file extraction, but hand `resolveEdges` the whole node set, and freeze the
discipline with a cold-equals-incremental test. graft can skip the re-parse because it
persists each file's unresolved `rawEdges` alongside its nodes; loom's call sites live only
in the tree-sitter tree, so unchanged files are re-PARSED and only their DB writes are
skipped. That is not laziness, it is required: `Resolver`'s bare-name fallback and its
dotted-suffix module lookup both resolve only when EXACTLY ONE file matches, so adding or
removing a def in file F can flip an edge in a file that has no edge to F at all. "Re-resolve
only the files whose edges touch changed files" would therefore still decay.

MEASURED PRICE of that honesty, one file touched, warm cache, M-series laptop:
conduit (78 files / 527 nodes / 1116 edges) 11ms -> 57ms, against 61ms for a cold index;
graphiti (198 files / 1877 nodes / 3981 edges) 15ms -> 148ms, against 160ms cold.
So an incremental run now costs ~92% of a cold one and the sha256 memo buys back only the
node-row churn. At ~0.15s for a 200-file repo that is far inside `loom index`'s budget, and
it is not on the gate's 1.5s/2.5s hook path at all — the gate reads the graph, it never
builds it. A repo large enough to feel this wants graft's persisted per-file raw-edge table,
which is a schema change, not a scheduling one.

§11.11 keeps SQL out of call sites: every statement loom issues against `nodes`/`edges`
lives in the `_put`/`_edge` helpers or in `delete_file_nodes`/`index_repo` directly.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any

from tree_sitter import Parser

from loom.indexer.naming import norm_path
from loom.indexer.naming import qualname as join_qualname
from loom.indexer.queries.python import LANGUAGE, Q_DEFS, Resolver, matches
from loom.server.db import immediate, iso, log_event, now_s
from loom.server.ids import node_id

# Directories never walked (BUILD-SPEC §9.1): tooling and vendor trees ONLY. The frozen set
# also carried `frontend`, `alembic` and `tests`; council W5 removed all three, because agents
# spend real edit budget in a test tree and an unindexed directory is a silent coordination
# hole — loom gates everything it indexes, and it indexes everything that is plausibly source.
# Still not configurable: a `--exclude` flag is a behavior change needing its own
# DECISIONS-DELTA entry (FINDINGS V10).
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "site-packages", "build", "dist", "__pycache__",
}
# Files above this size get no node at all (they are read+hashed EVERY run; a checked-in
# binary would tax every incremental pass). Documented gap: such files are ungated.
_INDEX_CAP_BYTES = 2_000_000

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
    """Repo-root-relative POSIX paths of every indexable file, sorted.

    ALL regular files under the cap are discovered (bench E3): non-Python files get a File
    node so FILE-LEVEL claims and gating work in any language; symbol-level parsing stays
    Python-only. Dotfiles at the top of a checkout (.gitignore, .mcp.json) are included —
    they are exactly the shared hot files teams fight over."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for f in filenames:
            if f.startswith(".loom.sqlite3"):
                # break4 cli-F2: the default db layout lives INSIDE the served checkout,
                # and indexing loom's own database (plus its -wal/-shm, rewritten on every
                # gate decision) kept `index_age` permanently stale. A --db under another
                # name is the operator's own layout choice and is not special-cased.
                continue
            full = os.path.join(dirpath, f)
            try:
                if not os.path.isfile(full) or os.path.getsize(full) > _INDEX_CAP_BYTES:
                    continue
            except OSError:
                continue
            out.append(norm_path(os.path.relpath(full, repo_root)))
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
    log_event(conn, "indexer", "indexed", f"deleted {rel_path} ({len(ids)} nodes)", repo)


def _write_file_nodes(conn, repo: str, rel: str, src: bytes,
                      ents: list[tuple[str, str, Any]]) -> None:
    """Re-mint one file's File/Class/Function nodes + CONTAINS edges. Idempotent.

    `ents` is `_entities(tree.root_node)`, computed by the caller for EVERY file (resolution
    needs the spans regardless) and handed here only for the files whose bytes moved — so
    node rows, their ids and their `updated` stamps stay untouched for unchanged files.
    CONTAINS is the one edge kind minted here: it never crosses a file, so a changed file's
    `delete_file_nodes` can never strand an unchanged file's CONTAINS edge.
    """
    delete_file_nodes(conn, repo, rel)
    now, file_id = iso(now_s()), node_id(repo, rel, "")
    _put(conn, (file_id, repo, rel, "", "File", _sha(src), "", 1, src.count(b"\n") + 1, now))
    owners: dict[int, str] = {}
    for q, kind, n in ents:
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


_CHUNK_FILES = 64
# BC3-2 (§11.45): node writes land in _CHUNK_FILES-sized transactions, so the write lock is
# RELEASED between chunks. A whole-rebuild lock made every /gate audit/renew write on a big
# tree exceed the hook's 1.5s budget and fail OPEN across every repo the database serves.


def index_repo(conn, repo: str, repo_root: str, changed_only: bool = False) -> dict[str, Any]:
    """Two-pass index of a repo. Returns `{files, nodes, edges, changed}` (repo totals).

    OWNS its transactions (§11.45): callers must NOT wrap this in `db.immediate()`. Parsing
    runs outside any transaction (pure CPU — the lock is not held while tree-sitter runs);
    node writes commit per `_CHUNK_FILES` chunk, each file's delete-then-insert atomic
    inside its chunk; the CALLS/IMPORTS swap stays ONE transaction — a reader must never
    see the edge set half-rebuilt. A reader between chunks sees some files re-minted and
    others not yet: the same self-healing state a crashed index leaves, cured by the next
    run (U1 cold≡incremental).
    """
    files = discover_files(repo_root)
    known = {r["path"]: r["body_hash"] for r in
             conn.execute("SELECT path, body_hash FROM nodes WHERE repo=? AND qualname=''", (repo,))}
    # Every file is read and hashed EVERY run, incremental or not (graft build.ts:211-220
    # makes the same call): a stat may decide whether a query bothers re-indexing, it may
    # not decide what the re-index itself looks at, or a same-length edit inside one mtime
    # tick becomes permanently invisible.
    sources: dict[str, bytes] = {}
    for rel in files:
        try:
            with open(os.path.join(repo_root, *rel.split("/")), "rb") as fh:
                sources[rel] = fh.read()
        except OSError:
            # A broken symlink or an unreadable file is one file loom cannot gate, not a
            # reason to leave the whole repo un-indexed (which would gate NOTHING).
            continue
    files = [rel for rel in files if rel in sources]
    changed = [rel for rel in files
               if not (changed_only and known.get(rel) == _sha(sources[rel]))]
    fresh = set(changed)
    gone = sorted(set(known) - set(files))
    changed += gone
    # Pass 1a: parse EVERY py file (spans and call sites live only in the tree), fresh or
    # not — the resolver is fed exactly the same inputs on an incremental run as on a cold
    # one, and that identity IS the U1 fix. Pure CPU, zero writes, no lock held.
    res, trees, ents_by = Resolver(), {}, {}
    py_files = [rel for rel in files if rel.endswith(".py")]
    for rel in py_files:
        trees[rel] = _PARSER.parse(sources[rel])
        ents = _entities(trees[rel].root_node)
        ents_by[rel] = ents
        res.add_known(rel, {q: k for q, k, _ in ents},
                      [(n.start_byte, n.end_byte, q) for q, _, n in ents])
        res.index_file(rel, trees[rel])
    # Pass 1b: node writes, chunked. Non-Python files get a File node only — enough for
    # file-level claims and gating in any language (bench E3); no symbols, no edges.
    if gone:
        with immediate(conn):
            for rel in gone:
                delete_file_nodes(conn, repo, rel)
    fresh_list = [rel for rel in files if rel in fresh]
    for i in range(0, len(fresh_list), _CHUNK_FILES):
        with immediate(conn):
            for rel in fresh_list[i:i + _CHUNK_FILES]:
                _write_file_nodes(conn, repo, rel, sources[rel], ents_by.get(rel, []))
    sources.clear()          # every remaining pass reads `trees`, not bytes
    # Pass 2a: whole-graph edge RESOLUTION — tree-walking CPU work, pure in-memory, so it
    # runs before the transaction opens; the lock is never held while the resolver thinks.
    edge_rows: list[tuple[str, str, str]] = []
    for rel in py_files:  # File->File IMPORTS (src = importer), then CALLS (src = caller).
        for dst in res.resolve_imports(rel):
            edge_rows.append((node_id(repo, rel, ""), node_id(repo, dst, ""), "IMPORTS"))
        for sq, dp, dq in res.resolve_calls(rel, trees[rel]):
            edge_rows.append((node_id(repo, rel, sq), node_id(repo, dp, dq), "CALLS"))
    # Pass 2b: the edge SWAP, one transaction of pure SQL. Both resolved kinds are dropped
    # for the WHOLE repo first — `delete_file_nodes` alone would leave an unchanged
    # caller's CALLS edge pointing at a callee that has since moved, and would never
    # restore the inbound edge it deleted when the callee was re-minted (the U1 decay).
    # Both `src` columns are in-repo by the frozen edge directions, so this touches no
    # other repo's rows.
    with immediate(conn):
        conn.execute("DELETE FROM edges WHERE kind IN ('CALLS','IMPORTS') AND src IN"
                     " (SELECT id FROM nodes WHERE repo=?)", (repo,))
        for src, dst, kind in edge_rows:
            _edge(conn, src, dst, kind)
        # Refresh SQLite's planner statistics: without this, declare_plan's closure queries
        # flip to a table-scan plan at ~django-tests scale — 9.3s vs 3.9ms, the 2,402x
        # cliff bench E2 measured and its judge reproduced. One statement, every index run.
        conn.execute("ANALYZE")
        n = conn.execute("SELECT COUNT(*) c FROM nodes WHERE repo=?", (repo,)).fetchone()["c"]
        e = conn.execute("SELECT COUNT(*) c FROM edges WHERE src IN"
                         " (SELECT id FROM nodes WHERE repo=?)", (repo,)).fetchone()["c"]
        log_event(conn, "indexer", "indexed",
                  f"{repo}: {len(files)} files, {n} nodes, {e} edges, {len(changed)} changed",
                  repo)
    return {"files": len(files), "nodes": n, "edges": e, "changed": changed}
