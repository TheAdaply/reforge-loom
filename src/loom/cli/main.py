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
# The STABLE part of the snippet's marker comment — everything after it is prose that may
# be reworded. `_append_snippet` matches on this prefix, never on the whole first line, so
# rewording the marker cannot make init re-append the block to an already-initialized repo.
SNIPPET_MARKER = "<!-- loom protocol v1"


def _die(msg: str, as_json: bool = False) -> None:
    sys.stderr.write((json.dumps({"ok": False, "error": msg}) if as_json else f"loom: {msg}") + "\n")
    raise SystemExit(1)


def _no_empty(args: argparse.Namespace, *flags: str) -> None:
    """beads §2.3: an empty narrowing value is a hard error, never a wildcard."""
    for flag in flags:
        if getattr(args, flag, None) == "":
            _die(f"--{flag.replace('_', '-')} was given an empty value; pass a real value")


def _repo_of(repo_root: str) -> str:
    # The `index` verb's default salt. Same basename rule `server.app.parse_repo_roots`
    # applies to a bare `--repo-root PATH`, so `loom index` and `loom serve` land on the
    # same name for the same checkout; serve reaches into the server for it, index keeps
    # this one because it never parses the multi-root forms.
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
    # Same display form as `server.ids.node_ref`, KEPT SEPARATE on purpose: this one takes a
    # ROW and tolerates NULLs. `ls`/`show` LEFT JOIN `nodes`, so a claim whose node was
    # dropped by a re-index (FINDINGS indexer-F5) arrives with path/qualname NULL, where
    # `node_ref(None, None)` would return None and print "None". Keep the `or ""`.
    return f"{row['path']}::{row['qualname']}" if row["qualname"] else (row["path"] or "")


def cmd_serve(args: argparse.Namespace) -> None:
    """MULTIREPO-SPEC §1: one server, one db, N repos — `--repo-root` is repeatable."""
    from loom.server.app import parse_repo_roots, serve

    try:
        repos = parse_repo_roots(args.repo_root or [], args.repo or "")
    except ValueError as exc:
        _die(str(exc))
    # With several roots the db lands beside the FIRST one unless --db says otherwise;
    # docs recommend an explicit --db for multi-repo, since "the first root" is a rule an
    # operator has to remember and a shared db is a thing a team wants to put on purpose.
    db = _db_of(args, next(iter(repos.values())))
    # PLAN §4.5: `loom serve` starts server PLUS indexer — one process, no separate
    # `loom index` step before first use. Incremental thereafter via mtime+hash, PER REPO.
    for repo, repo_root in repos.items():
        stats = _index(db, repo, repo_root, changed_only=None)
        print(f"loom: indexed {json.dumps({'repo': repo, **stats}, default=str)}", flush=True)
    # The repo salts are minted once, here, and echoed to every `loom init` (§11.19).
    serve(args.host, args.port, db, repos)


def _index(db: str, repo: str, repo_root: str, changed_only: bool | None) -> dict:
    """Shared index body for serve-at-boot and `loom index`. changed_only=None means
    "full on a fresh db, incremental once nodes exist" (the serve-boot rule)."""
    from loom.indexer.walk import index_repo

    init_db(db)
    conn = connect(db)
    if changed_only is None:
        # Scoped to THIS repo: with one db behind several repos, "warm" has to mean
        # "warm for this salt", or a second repo's first index would run incremental.
        changed_only = conn.execute("SELECT 1 FROM nodes WHERE repo=? LIMIT 1",
                                    (repo,)).fetchone() is not None
    stats = index_repo(conn, repo, repo_root, changed_only=changed_only)
    conn.close()
    return stats


def cmd_index(args: argparse.Namespace) -> None:
    repo_root = os.path.abspath(args.repo_root)
    # Mirror `serve`: a team that pinned a stable salt with `serve --repo NAME` must be able
    # to re-index under it, or the served graph goes permanently stale (§11.19).
    repo = args.repo or _repo_of(repo_root)
    stats = _index(_db_of(args, repo_root), repo, repo_root, changed_only=args.changed)
    print(json.dumps({"repo": repo, **stats}, default=str))


def _health(server: str) -> list[str]:
    """Ping `GET /health` for the served repo salts — one spelling, minted at serve (§11.19).

    Reads the multi-repo `repos` list, falling back to the single `repo` key so a checkout
    initialized against an OLDER server (or any of the frozen test stubs) still works.
    """
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/health", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _die(f"cannot reach {server}/health ({type(exc).__name__}); is `loom serve` running?")
    served = [str(r) for r in (data.get("repos") or [])] or \
        ([str(data["repo"])] if data.get("repo") else [])
    if not data.get("ok") or not served:
        _die(f"{server}/health did not name a repo; refusing to guess the id salt")
    return served


