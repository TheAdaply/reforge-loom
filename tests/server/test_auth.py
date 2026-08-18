"""ITERATION-2-SPEC §2 + §3 — honest `/state` caps and the opt-in shared token.

Two halves, matching §5's two bars.

UNIT is arithmetic and argument parsing, run without a socket: `state_payload` is called
directly with `STATE_NODE_CAP`/`STATE_EDGE_CAP` monkeypatched down to 2, which is why those
caps are module constants at all — reaching the real 600 would mean seeding 601 synthetic
nodes to assert one boolean.

INTEGRATION is the whole product against a REAL subprocess server started `--token`, in the
frozen rig style of `test_concurrency.py` / `test_multirepo.py`: PIPE logs, try/finally
terminate, explicit `--db`. Nothing is stubbed — `loom init`, `loom doctor` and the real
`loom-gate` console script all run for real against that server, because the failure this
whole section exists to prevent is precisely a credential that works in a unit test and not
on the wire.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

# The SDK's own httpx2 factory: it is where "configure headers" is documented for
# `streamable_http_client`, and it carries the MCP-recommended SSE timeouts a bare
# `httpx2.AsyncClient()` would not. Private module, public contract — see its docstring.
from mcp.shared._httpx_utils import create_mcp_http_client

import loom.server.app as app_mod
from conftest import REPO, nid
from loom.cli.main import main as cli_main
from loom.indexer.walk import index_repo
from loom.server.app import resolve_token, state_payload
from loom.server.db import connect, init_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PYREPO = str(FIXTURES / "pyrepo")
TOKEN = "s3cret-team-token"
ROW = re.compile(r"^\s+(PASS|FAIL|WARN)\s\s(.+?)\s\s+(.*)$")
UNAUTHORIZED = {"ok": False, "error": "unauthorized"}


# --------------------------------------------------------------------------- §5 unit: caps


@pytest.fixture(autouse=True)
def _restore_caps() -> Iterator[None]:
    """The cap constants are module state; put them back however the test ended."""
    caps = (app_mod.STATE_NODE_CAP, app_mod.STATE_EDGE_CAP)
    yield
    app_mod.STATE_NODE_CAP, app_mod.STATE_EDGE_CAP = caps


def test_state_reports_the_totals_behind_a_cap_that_bit(gconn) -> None:
    """§2/D7: `counts` stays POST-LIMIT (frozen consumers read it that way) while `totals`
    tells the truth and `truncated` says which axis was cut."""
    app_mod.STATE_NODE_CAP, app_mod.STATE_EDGE_CAP = 2, 2

    state = state_payload(gconn, REPO, [REPO])

    assert state["counts"] == {"nodes": 2, "edges": 2, "plans": 0, "claims": 0}
    assert state["totals"] == {"nodes": 10, "edges": 9}      # the whole seeded fixture graph
    assert state["truncated"] == {"nodes": True, "edges": True}
    assert len(state["nodes"]) == 2 and len(state["edges"]) == 2


def test_one_axis_can_be_truncated_while_the_other_is_whole(gconn) -> None:
    """The flags are per-axis: a repo with few files and many call edges must not be
    reported as node-truncated just because its edges were cut."""
    app_mod.STATE_NODE_CAP, app_mod.STATE_EDGE_CAP = 600, 3

    state = state_payload(gconn, REPO, [REPO])

    assert state["truncated"] == {"nodes": False, "edges": True}
    assert state["counts"]["nodes"] == state["totals"]["nodes"] == 10


def test_nothing_is_truncated_when_the_whole_graph_fits(gconn) -> None:
    state = state_payload(gconn, REPO, [REPO])

    assert state["truncated"] == {"nodes": False, "edges": False}
    assert state["counts"]["nodes"] == state["totals"]["nodes"]
    assert state["counts"]["edges"] == state["totals"]["edges"]


def test_the_edge_total_counts_the_same_edges_the_payload_ships(gconn) -> None:
    """The totals query MIRRORS the sent query's join (`JOIN nodes ON n.id = e.src`). An
    edge whose SOURCE belongs to another repo is in neither number — count it in one and
    `truncated` would report a truncation that never happened, on every poll, forever."""
    gconn.execute("INSERT INTO nodes (id, repo, path, qualname, kind, updated) "
                  "VALUES ('n-otherrp', 'other', 'x.py', '', 'File', '2020-01-01T00:00:00Z')")
    gconn.execute("INSERT INTO edges (src, dst, kind) VALUES ('n-otherrp', ?, 'CALLS')",
                  (nid("svc.py::login"),))
    gconn.commit()

    state = state_payload(gconn, REPO, [REPO])

    assert state["totals"]["edges"] == 9                      # the foreign-src edge is not ours
    assert state["truncated"] == {"nodes": False, "edges": False}


# --------------------------------------------------------------------------- §5 unit: token


def test_the_flag_wins_over_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("LOOM_TOKEN", "from-env")
    assert resolve_token("from-flag") == "from-flag"
    assert resolve_token(None) == "from-env"


def test_no_token_on_either_channel_is_an_open_server(monkeypatch) -> None:
    monkeypatch.delenv("LOOM_TOKEN", raising=False)
    assert resolve_token(None) == ""


def test_an_explicitly_empty_flag_is_a_hard_error(monkeypatch) -> None:
    """beads §2.3 on the one flag where the wildcard reading unlocks the server: a broken
    `--token "$UNSET_VAR"` must stop the process, never quietly serve without auth."""
    monkeypatch.delenv("LOOM_TOKEN", raising=False)
    for blank in ("", "   "):
        with pytest.raises(ValueError) as exc:
            resolve_token(blank)
        assert "--token" in str(exc.value)


def test_an_ambient_empty_env_token_reads_as_unset(monkeypatch) -> None:
    """§5a's asymmetry, the other half: an EXPORTED-EMPTY `LOOM_TOKEN` is how a shell or a
    CI runner spells "no value" — it opens the server rather than refusing to boot. Only
    the typed `--token ""` above is an error, because only that one asserts an intent."""
    for blank in ("", "   "):
        monkeypatch.setenv("LOOM_TOKEN", blank)
        assert resolve_token(None) == ""
        # ...and the flag still wins, even over an empty env var.
        assert resolve_token("real") == "real"


def test_serve_refuses_to_start_on_an_empty_token(tmp_path, monkeypatch, capsys) -> None:
    """The CLI turns that ValueError into an exit, before the indexer touches storage."""
    monkeypatch.delenv("LOOM_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["loom", "serve", "--repo-root", PYREPO,
                                      "--db", str(tmp_path / "never.sqlite3"), "--token", ""])
    with pytest.raises(SystemExit):
        cli_main()
    assert "--token" in capsys.readouterr().err
    assert not (tmp_path / "never.sqlite3").exists()


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
        if proc.poll() is not None:                            # pragma: no cover
            raise RuntimeError(f"server died: {proc.communicate()[1]}")
        try:
            socket.create_connection(("127.0.0.1", port), 0.25).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"server did not open port {port}")      # pragma: no cover


def request(url: str, body: dict | None = None, token: str = "") -> dict:
    """One plain-HTTP call; raises `HTTPError` on the 401 so tests can read its body."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        return json.loads(resp.read())


