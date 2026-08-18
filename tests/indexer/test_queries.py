"""M1 acceptance — the CALLS/IMPORTS checklist (27 known edges, budget is 20).

The checklist is a literal: every row names a real call site or import statement in
`tests/fixtures/pyrepo` and the resolution rule it exercises.
"""

from __future__ import annotations

import pytest

from loom.indexer.queries.python import (
    Q_CALL,
    Q_IMPORT,
    Q_IMPORT_FROM,
    Resolver,
    captures,
    dotted_parts,
)

REPO = "fx"

# (src_ref, dst_ref, rule exercised)
EXPECTED_CALLS = [
    ("up.py::surface", "up.py::deep", "bare name, same file"),
    ("lib/util.py::make_token", "lib/util.py::helper", "bare name, same file"),
    ("models.py::AuthResult", "lib/util.py::make_tag", "class-body call attributes to the Class node"),
    ("svc.py::AuthService", "lib/util.py::make_tag", "class-body call via `import lib.util as lu`"),
    ("svc.py::AuthService", "svc.py::route", "decorator call on a method attributes to the Class"),
    ("svc.py::AuthService/authenticate", "svc.py::Depends", "call site in a parameter default"),
    ("svc.py::AuthService/authenticate", "lib/util.py::make_token", "aliased dotted import `lu.f()`"),
    ("svc.py::AuthService/authenticate", "lib/util.py::helper", "call inside a closure rolls up"),
    ("svc.py::AuthService/verify", "models.py::AuthResult", "`Foo()` resolves to the class node"),
    ("svc.py::AuthService/verify", "svc.py::AuthService/salt", "`self.m()` inside a method"),
    ("svc.py::AuthService/Session/open", "svc.py::AuthService/salt", "`Class.method()` prefix, nested class"),
    ("svc.py::bootstrap", "svc.py::AuthService", "local class constructor"),
    ("svc.py::bootstrap", "up.py::deep", "`from up import deep`"),
    ("sub/deep.py::Digger/dig", "sub/sibling.py::probe", "`from . import sibling` then `sibling.probe()`"),
    ("sub/deep.py::twin", "up.py::deep", "`from ..up import deep` (relative climb)"),
    ("sub/sibling.py::probe", "up.py::surface", "function-local `from up import surface`"),
    ("sub/sibling.py::blend", "lib/util.py::helper", "`import lib.util as lu` then `lu.helper()`"),
]

EXPECTED_IMPORTS = [
    ("models.py", "lib/util.py", "from lib.util import make_tag"),
    ("svc.py", "lib/util.py", "import lib.util as lu"),
    ("svc.py", "models.py", "from models import AuthResult"),
    ("svc.py", "up.py", "from up import deep"),
    ("svc.py", "sub/deep.py", "TYPE_CHECKING-guarded import STILL emits the edge"),
    ("sub/deep.py", "sub/__init__.py", "from . import sibling -> the package"),
    ("sub/deep.py", "sub/sibling.py", "from . import sibling -> the submodule file"),
    ("sub/deep.py", "up.py", "from ..up import deep"),
    ("sub/sibling.py", "lib/util.py", "import lib.util as lu"),
    ("sub/sibling.py", "up.py", "function-local from up import surface"),
]

def test_checklist_is_at_least_twenty_edges() -> None:
    assert len(EXPECTED_CALLS) + len(EXPECTED_IMPORTS) >= 20


@pytest.mark.parametrize("src,dst,rule", EXPECTED_CALLS, ids=[f"{s}->{d}" for s, d, _ in EXPECTED_CALLS])
def test_expected_call_edge(graph, src: str, dst: str, rule: str) -> None:
    assert (src, dst) in graph.edges("CALLS"), rule


@pytest.mark.parametrize("src,dst,rule", EXPECTED_IMPORTS, ids=[f"{s}->{d}" for s, d, _ in EXPECTED_IMPORTS])
def test_expected_import_edge(graph, src: str, dst: str, rule: str) -> None:
    assert (src, dst) in graph.edges("IMPORTS"), rule