def _pick_repo(server: str, asked: str | None, served: list[str]) -> str:
    """§1: one served repo -> the name is optional; many -> it is required, listed verbatim."""
    if asked:
        if asked not in served:
            _die(f"{server} does not serve repo '{asked}'; it serves: {', '.join(served)}")
        return asked
    if len(served) == 1:
        return served[0]
    _die(f"{server} serves several repos; pass --repo NAME with one of: {', '.join(served)}")
    raise AssertionError                                   # pragma: no cover — _die exits


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
    """Append §8.2 to the repo CLAUDE.md, idempotent on `SNIPPET_MARKER`."""
    with open(os.path.join(_templates(), "CLAUDE.snippet.md"), encoding="utf-8") as fh:
        snippet = fh.read()
    path, existing = os.path.join(repo_root, "CLAUDE.md"), ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        if SNIPPET_MARKER in existing:
            return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if existing and not existing.endswith("\n") else "") + "\n" + snippet)
    return True


def _merge_mcp_json(repo_root: str, server: str) -> bool:
    """Register loom's MCP tool surface in the repo's .mcp.json (idempotent MERGE).

    Without this the protocol is a trap: CLAUDE.md tells agents to declare_plan and
    the gate denies them for not declaring, but a fresh session has no way to call
    the tool. Found by the post-MVP red-team pass; the hook enforces, this enables."""
    path = os.path.join(repo_root, ".mcp.json")
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            _die(f"{path} is not a JSON object; fix it by hand — loom will not overwrite it")
    servers = data.setdefault("mcpServers", {})
    entry = {"type": "http", "url": server.rstrip("/") + "/mcp"}
    if servers.get("loom") == entry:
        return False
    servers["loom"] = entry
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return True


def _write_config(path: str, server: str, agent: str, repo: str, repo_root: str) -> None:
    """Write the 4-key §7.5 config TOML, creating its directory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'server_url = "{server}"\nagent = "{agent}"\nrepo = "{repo}"\n'
                 f'repo_root = "{repo_root}"\n')


def cmd_init(args: argparse.Namespace) -> None:
    _no_empty(args, "server", "agent", "repo")
    repo_root = os.path.abspath(args.repo_root) if args.repo_root else os.getcwd()
    gate = shutil.which("loom-gate")
    if not gate:
        _die("`loom-gate` is not on PATH; install loom (uv sync) and re-run loom init")
    repo = _pick_repo(args.server, args.repo, _health(args.server))
    agent = args.agent or os.environ.get("USER") or "agent"
    home = os.path.expanduser("~/.loom")
    # PER-REPO config alongside the settings.json this verb already writes: the ONE global
    # slot is a single point of clobber — a second `loom init` for another repo silently
    # pointed the first repo's gate at the second repo's server. The global copy stays for
    # backward compat; `gate.load_config` prefers the per-repo file it walks up to.
    local_cfg = os.path.join(repo_root, ".claude", "loom.toml")
    _write_config(local_cfg, args.server, agent, repo, repo_root)
    _write_config(os.path.join(home, "config.toml"), args.server, agent, repo, repo_root)
    added = _merge_settings(repo_root, gate)
    mcp_added = _merge_mcp_json(repo_root, args.server)
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
          f"  config    {local_cfg} (per-repo; wins for edits under {repo_root})\n"
          f"  fallback  {os.path.join(home, 'config.toml')}\n"
          f"  hooks     {os.path.join(repo_root, '.claude', 'settings.json')} "
          f"({added} group(s) added, gate verified)\n"
          f"  mcp       {os.path.join(repo_root, '.mcp.json')} "
          f"({'loom server added' if mcp_added else 'already registered'})\n"
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
        plan = conn.execute("SELECT agent, repo, status FROM plans WHERE id = ?",
                            (args.plan_id,)).fetchone()
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
            log_event(conn, args.agent, "released", args.plan_id, plan["repo"])
    conn.close()
    if err:
        _die(err)
    print(json.dumps({"ok": True, "released_claims": freed, "plan_status": "done"}))


VERBS = {
    "serve": (cmd_serve, "run the loom MCP + /gate server",
              [("--repo-root", {"required": True, "action": "append",
                                "help": "PATH or NAME=PATH; repeat for several repos"}),
               ("--repo", {"default": "", "help": "rename the single repo of a one-root server"}),
               ("--host", {"default": "0.0.0.0"}), ("--port", {"type": int, "default": 8790}),
               ("--db", {"help": "recommended when serving several repos"})]),
    "init": (cmd_init, "register the PreToolUse gate in this repo",
             [("--server", {"required": True}), ("--agent", {}),
              ("--repo", {"help": "required when the server serves several repos"}),
              ("--repo-root", {"help": "defaults to the current directory"})]),
    "index": (cmd_index, "index a repo into the loom graph",
              [("--repo-root", {"required": True}), ("--repo", {"default": ""}), ("--db", {}),
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
