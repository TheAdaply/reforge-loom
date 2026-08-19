"""M3 — the gate.py exit contract (§7.3), the §6 wire mapping, and the no-override law (§7.4).

Every case pipes a real `tests/fixtures/pretooluse/*.json` payload into gate.py as a subprocess
against the canned-response stub. No M2 code is imported anywhere in this file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loom.hook.locator import UNSCOPED_TMPL

# §7.4 no-override law: frozen forbidden substrings, asserted case-insensitively on EVERY deny.
FORBIDDEN = ("force", "bypass", "override", "unclaim", "release(", "--force")

OWNER_SPEC = """# Spec: harden logout

**Agent**: aria  **Plan**: lm-4f2a  **Repo/branch**: demo / main

## Goal *(mandatory)*

Tighten AuthService.logout so a stale token cannot be reused. A second logout with the same
token returns None without touching the store.

## Write targets *(mandatory)*

- src/app.py::AuthService/logout

## New/changed interfaces *(mandatory)*

- UNCHANGED-BUT-LOAD-BEARING `AuthService.logout(self, token: str) -> None`

## Assumes *(mandatory)*

- src/app.py::_mint

## Out of scope *(mandatory)*

Token minting and the helper path are untouched.
"""

FOREIGN_MSG = (
    'loom: BLOCKED — src/app.py::AuthService/logout is claimed by "aria" under plan lm-4f2a '
    '"harden logout", expires 2026-08-18T14:20:07Z (in 12m).\n'
    "Its spec follows. Build against its declared interfaces, or rescope your plan around it, "
    "or wait for expiry.\n\n" + OWNER_SPEC
)
OUT_OF_SCOPE_MSG = (
    'loom: src/app.py::helper is outside your declared plan lm-9c1x "cache tokens". '
    'Call rescope(plan_id="lm-9c1x", add_targets=["src/app.py::helper"]), then retry this edit.'
)
NO_PLAN_MSG = (
    'loom: no active plan for agent "agent-a". Before editing: write a spec from '
    "templates/spec.md, resolve every target with resolve_nodes, call declare_plan, then retry "
    "this edit."
)

ALLOW = {"decision": "allow", "case": "in_plan", "message": "", "node_id": "n-1a2b3c4d",
         "plan_id": "lm-9c1x"}
DENY_FOREIGN = {"decision": "deny", "case": "foreign_claim", "message": FOREIGN_MSG,
                "node_id": "n-1a2b3c4d", "plan_id": None}
DENY_SCOPE = {"decision": "deny", "case": "out_of_scope", "message": OUT_OF_SCOPE_MSG,
              "node_id": "n-9f8e7d6c", "plan_id": "lm-9c1x"}
DENY_NO_PLAN = {"decision": "deny", "case": "no_plan", "message": NO_PLAN_MSG,
                "node_id": "n-5566aabb", "plan_id": None}


def assert_no_override(text: str) -> None:
    lowered = text.lower()
    for bad in FORBIDDEN:
        assert bad not in lowered, f"deny surface names an escape hatch: {bad!r}"


def test_allow_in_plan_is_silent_exit_zero(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    stub.response = ALLOW
    r = run_gate(payload("in_plan_allow"))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    # §6 wire mapping: the Edit's old_string sits inside AuthService.authenticate.
    assert stub.requests == [
        {"agent": "agent-a", "repo": "demo", "path": "src/app.py",
         "qualname": "AuthService/authenticate", "tool_name": "Edit"}
    ]


def test_allow_never_emits_permission_decision(
    stub, repo_root, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    stub.response = ALLOW
    r = run_gate(payload("in_plan_allow"))
    assert "permissionDecision" not in r.stdout


@pytest.mark.parametrize(
    ("fixture", "response", "expected"),
    [
        ("foreign_claim_deny", DENY_FOREIGN, FOREIGN_MSG),
        ("out_of_scope_deny", DENY_SCOPE, OUT_OF_SCOPE_MSG),
        ("no_plan_deny", DENY_NO_PLAN, NO_PLAN_MSG),
    ],
)
def test_deny_relays_message_on_stderr_exit_two(
    stub, repo_root, configure, payload, run_gate, fixture, response, expected
) -> None:
    configure(stub.url, repo_root)
    stub.response = response
    r = run_gate(payload(fixture))
    assert r.returncode == 2
    assert r.stdout == ""
    assert expected in r.stderr
    assert_no_override(r.stderr)


def test_foreign_claim_deny_carries_the_owner_spec(
    stub, repo_root, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    stub.response = DENY_FOREIGN
    r = run_gate(payload("foreign_claim_deny"))
    assert "## New/changed interfaces" in r.stderr
    assert stub.requests[0]["qualname"] == "AuthService/logout"


def test_unscoped_replace_in_files_denies_locally(
    stub, repo_root, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    r = run_gate(payload("replace_in_files_unscoped"))
    assert r.returncode == 2
    assert UNSCOPED_TMPL in r.stderr
    assert stub.requests == []  # DENY_LOCAL: no server round trip (§7.2)
    assert_no_override(r.stderr)


def test_unknown_tool_passes_without_calling_the_server(
    stub, repo_root, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    r = run_gate(payload("unknown_tool_pass"))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert stub.requests == []


def test_serena_symbol_tools_map_to_the_wire(
    stub, repo_root, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    stub.response = ALLOW
    run_gate(payload("serena_replace_symbol_body"))
    run_gate(payload("serena_safe_delete_pattern"))
    seen = [(q["path"], q["qualname"], q["tool_name"].rsplit("__", 1)[-1]) for q in stub.requests]
    assert seen == [
        ("src/app.py", "AuthService/authenticate", "replace_symbol_body"),
        ("src/app.py", "AuthService/logout", "safe_delete_symbol"),
    ]


def test_notebook_edit_is_file_level(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    stub.response = ALLOW
    r = run_gate(payload("notebook_edit"))
    assert r.returncode == 0
    assert stub.requests[0]["path"] == "notebooks/explore.ipynb"
    assert stub.requests[0]["qualname"] is None


def test_write_tool_is_file_level(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    stub.response = DENY_NO_PLAN
    r = run_gate(payload("no_plan_deny"))
    assert r.returncode == 2
    assert stub.requests[0]["qualname"] is None


def test_lenient_parsing_of_subagent_and_missing_fields(
    stub, repo_root, home, configure, payload, run_gate
) -> None:
    configure(stub.url, repo_root)
    stub.response = ALLOW
    r1 = run_gate(payload("subagent_fields_present"))
    r2 = run_gate(payload("missing_permission_mode"))
    assert (r1.returncode, r2.returncode) == (0, 0)
    # A closure body rolls up to its enclosing claimable function (§4 granularity).
    assert stub.requests[0]["qualname"] == "_mint"
    assert stub.requests[1]["qualname"] == "helper"
    audit = Path(home, ".loom", "gate-audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(audit[0])["agent_id"] == "agent-7f3"
    assert json.loads(audit[0])["session_id"] == "sess-0001"


def test_loom_bypass_is_human_only_and_audited(
    stub, repo_root, home, configure, payload, run_gate
) -> None:
    """The escape hatch is real, silent to the model, and leaves a record (§7.4)."""
    configure(stub.url, repo_root)
    stub.response = DENY_FOREIGN
    r = run_gate(payload("foreign_claim_deny"), {"LOOM_BYPASS": "1"})
    assert (r.returncode, r.stdout) == (0, "")
    assert stub.requests == []
    record = json.loads(Path(home, ".loom", "gate-audit.jsonl").read_text(encoding="utf-8"))
    assert record["decision"] == "bypass"


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " ", ""])
def test_loom_bypass_off_spellings_do_not_disable_the_gate(
    stub, repo_root, configure, payload, run_gate, value
) -> None:
    """gate-#6: `LOOM_BYPASS=0`/`=false`/`=no` meant OFF, yet bare truthiness killed the gate."""
    configure(stub.url, repo_root)
    stub.response = DENY_FOREIGN
    r = run_gate(payload("foreign_claim_deny"), {"LOOM_BYPASS": value})
    assert stub.requests, f"LOOM_BYPASS={value!r} skipped the server call"
    assert r.returncode == 2
    assert FOREIGN_MSG in r.stderr


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " True "])
def test_loom_bypass_on_spellings_still_bypass(
    stub, repo_root, home, configure, payload, run_gate, value
) -> None:
    configure(stub.url, repo_root)
    stub.response = DENY_FOREIGN
    r = run_gate(payload("foreign_claim_deny"), {"LOOM_BYPASS": value})
    assert (r.returncode, r.stdout, stub.requests) == (0, "", [])
    record = json.loads(Path(home, ".loom", "gate-audit.jsonl").read_text(encoding="utf-8"))
    assert record["decision"] == "bypass"


