"""Dashboard endpoints: / serves the page, /state serves the poll feed (fail-soft)."""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import server_process

from loom.cli.main import _templates
from loom.server.claims import SWEEP_GRACE_S, declare_plan, sweep
from loom.server.db import connect, immediate, iso, now_s

SPEC = """# Spec: Dashboard test plan

## Goal *(mandatory)*
Exercise the state feed. Two sentences to satisfy the validator shape.

## Write targets *(mandatory)*
- svc.py::AuthService/authenticate

## New/changed interfaces *(mandatory)*
None.

## Assumes *(mandatory)*
None.

## Out of scope *(mandatory)*
Everything else.
"""


@pytest.fixture()
def live_server(graph_db, tmp_path) -> Iterator[tuple[str, str]]:
    """(base_url, db_path) of a subprocess server over the seeded fixture graph."""
    with server_process("--repo-root", str(tmp_path), "--repo", "demo",
                        "--db", graph_db) as (port, _p):
        yield f"http://127.0.0.1:{port}", graph_db


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_dashboard_page_serves(live_server):
    base, _db = live_server
    status, body = _get(base + "/")
    assert status == 200
    assert "<title>loom</title>" in body
    assert "the fabric" in body  # signature panel present


def test_the_repo_switcher_is_wired_but_stays_invisible_on_a_one_repo_server(live_server):
    """MULTIREPO-SPEC §5: the page carries the switcher; `state.repos` decides whether it
    draws. A single served repo must leave today's header exactly as it was, so the whole
    feature reduces to one empty container plus a poll URL that can carry `?repo=`."""
    base, _db = live_server
    page = _get(base + "/")[1]
    assert 'id="repos"' in page                                   # container present...
    assert "repos.length > 1" in page                             # ...drawn only for many
    assert '"/state?repo=" + encodeURIComponent(selectedRepo)' in page

    state = json.loads(_get(base + "/state")[1])
    assert state["repos"] == ["demo"] and state["repo"] == "demo"  # /state plumbing


def test_the_fixture_repo_is_below_the_focus_threshold_and_untruncated(live_server):
    """ITERATION-2-SPEC §1/§2, the cheap half. Focus mode is pure browser JS, so CI asserts
    the two things it can: the page SHIPS the frozen thresholds and copy, and the fixture
    repo's own `/state` puts it on the FULL-VIEW side of every switch — 4 files is under the
    >12 threshold and nothing is capped, so neither header note can appear. (The conduit-
    sized visual is the orchestrator's screenshot job, not CI's.)"""
    base, _db = live_server
    page = _get(base + "/")[1]
    assert "const FOCUS_FILE_THRESHOLD = 12;" in page                  # threshold frozen at 12
    assert "const BEAD_CAP = 14;" in page                              # 14 beads per thread
    assert "files with active claims surface automatically" in page    # exact scope-note copy
    assert "graph truncated — showing " in page                        # §2 copy, cap not hardcoded
    assert "truncated to first 600" not in page        # the cap is read from /state, not typed
    assert "A hollow bead is unclaimed." in page                       # the static legend
    assert "index behind working tree" in page                         # exact U2 copy
    assert "loom index --changed refreshes" in page                    # ...naming its own fix
    assert 'id="nNodes"' in page                                       # the totals-fed tile

    state = json.loads(_get(base + "/state")[1])
    files = {n["path"] for n in state["nodes"]}
    assert len(files) == 4 and len(files) <= 12          # ≤ 12 files → today's full view
    assert state["truncated"] == {"nodes": False, "edges": False}   # → no truncation note
    assert state["totals"]["nodes"] == state["counts"]["nodes"]     # tile == honest COUNT(*)
    # U2: the seeded fixture graph was INSERTed, never indexed, so there is no index event
    # to be behind — the note stays off and the header keeps its frozen single line.
    assert state["index_age"] == {"indexed_at": None, "dirty_files": 0, "stale": False}


def test_state_shape_and_claims(live_server):
    base, db = live_server
    conn = connect(db)
    row = conn.execute("SELECT repo, id FROM nodes WHERE qualname LIKE '%authenticate%'").fetchone()
    repo = row["repo"]
    with immediate(conn):
        granted = declare_plan(
            conn, agent="dash-agent", repo=repo, branch="main", title="Dashboard test plan",
            spec_md=SPEC, write_targets=["svc.py::AuthService/authenticate"], assumes=[],
            ttl_s=600, now=now_s())
    assert granted["ok"], granted

    status, body = _get(base + "/state")
    assert status == 200
    state = json.loads(body)
    assert state["ok"] is True
    assert state["repo"] == repo
    assert isinstance(state["now"], float)
    assert state["counts"]["nodes"] > 0 and state["counts"]["edges"] > 0
    assert {n["id"] for n in state["nodes"]} >= {row["id"]}
    assert any(p["agent"] == "dash-agent" for p in state["plans"])
    assert "spec_md" not in state["plans"][0]  # deliberately not shipped per poll
    claimed = {c["node_id"] for c in state["claims"]}
    assert row["id"] in claimed  # the declared target is visibly claimed
    assert "dash-agent" in state["agents"]
    assert any(e["action"] == "declared" for e in state["events"])


