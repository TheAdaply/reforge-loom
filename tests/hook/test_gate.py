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
    'loom: no active plan for agent "akash-mbp". Before editing: write a spec from '
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
        {"agent": "akash-mbp", "repo": "demo", "path": "src/app.py",
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
