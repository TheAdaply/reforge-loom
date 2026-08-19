"""M1 — the EXACT node set (ids, qualnames, kinds) and CONTAINS edges of the fixture repo.

Every adversarial case from conduit-verify §2.4 is pinned here, positively (the node/edge
exists) or negatively (§4's granularity and block-statement rules keep it out).
"""

from __future__ import annotations

import os

import pytest

from loom.indexer.walk import EXCLUDE_DIRS, discover_files
from loom.server.ids import node_id, node_ref, split_ref

REPO = "fx"

# (path, qualname, kind) — the complete, frozen expectation for tests/fixtures/pyrepo.
EXPECTED_NODES = {
    ("lib/__init__.py", "", "File"),
    ("lib/util.py", "", "File"),
    ("lib/util.py", "helper", "Function"),
    ("lib/util.py", "make_tag", "Function"),
    ("lib/util.py", "make_token", "Function"),
    ("models.py", "", "File"),
    ("models.py", "AuthResult", "Class"),
    ("models.py", "AuthResult/token", "Function"),
    ("sub/__init__.py", "", "File"),
    ("sub/deep.py", "", "File"),
    ("sub/deep.py", "Digger", "Class"),
    ("sub/deep.py", "Digger/dig", "Function"),
    ("sub/deep.py", "window", "Function"),
    ("sub/deep.py", "twin", "Function"),
    ("sub/deep.py", "twin[1]", "Function"),
    ("sub/sibling.py", "", "File"),
    ("sub/sibling.py", "probe", "Function"),
    ("sub/sibling.py", "blend", "Function"),
    ("svc.py", "", "File"),
    ("svc.py", "Depends", "Function"),
    ("svc.py", "get_db", "Function"),
    ("svc.py", "route", "Function"),
    ("svc.py", "AuthService", "Class"),
    ("svc.py", "AuthService/salt", "Function"),
    ("svc.py", "AuthService/authenticate", "Function"),
    ("svc.py", "AuthService/verify", "Function"),
    ("svc.py", "AuthService/Session", "Class"),
    ("svc.py", "AuthService/Session/open", "Function"),
    ("svc.py", "bootstrap", "Function"),
    ("up.py", "", "File"),
    ("up.py", "deep", "Function"),
    ("up.py", "surface", "Function"),
}

EXPECTED_CONTAINS = {
    ("lib/util.py", "lib/util.py::helper"),
    ("lib/util.py", "lib/util.py::make_tag"),
    ("lib/util.py", "lib/util.py::make_token"),
    ("models.py", "models.py::AuthResult"),
    ("models.py::AuthResult", "models.py::AuthResult/token"),
    ("sub/deep.py", "sub/deep.py::Digger"),
    ("sub/deep.py", "sub/deep.py::window"),
    ("sub/deep.py", "sub/deep.py::twin"),
    ("sub/deep.py", "sub/deep.py::twin[1]"),
    ("sub/deep.py::Digger", "sub/deep.py::Digger/dig"),
    ("sub/sibling.py", "sub/sibling.py::probe"),
    ("sub/sibling.py", "sub/sibling.py::blend"),
    ("svc.py", "svc.py::Depends"),
    ("svc.py", "svc.py::get_db"),
    ("svc.py", "svc.py::route"),
    ("svc.py", "svc.py::AuthService"),
    ("svc.py", "svc.py::bootstrap"),
    ("svc.py::AuthService", "svc.py::AuthService/salt"),
    ("svc.py::AuthService", "svc.py::AuthService/authenticate"),
    ("svc.py::AuthService", "svc.py::AuthService/verify"),
    ("svc.py::AuthService", "svc.py::AuthService/Session"),
    ("svc.py::AuthService/Session", "svc.py::AuthService/Session/open"),
    ("up.py", "up.py::deep"),
    ("up.py", "up.py::surface"),
}

# §4 granularity + block-statement rules: these must NEVER be minted.
FORBIDDEN_QUALNAMES = [
    "AuthService/typed_only",            # def under `if TYPE_CHECKING:` in a class body
    "typed_only",
    "AuthService/authenticate/_finish",  # closure inside a method
    "_finish",
    "route/wrap",                        # closure inside a module-level function
    "wrap",
    "AuthResult.token",                  # the plan's dotted form is dead everywhere in loom
]


def test_discover_files_lists_every_py_file(repo_root: str) -> None:
    assert discover_files(repo_root) == [
        "lib/__init__.py", "lib/util.py", "models.py", "sub/__init__.py",
        "sub/deep.py", "sub/sibling.py", "svc.py", "up.py",
    ]


def test_exclude_dirs_are_pruned(repo_root: str) -> None:
    # Vendor/tooling dirs are pruned; `tests/` is DELIBERATELY indexed since council W5 —
    # an ungated test tree was a silent coordination hole (see test_all_languages.py).
    for bad in ("__pycache__", "node_modules", ".venv"):
        assert bad in EXCLUDE_DIRS
        os.makedirs(os.path.join(repo_root, bad), exist_ok=True)
        with open(os.path.join(repo_root, bad, "ghost.py"), "w") as fh:
            fh.write("def ghost(): pass\n")
    assert "tests" not in EXCLUDE_DIRS
    assert not [p for p in discover_files(repo_root) if "ghost" in p]


def test_exact_node_set(graph) -> None:
    rows = graph.nodes()
    assert set(rows.values()) == EXPECTED_NODES
    assert len(rows) == len(EXPECTED_NODES)


def test_node_ids_are_the_deterministic_server_side_ids(graph) -> None:
    for nid, (path, qual, _kind) in graph.nodes().items():
        assert nid == node_id(REPO, path, qual)
        assert nid.startswith("n-") and len(nid) == 10


