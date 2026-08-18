"""MULTIREPO-SPEC §1-§3 + §6 — one loom server, many repositories.

The correctness heart is `test_the_same_ref_in_two_repos_is_two_independent_claims`:
`ids.node_id` salts on `repo`, so `svc.py` in repo A and `svc.py` in repo B are DIFFERENT
nodes. Two fixture repos deliberately share that one file name (and share nothing else —
every symbol in `fixtures/pyrepo2` is absent from `fixtures/pyrepo`), so a server that
dropped the salt, or a route that ignored `body.repo`, fails here and only here.

Integration rig follows the frozen pattern of `test_concurrency.py`: pre-index a throwaway
db, then run the REAL server as a subprocess with PIPE logs and a try/finally terminate.
`--db` is always explicit — the multi-root default would write into the fixture tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import SPEC
from mcp import Client

from loom.cli.main import main as cli_main
from loom.indexer.walk import index_repo
from loom.server.app import build_server, parse_repo_roots
from loom.server.db import connect, init_db, log_event
from loom.server.ids import node_id

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PYREPO = str(FIXTURES / "pyrepo")
PYREPO2 = str(FIXTURES / "pyrepo2")

# The one ref the two fixture repos share, by construction (see fixtures/pyrepo2/svc.py).
SHARED = "svc.py"


# --------------------------------------------------------------------------- rig


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:                        # pragma: no cover
            raise RuntimeError(f"server died: {proc.communicate()[1]}")
        try:
            socket.create_connection(("127.0.0.1", port), 0.25).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"server did not open port {port}")   # pragma: no cover


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200
        return json.loads(r.read())


def post(base: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
        return json.loads(r.read())


def tool(base: str, name: str, args: dict[str, Any]) -> dict:
    """One in-process-free MCP call over the streamable-http endpoint."""
    async def go() -> dict:
        async with Client(base + "/mcp") as c:
            return (await c.call_tool(name, args)).structured_content
    return asyncio.run(go())


def declare(base: str, agent: str, repo: str, targets: list[str]) -> dict:
    return tool(base, "declare_plan", {"agent": agent, "title": f"{agent} on {repo}",
                                       "spec_md": SPEC, "write_targets": targets,
                                       "repo": repo})


@pytest.fixture()
def two_repo_db(tmp_path) -> str:
    """A db holding BOTH fixture repos, indexed under the salts `alpha` and `beta`."""
    db = str(tmp_path / "multi.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        index_repo(conn, "alpha", PYREPO, changed_only=False)
        index_repo(conn, "beta", PYREPO2, changed_only=False)
    finally:
        conn.close()
    return db


@pytest.fixture()
def two_repo_server(two_repo_db: str) -> Iterator[tuple[str, str]]:
    """(base_url, db_path) of a subprocess server serving alpha=pyrepo, beta=pyrepo2."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app",
         "--repo-root", f"alpha={PYREPO}", "--repo-root", f"beta={PYREPO2}",
         "--db", two_repo_db, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}", two_repo_db
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                   # pragma: no cover
            proc.kill()


# --------------------------------------------------------------------------- §6.b/c
# THE multi-repo correctness property. Written first, on purpose.


def test_the_same_ref_in_two_repos_is_two_independent_claims(two_repo_server) -> None:
    """§6.b + §6.c: `svc.py` names a real node in BOTH served repos.

    (b) aria claiming it in alpha must not block bo claiming it in beta — the ids are
    salted apart, so there is no shared ground to contend for.
    (c) afterwards the SAME wire body, differing only in `repo`, must be denied in alpha
    (aria's foreign claim) and allowed in beta (bo's own plan). A /gate that ignored
    `body.repo` would answer identically for both.
    """
    base, _db = two_repo_server

    a = declare(base, "aria", "alpha", [SHARED])
    b = declare(base, "bo", "beta", [SHARED])
    assert a["ok"] is True, a
    assert b["ok"] is True, b                       # no conflict across the repo boundary
    assert a["warnings"] == [] and b["warnings"] == []
    assert a["claimed_write"] == [node_id("alpha", SHARED, "")]
    assert b["claimed_write"] == [node_id("beta", SHARED, "")]
    assert a["claimed_write"] != b["claimed_write"]  # the salt, made visible

    body = {"agent": "bo", "path": SHARED, "qualname": None, "tool_name": "Edit"}
    denied = post(base, "/gate", dict(body, repo="alpha"))
    allowed = post(base, "/gate", dict(body, repo="beta"))

    assert (denied["decision"], denied["case"]) == ("deny", "foreign_claim")
    assert denied["plan_id"] == a["plan_id"]
    assert 'is claimed by "aria"' in denied["message"]
    assert (allowed["decision"], allowed["case"]) == ("allow", "in_plan")
    assert allowed["plan_id"] == b["plan_id"] and allowed["message"] == ""
    assert denied["node_id"] != allowed["node_id"]