def test_fail_open_when_server_is_down(stub, repo_root, configure, payload, run_gate) -> None:
    configure("http://127.0.0.1:9", repo_root)  # discard port: connection refused
    r = run_gate(payload("server_down_failopen"))
    assert r.returncode == 0
    assert json.loads(r.stdout)["systemMessage"] == (
        "loom: coordination server unreachable — edit allowed, claims NOT checked"
    )
    assert "loom: WARNING — gate failed open (" in r.stderr


def test_fail_open_on_timeout(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    stub.delay = 2.5  # > the frozen 1.5 s client timeout
    r = run_gate(payload("server_down_failopen"))
    assert r.returncode == 0
    assert "systemMessage" in r.stdout
    assert "gate failed open" in r.stderr


def test_fail_open_on_non_200(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    stub.status = 500
    r = run_gate(payload("server_down_failopen"))
    assert r.returncode == 0
    assert "systemMessage" in r.stdout


@pytest.mark.parametrize("case, server_url, status", [
    ("URLError", "http://127.0.0.1:9", 200),      # nothing listening
    ("HTTPError", "", 500),                       # the stub, answering 500 (a full-disk /gate)
])
def test_a_fail_open_is_audited_as_a_fail_open_not_as_an_allow(
    stub, repo_root, home, configure, payload, run_gate, case, server_url, status
) -> None:
    """break3 chaos-F8: an OFF gate wrote the same audit line as a checked, allowed edit.

    `~/.loom/gate-audit.jsonl` is the only record an incident is reconstructed from. Every
    fail-open — no config, dead server, 500 from a disk-full `/gate` (chaos-F2), the wall
    deadline — fell through to `main`'s `setdefault("decision", "allow")` and became
    indistinguishable from a decision the server actually made. That is what made chaos-F1
    and chaos-F2 forensically invisible.
    """
    configure(server_url or stub.url, repo_root)
    stub.status = status
    r = run_gate(payload("server_down_failopen"))

    assert r.returncode == 0                                    # still fail-OPEN
    record = json.loads(Path(home, ".loom", "gate-audit.jsonl").read_text(encoding="utf-8"))
    assert record["decision"] == "fail_open" and record["case"] == "fail_open"
    assert record["reason"] == case
    # ...and a real allow is still a plain allow, or the distinction buys nothing.
    stub.status, stub.response = 200, ALLOW
    configure(stub.url, repo_root)
    Path(home, ".loom", "gate-audit.jsonl").unlink()
    run_gate(payload("in_plan_allow"))
    clean = json.loads(Path(home, ".loom", "gate-audit.jsonl").read_text(encoding="utf-8"))
    assert clean["decision"] == "allow" and "reason" not in clean


def test_fail_open_without_config(repo_root, home, payload, run_gate) -> None:
    r = run_gate(payload("in_plan_allow"))
    assert r.returncode == 0
    assert json.loads(r.stdout)["systemMessage"] == "loom: not initialized — run loom init"


def test_garbage_stdin_exits_zero(run_gate) -> None:
    for junk in ("", "not json{", "[1, 2, 3]", "null"):
        r = run_gate(junk)
        assert r.returncode == 0, junk


def test_only_exit_codes_zero_and_two(stub, repo_root, configure, payload, run_gate) -> None:
    configure(stub.url, repo_root)
    seen = set()
    for fixture, response in (
        ("in_plan_allow", ALLOW),
        ("foreign_claim_deny", DENY_FOREIGN),
        ("replace_in_files_unscoped", ALLOW),
        ("unknown_tool_pass", ALLOW),
    ):
        stub.response = response
        seen.add(run_gate(payload(fixture)).returncode)
    seen.add(run_gate("}{").returncode)
    assert seen <= {0, 2}


def test_hook_imports_neither_mcp_nor_starlette() -> None:
    """§9.2: the hook's whole import closure is stdlib + loom.indexer.naming."""
    probe = (
        "import sys, loom.hook.gate, loom.hook.locator; "
        "bad = {'mcp', 'starlette', 'tree_sitter', 'anyio'} & set(sys.modules); "
        "assert not bad, bad; print('hook-imports-ok')"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "hook-imports-ok" in r.stdout


def test_loom_agent_and_config_env_overrides(tmp_path, monkeypatch):
    """Several agents under one OS user need per-process identity (live-fire rig requirement)."""
    from loom.hook.gate import load_config

    cfg = tmp_path / "alt-config.toml"
    cfg.write_text('server_url = "http://x"\nagent = "filed"\nrepo = "r"\nrepo_root = "/tmp"\n')
    monkeypatch.setenv("LOOM_CONFIG", str(cfg))
    monkeypatch.setenv("LOOM_AGENT", "override-agent")
    loaded = load_config()
    assert loaded is not None
    assert loaded["agent"] == "override-agent"
    monkeypatch.delenv("LOOM_AGENT")
    assert load_config()["agent"] == "filed"


def test_load_config_prefers_the_per_repo_file_over_the_global_one(tmp_path, monkeypatch):
    """xm-F1: LOOM_CONFIG > <repo_root>/.claude/loom.toml (walked up) > ~/.loom/config.toml."""
    import os

    from loom.hook.gate import config_start_dir, load_config

    def write(path, server, repo, root):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            f'server_url = "{server}"\nagent = "bob"\nrepo = "{repo}"\nrepo_root = "{root}"\n',
            encoding="utf-8")

    home = tmp_path / "home"
    repo = tmp_path / "alpha"
    (repo / "src").mkdir(parents=True)
    write(home / ".loom" / "config.toml", "http://global", "beta-salt", str(tmp_path / "beta"))
    write(repo / ".claude" / "loom.toml", "http://alpha", "alpha-salt", str(repo))
    write(tmp_path / "explicit.toml", "http://explicit", "env-salt", str(repo))
    monkeypatch.setenv("HOME", str(home))
    for stray in ("LOOM_CONFIG", "LOOM_AGENT"):
        monkeypatch.delenv(stray, raising=False)

    # no start dir -> the global slot (backward compat, and what garbage payloads get)
    assert load_config()["repo"] == "beta-salt"
    # walk up from the edited file's directory -> the repo that owns it
    assert load_config(str(repo / "src"))["repo"] == "alpha-salt"
    assert load_config(str(repo / "src"))["server_url"] == "http://alpha"
    # a directory outside any initialized repo still falls back
    assert load_config(str(tmp_path / "elsewhere"))["repo"] == "beta-salt"
    # LOOM_CONFIG still wins outright
    monkeypatch.setenv("LOOM_CONFIG", str(tmp_path / "explicit.toml"))
    assert load_config(str(repo / "src"))["repo"] == "env-salt"
    monkeypatch.delenv("LOOM_CONFIG")

    # the start dir comes from the edited file, falling back to the payload cwd
    assert config_start_dir(
        {"cwd": str(repo), "tool_input": {"file_path": str(repo / "src" / "mod.py")}}
    ) == str(repo / "src")
    assert config_start_dir({"cwd": str(repo), "tool_input": {"content": "x"}}) == str(repo)
    assert config_start_dir({}) is None
    assert config_start_dir({"tool_input": {"file_path": "rel/path.py"}}) is None
    assert os.path.isdir(str(repo))


def test_wall_deadline_beats_dripping_server(tmp_path, monkeypatch):
    """gate-F4: a server dripping bytes must not hold the edit past the wall deadline (fail-open)."""
    import http.server
    import json
    import os
    import socketserver
    import subprocess
    import sys
    import threading
    import time as _time

    class Drip(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            for _ in range(60):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except Exception:
                    return
                _time.sleep(1.0)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), Drip)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    target = repo_root / "m.py"
    target.write_text("def f():\n    return 1\n")
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(f'server_url = "http://127.0.0.1:{port}"\nagent = "a"\nrepo = "r"\n'
                   f'repo_root = "{repo_root}"\n')
    payload = {"tool_name": "Edit",
               "tool_input": {"file_path": str(target), "old_string": "return 1",
                              "new_string": "return 2"}}
    env = {**os.environ, "LOOM_CONFIG": str(cfg), "HOME": str(tmp_path)}
    t0 = _time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "loom.hook.gate"], input=json.dumps(payload),
                         capture_output=True, text=True, timeout=15, env=env)
    elapsed = _time.monotonic() - t0
    srv.shutdown()
    assert proc.returncode == 0, proc.stderr
    assert "wall_deadline" in proc.stderr or "failed open" in proc.stderr
    assert "systemMessage" in proc.stdout
    assert elapsed < 8, f"gate took {elapsed:.1f}s against a dripping server"


def test_locator_size_cap_falls_to_file_level(tmp_path):
    """gate-F5: files above the parse cap gate at FILE level instead of stalling on ast.parse."""
    from loom.hook.locator import _PARSE_CAP_BYTES, locate

    big = tmp_path / "big.py"
    big.write_text("def f():\n    return 1\n" + ("# pad\n" * (_PARSE_CAP_BYTES // 6)))
    assert big.stat().st_size > _PARSE_CAP_BYTES
    loc = locate("Edit", {"file_path": str(big), "old_string": "return 1",
                          "new_string": "return 2"}, str(tmp_path))
    assert loc.action == "gate" and loc.qualname is None  # file-level, no parse