def test_a_ttl_sweep_does_not_grow_a_system_actor_chip(live_server):
    """E22: `sweep` logs its `expired` rows as the SYSTEM actor `loom` (claims.py), and the
    agent-chip list was built from every event actor except `indexer`. So the first TTL
    sweep made a phantom teammate named "loom" appear on the dashboard forever.

    The event itself must stay visible — the gate feed is how a human sees expiries — it is
    only the *agent chips* that must exclude system actors.
    """
    base, db = live_server
    conn = connect(db)
    now = now_s()
    # declare_plan cannot mint an already-expired plan; seed the row the sweep will find.
    with immediate(conn):
        conn.execute(
            "INSERT INTO plans (id,agent,repo,branch,title,spec_md,status,created,updated,"
            "ttl_expires) VALUES (?,?,?,?,?,?,'active',?,?,?)",
            ("lm-stale1", "ghost-agent", "demo", "", "stale plan", SPEC,
             iso(now), iso(now), now - SWEEP_GRACE_S - 100))
        swept = sweep(conn, "demo", now)
    assert swept == ["lm-stale1"], swept
    conn.close()

    state = json.loads(_get(base + "/state")[1])
    assert state["ok"] is True
    assert "loom" not in state["agents"], f"system actor leaked into the chips: {state['agents']}"
    assert "indexer" not in state["agents"]
    # ... while the audit trail of the sweep is still on the feed.
    assert any(e["action"] == "expired" and e["actor"] == "loom" for e in state["events"])


def _mini_state(**over) -> dict:
    """The smallest /state payload `render()` accepts, with a real claim mix."""
    nodes = [
        {"id": "n-f1", "path": "svc.py", "qualname": "", "kind": "File"},
        {"id": "n-c1", "path": "svc.py", "qualname": "AuthService", "kind": "Class"},
        {"id": "n-m1", "path": "svc.py", "qualname": "AuthService/authenticate",
         "kind": "Function"},
        {"id": "n-b1", "path": "svc.py", "qualname": "bootstrap", "kind": "Function"},
    ]
    base = {
        "ok": True, "repo": "demo", "repos": ["demo"], "now": 1_787_000_000.0,
        "counts": {"nodes": 4, "edges": 3, "plans": 1, "claims": 2},
        "totals": {"nodes": 4, "edges": 3},
        "truncated": {"nodes": False, "edges": False},
        "index_age": {"indexed_at": None, "dirty_files": 0, "stale": False},
        "nodes": nodes,
        "edges": [{"src": "n-f1", "dst": "n-c1", "kind": "CONTAINS"},
                  {"src": "n-c1", "dst": "n-m1", "kind": "CONTAINS"},
                  {"src": "n-b1", "dst": "n-c1", "kind": "CALLS"}],
        "plans": [{"id": "lm-x1", "agent": "aria", "title": "boot work",
                   "created": "2026-08-19T10:00:00Z", "ttl_expires": 1_787_001_800.0}],
        "claims": [
            {"node_id": "n-b1", "plan_id": "lm-x1", "mode": "write", "origin": "target",
             "agent": "aria"},
            {"node_id": "n-c1", "plan_id": "lm-x1", "mode": "write", "origin": "expanded",
             "agent": "aria"},
        ],
        "events": [{"ts": "2026-08-19T10:00:00Z", "actor": "aria", "action": "declared",
                    "detail": "lm-x1"}],
        "agents": ["aria"],
    }
    base.update(over)
    return base


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="node not on PATH — the dashboard JS harness cannot run")
def test_dashboard_script_executes_over_real_payloads(tmp_path) -> None:
    """BC3-6: string-matching the page source proves the MARKUP exists, not that the
    script RUNS. Execute the page's own <script> under a stub DOM (tests/server/js/run.js)
    over three payload shapes and pin what it renders — including the §11.40 expanded-claim
    tooltip — and that rendering is deterministic."""
    states = {
        "normal": _mini_state(),
        "empty": _mini_state(plans=[], claims=[], events=[], agents=[],
                             counts={"nodes": 4, "edges": 3, "plans": 0, "claims": 0}),
        "stale_truncated": _mini_state(
            truncated={"nodes": True, "edges": True},
            index_age={"indexed_at": 1_786_999_000.0, "dirty_files": 3, "stale": True}),
    }
    sp = tmp_path / "states.json"
    sp.write_text(json.dumps(states), encoding="utf-8")
    page = Path(_templates()) / "dashboard.html"
    runjs = Path(__file__).parent / "js" / "run.js"
    runs = [subprocess.run([shutil.which("node"), str(runjs), str(page), str(sp)],
                           capture_output=True, text=True, timeout=60) for _ in range(2)]
    for r in runs:
        assert r.returncode == 0, r.stderr
    out = runs[0].stdout
    assert out == runs[1].stdout                       # rendering is deterministic
    assert "write (expanded — this node only) · aria" in out   # §11.40 tooltip branch ran
    assert "write · aria" in out                                # named claim stays plain
    assert "##### empty" in out and "##### stale_truncated" in out