def test_the_two_fixture_repos_share_a_path_and_no_symbol() -> None:
    """The premise of the test above, asserted rather than assumed."""
    def symbols(root: str) -> set[str]:
        db_syms: set[str] = set()
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".py"):
                    src = Path(dirpath, name).read_text(encoding="utf-8")
                    db_syms |= {line.split("(")[0].split()[-1]
                                for line in src.splitlines()
                                if line.lstrip().startswith(("def ", "class ", "async def "))}
        return db_syms

    assert symbols(PYREPO) & symbols(PYREPO2) == set()
    assert Path(PYREPO, SHARED).is_file() and Path(PYREPO2, SHARED).is_file()


# --------------------------------------------------------------------------- §6.a


def test_health_lists_every_served_repo_and_keeps_the_repo_key(two_repo_server) -> None:
    base, _db = two_repo_server
    assert get(base + "/health") == {"ok": True, "repo": "alpha", "repos": ["alpha", "beta"],
                                     "auth": "open"}          # D8: mode, always named


def test_an_unserved_or_absent_repo_on_the_gate_is_advisory(two_repo_server) -> None:
    base, _db = two_repo_server
    declare(base, "aria", "alpha", [SHARED])
    body = {"agent": "bo", "path": SHARED, "qualname": None, "tool_name": "Edit"}
    for variant in ({"repo": "gamma"}, {"repo": ""}, {}):
        d = post(base, "/gate", dict(body, **variant))
        assert (d["decision"], d["case"]) == ("allow", "unindexed"), variant
        assert d["message"] == "" and d["node_id"] is None and d["plan_id"] is None


