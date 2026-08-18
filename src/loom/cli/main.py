"""M3 — the loom CLI (BUILD-SPEC §9.1) and the §7.5 registration contract.

Verbs: serve · init · index · ls · show · release. ls/show/release are LOCAL-DB admin verbs
(agents use the MCP tools; remote CLI is v2). beads §2.3 conventions: an empty value on a
narrowing flag is a hard error, never a wildcard; agent mode (`CLAUDE_CODE` /
`LOOM_AGENT_MODE=1`) prints one line per row, no color; `--json` errors are JSON; truncation
notices reach stderr only when it is a tty. Cross-module imports stay inside §9.2's allowance
(`server.app`, `server.db`, `indexer.walk`), so these verbs carry their own small SQL.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from loom.server.db import connect, immediate, init_db, iso, log_event, now_s

# §7.5 frozen matcher groups: the exact-string list, then the SUFFIX regex — never
# `mcp__serena__.*`, because the MCP server key is user-minted (GATE-1 fix 4).
MATCHERS = (
    "Edit|Write|MultiEdit|NotebookEdit",
    "mcp__.*__(replace_symbol_body|insert_after_symbol|insert_before_symbol|rename_symbol"
    "|safe_delete_symbol|create_text_file|replace_content|replace_in_files|delete_lines"
    "|replace_lines|insert_at_line)",
)
HOOK_ENTRY = {"type": "command", "command": "", "args": [], "timeout": 5,
              "statusMessage": "loom: checking claims"}
# Deterministic exit-2 payload for post-write gate verification: `replace_in_files` over an
# unscoped path set is denied HOOK-side (§7.2), so the check never depends on index state.
VERIFY_PAYLOAD = {"tool_name": "mcp__loom__replace_in_files",
                  "tool_input": {"relative_path": "", "dry_run": False}}
# The ONE agent-invisible place the escape hatch is documented (§7.4 no-override law).
BYPASS_NOTE = ("Human escape hatch: LOOM_BYPASS=1 in your own shell makes the gate pass that\n"
               "process through. Every use is written to ~/.loom/gate-audit.jsonl. Agents are\n"
               "never told this exists — no deny message ever names a way around a claim.")
ACTIVE = "c.released IS NULL AND p.status = 'active' AND p.ttl_expires > ?"


def _die(msg: str, as_json: bool = False) -> None:
    sys.stderr.write((json.dumps({"ok": False, "error": msg}) if as_json else f"loom: {msg}") + "\n")
    raise SystemExit(1)


def _no_empty(args: argparse.Namespace, *flags: str) -> None:
    """beads §2.3: an empty narrowing value is a hard error, never a wildcard."""
    for flag in flags:
        if getattr(args, flag, None) == "":
            _die(f"--{flag.replace('_', '-')} was given an empty value; pass a real value")


def _repo_of(repo_root: str) -> str:
    return os.path.basename(repo_root.rstrip("/")) or "repo"


def _db_of(args: argparse.Namespace, repo_root: str = "") -> str:
    return args.db or os.path.join(repo_root or os.getcwd(), ".loom.sqlite3")


def _existing_db_of(args: argparse.Namespace) -> str:
    # Read verbs must not conjure an empty database at a mistyped path and
    # report "0 active claims" — a missing db is an operator error, said plainly.
    path = _db_of(args)
    if not os.path.exists(path):
        _die(f"no loom database at {path} — is `loom serve` running with this --db?")
    return path


def _templates() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


def _ref(row) -> str:
    return f"{row['path']}::{row['qualname']}" if row["qualname"] else (row["path"] or "")


def cmd_serve(args: argparse.Namespace) -> None:
    from loom.indexer.walk import index_repo
    from loom.server.app import serve

    repo_root = os.path.abspath(args.repo_root)
    repo = args.repo or _repo_of(repo_root)
    db = _db_of(args, repo_root)
    # PLAN §4.5: `loom serve` starts server PLUS indexer — one process, no separate
    # `loom index` step before first use. Incremental thereafter via mtime+hash.
    init_db(db)
    conn = connect(db)
    has_nodes = conn.execute("SELECT 1 FROM nodes LIMIT 1").fetchone() is not None
    stats = index_repo(conn, repo, repo_root, changed_only=has_nodes)
    conn.close()
    print(f"loom: indexed {json.dumps(stats, default=str)}", flush=True)
    # The repo salt is minted once, here, and echoed to every `loom init` (§11.19).
    serve(args.host, args.port, db, repo, repo_root)


def cmd_index(args: argparse.Namespace) -> None:
    from loom.indexer.walk import index_repo

    repo_root = os.path.abspath(args.repo_root)
    init_db(_db_of(args, repo_root))
    conn = connect(_db_of(args, repo_root))
    stats = index_repo(conn, _repo_of(repo_root), repo_root, changed_only=args.changed)
    conn.close()
    print(json.dumps(stats, default=str))


def _health(server: str) -> str:
    """Ping `GET /health` for the repo salt — one spelling, minted at serve (§11.19)."""
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/health", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _die(f"cannot reach {server}/health ({type(exc).__name__}); is `loom serve` running?")
    if not data.get("ok") or not data.get("repo"):
        _die(f"{server}/health did not name a repo; refusing to guess the id salt")
    return str(data["repo"])


def _merge_settings(repo_root: str, gate: str) -> int:
    """READ-MODIFY-WRITE `.claude/settings.json`; idempotent on the loom command string (§7.5)."""
    path = os.path.join(repo_root, ".claude", "settings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            _die(f"{path} is not a JSON object; fix it by hand — loom will not overwrite it")
    groups, added = data.setdefault("hooks", {}).setdefault("PreToolUse", []), 0
    for matcher in MATCHERS:
        group = next((g for g in groups if isinstance(g, dict) and g.get("matcher") == matcher), None)
        if group is None:
            group = {"matcher": matcher, "hooks": []}
            groups.append(group)
        hooks = group.setdefault("hooks", [])
        if not any(isinstance(h, dict) and h.get("command") == gate for h in hooks):
            hooks.append({**HOOK_ENTRY, "command": gate})
            added += 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return added


def _append_snippet(repo_root: str) -> bool:
    """Append §8.2 to the repo CLAUDE.md, idempotent on the protocol marker comment."""
    with open(os.path.join(_templates(), "CLAUDE.snippet.md"), encoding="utf-8") as fh:
        snippet = fh.read()
    path, existing = os.path.join(repo_root, "CLAUDE.md"), ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        if snippet.splitlines()[0] in existing:
            return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if existing and not existing.endswith("\n") else "") + "\n" + snippet)
    return True


def cmd_init(args: argparse.Namespace) -> None:
    _no_empty(args, "server", "agent")
    repo_root = os.path.abspath(args.repo_root) if args.repo_root else os.getcwd()
    gate = shutil.which("loom-gate")
    if not gate:
        _die("`loom-gate` is not on PATH; install loom (uv sync) and re-run loom init")
    repo = _health(args.server)
    agent = args.agent or os.environ.get("USER") or "agent"
    home = os.path.expanduser("~/.loom")
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "config.toml"), "w", encoding="utf-8") as fh:
        fh.write(f'server_url = "{args.server}"\nagent = "{agent}"\nrepo = "{repo}"\n'
                 f'repo_root = "{repo_root}"\n')
    added = _merge_settings(repo_root, gate)
    appended = _append_snippet(repo_root)
    # A mistyped command path leaves the gate silently disabled — prove exit 2 (§7.5).
    try:
        proc = subprocess.run([gate], input=json.dumps(VERIFY_PAYLOAD), capture_output=True,
                              text=True, timeout=20)
    except Exception as exc:
        _die(f"gate verification could not run {gate} ({type(exc).__name__})")
    if proc.returncode != 2:
        _die(f"gate verification failed: {gate} exited {proc.returncode}, expected 2")
    print(f"loom: initialized for repo '{repo}' as agent '{agent}'\n"
          f"  config    {os.path.join(home, 'config.toml')}\n"
          f"  hooks     {os.path.join(repo_root, '.claude', 'settings.json')} "
          f"({added} group(s) added, gate verified)\n"
          f"  protocol  {'appended to' if appended else 'already in'} "
          f"{os.path.join(repo_root, 'CLAUDE.md')}\n"
          f"  spec      {os.path.join(_templates(), 'spec.md')}\n\n{BYPASS_NOTE}")


def cmd_ls(args: argparse.Namespace) -> None:
    conn = connect(_existing_db_of(args))
    rows = conn.execute(
        "SELECT c.node_id, c.mode, c.plan_id, p.agent, p.title, p.ttl_expires, n.path, n.qualname "
        "FROM claims c LEFT JOIN plans p ON p.id = c.plan_id LEFT JOIN nodes n ON n.id = c.node_id "
        f"WHERE {ACTIVE} ORDER BY p.updated DESC", (now_s(),)).fetchall()
    conn.close()
    if args.json:
        print(json.dumps([{**dict(r), "ref": _ref(r), "expires_iso": iso(r["ttl_expires"])}
                          for r in rows]))
        return
    if not (os.environ.get("CLAUDE_CODE") or os.environ.get("LOOM_AGENT_MODE") == "1"):
        print(f"{len(rows)} active claim(s)")
    for r in rows:
        print(f"{r['mode']}\t{_ref(r)}\t{r['plan_id']}\t{r['agent']}\t{r['title']}"
              f"\t{iso(r['ttl_expires'])}")


def cmd_show(args: argparse.Namespace) -> None:
    conn = connect(_existing_db_of(args))
    if args.id.startswith("lm-"):
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (args.id,)).fetchone()
        if plan is None:
            _die(f"unknown plan {args.id}")
        rows = conn.execute(
            "SELECT c.mode, n.path, n.qualname FROM claims c LEFT JOIN nodes n ON n.id = c.node_id "
            "WHERE c.plan_id = ? AND c.released IS NULL", (args.id,)).fetchall()
        spec = plan["spec_md"]
        if len(spec) > 4000 and sys.stderr.isatty():
            sys.stderr.write("loom: spec truncated to 4000 chars\n")
        print(f"{plan['id']}  {plan['status']}  agent={plan['agent']}  repo={plan['repo']}\n"
              f"title: {plan['title']}\nexpires: {iso(plan['ttl_expires'])}\n"
              + "".join(f"  {r['mode']:5s} {_ref(r)}\n" for r in rows) + f"\n{spec[:4000]}")
    else:
        node = conn.execute("SELECT * FROM nodes WHERE id = ?", (args.id,)).fetchone()
        if node is None:
            _die(f"unknown node {args.id}")
        rows = conn.execute(
            "SELECT c.mode, c.plan_id, p.agent FROM claims c LEFT JOIN plans p ON p.id = c.plan_id "
            f"WHERE c.node_id = ? AND {ACTIVE}", (args.id, now_s())).fetchall()
        print(f"{node['id']}  {node['kind']}  {_ref(node)}\n"
              f"lines {node['start_line']}-{node['end_line']}  updated {node['updated']}\n"
              + "".join(f"  claimed {r['mode']} by {r['agent']} ({r['plan_id']})\n" for r in rows))
    conn.close()


def cmd_release(args: argparse.Namespace) -> None:
    _no_empty(args, "agent")
    conn = connect(_existing_db_of(args))
    stamp, err, freed = iso(now_s()), "", 0
    with immediate(conn):
        plan = conn.execute("SELECT agent, status FROM plans WHERE id = ?", (args.plan_id,)).fetchone()
        if plan is None:
            err = "unknown_plan"
        elif plan["agent"] != args.agent:
            err = "not_owner"
        elif plan["status"] != "active":
            err = "not_active"
        else:
            freed = conn.execute("UPDATE claims SET released = ? WHERE plan_id = ? AND released "
                                 "IS NULL", (stamp, args.plan_id)).rowcount
            conn.execute("UPDATE plans SET status = 'done', updated = ? WHERE id = ?",
                         (stamp, args.plan_id))
            log_event(conn, args.agent, "released", args.plan_id)
    conn.close()
    if err:
        _die(err)
    print(json.dumps({"ok": True, "released_claims": freed, "plan_status": "done"}))


VERBS = {
    "serve": (cmd_serve, "run the loom MCP + /gate server",
              [("--repo-root", {"required": True}), ("--repo", {"default": ""}),
               ("--host", {"default": "0.0.0.0"}), ("--port", {"type": int, "default": 8790}),
               ("--db", {})]),
    "init": (cmd_init, "register the PreToolUse gate in this repo",
             [("--server", {"required": True}), ("--agent", {}),
              ("--repo-root", {"help": "defaults to the current directory"})]),
    "index": (cmd_index, "index a repo into the loom graph",
              [("--repo-root", {"required": True}), ("--db", {}),
               ("--changed", {"action": "store_true"})]),
    "ls": (cmd_ls, "list active claims", [("--db", {}), ("--json", {"action": "store_true"})]),
    "show": (cmd_show, "show a plan (lm-...) or a node (n-...)", [("id", {}), ("--db", {})]),
    "release": (cmd_release, "release a plan's claims (owner only)",
                [("plan_id", {}), ("--agent", {"required": True}), ("--db", {})]),
}


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom", description="loom — spec-driven coordination gate")
    sub = ap.add_subparsers(dest="cmd")
    for name, (fn, help_text, flags) in VERBS.items():
        parser = sub.add_parser(name, help=help_text)
        parser.set_defaults(fn=fn)
        for flag, opts in flags:
            parser.add_argument(flag, **({"default": None} | opts if flag.startswith("-") else opts))
    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help(sys.stderr)
        raise SystemExit(2)
    args.fn(args)


if __name__ == "__main__":
    main()
