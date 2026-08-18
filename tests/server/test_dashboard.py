"""Dashboard endpoints: / serves the page, /state serves the poll feed (fail-soft)."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator

import pytest

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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--repo-root", str(tmp_path),
         "--repo", "demo", "--port", str(port), "--host", "127.0.0.1", "--db", graph_db],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.15)
        yield f"http://127.0.0.1:{port}", graph_db
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


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
