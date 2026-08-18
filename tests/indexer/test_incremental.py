"""M1 — incremental re-index: only the changed file's rows move, ids stay stable.

ACCEPTED MVP CAVEAT (falkordb C5), asserted explicitly in
`test_inbound_calls_from_unchanged_files_go_stale`: `delete_file_nodes` drops EVERY edge
touching a re-indexed file, and only changed files are re-resolved, so inbound CALLS from
unchanged files are lost until those files change. A full index always restores them.
"""

from __future__ import annotations

from collections import Counter

from loom.server.ids import node_id

REPO = "fx"


def _snapshot(graph) -> tuple[dict, Counter]:
    return graph.nodes(), Counter(graph.raw_edges())


def test_unchanged_repo_reindexes_nothing(graph) -> None:
    before = _snapshot(graph)
    stats = graph.reindex()
    assert stats["changed"] == []
    assert _snapshot(graph) == before


def test_only_the_mutated_files_rows_change(graph) -> None:
    """svc.py is a leaf: nothing in the repo imports it or calls into it."""
    nodes_before, edges_before = _snapshot(graph)
    svc_ids_before = {i for i, (p, _q, _k) in nodes_before.items() if p == "svc.py"}
    graph.write("svc.py", graph.read("svc.py") + "\n\ndef extra():\n    return deep(2)\n")

    stats = graph.reindex()
    assert stats["changed"] == ["svc.py"]

    nodes_after, edges_after = _snapshot(graph)
    svc_ids = svc_ids_before | {i for i, (p, _q, _k) in nodes_after.items() if p == "svc.py"}

    # Every node row that appeared or vanished belongs to svc.py.
    for nid in set(nodes_before) ^ set(nodes_after):
        assert nid in svc_ids, nodes_before.get(nid) or nodes_after.get(nid)
    # Every edge row that appeared or vanished touches an svc.py node.
    for src, dst, _kind in set((edges_after - edges_before) + (edges_before - edges_after)):
        assert src in svc_ids or dst in svc_ids

    # The new symbol landed, with its CONTAINS and its resolved CALLS edge.
    assert ("svc.py", "extra", "Function") in nodes_after.values()
    assert ("svc.py", "svc.py::extra") in graph.edges("CONTAINS")
    assert ("svc.py::extra", "up.py::deep") in graph.edges("CALLS")


def test_unrelated_node_ids_are_stable(graph) -> None:
    before = {i: v for i, v in graph.nodes().items() if v[0] != "svc.py"}
    graph.write("svc.py", graph.read("svc.py") + "\n\ndef extra():\n    return 1\n")
    graph.reindex()
    after = {i: v for i, v in graph.nodes().items() if v[0] != "svc.py"}
    assert after == before


def test_surviving_symbols_keep_their_ids_and_gain_new_hashes(graph) -> None:
    keep = node_id(REPO, "svc.py", "AuthService/authenticate")
    row_before = graph.conn.execute("SELECT body_hash, start_line FROM nodes WHERE id=?", (keep,)).fetchone()
    graph.write("svc.py", "# a new leading comment\n" + graph.read("svc.py"))
    graph.reindex()
    row_after = graph.conn.execute("SELECT body_hash, start_line FROM nodes WHERE id=?", (keep,)).fetchone()
    assert row_after is not None                                  # id is content-addressed, not positional
    assert row_after["body_hash"] == row_before["body_hash"]       # body text unchanged
    assert row_after["start_line"] == row_before["start_line"] + 1  # span is a locator aid only


def test_deleted_file_drops_its_nodes_and_edges(graph) -> None:
    import os

    doomed = {i for i, (p, _q, _k) in graph.nodes().items() if p == "sub/sibling.py"}
    assert doomed
    os.remove(os.path.join(graph.root, "sub", "sibling.py"))
    stats = graph.reindex()
    assert "sub/sibling.py" in stats["changed"]
    assert not (doomed & set(graph.nodes()))
    for src, dst, _kind in graph.raw_edges():
        assert src not in doomed and dst not in doomed


def test_claims_on_deleted_nodes_survive_as_tombstoned_orphans(graph) -> None:
    """§9.1: the indexer never deletes claim rows — the §2 LEFT JOIN judges them dead."""
    import os

    nid = node_id(REPO, "sub/sibling.py", "probe")
    graph.conn.execute(
        "INSERT INTO claims (node_id, plan_id, mode, created) VALUES (?,?,?,?)",
        (nid, "lm-orphan", "write", "2026-08-18T00:00:00Z"),
    )
    os.remove(os.path.join(graph.root, "sub", "sibling.py"))
    graph.reindex()
    assert graph.conn.execute("SELECT COUNT(*) c FROM claims WHERE node_id=?", (nid,)).fetchone()["c"] == 1
    assert graph.conn.execute("SELECT COUNT(*) c FROM nodes WHERE id=?", (nid,)).fetchone()["c"] == 0


def test_inbound_calls_from_unchanged_files_go_stale(graph) -> None:
    """The accepted falkordb C5 caveat, pinned so a future fix has to update this test."""
    assert ("sub/deep.py::Digger/dig", "sub/sibling.py::probe") in graph.edges("CALLS")
    graph.write("sub/sibling.py", graph.read("sub/sibling.py") + "\n\ndef tail():\n    return 1\n")
    graph.reindex()
    # sibling.py was re-minted; deep.py was not re-resolved, so its inbound edge is gone...
    assert ("sub/deep.py::Digger/dig", "sub/sibling.py::probe") not in graph.edges("CALLS")
    # ...and a full index restores it.
    graph.reindex(changed_only=False)
    assert ("sub/deep.py::Digger/dig", "sub/sibling.py::probe") in graph.edges("CALLS")


def test_changed_only_skips_untouched_files(graph) -> None:
    graph.write("up.py", graph.read("up.py").replace("value * 2", "value * 3"))
    stats = graph.reindex()
    assert stats["changed"] == ["up.py"]
    assert stats["nodes"] == len(graph.nodes())