def refused(url: str, body: dict | None = None, token: str = "") -> dict:
    """The 401 body, asserted to BE a 401 — never a 200 of some other shape."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        request(url, body, token)
    assert exc.value.code == 401
    return json.loads(exc.value.read())


def tool(base: str, token: str, name: str, args: dict[str, Any]) -> dict:
    """One MCP call over streamable-http, carrying the shared token."""
    async def go() -> dict:
        async with create_mcp_http_client(
                headers={"Authorization": f"Bearer {token}"}) as http_client:
            transport = streamable_http_client(base + "/mcp", http_client=http_client)
            async with Client(transport) as c:
                return (await c.call_tool(name, args)).structured_content
    return asyncio.run(go())


@pytest.fixture()
def token_server(tmp_path) -> Iterator[tuple[str, str]]:
    """(base_url, db_path) of a REAL server started `--token`, over an indexed `alpha`."""
    db = str(tmp_path / "auth.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        index_repo(conn, "alpha", PYREPO, changed_only=False)
    finally:
        conn.close()
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--repo-root", f"alpha={PYREPO}",
         "--db", db, "--host", "127.0.0.1", "--port", str(port), "--token", TOKEN],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for_port(port, proc)
        yield f"http://127.0.0.1:{port}", db
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                       # pragma: no cover
            proc.kill()


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["loom", *argv])
    cli_main()


# --------------------------------------------------------------------------- §3 the wall


def test_health_stays_open_and_names_the_mode(token_server) -> None:
    """D8: `/health` is how an uncredentialed client LEARNS it needs a credential, so it
    is the one route the wall does not stand in front of."""
    base, _db = token_server
    assert request(base + "/health") == {"ok": True, "repo": "alpha", "repos": ["alpha"],
                                         "auth": "token"}


def test_state_is_401_without_the_header_and_200_with_it(token_server) -> None:
    base, _db = token_server

    assert refused(base + "/state") == UNAUTHORIZED
    assert refused(base + "/state", token="wrong-token") == UNAUTHORIZED

    state = request(base + "/state", token=TOKEN)
    assert state["ok"] is True and state["repo"] == "alpha"
    # ...and D7 rides along on the same body.
    assert state["totals"]["nodes"] > 0
    assert state["truncated"] == {"nodes": False, "edges": False}


def test_gate_is_401_without_the_header_and_routes_with_it(token_server) -> None:
    """§3: a refusal on `/gate` is a 401, NOT a 200 gate shape. The hook reads any non-200
    as fail-open, which is the right posture for a client whose credential is wrong — the
    coordination layer is advisory and a misconfiguration must not brick every edit."""
    base, _db = token_server
    body = {"agent": "aria", "repo": "alpha", "path": "svc.py", "qualname": None,
            "tool_name": "Edit"}

    denied = refused(base + "/gate", body)
    assert denied == UNAUTHORIZED
    assert "decision" not in denied                            # never a gate-shaped 401

    allowed = request(base + "/gate", body, token=TOKEN)
    assert set(allowed) == {"decision", "case", "message", "node_id", "plan_id"}


def test_the_mcp_mount_is_behind_the_same_wall(token_server) -> None:
    base, _db = token_server
    assert refused(base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}) == UNAUTHORIZED


def test_an_mcp_client_carrying_the_header_works_end_to_end(token_server) -> None:
    """The client half of §3: headers on the httpx2 client the transport is handed."""
    base, _db = token_server
    assert tool(base, TOKEN, "list_claims", {}) == {"ok": True, "claims": []}

    granted = tool(base, TOKEN, "resolve_nodes", {"queries": ["svc.py"]})
    assert granted["resolved"][0]["matches"], granted


# --------------------------------------------------------------------------- §3 init/doctor


def test_init_needs_the_token_and_then_plumbs_it_everywhere(
        token_server, tmp_path, monkeypatch, capsys) -> None:
    """D10: without `--token` init dies BEFORE writing anything; with it, the secret lands
    in both the config the hook reads and the `.mcp.json` Claude Code reads."""
    base, _db = token_server
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    root = tmp_path / "checkout"
    root.mkdir()

    with pytest.raises(SystemExit):
        run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
                "--repo-root", str(root))
    assert "requires a token" in capsys.readouterr().err
    assert not (root / ".claude").exists()                     # nothing half-written

    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--token", TOKEN)
    capsys.readouterr()

    cfg = (root / ".claude" / "loom.toml").read_text(encoding="utf-8")
    assert f'token = "{TOKEN}"' in cfg and 'repo = "alpha"' in cfg
    entry = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["loom"]
    assert entry["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert entry["url"] == base + "/mcp"


def test_the_gate_hook_sends_the_configured_token(token_server, tmp_path, monkeypatch,
                                                  capsys) -> None:
    """§3's four lines in `hook/gate.py`, proven over the wire rather than by inspection:
    the SAME wire body is a 401 unauthenticated and a real decision with the config token."""
    from loom.hook import gate as hook_gate

    base, _db = token_server
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    root = tmp_path / "checkout"
    root.mkdir()
    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--token", TOKEN)
    capsys.readouterr()

    cfg = hook_gate.load_config(str(root))
    assert cfg is not None and cfg["token"] == TOKEN
    body = {"agent": "aria", "repo": "alpha", "path": "svc.py", "qualname": None,
            "tool_name": "Edit"}

    assert hook_gate.call_gate(cfg, body)["decision"] in ("allow", "deny")
    with pytest.raises(urllib.error.HTTPError) as exc:
        hook_gate.call_gate({**cfg, "token": ""}, body)
    assert exc.value.code == 401                               # -> the caller's fail-open


def doctor(monkeypatch: pytest.MonkeyPatch, capsys, root: Path) -> tuple[dict, int]:
    code = 0
    try:
        run_cli(monkeypatch, "doctor", "--repo-root", str(root))
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr().out
    rows = {m.group(2).strip(): (m.group(1), m.group(3))
            for m in map(ROW.match, out.splitlines()) if m}
    assert rows, out
    return rows, code


def test_doctor_is_all_pass_on_a_tokened_rig(token_server, tmp_path, monkeypatch,
                                             capsys) -> None:
    """D11: every row green against a server that demands a credential — including the
    gate round-trip, which runs the REAL `loom-gate` binary. (That payload is denied
    hook-side by design, so it proves the chain without needing the server to answer; the
    server-side half of the token is what rows 3 and 9 exercise.)"""
    base, _db = token_server
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    monkeypatch.delenv("LOOM_BYPASS", raising=False)
    root = tmp_path / "checkout"
    root.mkdir()
    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--token", TOKEN)
    capsys.readouterr()

    rows, code = doctor(monkeypatch, capsys, root)

    assert code == 0
    assert [s for s, _d in rows.values()] == ["PASS"] * 9
    assert "auth=token" in rows["server"][1]
    assert rows["auth"] == ("PASS", "shared token accepted")


def test_doctor_fails_the_auth_row_on_a_wrong_token(token_server, tmp_path, monkeypatch,
                                                    capsys) -> None:
    """The row exists for exactly this: a checkout that LOOKS wired — config present, hook
    registered, server reachable — and whose every real call gets a 401."""
    base, _db = token_server
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    monkeypatch.delenv("LOOM_BYPASS", raising=False)
    root = tmp_path / "checkout"
    root.mkdir()
    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--token", TOKEN)
    capsys.readouterr()
    cfg = root / ".claude" / "loom.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(TOKEN, "stale-token"),
                   encoding="utf-8")

    rows, code = doctor(monkeypatch, capsys, root)

    assert code == 1
    assert rows["auth"] == ("FAIL",
                            "server requires a token — re-run loom init with --token")
    # ...and the rows that do not depend on the credential stay green, so the table still
    # points at the one thing to fix.
    assert [rows[c][0] for c in ("config", "server", "repo match", "gate binary",
                                 "hook registered", "mcp registered",
                                 "gate round-trip")] == ["PASS"] * 7
