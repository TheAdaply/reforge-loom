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

from loom.cli.main import MATCHERS, _templates, main
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

    run_cli(monkeypatch, "init", "--server", stub.url, "--agent", "akash-mbp", "--repo-root", repo_root)
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


def test_init_refuses_an_unreachable_server(repo_root, home, fake_gate, monkeypatch) -> None:
    monkeypatch.setenv("HOME", home)
    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "init", "--server", "http://127.0.0.1:9", "--repo-root", repo_root)
    assert not Path(repo_root, ".claude").exists()


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
        "INSERT INTO claims VALUES (?,?,?,?,?)", ("n-node0001", "lm-9c1x", "write", stamp, None)
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
