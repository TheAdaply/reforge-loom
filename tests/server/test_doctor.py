"""MULTIREPO-SPEC §4 + §6.f + ITERATION-2-SPEC D11 — `loom doctor`, the nine-check table.

The rig is deliberately REAL end to end: a subprocess server over two served repos (only
one of them indexed, so the WARN branch has ground to stand on), a checkout wired by the
actual `loom init` verb, and the actual `loom-gate` console script for the round-trip.
Nothing here is stubbed except `HOME`, which is redirected into tmp so the developer's own
`~/.loom/config.toml` can never leak into a check (and the gate's audit log lands in tmp).

The failure tests cover §6.f's named rows — dead server, missing hook — and each one also
asserts what must STILL pass: a doctor that goes red everywhere as soon as anything breaks
tells an operator nothing about where to look.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from loom.cli.main import main as cli_main
from loom.indexer.walk import index_repo
from loom.server.db import connect, init_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PYREPO = str(FIXTURES / "pyrepo")
PYREPO2 = str(FIXTURES / "pyrepo2")
CHECKS = ("config", "server", "auth", "repo match", "gate binary", "hook registered",
          "mcp registered", "gate round-trip", "index freshness")
ROW = re.compile(r"^\s+(PASS|FAIL|WARN)\s\s(.+?)\s\s+(.*)$")


# --------------------------------------------------------------------------- rig


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


@pytest.fixture()
def doctor_server(tmp_path) -> Iterator[tuple[str, subprocess.Popen]]:
    """A server over `alpha` (indexed) and `beta` (served but NEVER indexed)."""
    db = str(tmp_path / "doctor.sqlite3")
    init_db(db)
    conn = connect(db)
    try:
        index_repo(conn, "alpha", PYREPO, changed_only=False)
    finally:
        conn.close()
    port = free_port()
    # `python -m loom.server.app`, not `loom serve`: serve indexes every root at boot,
    # which would quietly index `beta` and take the freshness WARN away.
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--repo-root", f"alpha={PYREPO}",
         "--repo-root", f"beta={PYREPO2}", "--db", db, "--host", "127.0.0.1",
         "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:                          # pragma: no cover
                raise RuntimeError(f"server died: {proc.communicate()[1]}")
            try:
                socket.create_connection(("127.0.0.1", port), 0.25).close()
                break
            except OSError:
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                        # pragma: no cover
            proc.kill()


def run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["loom", *argv])
    cli_main()


def wired(monkeypatch: pytest.MonkeyPatch, tmp_path, base: str, repo: str) -> Path:
    """A checkout wired by the real `loom init` — the state doctor is meant to confirm."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    monkeypatch.delenv("LOOM_BYPASS", raising=False)
    root = tmp_path / f"checkout-{repo}"
    root.mkdir()
    run_cli(monkeypatch, "init", "--server", base, "--agent", "aria",
            "--repo-root", str(root), "--repo", repo)
    return root


Rows = dict[str, tuple[str, str]]


def doctor(monkeypatch: pytest.MonkeyPatch, capsys, root: Path) -> tuple[Rows, int, str]:
    """Run the verb; return ({check: (status, detail)}, exit_code, stderr)."""
    code = 0
    try:
        run_cli(monkeypatch, "doctor", "--repo-root", str(root))
    except SystemExit as exc:
        code = int(exc.code or 0)
    cap = capsys.readouterr()
    rows: Rows = {m.group(2).strip(): (m.group(1), m.group(3))
                  for m in map(ROW.match, cap.out.splitlines()) if m}
    assert list(rows) == list(CHECKS), cap.out    # all nine, always, in spec order
    return rows, code, cap.err


def statuses(rows: Rows, *, skip: str = "") -> list[str]:
    return [status for name, (status, _d) in rows.items() if name != skip]


# --------------------------------------------------------------------------- §6.f


