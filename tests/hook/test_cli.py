"""M3 — `loom init` registration (§7.5) and the local-DB admin verbs (§9.1).

Every path is tmp-scoped: HOME, the repo root, `.claude/settings.json`, and `loom-gate`
itself are all fakes created per test — no real settings file is ever touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from loom.cli.main import MATCHERS, SNIPPET_MARKER, _templates, main
from loom.server.db import connect, init_db, iso, now_s

USER_GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/audit.sh"}]}


@pytest.fixture()
def fake_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A `loom-gate` stand-in that exits 2, so init's post-write verification is deterministic."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gate = bindir / "loom-gate"
    gate.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    gate.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return str(gate)


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["loom", *argv])
    main()


def read_settings(repo_root: str) -> dict:
    return json.loads(Path(repo_root, ".claude", "settings.json").read_text(encoding="utf-8"))


def test_init_merges_into_existing_hooks_and_is_idempotent(
    stub, repo_root, home, fake_gate, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", home)
    settings = Path(repo_root, ".claude", "settings.json")
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [USER_GROUP]}}), encoding="utf-8")

    run_cli(monkeypatch, "init", "--server", stub.url, "--agent", "agent-a", "--repo-root", repo_root)
    groups = read_settings(repo_root)["hooks"]["PreToolUse"]
    assert groups[0] == USER_GROUP  # the user's own hook survives untouched
    assert [g["matcher"] for g in groups[1:]] == list(MATCHERS)
    assert all(g["hooks"][0]["command"] == fake_gate for g in groups[1:])
    assert groups[1]["hooks"][0] == {
        "type": "command", "command": fake_gate, "args": [],
        "timeout": 5, "statusMessage": "loom: checking claims",
    }

    run_cli(monkeypatch, "init", "--server", stub.url, "--repo-root", repo_root)
    again = read_settings(repo_root)["hooks"]["PreToolUse"]
    assert len(again) == 3
    assert [len(g["hooks"]) for g in again] == [1, 1, 1]
    assert "initialized for repo 'demo'" in capsys.readouterr().out


def test_init_appends_to_a_loom_group_that_already_holds_a_user_hook(
    stub, repo_root, home, fake_gate, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", home)
    other = {"type": "command", "command": "/usr/bin/other-gate"}
    settings = Path(repo_root, ".claude", "settings.json")
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"hooks": {"PreToolUse": [{"matcher": MATCHERS[0], "hooks": [other]}]}}),
        encoding="utf-8",
    )
    run_cli(monkeypatch, "init", "--server", stub.url, "--repo-root", repo_root)
    groups = read_settings(repo_root)["hooks"]["PreToolUse"]
    assert groups[0]["hooks"][0] == other
    assert groups[0]["hooks"][1]["command"] == fake_gate


def test_init_writes_config_and_appends_the_protocol_once(
    stub, repo_root, home, fake_gate, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", home)
    Path(repo_root, "CLAUDE.md").write_text("# demo repo\n", encoding="utf-8")
    run_cli(monkeypatch, "init", "--server", stub.url, "--agent", "aria", "--repo-root", repo_root)
    run_cli(monkeypatch, "init", "--server", stub.url, "--agent", "aria", "--repo-root", repo_root)

    cfg = Path(home, ".loom", "config.toml").read_text(encoding="utf-8")
    assert f'server_url = "{stub.url}"' in cfg
    assert 'agent = "aria"' in cfg
    assert 'repo = "demo"' in cfg  # echoed from GET /health — one spelling of the id salt (§11.19)
    assert f'repo_root = "{repo_root}"' in cfg

    claude_md = Path(repo_root, "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.startswith("# demo repo\n")
    assert claude_md.count("<!-- loom protocol v1") == 1
    assert "## loom — shared-repo coordination protocol" in claude_md


def test_init_does_not_re_append_over_an_older_marker_wording(
    stub, repo_root, home, fake_gate, monkeypatch
) -> None:
    """B9: the marker's prose may be reworded; idempotency keys on `SNIPPET_MARKER` only.

    A repo initialized by an older loom carries the OLD first line. Matching the whole
    first line would miss it and append a second protocol block on every re-init.
    """
    monkeypatch.setenv("HOME", home)
    old = ("<!-- loom protocol v1 — written by `loom init`; edits here are overwritten "
           "on re-init -->\n## loom — shared-repo coordination protocol\n")
    Path(repo_root, "CLAUDE.md").write_text("# demo repo\n\n" + old, encoding="utf-8")

    run_cli(monkeypatch, "init", "--server", stub.url, "--agent", "aria", "--repo-root", repo_root)

    claude_md = Path(repo_root, "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.count(SNIPPET_MARKER) == 1          # no second block appended
    assert claude_md.count("## loom — shared-repo coordination protocol") == 1
    assert "overwritten on re-init" in claude_md         # the human's file is left alone


def test_the_shipped_marker_starts_with_the_stable_prefix() -> None:
    """The guard is only stable if the template it guards actually carries the prefix."""
    with open(os.path.join(_templates(), "CLAUDE.snippet.md"), encoding="utf-8") as fh:
        first = fh.readline()
    assert first.startswith(SNIPPET_MARKER)


def test_init_refuses_an_unreachable_server(repo_root, home, fake_gate, monkeypatch) -> None:
    monkeypatch.setenv("HOME", home)
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "init", "--server", "http://127.0.0.1:9", "--repo-root", repo_root)
    assert not Path(repo_root, ".claude").exists()


@pytest.mark.parametrize("settings_json, complaint", [
    ({"hooks": {"PreToolUse": {"matcher": "Edit"}}}, '"hooks.PreToolUse"'),
    ({"hooks": [{"matcher": "Edit"}]}, '"hooks"'),
])
def test_init_refuses_a_malformed_settings_shape_instead_of_a_traceback(
    stub, repo_root, home, fake_gate, monkeypatch, capsys, settings_json, complaint
) -> None:
    """break3 journey-J4c/J4d: valid JSON, wrong SHAPE — and `loom init` crashed.

    `PreToolUse` as an object gave `AttributeError: 'dict' object has no attribute 'append'`;
    `hooks` as an array gave `'list' object has no attribute 'setdefault'`. Both are shapes a
    hand-edited settings.json really takes, and both dumped a traceback from the middle of a
    verb that is writing a checkout's files. The JSONDecodeError guard beside them has always
    said the right thing; these two shapes simply were not checked.
    """
    monkeypatch.setenv("HOME", home)
    settings = Path(repo_root, ".claude", "settings.json")
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(settings_json), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "init", "--server", stub.url, "--repo-root", repo_root)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "AttributeError" not in err
    assert complaint in err and "fix it by hand" in err
    assert json.loads(settings.read_text(encoding="utf-8")) == settings_json   # untouched


def test_empty_narrowing_flag_is_a_hard_error(stub, repo_root, home, fake_gate, monkeypatch) -> None:
    monkeypatch.setenv("HOME", home)
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "init", "--server", "", "--repo-root", repo_root)


@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """One node, one active plan, one write claim — enough for ls / show / release."""
    db = str(tmp_path / "loom.sqlite3")
    init_db(db)
    conn = connect(db)
    stamp = iso(now_s())
    conn.execute(
        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("n-node0001", "demo", "src/app.py", "AuthService/authenticate", "Function", "", "", 7, 9, stamp),
    )
    conn.execute(
        "INSERT INTO plans VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("lm-9c1x", "aria", "demo", "main", "cache tokens", "# Spec: cache tokens\n",
         "active", stamp, stamp, now_s() + 1800),
    )
    conn.execute(
        "INSERT INTO claims (node_id, plan_id, mode, created, released) VALUES (?,?,?,?,?)",
        ("n-node0001", "lm-9c1x", "write", stamp, None),
    )
    conn.close()
    return db


def test_ls_lists_active_claims_in_both_modes(seeded_db, monkeypatch, capsys) -> None:
    for ambient in ("CLAUDE_CODE", "LOOM_AGENT_MODE"):  # agent mode must not leak in from the host
        monkeypatch.delenv(ambient, raising=False)
    run_cli(monkeypatch, "ls", "--db", seeded_db)
    plain = capsys.readouterr().out
    assert "src/app.py::AuthService/authenticate" in plain
    assert plain.startswith("1 active claim(s)")

    run_cli(monkeypatch, "ls", "--db", seeded_db, "--json")
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["ref"] == "src/app.py::AuthService/authenticate"
    assert (rows[0]["mode"], rows[0]["agent"], rows[0]["plan_id"]) == ("write", "aria", "lm-9c1x")
    assert rows[0]["origin"] == "target"    # break4 cli-F1: authority depth is board-visible


def test_show_renders_plans_and_nodes(seeded_db, monkeypatch, capsys) -> None:
    run_cli(monkeypatch, "show", "lm-9c1x", "--db", seeded_db)
    out = capsys.readouterr().out
    assert "lm-9c1x  active  agent=aria" in out
    assert "write src/app.py::AuthService/authenticate" in out
    assert "# Spec: cache tokens" in out

    run_cli(monkeypatch, "show", "n-node0001", "--db", seeded_db)
    out = capsys.readouterr().out
    assert "n-node0001  Function  src/app.py::AuthService/authenticate" in out
    assert "claimed write by aria (lm-9c1x)" in out

    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "show", "lm-nope", "--db", seeded_db)


def test_release_is_owner_only_and_tombstones_claims(seeded_db, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "release", "lm-9c1x", "--agent", "mallory", "--db", seeded_db)

    run_cli(monkeypatch, "release", "lm-9c1x", "--agent", "aria", "--db", seeded_db)
    assert json.loads(capsys.readouterr().out) == {
        "ok": True, "released_claims": 1, "plan_status": "done"
    }
    conn = sqlite3.connect(seeded_db)
    assert conn.execute("SELECT status FROM plans WHERE id='lm-9c1x'").fetchone()[0] == "done"
    assert conn.execute("SELECT released FROM claims").fetchone()[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM events WHERE action='released'").fetchone()[0] == 1
    conn.close()

    with pytest.raises(SystemExit):  # already released -> not_active, never a second tombstone
        run_cli(monkeypatch, "release", "lm-9c1x", "--agent", "aria", "--db", seeded_db)


def test_ls_and_show_mark_expanded_claims(seeded_db, monkeypatch, capsys) -> None:
    """break4 cli-F1, the display half: a CALLS-swept claim covers its node ONLY, so it
    must LOOK different on every text surface — printed identically to a named claim, the
    board answers the opposite of the gate one level down."""
    conn = connect(seeded_db)
    conn.execute("UPDATE claims SET origin='expanded' WHERE plan_id = 'lm-9c1x'")
    conn.close()
    for ambient in ("CLAUDE_CODE", "LOOM_AGENT_MODE"):
        monkeypatch.delenv(ambient, raising=False)
    run_cli(monkeypatch, "ls", "--db", seeded_db, "--json")
    assert json.loads(capsys.readouterr().out)[0]["origin"] == "expanded"
    run_cli(monkeypatch, "ls", "--db", seeded_db)
    assert "write (expanded)\t" in capsys.readouterr().out
    run_cli(monkeypatch, "show", "lm-9c1x", "--db", seeded_db)
    assert "(expanded — this node only)" in capsys.readouterr().out
    run_cli(monkeypatch, "show", "n-node0001", "--db", seeded_db)
    assert ", expanded — this node only)" in capsys.readouterr().out


def test_ls_hides_released_and_expired_claims(seeded_db, monkeypatch, capsys) -> None:
    conn = connect(seeded_db)
    conn.execute("UPDATE plans SET ttl_expires = ? WHERE id = 'lm-9c1x'", (now_s() - 10,))
    conn.close()
    run_cli(monkeypatch, "ls", "--db", seeded_db, "--json")
    assert json.loads(capsys.readouterr().out) == []


def test_templates_ship_in_package_with_the_frozen_shape() -> None:
    """§8 templates are the agent-facing contract; `loom init` reads them from the package."""
    spec = Path(_templates(), "spec.md").read_text(encoding="utf-8")
    snippet = Path(_templates(), "CLAUDE.snippet.md").read_text(encoding="utf-8")
    for heading in ("Goal", "Write targets", "New/changed interfaces", "Assumes", "Out of scope"):
        assert f"## {heading} *(mandatory)*" in spec
    # The §5.10 declare-time validator rejects anything longer than 60 lines / 8000 chars.
    assert len(spec.splitlines()) <= 60
    assert len(spec) <= 8000
    assert snippet.startswith("<!-- loom protocol v1")
    assert "## loom — shared-repo coordination protocol" in snippet


def test_ls_missing_db_dies_cleanly(tmp_path, capsys, monkeypatch):
    """Read verbs must not conjure an empty db at a mistyped path (orchestrator fix, post-final-gate)."""
    missing = str(tmp_path / "nope" / "absent.sqlite3")
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "ls", "--db", missing)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no loom database" in err and missing in err


def test_serve_indexes_at_boot(tmp_path, monkeypatch, capsys):
    """PLAN §4.5: `loom serve` = server plus indexer, one process (orchestrator fix)."""
    import sqlite3

    import loom.server.app as app_mod

    fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pyrepo")
    db = str(tmp_path / "serve.sqlite3")
    monkeypatch.setattr(app_mod, "serve", lambda *a, **k: None)
    run_cli(monkeypatch, "serve", "--repo-root", os.path.abspath(fixture), "--db", db)
    con = sqlite3.connect(db)
    nodes = con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    edges = con.execute("SELECT count(*) FROM edges").fetchone()[0]
    assert nodes > 0 and edges > 0
    assert "loom: indexed" in capsys.readouterr().out


def test_init_registers_mcp_server(tmp_path, monkeypatch):
    """Red-team fix: without .mcp.json agents are TOLD to declare_plan but CANNOT call it."""
    import json as _json

    from loom.cli import main as cli_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # pre-existing .mcp.json with another server must be MERGED, not clobbered
    (repo_root / ".mcp.json").write_text(_json.dumps(
        {"mcpServers": {"other": {"type": "http", "url": "http://x/mcp"}}}))
    # `_health` returns the whole `/health` BODY (MULTIREPO-SPEC §1 + ITERATION-2 D8):
    # init has to know whether the name is auto-selectable before it can auto-select it,
    # and whether the server wants a token before it writes anything.
    monkeypatch.setattr(cli_mod, "_health", lambda server: {"ok": True, "repos": ["testrepo"]})
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: "/usr/bin/true-gate")
    monkeypatch.setattr(cli_mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 2, "stderr": ""})())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    run_cli(monkeypatch, "init", "--server", "http://127.0.0.1:9999",
            "--agent", "tester", "--repo-root", str(repo_root))
    data = _json.loads((repo_root / ".mcp.json").read_text())
    assert data["mcpServers"]["loom"] == {"type": "http", "url": "http://127.0.0.1:9999/mcp"}
    assert data["mcpServers"]["other"]["url"] == "http://x/mcp"  # merged, not clobbered


# --------------------------------------------------------------------------- P0-3 / xm-F1
# A second `loom init` for ANOTHER repo used to clobber the ONE global ~/.loom/config.toml,
# so every later edit in the FIRST repo was checked against the SECOND repo's server — which
# answers `allow` for a salt it does not serve. `loom init` now also writes a per-repo
# <repo_root>/.claude/loom.toml and gate.load_config walks up to it.


def _salt_stub(salt: str):
    """A throwaway loom-server stand-in: GET /health names `salt`, POST /gate records+allows."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send(json.dumps({"ok": True, "repo": self.server.salt}).encode())

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.server.requests.append(json.loads(self.rfile.read(length) or b"{}"))
            self._send(json.dumps({"decision": "allow", "case": "in_plan", "message": "",
                                   "node_id": None, "plan_id": None}).encode())

        def log_message(self, *args: object) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    srv.salt, srv.requests = salt, []
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_init_for_a_second_repo_does_not_disarm_the_first(
    tmp_path, home, fake_gate, monkeypatch
) -> None:
    """xm-F1: init repo A, then repo B; an edit in A must still reach A's server."""
    import subprocess

    monkeypatch.setenv("HOME", home)
    roots = []
    for name in ("alpha", "beta"):
        root = tmp_path / name
        (root / "src").mkdir(parents=True)
        (root / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        roots.append(root)
    first, second = _salt_stub("alpha-salt"), _salt_stub("beta-salt")
    try:
        run_cli(monkeypatch, "init", "--server", first.url, "--agent", "bob",
                "--repo-root", str(roots[0]))
        run_cli(monkeypatch, "init", "--server", second.url, "--agent", "bob",
                "--repo-root", str(roots[1]))

        # 1. each repo carries its OWN config next to the settings.json init already wrote
        cfg_a = Path(roots[0], ".claude", "loom.toml").read_text(encoding="utf-8")
        cfg_b = Path(roots[1], ".claude", "loom.toml").read_text(encoding="utf-8")
        assert f'server_url = "{first.url}"' in cfg_a and 'repo = "alpha-salt"' in cfg_a
        assert f'server_url = "{second.url}"' in cfg_b and 'repo = "beta-salt"' in cfg_b
        # 2. the global slot still exists (backward compat) and holds the FIRST init: a
        #    later init must not repoint the fallback, which is the stale-global bug the
        #    per-repo file was introduced to fix.
        global_cfg = Path(home, ".loom", "config.toml").read_text(encoding="utf-8")
        assert f'server_url = "{first.url}"' in global_cfg
        assert second.url not in global_cfg

        # 3. the real hook, editing in repo A AFTER repo B was initialized
        payload = json.dumps({
            "session_id": "s-1", "cwd": str(roots[0]), "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(roots[0] / "src" / "mod.py"), "content": "x"},
        })
        env = {**os.environ, "HOME": home}
        for stray in ("LOOM_CONFIG", "LOOM_AGENT", "LOOM_BYPASS", "LOOM_AGENT_MODE"):
            env.pop(stray, None)
        proc = subprocess.run([sys.executable, "-m", "loom.hook.gate"], input=payload,
                              capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0
        assert second.requests == []  # the OTHER repo's server is never consulted
        assert [(r["repo"], r["path"]) for r in first.requests] == [("alpha-salt", "src/mod.py")]
    finally:
        for srv in (first, second):
            srv.shutdown()
            srv.server_close()


# --------------------------------------------------------------------------- P1-2 / xm-F3


def test_index_honours_an_explicit_repo_salt(tmp_path, monkeypatch, capsys) -> None:
    """xm-F3: `serve --repo NAME` pinned a salt; re-index must be able to write under it."""
    repo_root = tmp_path / "clone-with-other-basename"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = str(tmp_path / "loom.sqlite3")

    run_cli(monkeypatch, "index", "--repo-root", str(repo_root), "--db", db,
            "--repo", "teamrepo")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["repo"] == "teamrepo"

    con = sqlite3.connect(db)
    assert [r[0] for r in con.execute("SELECT DISTINCT repo FROM nodes")] == ["teamrepo"]

    # default is still the basename, and it is printed
    run_cli(monkeypatch, "index", "--repo-root", str(repo_root), "--db", db)
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["repo"] == \
        "clone-with-other-basename"
    assert set(r[0] for r in con.execute("SELECT DISTINCT repo FROM nodes")) == {
        "teamrepo", "clone-with-other-basename"}
    con.close()


def test_index_warns_the_operator_that_it_holds_the_write_lock(tmp_path, monkeypatch,
                                                               capsys) -> None:
    """break3 chaos-F1 mitigation: the silent multi-second coordination gap gets a voice.

    `_index` wraps the whole rebuild in one `BEGIN IMMEDIATE`. On a big tree that lock is
    held for seconds, and the gate is not a pure reader — its audit row and its implicit
    renew are writes — so live `/gate` calls blew past the hook's 1.5s timeout and FAILED
    OPEN, across every repo on the database, with no signal anywhere. Chunking the rebuild
    is the real fix and is backlog; this row is the operator's warning that it is happening.

    The notice goes to STDERR on purpose: `loom index`'s stdout is one line of JSON that
    callers parse, and it must stay that.
    """
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    run_cli(monkeypatch, "index", "--repo-root", str(repo_root),
            "--db", str(tmp_path / "loom.sqlite3"))

    cap = capsys.readouterr()
    assert json.loads(cap.out.strip())["repo"] == "repo"        # stdout is still just JSON
    assert "WARNING" in cap.err and "write lock" in cap.err and "fail OPEN" in cap.err
    assert "released the write lock after" in cap.err           # ...and for how long


def test_init_gitignores_the_identity_file(tmp_path, monkeypatch):
    """Two-user simulation finding: a teammate's `git add -A` must never be able to commit
    their per-user loom.toml into the shared repo (pulling it re-identifies everyone)."""
    from loom.cli import main as cli_mod

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".gitignore").write_text("__pycache__/\n")
    monkeypatch.setattr(cli_mod, "_health", lambda server: {"ok": True, "repos": ["testrepo"]})
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: "/usr/bin/true-gate")
    monkeypatch.setattr(cli_mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 2, "stderr": ""})())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    run_cli(monkeypatch, "init", "--server", "http://127.0.0.1:9999",
            "--agent", "t", "--repo-root", str(repo_root))
    lines = (repo_root / ".gitignore").read_text().splitlines()
    assert ".claude/loom.toml" in lines and lines[0] == "__pycache__/"  # appended, not clobbered
    assert ".loom.sqlite3*" in lines       # break4 cli-F2: never commit the live database
    run_cli(monkeypatch, "init", "--server", "http://127.0.0.1:9999",
            "--agent", "t", "--repo-root", str(repo_root))
    after = (repo_root / ".gitignore").read_text().splitlines()
    assert after.count(".claude/loom.toml") == 1 and after.count(".loom.sqlite3*") == 1
