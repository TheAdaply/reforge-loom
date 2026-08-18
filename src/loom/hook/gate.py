"""M3 — BUILD-SPEC §7.1/§7.3: the PreToolUse gate. Exits ONLY 0 or 2, never 1.

stdlib + `loom.hook.locator` (-> `loom.indexer.naming`) only: importing `mcp`/starlette here
would burn the whole ~2 s PreToolUse budget on every edit (§1, §9.2). Every failure is
FAIL-OPEN — the coordination layer is advisory and must never brick an edit.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime

from loom.hook.locator import locate

_CFG_KEYS = ("server_url", "agent", "repo", "repo_root")
_UNREACHABLE = "loom: coordination server unreachable — edit allowed, claims NOT checked"
_NOT_INITIALIZED = "loom: not initialized — run loom init"
# `decide()` is pure (§9.1), so the audit record it computes is parked here for main()'s IO.
_REC: dict = {}


def load_config() -> dict | None:
    """`~/.loom/config.toml` via tomllib; missing or incomplete -> None (fail-open).

    Per-process overrides for the several-agents-one-OS-user case: `LOOM_CONFIG`
    replaces the config path, `LOOM_AGENT` replaces the agent identity."""
    try:
        path = os.environ.get("LOOM_CONFIG") or os.path.expanduser("~/.loom/config.toml")
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except Exception:
        return None
    if os.environ.get("LOOM_AGENT"):
        cfg["agent"] = os.environ["LOOM_AGENT"]
    return cfg if all(isinstance(cfg.get(k), str) and cfg.get(k) for k in _CFG_KEYS) else None


def call_gate(cfg: dict, body: dict, timeout_s: float = 1.5) -> dict:
    """POST the §6 wire body to `/gate`; urlopen raises on timeout / non-2xx / bad JSON."""
    req = urllib.request.Request(cfg["server_url"].rstrip("/") + "/gate", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fail_open(reason: str) -> tuple[int, str, str]:
    """Loud on stdout (the only channel visible at exit 0); stderr is the debug/audit line."""
    message = _NOT_INITIALIZED if reason == "no_config" else _UNREACHABLE
    return (0, json.dumps({"systemMessage": message}),
            f"loom: WARNING — gate failed open ({reason}); coordination degraded")


def decide(payload: dict, cfg: dict) -> tuple[int, str, str]:
    """(exit_code, stdout, stderr) for one PreToolUse payload; raises -> caller fails open."""
    tool_name = payload.get("tool_name") or payload.get("toolName")
    tool_input = payload.get("tool_input") or payload.get("toolInput")
    _REC.update(session_id=payload.get("session_id") or payload.get("sessionId") or "",
                agent_id=payload.get("agent_id") or payload.get("agentId") or "",
                tool_name=tool_name if isinstance(tool_name, str) else "")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return 0, "", ""
    loc = locate(tool_name, tool_input, cfg["repo_root"])
    if loc.action == "pass":
        return 0, "", ""
    _REC.update(path=loc.path, qualname=loc.qualname)
    if loc.action == "deny_local":
        _REC.update(decision="deny", case="unscoped")
        return 2, "", loc.message
    resp = call_gate(cfg, {"agent": cfg["agent"], "repo": cfg["repo"], "path": loc.path,
                           "qualname": loc.qualname, "tool_name": tool_name})
    if resp.get("decision") not in ("allow", "deny"):
        raise ValueError("malformed gate response")
    _REC.update(decision=resp["decision"], case=resp.get("case"), plan_id=resp.get("plan_id"))
    if resp["decision"] == "deny":
        return 2, "", str(resp.get("message") or "")
    return 0, "", ""


def audit(rec: dict) -> None:
    """One JSON line per decision under an exclusive flock; never raises (§7.3)."""
    try:
        home = os.path.expanduser("~/.loom")
        os.makedirs(home, exist_ok=True)
        line = json.dumps({k: v[:256] if isinstance(v, str) else v for k, v in rec.items()})
        with open(os.path.join(home, "gate-audit.jsonl"), "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(line + "\n")
    except Exception:
        pass


def main() -> None:
    """The console script: every path lands on exit 0 or exit 2 — never 1 (§7.3)."""
    try:
        _REC["ts"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        cfg = load_config()
        if os.environ.get("LOOM_BYPASS"):
            # Human-only escape hatch, documented solely in `loom init` output (§7.4).
            _REC.update(decision="bypass", case="bypass")
            verdict = (0, "", "loom: gate bypassed by LOOM_BYPASS; recorded in ~/.loom/gate-audit.jsonl")
        else:
            verdict = fail_open("no_config") if cfg is None else decide(payload, cfg)
    except BaseException as exc:  # noqa: BLE001 — fail-open IS the contract
        verdict = fail_open(type(exc).__name__)
    code, out, err = verdict
    _REC.setdefault("decision", "deny" if code else "allow")
    audit(_REC)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err + "\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