@pytest.mark.parametrize("qual", FORBIDDEN_QUALNAMES)
def test_non_claimable_definitions_are_never_minted(graph, qual: str) -> None:
    assert qual not in {q for _p, q, _k in graph.nodes().values()}


def test_contains_edges_exact(graph) -> None:
    assert graph.edges("CONTAINS") == EXPECTED_CONTAINS


def test_duplicate_top_level_defs_get_the_index_suffix(graph) -> None:
    quals = sorted(q for p, q, _k in graph.nodes().values() if p == "sub/deep.py" and q.startswith("twin"))
    assert quals == ["twin", "twin[1]"]
    lines = [
        graph.conn.execute(
            "SELECT start_line FROM nodes WHERE repo=? AND path='sub/deep.py' AND qualname=?", (REPO, q)
        ).fetchone()["start_line"]
        for q in ("twin", "twin[1]")
    ]
    assert lines[0] < lines[1]  # the `[i]` counter runs in source order over KEPT defs


def test_decorated_async_def_anchors_to_the_inner_function_definition(graph) -> None:
    row = graph.conn.execute(
        "SELECT start_line FROM nodes WHERE repo=? AND path='svc.py' AND qualname=?",
        (REPO, "AuthService/verify"),
    ).fetchone()
    src = graph.read("svc.py").splitlines()
    assert src[row["start_line"] - 1].lstrip().startswith("async def verify")
    assert src[row["start_line"] - 2].lstrip().startswith("@route")  # decorator is excluded


def test_nested_class_and_its_method_are_claimable(graph) -> None:
    quals = {q for p, q, _k in graph.nodes().values() if p == "svc.py"}
    assert "AuthService/Session" in quals
    assert "AuthService/Session/open" in quals


NESTED_SRC = (
    "class A:\n"
    "    class B:\n"           # intermediate: body holds ONLY another class
    "        class C:\n"
    "            def z(self):\n"
    "                return 1\n"
    "\n"
    "    class Meta:\n"        # method-less nested class (the single-level form)
    "        ordering = ['id']\n"
    "\n"
    "    def m(self):\n"
    "        return 2\n"
)


def test_intermediate_and_method_less_nested_classes_are_claimable(graph) -> None:
    """FINDINGS P1-5 / P2-6, closed by the Q_DEFS collapse.

    The three module-anchored queries only ever saw a nested class as a side effect of it
    directly containing a function, so `B` (body = one class) and `Meta` (body = one
    assignment) were skipped — and `C` was then minted as `A/C`, a qualname that appears
    nowhere in the source. `_claimable` is now the only claimability rule.
    """
    graph.write("nest.py", NESTED_SRC)
    graph.reindex()
    quals = {q for p, q, _k in graph.nodes().values() if p == "nest.py"}
    assert quals == {"", "A", "A/B", "A/B/C", "A/B/C/z", "A/Meta", "A/m"}
    assert "A/C" not in quals  # the old, source-less qualname is gone
    assert ("nest.py::A", "nest.py::A/B") in graph.edges("CONTAINS")
    assert ("nest.py::A/B", "nest.py::A/B/C") in graph.edges("CONTAINS")
    assert ("nest.py::A/B/C", "nest.py::A/B/C/z") in graph.edges("CONTAINS")


def test_indexer_and_locator_agree_on_nested_qualnames(graph) -> None:
    """§4's frozen law: the indexer (tree-sitter) and the hook locator (stdlib `ast`) must
    apply the SAME rule. When they disagreed, an owner who declared the only ref loom
    offered was denied `out_of_scope` on their own code (P1-5)."""
    from loom.hook.locator import collect_symbol_spans

    graph.write("nest.py", NESTED_SRC)
    graph.reindex()
    indexed = {q for p, q, _k in graph.nodes().values() if p == "nest.py" and q}
    located = {span[0] for span in collect_symbol_spans(NESTED_SRC)}
    assert indexed == located


def test_hashes_and_spans_are_populated(graph) -> None:
    for r in graph.conn.execute("SELECT * FROM nodes WHERE repo=?", (REPO,)):
        assert len(r["body_hash"]) == 64
        assert r["start_line"] >= 1 and r["end_line"] >= r["start_line"]
        # sig_hash (v2 impact insurance) is set for every symbol node, '' only for Files.
        assert (r["sig_hash"] == "") == (r["kind"] == "File")


def test_full_index_is_idempotent(graph) -> None:
    before = (graph.nodes(), graph.raw_edges())
    graph.reindex(changed_only=False)
    assert (graph.nodes(), graph.raw_edges()) == before


def test_node_ref_round_trip_for_every_node(graph) -> None:
    for path, qual, _kind in graph.nodes().values():
        assert split_ref(node_ref(path, qual)) == (path, qual)


def test_index_repo_stats(graph) -> None:
    stats = graph.reindex(changed_only=False)
    assert stats["files"] == 8
    assert stats["nodes"] == len(EXPECTED_NODES)
    assert stats["edges"] == len(graph.raw_edges())
    assert sorted(stats["changed"]) == discover_files(graph.root)


def test_looms_own_database_files_are_never_indexed(repo_root: str) -> None:
    """break4 cli-F2: the default layout puts `.loom.sqlite3` INSIDE the served checkout;
    indexing it (and its WAL, rewritten on every gate decision) kept `index_age`
    permanently stale and minted meaningless File nodes."""
    for f in (".loom.sqlite3", ".loom.sqlite3-wal", ".loom.sqlite3-shm"):
        with open(os.path.join(repo_root, f), "w") as fh:
            fh.write("x")
    assert not [p for p in discover_files(repo_root) if ".loom.sqlite3" in p]