def test_a_malformed_gate_body_still_gets_the_five_frozen_keys(two_repo_server) -> None:
    """P2-3: the route promises "always HTTP 200 with exactly the five keys". An unparseable
    body, a JSON array, a scalar and a non-string qualname all used to raise, so the route
    500'd — which the hook fails open on, silently, while the server looks healthy."""
    base, _db = two_repo_server
    five = {"decision", "case", "message", "node_id", "plan_id"}
    for raw in (b"", b"not json at all", b"[1, 2, 3]", b'"a string"', b"7"):
        req = urllib.request.Request(base + "/gate", data=raw,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200, raw
            d = json.loads(r.read())
        assert set(d) == five, raw
        assert (d["decision"], d["case"]) == ("allow", "unindexed"), raw

    # A non-string qualname reaches `prefix_candidates` and must be read as "no symbol".
    d = post(base, "/gate", {"agent": "bo", "repo": "alpha", "path": SHARED,
                             "qualname": ["not", "a", "string"], "tool_name": "Edit"})
    assert set(d) == five and d["decision"] in ("allow", "deny")


# --------------------------------------------------------------------------- §6.d


def test_state_scopes_nodes_and_events_and_advertises_every_repo(two_repo_server) -> None:
    base, _db = two_repo_server
    declare(base, "aria", "alpha", [SHARED])
    declare(base, "bo", "beta", [SHARED])

    alpha = get(base + "/state?repo=alpha")
    beta = get(base + "/state?repo=beta")
    for state in (alpha, beta):
        assert state["ok"] is True
        assert state["repos"] == ["alpha", "beta"]

    assert alpha["repo"] == "alpha" and beta["repo"] == "beta"
    alpha_paths = {n["path"] for n in alpha["nodes"]}
    beta_paths = {n["path"] for n in beta["nodes"]}
    assert "up.py" in alpha_paths and "up.py" not in beta_paths
    assert "ledger.py" in beta_paths and "ledger.py" not in alpha_paths
    assert alpha["counts"]["nodes"] > beta["counts"]["nodes"] > 0

    assert [p["agent"] for p in alpha["plans"]] == ["aria"]
    assert [p["agent"] for p in beta["plans"]] == ["bo"]
    assert alpha["agents"] == ["aria"] and beta["agents"] == ["bo"]
    assert {e["actor"] for e in alpha["events"]} == {"aria", "indexer"}
    assert {e["actor"] for e in beta["events"]} == {"bo", "indexer"}
    assert any(e["detail"].startswith("alpha:") for e in alpha["events"])
    assert not any(e["detail"].startswith("beta:") for e in alpha["events"])

    # missing / unknown ?repo= falls back to the first served repo (back-compat).
    assert get(base + "/state")["repo"] == "alpha"
    assert get(base + "/state?repo=nope")["repo"] == "alpha"


def test_state_shows_pre_migration_event_rows_in_every_repo(two_repo_server) -> None:
    """§3, documented: history written before the migration carries repo '' and is shown
    in every repo's feed rather than vanishing from all of them."""
    base, db = two_repo_server
    conn = connect(db)
    try:
        conn.execute("INSERT INTO events (ts, actor, action, detail) "
                     "VALUES ('2020-01-01T00:00:00Z', 'legacy', 'declared', 'lm-old')")
        assert [r["repo"] for r in conn.execute(
            "SELECT repo FROM events WHERE actor='legacy'")] == [""]
    finally:
        conn.close()
    for name in ("alpha", "beta"):
        feed = get(f"{base}/state?repo={name}")["events"]
        assert any(e["actor"] == "legacy" for e in feed), name


# --------------------------------------------------------------------------- §6.e


@pytest.fixture()
def fake_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A `loom-gate` stand-in that exits 2, so init's post-write verification is honest."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gate = bindir / "loom-gate"
    gate.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    gate.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return str(gate)


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["loom", *argv])
    cli_main()


def test_init_against_a_multi_repo_server_requires_a_repo_name(
        two_repo_server, tmp_path, monkeypatch, fake_gate, capsys) -> None:
    base, _db = two_repo_server
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "checkout"
    root.mkdir()

    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
                "--repo-root", str(root))
    err = capsys.readouterr().err
    assert "alpha" in err and "beta" in err                 # names listed verbatim
    assert not (root / ".claude").exists()                  # nothing half-written

    with pytest.raises(SystemExit):                         # a name it does not serve
        run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
                "--repo-root", str(root), "--repo", "gamma")
    assert "alpha" in capsys.readouterr().err

    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--repo", "beta")
    cfg = (root / ".claude" / "loom.toml").read_text(encoding="utf-8")
    assert 'repo = "beta"' in cfg and f'server_url = "{base}"' in cfg
    assert "initialized for repo 'beta'" in capsys.readouterr().out


def test_init_still_auto_selects_the_only_repo_of_a_single_repo_server(
        tmp_path, monkeypatch, fake_gate, capsys) -> None:
    """Back-compat: one served repo -> `--repo` stays optional (today's behavior)."""
    db = str(tmp_path / "one.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        index_repo(conn, "solo", PYREPO2, changed_only=False)
    finally:
        conn.close()
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--repo-root", f"solo={PYREPO2}",
         "--db", db, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_port(port, proc)
        base = f"http://127.0.0.1:{port}"
        assert get(base + "/health") == {"ok": True, "repo": "solo", "repos": ["solo"],
                                         "auth": "open"}
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        root = tmp_path / "checkout"
        root.mkdir()
        run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
                "--repo-root", str(root))
        assert 'repo = "solo"' in (root / ".claude" / "loom.toml").read_text(encoding="utf-8")
        assert "initialized for repo 'solo'" in capsys.readouterr().out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                   # pragma: no cover
            proc.kill()


# --------------------------------------------------------------------------- §6 unit: CLI args