def test_doctor_passes_every_check_on_a_wired_checkout(
        doctor_server, tmp_path, monkeypatch, capsys) -> None:
    base, _proc = doctor_server
    root = wired(monkeypatch, tmp_path, base, "alpha")

    rows, code, _err = doctor(monkeypatch, capsys, root)

    assert code == 0
    assert statuses(rows) == ["PASS"] * 9
    assert str(root / ".claude" / "loom.toml") in rows["config"][1]
    assert "repo=alpha" in rows["config"][1] and "agent=aria" in rows["config"][1]
    assert "alpha, beta" in rows["server"][1]        # served names, printed verbatim
    assert "auth=open" in rows["server"][1]          # D11: the mode, on the server row
    assert rows["auth"] == ("PASS", "server is open; no token configured")
    assert "loom-gate" in rows["gate binary"][1]
    assert "exit 2" in rows["gate round-trip"][1]    # the real chain, not a stub
    assert re.match(r"\d+ node\(s\) indexed for 'alpha'", rows["index freshness"][1])


def test_doctor_warns_but_exits_zero_when_the_repo_is_unindexed(
        doctor_server, tmp_path, monkeypatch, capsys) -> None:
    """§4.8: `beta` is served and correctly configured — it just has no graph yet."""
    base, _proc = doctor_server
    root = wired(monkeypatch, tmp_path, base, "beta")

    rows, code, _err = doctor(monkeypatch, capsys, root)

    assert code == 0                                 # WARN alone never fails the run
    assert rows["index freshness"][0] == "WARN"
    assert "loom index --repo beta" in rows["index freshness"][1]
    assert rows["repo match"] == ("PASS", "'beta' is served")
    assert statuses(rows, skip="index freshness") == ["PASS"] * 8


def test_doctor_fails_on_a_dead_server(doctor_server, tmp_path, monkeypatch, capsys) -> None:
    base, proc = doctor_server
    root = wired(monkeypatch, tmp_path, base, "alpha")
    proc.terminate()
    proc.wait(timeout=10)

    rows, code, err = doctor(monkeypatch, capsys, root)

    assert code == 1 and "check(s) failed" in err
    assert [rows[c][0] for c in ("server", "auth", "repo match",
                                 "index freshness")] == ["FAIL"] * 4
    assert "unreachable" in rows["server"][1] and "loom serve" in rows["server"][1]
    # ...and the LOCAL half of the chain is still green, so the operator knows where to look.
    assert [rows[c][0] for c in ("config", "gate binary", "hook registered",
                                 "mcp registered")] == ["PASS"] * 4
    # The §7.5 payload is denied hook-side, so the round trip stays honest with no server.
    assert rows["gate round-trip"] == ("PASS", "exit 2 (deny) from the real gate")


def test_doctor_fails_when_the_hook_is_not_registered(
        doctor_server, tmp_path, monkeypatch, capsys) -> None:
    base, _proc = doctor_server
    root = wired(monkeypatch, tmp_path, base, "alpha")
    settings = root / ".claude" / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [
        {"type": "command", "command": "/usr/bin/other-tool"}]}]}}), encoding="utf-8")

    rows, code, _err = doctor(monkeypatch, capsys, root)

    assert code == 1
    assert rows["hook registered"][0] == "FAIL"
    assert "run loom init" in rows["hook registered"][1]
    assert str(settings) in rows["hook registered"][1]
    assert statuses(rows, skip="hook registered") == ["PASS"] * 8


def test_doctor_fails_a_checkout_that_was_never_initialized(
        tmp_path, monkeypatch, capsys) -> None:
    """No config, no server, no hook: every dependent check says so instead of exploding."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    root = tmp_path / "bare"
    root.mkdir()

    rows, code, _err = doctor(monkeypatch, capsys, root)

    assert code == 1
    assert rows["config"][0] == "FAIL" and "loom init" in rows["config"][1]
    assert rows["server"] == ("FAIL", "skipped — no config to name a server")
    assert rows["gate round-trip"][0] == "FAIL"
    assert rows["gate binary"][0] == "PASS"          # the install itself is fine