def test_no_unexpected_call_or_import_edges(graph) -> None:
    """Precision, not just recall: the resolver invents nothing."""
    assert graph.edges("CALLS") == {(s, d) for s, d, _ in EXPECTED_CALLS}
    assert graph.edges("IMPORTS") == {(s, d) for s, d, _ in EXPECTED_IMPORTS}


def test_imports_are_file_to_file_only(graph) -> None:
    for src, dst in graph.edges("IMPORTS"):
        assert "::" not in src and "::" not in dst


def test_calls_never_double_attribute_a_site(graph) -> None:
    """One CALLS edge per resolved site — `lu.make_tag()` in the class body is not
    also charged to the File node, and the closure call is not charged twice."""
    calls = graph.edges("CALLS")
    assert ("svc.py", "lib/util.py::make_tag") not in calls
    assert ("svc.py", "svc.py::Depends") not in calls
    assert ("svc.py::AuthService", "lib/util.py::helper") not in calls


def test_external_and_uninferable_targets_are_dropped(graph) -> None:
    dsts = {d for _s, d in graph.edges("CALLS")}
    assert not [d for d in dsts if "timedelta" in d]         # stdlib import, not in the repo
    assert not [d for d in dsts if d.endswith("::_finish")]  # closures are never nodes
    # `Depends(get_db)`: `get_db` is PASSED, not called — a reference is not a call site.
    assert "svc.py::get_db" not in dsts
    # CUT feature (assignment-type inference): `svc = AuthService(); ...` yields no edge
    # from `bootstrap` through `svc`, only the direct constructor call.
    assert ("svc.py::bootstrap", "svc.py::AuthService") in graph.edges("CALLS")


def test_wildcard_imports_are_ignored(graph) -> None:
    graph.write("wild.py", "from lib.util import *\n\n\ndef pull():\n    return helper('x')\n")
    graph.reindex()
    assert ("wild.py", "lib/util.py") not in graph.edges("IMPORTS")
    # `helper` is still reachable through the unique bare-name fallback, not through `*`.
    assert ("wild.py::pull", "lib/util.py::helper") in graph.edges("CALLS")


def test_ambiguous_bare_names_are_dropped(graph) -> None:
    graph.write("dup_a.py", "def collide():\n    return 1\n")
    graph.write("dup_b.py", "def collide():\n    return 2\n")
    graph.write("caller.py", "def go():\n    return collide()\n")
    graph.reindex()
    assert not [d for _s, d in graph.edges("CALLS") if d.endswith("::collide")]


def test_query_helpers_and_dotted_parts() -> None:
    """`captures` for single-capture queries; `dotted_parts` for the callee expression."""
    from tree_sitter import Parser

    from loom.indexer.queries.python import LANGUAGE

    tree = Parser(LANGUAGE).parse(b"import os\nfrom a import b\nx.y.z()\nq()\n(lambda: 1)()\n")
    assert len(captures(Q_IMPORT, tree.root_node)["stmt"]) == 1
    assert len(captures(Q_IMPORT_FROM, tree.root_node)["stmt"]) == 1
    calls = captures(Q_CALL, tree.root_node)["reference.call"]
    parts = [dotted_parts(c.child_by_field_name("function")) for c in calls]
    assert ["x", "y", "z"] in parts
    assert ["q"] in parts
    assert None in parts  # a lambda call is not a dotted name -> never guessed


def test_resolver_drops_unknown_modules() -> None:
    r = Resolver()
    r.add_known("a.py", {"f": "Function"}, [(0, 10, "f")])
    assert r.resolve_imports("a.py") == []
    assert r._module_path("a.py", "nowhere.at.all", 0) is None
    assert r._module_path("a.py", "x", 9) is None  # climbing past the repo root