def test_parse_repo_roots_accepts_both_forms() -> None:
    assert parse_repo_roots([PYREPO]) == {"pyrepo": PYREPO}
    assert parse_repo_roots([f"alpha={PYREPO}"]) == {"alpha": PYREPO}
    assert parse_repo_roots([f"alpha={PYREPO}", PYREPO2]) == {"alpha": PYREPO,
                                                              "pyrepo2": PYREPO2}
    # ordered: the first root is the default repo and the default db location.
    assert list(parse_repo_roots([f"b={PYREPO2}", f"a={PYREPO}"])) == ["b", "a"]
    # relative roots are absolutised; a trailing slash does not change the basename.
    assert parse_repo_roots([PYREPO + "/"]) == {"pyrepo": PYREPO}
    assert parse_repo_roots(["."]) == {os.path.basename(os.getcwd()): os.getcwd()}


def test_parse_repo_roots_rejects_ambiguous_and_empty_input() -> None:
    for bad in ([], [""], [f"={PYREPO}"], ["alpha="], ["  =" + PYREPO]):
        with pytest.raises(ValueError):
            parse_repo_roots(bad)                     # type: ignore[arg-type]


def test_duplicate_repo_names_are_a_hard_error() -> None:
    with pytest.raises(ValueError) as exc:
        parse_repo_roots([f"alpha={PYREPO}", f"alpha={PYREPO2}"])
    assert "alpha" in str(exc.value)
    with pytest.raises(ValueError):                   # same basename, two checkouts
        parse_repo_roots([PYREPO, PYREPO])


def test_repo_flag_is_only_valid_with_one_unprefixed_root() -> None:
    assert parse_repo_roots([PYREPO], repo="teamrepo") == {"teamrepo": PYREPO}
    with pytest.raises(ValueError):                   # --repo + several roots
        parse_repo_roots([PYREPO, PYREPO2], repo="teamrepo")
    with pytest.raises(ValueError):                   # --repo + an explicit NAME=
        parse_repo_roots([f"alpha={PYREPO}"], repo="teamrepo")


def test_serve_indexes_every_repo_at_boot(tmp_path, monkeypatch, capsys) -> None:
    """§1: one `loom: indexed {"repo": NAME, ...}` line per repo, before the server runs."""
    import loom.server.app as app_mod

    db = str(tmp_path / "boot.sqlite3")
    monkeypatch.setattr(app_mod, "serve", lambda *a, **k: None)
    run_cli(monkeypatch, "serve", "--repo-root", f"alpha={PYREPO}",
            "--repo-root", f"beta={PYREPO2}", "--db", db)
    lines = [json.loads(ln.split("loom: indexed ", 1)[1])
             for ln in capsys.readouterr().out.splitlines() if "loom: indexed" in ln]
    assert [ln["repo"] for ln in lines] == ["alpha", "beta"]
    assert all(ln["nodes"] > 0 and ln["files"] > 0 for ln in lines)

    conn = connect(db)
    try:
        salts = [r["repo"] for r in conn.execute("SELECT DISTINCT repo FROM nodes ORDER BY repo")]
    finally:
        conn.close()
    assert salts == ["alpha", "beta"]


def test_serve_rejects_duplicate_names_and_repo_with_many_roots(
        tmp_path, monkeypatch, capsys) -> None:
    db = str(tmp_path / "never.sqlite3")
    for argv in ((f"alpha={PYREPO}", f"alpha={PYREPO2}"),):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "serve", "--repo-root", argv[0], "--repo-root", argv[1],
                    "--db", db)
        assert "alpha" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "serve", "--repo-root", PYREPO, "--repo-root", PYREPO2,
                "--repo", "teamrepo", "--db", db)
    assert "--repo" in capsys.readouterr().err
    assert not os.path.exists(db)                     # dies before touching storage


# --------------------------------------------------------------------------- §6 unit: migration


def test_events_repo_migration_is_idempotent_and_back_compatible(tmp_path) -> None:
    """§3: `ALTER TABLE events ADD COLUMN repo` runs once, on a pre-migration database."""
    db = str(tmp_path / "old.sqlite3")
    con = connect(db)
    try:                                              # the OLD (pre-migration) events table
        con.execute("CREATE TABLE events (ts TEXT NOT NULL, actor TEXT NOT NULL, "
                    "action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")
        con.execute("INSERT INTO events VALUES ('2020-01-01T00:00:00Z','aria','declared','lm-x')")
    finally:
        con.close()

    for _ in range(3):                                # idempotent: never a duplicate column
        init_db(db)

    con = connect(db)
    try:
        cols = [r["name"] for r in con.execute("PRAGMA table_info(events)")]
        assert cols.count("repo") == 1
        assert con.execute("SELECT repo FROM events WHERE actor='aria'").fetchone()["repo"] == ""
        log_event(con, "bo", "denied", "d")                       # old 4-arg call site
        log_event(con, "cy", "allowed", "d", repo="alpha")        # new one
        rows = dict(con.execute("SELECT actor, repo FROM events WHERE actor IN ('bo','cy')"))
    finally:
        con.close()
    assert rows == {"bo": "", "cy": "alpha"}


def test_a_fresh_database_ships_the_repo_column(tmp_path) -> None:
    db = str(tmp_path / "fresh.sqlite3")
    init_db(db)
    con = connect(db)
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}
    finally:
        con.close()
    assert "repo" in cols


# --------------------------------------------------------------------------- §6 unit: tools


@pytest.fixture()
def two_repo_mcp(graph_db: str, tmp_path):
    """An in-process server serving the seeded `demo` graph plus an empty second repo."""
    return build_server(graph_db, {"demo": str(tmp_path), "other": str(tmp_path)})


def call(mcp, name: str, args: dict[str, Any]) -> dict:
    async def go() -> dict:
        async with Client(mcp) as c:
            return (await c.call_tool(name, args)).structured_content
    return asyncio.run(go())


def test_tools_default_to_the_first_served_repo(two_repo_mcp) -> None:
    """§2: `repo: str = ""` resolves to served[0] — single-repo behavior preserved."""
    default = call(two_repo_mcp, "resolve_nodes", {"queries": ["authenticate"]})
    explicit = call(two_repo_mcp, "resolve_nodes", {"queries": ["authenticate"],
                                                    "repo": "demo"})
    assert default == explicit
    assert default["resolved"][0]["matches"][0]["ref"] == "svc.py::AuthService/authenticate"

    # ...and the OTHER served repo is a real, separate namespace: same query, no match.
    other = call(two_repo_mcp, "resolve_nodes", {"queries": ["authenticate"], "repo": "other"})
    assert other["resolved"][0]["matches"] == []

    checked = call(two_repo_mcp, "check", {"agent": "aria", "node": "models.py::User"})
    assert checked["allow"] is False and checked["case"] == "no_plan"


def test_an_unserved_repo_returns_the_unknown_repo_shape(two_repo_mcp) -> None:
    """§2: additive `served` list, so the caller can fix the argument without a round trip."""
    expected = {"ok": False, "reason": "unknown_repo", "served": ["demo", "other"]}
    assert call(two_repo_mcp, "resolve_nodes", {"queries": [], "repo": "nope"}) == expected
    assert call(two_repo_mcp, "list_claims", {"repo": "nope"}) == expected
    assert call(two_repo_mcp, "declare_plan", {"agent": "a", "title": "t", "spec_md": SPEC,
                                               "write_targets": ["models.py::User"],
                                               "repo": "nope"}) == expected
    assert call(two_repo_mcp, "check", {"agent": "a", "node": "models.py::User",
                                        "repo": "nope"}) == expected


def test_a_plan_declared_in_one_repo_is_invisible_to_the_other(two_repo_mcp) -> None:
    granted = call(two_repo_mcp, "declare_plan", {"agent": "aria", "title": "t",
                                                  "spec_md": SPEC,
                                                  "write_targets": ["models.py::User"]})
    assert granted["ok"] is True
    assert [c["plan_id"] for c in call(two_repo_mcp, "list_claims", {})["claims"]] == \
        [granted["plan_id"]]
    assert call(two_repo_mcp, "list_claims", {"repo": "other"})["claims"] == []
