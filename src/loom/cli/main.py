"""M3 — the loom CLI (BUILD-SPEC §9.1) and the §7.5 registration contract.

Verbs: serve · init · doctor · index · ls · show · release. ls/show/release are LOCAL-DB
admin verbs (agents use the MCP tools; remote CLI is v2). `doctor` is MULTIREPO-SPEC §4.
beads §2.3 conventions: an empty value on a
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
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NoReturn

from loom import __version__
from loom.server.db import connect, immediate, init_db, iso, now_s

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
# `loom doctor`'s round-trip payload, which must reach the SERVER (break3 chaos-F3 — the
# hook-side one above proved only that the binary runs). A `Write` to a path under the repo
# root always gates; this name is never a real file, so the honest answer is `new_path` —
# allow, silent, and impossible to get without a live `/gate` on the other end.
_PROBE_REL = ".loom-doctor-probe.py"
# The ONE agent-invisible place the escape hatch is documented (§7.4 no-override law).
BYPASS_NOTE = ("Human escape hatch: LOOM_BYPASS=1 in your own shell makes the gate pass that\n"
               "process through. Every use is written to ~/.loom/gate-audit.jsonl. Agents are\n"
               "never told this exists — no deny message ever names a way around a claim.")
ACTIVE = "c.released IS NULL AND p.status = 'active' AND p.ttl_expires > ?"
# The STABLE part of the snippet's marker comment — everything after it is prose that may
# be reworded. `_append_snippet` matches on this prefix, never on the whole first line, so
# rewording the marker cannot make init re-append the block to an already-initialized repo.
SNIPPET_MARKER = "<!-- loom protocol v1"


def probe_payload(repo_root: str) -> dict:
    """`loom doctor`'s check-8 stdin (break3 chaos-F3). Module-level so a test can pipe the
    same bytes the verb does."""
    return {"tool_name": "Write",
            "tool_input": {"file_path": os.path.join(repo_root, _PROBE_REL), "content": ""}}


def _die(msg: str) -> NoReturn:
    sys.stderr.write(f"loom: {msg}\n")
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
    """MULTIREPO-SPEC §1: one server, one db, N repos — `--repo-root` is repeatable.

    ITERATION-2-SPEC §3: `--token SECRET` (or `LOOM_TOKEN`) turns identity on. Both
    spellings are resolved by the SERVER's `resolve_token`, so `loom serve` and
    `python -m loom.server.app` cannot drift on precedence or on the empty-value error."""
    from loom.server.app import parse_repo_roots, resolve_token, serve

    token = ""
    try:
        repos = parse_repo_roots(args.repo_root or [], args.repo or "")
        token = resolve_token(args.token)
    except ValueError as exc:
        _die(str(exc))
    # With several roots the db lands beside the FIRST one unless --db says otherwise;
    # docs recommend an explicit --db for multi-repo, since "the first root" is a rule an
    # operator has to remember and a shared db is a thing a team wants to put on purpose.
    db = _db_of(args, next(iter(repos.values())))
    # PLAN §4.5: `loom serve` starts server PLUS indexer — one process, no separate
    # `loom index` step before first use. Incremental thereafter via mtime+hash, PER REPO.
    if os.environ.get("LOOM_ARM"):
        # `LOOM_ARM=claims_only` blanks `spec_md` everywhere it is surfaced. That turns every
        # deny into a refusal with no reason attached, which reads exactly like a bug. It is
        # an evaluation control arm, so say so at boot rather than let someone debug it.
        print(f"loom: LOOM_ARM={os.environ['LOOM_ARM']} — evaluation arm active; spec text is "
              "suppressed in conflicts and deny messages", flush=True)
    for repo, repo_root in repos.items():
        stats = _index(db, repo, repo_root, changed_only=None)
        print(f"loom: indexed {json.dumps({'repo': repo, **stats}, default=str)}", flush=True)
    # The repo salts are minted once, here, and echoed to every `loom init` (§11.19).
    serve(args.host, args.port, db, repos, token)


def _index(db: str, repo: str, repo_root: str, changed_only: bool | None) -> dict:
    """Shared index body for serve-at-boot and `loom index`. changed_only=None means
    "full on a fresh db, incremental once nodes exist" (the serve-boot rule)."""
    from loom.indexer.walk import index_repo

    init_db(db)
    conn = connect(db)
    try:
        if changed_only is None:
            # Scoped to THIS repo: with one db behind several repos, "warm" has to mean
            # "warm for this salt", or a second repo's first index would run incremental.
            changed_only = conn.execute("SELECT 1 FROM nodes WHERE repo=? LIMIT 1",
                                        (repo,)).fetchone() is not None
        # `index_repo` OWNS its transactions (§11.45, closing break3 chaos-F1): parsing
        # runs lock-free, node writes commit in chunks so concurrent /gate audit/renew
        # writes queue for one chunk at most, and only the edge swap is a single
        # transaction. Wrapping it in `immediate()` here would be a nested-BEGIN error —
        # and would re-create the whole-rebuild lock that made big-tree gate calls
        # exceed the hook budget and fail OPEN across every repo this database serves.
        started = time.monotonic()
        stats = index_repo(conn, repo, repo_root, changed_only=changed_only)
        sys.stderr.write(f"loom: indexed '{repo}' in {time.monotonic() - started:.1f}s "
                         "(write lock taken per chunk, never for the whole rebuild)\n")
    finally:
        conn.close()
    return stats


def cmd_index(args: argparse.Namespace) -> None:
    repo_root = os.path.abspath(args.repo_root)
    # Mirror `serve`: a team that pinned a stable salt with `serve --repo NAME` must be able
    # to re-index under it, or the served graph goes permanently stale (§11.19).
    repo = args.repo or _repo_of(repo_root)
    changed_only = False if args.full else (args.changed or None)
    stats = _index(_db_of(args, repo_root), repo, repo_root, changed_only=changed_only)
    print(json.dumps({"repo": repo, **stats}, default=str))


def _get_json(url: str, timeout: float, token: str = "") -> dict:
    """GET one JSON document; raises on timeout, non-2xx and malformed bodies alike.

    A non-empty `token` is sent as §3's shared bearer — a 401 arrives as `HTTPError`, which
    `doctor` catches by code to tell "wrong/missing credential" from "server is down"."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _served(data: dict) -> list[str]:
    """The repo salts a `/health` body names: the multi-repo `repos` list, falling back to
    the single `repo` key so a checkout initialized against an OLDER server (or any of the
    frozen test stubs) still works."""
    return [str(r) for r in (data.get("repos") or [])] or \
        ([str(data["repo"])] if data.get("repo") else [])


def _auth_mode(data: dict) -> str:
    """D8: `"token"` iff `/health` says so. Anything else — including a body from a server
    older than this spec, which has no `auth` key at all — reads as an OPEN server."""
    return "token" if str(data.get("auth") or "") == "token" else "open"


def _health(server: str) -> dict:
    """Ping `GET /health` and return the WHOLE body: the served repo salts (one spelling,
    minted at serve — §11.19) plus D8's `auth` mode. `/health` stays open on a tokened
    server precisely so this call can happen before the caller owns a credential."""
    try:
        data = _get_json(server.rstrip("/") + "/health", 5)
    except Exception as exc:
        _die(f"cannot reach {server}/health ({type(exc).__name__}); is `loom serve` running?")
    if not data.get("ok") or not _served(data):
        _die(f"{server}/health did not name a repo; refusing to guess the id salt")
    return data


def _pick_repo(server: str, asked: str | None, served: list[str]) -> str:
    """§1: one served repo -> the name is optional; many -> it is required, listed verbatim."""
    if asked:
        if asked not in served:
            _die(f"{server} does not serve repo '{asked}'; it serves: {', '.join(served)}")
        return asked
    if len(served) == 1:
        return served[0]
    _die(f"{server} serves several repos; pass --repo NAME with one of: {', '.join(served)}")


def _read_json_object(path: str) -> dict:
    """The READ half of `init`'s two read-modify-write files: `{}` when absent, a hard error
    when present and unusable. `doctor`'s `_json_file` answers the same question the other
    way round — it REPORTS a broken file as a failed row — because `init` is mid-way through
    writing a checkout and must neither dump a traceback nor overwrite a human's file."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            _die(f"{path} is not valid JSON; fix it by hand — loom will not overwrite it")
    if not isinstance(data, dict):
        _die(f"{path} is not a JSON object; fix it by hand — loom will not overwrite it")
    return data


def _merge_settings(repo_root: str, gate: str) -> int:
    """READ-MODIFY-WRITE `.claude/settings.json`; idempotent on the loom command string (§7.5)."""
    path = os.path.join(repo_root, ".claude", "settings.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = _read_json_object(path)
    # The same guard, one level down (break3 journey-J4c/J4d). A `hooks` that is an ARRAY
    # (`'list' object has no attribute 'setdefault'`) or a `PreToolUse` that is an OBJECT
    # (`'dict' object has no attribute 'append'`) is a hand-edited settings.json — Claude
    # Code's own schema names both keys — and it used to dump an AttributeError traceback
    # from the middle of `loom init`, after the config and before the hooks. Same style,
    # same promise as the two guards above: say what is wrong, write nothing.
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        _die(f'{path} has a non-object "hooks"; fix it by hand — loom will not overwrite it')
    groups, added = hooks.setdefault("PreToolUse", []), 0
    if not isinstance(groups, list):
        _die(f'{path} has a non-array "hooks.PreToolUse"; fix it by hand — loom will not '
             "overwrite it")
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
    if os.path.islink(path) and not os.path.realpath(path).startswith(
            os.path.realpath(repo_root) + os.sep):
        # break3 J4e: a CLAUDE.md symlinked OUT of the repo means init would write through
        # it into a file that is not this repo's. The snippet is advisory — skip, loudly.
        sys.stderr.write("loom: CLAUDE.md is a symlink out of the repo; snippet NOT appended"
                         " — add the loom section to the real instructions file yourself\n")
        return False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
        if SNIPPET_MARKER in existing:
            return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if existing and not existing.endswith("\n") else "") + "\n" + snippet)
    return True


def _merge_mcp_json(repo_root: str, server: str, token: str = "") -> bool:
    """Register loom's MCP tool surface in the repo's .mcp.json (idempotent MERGE).

    Without this the protocol is a trap: CLAUDE.md tells agents to declare_plan and
    the gate denies them for not declaring, but a fresh session has no way to call
    the tool. Found by the post-MVP red-team pass; the hook enforces, this enables."""
    path = os.path.join(repo_root, ".mcp.json")
    data = _read_json_object(path)
    servers = data.setdefault("mcpServers", {})
    entry = {"type": "http", "url": server.rstrip("/") + "/mcp"}
    if token:
        # D10: the MCP client is a THIRD caller (after the hook and the CLI) and the only
        # one loom does not run itself — Claude Code reads this file, so the credential has
        # to travel with the registration or `/mcp` answers it 401 forever.
        entry["headers"] = {"Authorization": f"Bearer {token}"}
    if servers.get("loom") == entry:
        return False
    servers["loom"] = entry
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return True


def _toml_str(v: str) -> str:
    """A TOML basic string. `json.dumps` escapes `"` and `\\` exactly as TOML does, so an
    unescaped quote or a Windows-shaped path can no longer produce a file that
    `gate.load_config` silently rejects — leaving a permanently fail-open gate whose only
    symptom is a warning line."""
    return json.dumps(v)


def _write_config(path: str, server: str, agent: str, repo: str, repo_root: str,
                  token: str = "") -> None:
    """Write the 4-key §7.5 config TOML, creating its directory.

    D10 adds an OPTIONAL fifth key. It is omitted entirely for an open server, so the file
    an open checkout carries is byte-identical to the one it carried before ITERATION-2 —
    and `gate.load_config` keeps requiring exactly the four mandatory keys."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"server_url = {_toml_str(server)}\nagent = {_toml_str(agent)}\n"
                 f"repo = {_toml_str(repo)}\nrepo_root = {_toml_str(repo_root)}\n"
                 + (f"token = {_toml_str(token)}\n" if token else ""))


def _ignore_identity(repo_root: str) -> bool:
    """Idempotently gitignore loom's per-checkout files. The PER-USER identity file: found
    live in the two-user simulation — one user's `git add -A` committed their `loom.toml`
    into the shared repo, and pulling it would silently re-identify every teammate as
    them. The database (break4 cli-F2): the default layout puts `.loom.sqlite3*` inside
    the served checkout, and committing a live WAL-mode database is never right."""
    path = os.path.join(repo_root, ".gitignore")
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    have = set(existing.splitlines())
    missing = [ln for ln in (".claude/loom.toml", ".loom.sqlite3*") if ln not in have]
    if not missing:
        return False
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("" if not existing or existing.endswith("\n") else "\n")
                 + "".join(ln + "\n" for ln in missing))
    return True


def cmd_init(args: argparse.Namespace) -> None:
    _no_empty(args, "server", "agent", "repo", "token")
    repo_root = os.path.abspath(args.repo_root) if args.repo_root else os.getcwd()
    gate = shutil.which("loom-gate")
    if not gate:
        _die("`loom-gate` is not on PATH; install loom (uv sync) and re-run loom init")
    health = _health(args.server)
    repo = _pick_repo(args.server, args.repo, _served(health))
    token = args.token or ""
    # §3: the token is required IFF the server says it is. Checked here, before the first
    # file is written, so a forgotten --token leaves no half-wired checkout behind — and
    # never accepted silently against an open server that would ignore it.
    if _auth_mode(health) == "token" and not token:
        _die(f"{args.server} requires a token — re-run loom init with --token SECRET "
             "(ask whoever runs `loom serve` for the team's shared secret)")
    agent = args.agent or os.environ.get("USER") or "agent"
    home = os.path.expanduser("~/.loom")
    # PER-REPO config alongside the settings.json this verb already writes: the ONE global
    # slot is a single point of clobber — a second `loom init` for another repo silently
    # pointed the first repo's gate at the second repo's server. The global copy stays for
    # backward compat; `gate.load_config` prefers the per-repo file it walks up to.
    local_cfg = os.path.join(repo_root, ".claude", "loom.toml")
    _write_config(local_cfg, args.server, agent, repo, repo_root, token)
    # The GLOBAL copy is written only if there is none yet. `gate.load_config` still READS it
    # as the last fallback, but rewriting it on every init re-creates the exact stale-global
    # bug the per-repo file was added to fix: initializing a second repo would repoint the
    # first repo's fallback at the second repo's server, which answers `allow` for a salt it
    # does not serve.
    global_cfg = os.path.join(home, "config.toml")
    if not os.path.exists(global_cfg):
        _write_config(global_cfg, args.server, agent, repo, repo_root, token)
    added = _merge_settings(repo_root, gate)
    mcp_added = _merge_mcp_json(repo_root, args.server, token)
    appended = _append_snippet(repo_root)
    _ignore_identity(repo_root)      # the per-user loom.toml must never be committed
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
          f"  fallback  {global_cfg}\n"
          f"  hooks     {os.path.join(repo_root, '.claude', 'settings.json')} "
          f"({added} group(s) added, gate verified)\n"
          f"  mcp       {os.path.join(repo_root, '.mcp.json')} "
          f"({'loom server added' if mcp_added else 'already registered'}"
          f"{', with the shared-token header' if token else ''})\n"
          f"  protocol  {'appended to' if appended else 'already in'} "
          f"{os.path.join(repo_root, 'CLAUDE.md')}\n"
          f"  spec      {os.path.join(_templates(), 'spec.md')}\n"
          f"  override  LOOM_CONFIG=<path> replaces the config above; LOOM_AGENT=<name>\n"
          f"            replaces the identity — for several agents under one OS user\n"
          f"\n{BYPASS_NOTE}")


def _walk_up(start: str, *parts: str) -> str | None:
    """Nearest `<ancestor>/<*parts>` at or above `start` — the walk `gate` does for its
    per-repo config, applied to `.claude/settings.json` and `.mcp.json` (which Claude Code
    itself also resolves by walking up from the session's cwd)."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, *parts)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _json_file(path: str | None) -> dict:
    """A JSON object read from `path`; anything unreadable or non-object reads as empty —
    doctor REPORTS a broken file as a failed check, it never dies on one."""
    try:
        with open(path, encoding="utf-8") as fh:                  # type: ignore[arg-type]
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def cmd_doctor(args: argparse.Namespace) -> None:
    """MULTIREPO-SPEC §4 + ITERATION-2-SPEC D11 + U2: ten checks over ONE checkout,
    PASS/FAIL/WARN, exit 0 iff no FAIL.

    The sellability tool — "is loom actually wired up here?" answered without reading any
    docs. Every check catches its own failure and degrades to a FAIL row: the table always
    prints all ten, because the row that explains the breakage is usually not the first
    one to fail. `loom-gate` runs for real (check 8), so a green table means the whole
    chain — config discovery, hook binary, server, auth, claim decision — just worked.

    Checks 9 and 10 are the two ways a graph can be untrue — absent, and behind — and both
    are WARN-only: neither can ever fail a CI run over something a single `loom index` fixes.
    """
    from loom.hook import gate as hook_gate  # lazy: keeps `loom serve`'s import graph thin

    start = os.path.abspath(args.repo_root) if args.repo_root else os.getcwd()
    rows: list[tuple[str, str, str]] = []

    def row(name: str, ok: bool, detail: str, warn: bool = False) -> None:
        rows.append(("WARN" if warn else "PASS" if ok else "FAIL", name, detail))

    # 1 config — the gate's own discovery, so doctor and the hook can never disagree.
    cfg_path = (os.environ.get("LOOM_CONFIG") or hook_gate._walk_up_config(start)
                or os.path.expanduser("~/.loom/config.toml"))
    cfg = hook_gate.load_config(hook_gate.config_start_dir({"cwd": start}))
    base = cfg["server_url"].rstrip("/") if cfg else ""     # every URL below is built on it
    row("config", cfg is not None,
        f"{cfg_path} (repo={cfg['repo']}, agent={cfg['agent']})" if cfg else
        f"no usable config at {cfg_path} — run `loom init --server URL` in this repo")

    # 2 server — `/health` is the one route a tokened server leaves open, so this row is
    # readable even when every other server-side row is about to say 401.
    served: list[str] = []
    mode = "open"
    detail = "skipped — no config to name a server"
    if cfg:
        try:
            data = _get_json(base + "/health", 3)
            served = _served(data)
            mode = _auth_mode(data)
            detail = (f"{cfg['server_url']} serves: {', '.join(served) or '(nothing)'} "
                      f"(auth={mode})")
        except Exception as exc:
            detail = (f"{cfg['server_url']} unreachable ({type(exc).__name__}) — "
                      "is `loom serve` running?")
    row("server", bool(served), detail)

    # 3 auth (D11) — identity is opt-in, so the only failure that matters is a server that
    # WANTS a credential this checkout cannot produce. Probed over `/state` rather than
    # trusted from `/health`'s advertisement: a token that is present but WRONG looks
    # identical in the config and lands as the same 401 every real call would get.
    cfg_token = str(cfg.get("token") or "") if cfg else ""
    ok, detail = False, "skipped — no reachable server to ask"
    if cfg and served:
        try:
            _get_json(base + "/state", 3, cfg_token)
            ok = True
            detail = ("shared token accepted" if cfg_token else
                      "server is open; no token configured")
        except urllib.error.HTTPError as exc:
            detail = ("server requires a token — re-run loom init with --token"
                      if exc.code == 401 else f"/state answered HTTP {exc.code}")
        except Exception as exc:
            detail = f"/state unreachable ({type(exc).__name__})"
    row("auth", ok, detail)

    # 4 repo match — a salt the server does not serve is answered `allow/unindexed`
    # forever: the gate looks alive and checks nothing. That is the silent failure.
    repo = cfg["repo"] if cfg else ""
    row("repo match", bool(repo) and repo in served,
        f"'{repo}' is served" if repo and repo in served else
        f"config repo '{repo}' is not served ({', '.join(served) or 'nothing served'})")

    # 5 gate binary
    gate_bin = shutil.which("loom-gate") or ""
    row("gate binary", bool(gate_bin) and os.access(gate_bin, os.X_OK),
        gate_bin or "`loom-gate` is not on PATH — install loom (uv sync) and re-run loom init")

    # 6 hook registered — match the COMMAND SUBSTRING, not today's `shutil.which` path: a
    # moved venv is a stale hook to fix, but an unmoved one must not read as unregistered.
    settings = _walk_up(start, ".claude", "settings.json")
    hooked = any("loom-gate" in str(hook.get("command") or "")
                 for group in (_json_file(settings).get("hooks") or {}).get("PreToolUse") or []
                 if isinstance(group, dict)
                 for hook in group.get("hooks") or [] if isinstance(hook, dict))
    row("hook registered", hooked, settings if hooked else
        f"no loom-gate PreToolUse hook in {settings or '<no .claude/settings.json>'} "
        "— run loom init")

    # 7 mcp registered
    mcp_path = _walk_up(start, ".mcp.json")
    want = base + "/mcp" if cfg else ""
    entry = (_json_file(mcp_path).get("mcpServers") or {}).get("loom") or {}
    url = str(entry.get("url") or "") if isinstance(entry, dict) else ""
    row("mcp registered", bool(want) and url == want, f"{mcp_path} -> {url}" if want and url == want
        else f"mcpServers.loom.url is {url or 'absent'} in "
             f"{mcp_path or '<no .mcp.json>'}, expected {want or '<no config>'}")

    # 8 gate round-trip — the whole chain, SERVER INCLUDED (break3 chaos-F3). This row used
    # to pipe the §7.5 VERIFY_PAYLOAD, which `locator.deny_local` refuses hook-side: it never
    # reached `call_gate`, so it printed PASS with the server dead or `/gate` answering 500 —
    # a green table over the one failure the operator most needs to see. `PROBE_PAYLOAD`
    # names a path under this repo root instead, which the gate must ask the server about.
    # The verdict is deliberately not a decision: any answer at all means the chain works
    # (a never-written path answers `new_path`, an unindexed repo `unindexed` — both allow,
    # both silent). A fail-open, a bypass or a deny all write to stderr, and that is the FAIL.
    ok, detail = False, "skipped — needs a config and `loom-gate` on PATH"
    if cfg and gate_bin:
        try:
            proc = subprocess.run([gate_bin], input=json.dumps(probe_payload(cfg["repo_root"])),
                                  text=True, capture_output=True, timeout=20,
                                  env={**os.environ, "LOOM_CONFIG": cfg_path})
            noise = proc.stderr.strip()
            ok = proc.returncode == 0 and not noise
            detail = ("the real gate reached the server and it answered" if ok else
                      f"exit {proc.returncode}: {noise[-120:] or 'no diagnostic'}")
        except Exception as exc:
            detail = f"could not run {gate_bin} ({type(exc).__name__})"
    row("gate round-trip", ok, detail)

    # 9 index freshness / 10 index staleness — both WARN, never FAIL: an unindexed or a
    # behind repo gates less than it should, but each is a one-command fix and neither is a
    # reason to fail a CI-style check. One `/state` fetch answers both rows.
    # break3 chaos-F7: this row printed FAIL whenever it could not ask — no server, or an
    # unserved repo — contradicting its own "both WARN, never FAIL" contract two lines up
    # and §4.8. It never fired alone, so no exit code ever turned on it; it was still the
    # table telling an operator the graph is broken when the truth is "the row above already
    # said the server is down". Cannot-tell is a WARN, exactly like behind-the-tree.
    state: dict = {}
    ok, warn, detail = False, True, "cannot tell — server or repo unknown; see the rows above"
    if cfg and repo in served and served:
        try:
            state = _get_json(f"{base}/state?repo={urllib.parse.quote(repo)}", 3, cfg_token)
            nodes = int((state.get("counts") or {}).get("nodes") or 0)
            ok, warn = nodes > 0, nodes == 0
            detail = (f"{nodes} node(s) indexed for '{repo}'" if nodes else
                      f"'{repo}' has no indexed nodes — run `loom index --repo {repo} "
                      "--repo-root PATH`")
        except Exception as exc:
            detail = f"/state unreachable ({type(exc).__name__})"
    row("index freshness", ok, detail, warn=warn)

    # 10 index staleness (U2) — the graph exists but the working tree has moved past it.
    # A server older than U2 sends no `index_age` at all, which reads as "cannot tell" and
    # must not manufacture a warning: absent evidence is not evidence of staleness.
    age = state.get("index_age") if isinstance(state.get("index_age"), dict) else None
    stale = bool(age and age.get("stale"))
    detail = "cannot tell — no /state answer to read an index age from"
    if age is not None:
        dirty = int(age.get("dirty_files") or 0)
        detail = (f"index behind working tree — {dirty} file(s) changed since the last "
                  f"index; run `loom index --repo {repo} --repo-root PATH --changed`"
                  if stale else
                  "index matches the working tree" if age.get("indexed_at") else
                  f"'{repo}' has never been indexed — see the row above")
    row("index staleness", not stale, detail, warn=stale)

    pad = max(len(name) for _s, name, _d in rows)
    print(f"loom doctor — {start}")
    for status, name, detail in rows:
        print(f"  {status}  {name.ljust(pad)}  {detail}")
    failed = sum(status == "FAIL" for status, _n, _d in rows)
    if failed:
        sys.stdout.flush()          # the table is the answer; keep it ABOVE the exit line
        _die(f"{failed} check(s) failed")
    print(f"  ok — {len(rows)} checks, no failures")


def cmd_ls(args: argparse.Namespace) -> None:
    conn = connect(_existing_db_of(args))
    rows = conn.execute(
        "SELECT c.node_id, c.mode, c.origin, c.plan_id, p.agent, p.title, p.ttl_expires, "
        "n.path, n.qualname "
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
        # §11.40: an expansion-acquired claim covers its own node ONLY — printed the same
        # as a named claim, the board would answer the opposite of the gate one level down.
        mode = r["mode"] + (" (expanded)" if r["origin"] == "expanded" else "")
        print(f"{mode}\t{_ref(r)}\t{r['plan_id']}\t{r['agent']}\t{r['title']}"
              f"\t{iso(r['ttl_expires'])}")


def cmd_show(args: argparse.Namespace) -> None:
    conn = connect(_existing_db_of(args))
    if args.id.startswith("lm-"):
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (args.id,)).fetchone()
        if plan is None:
            _die(f"unknown plan {args.id}")
        rows = conn.execute(
            "SELECT c.mode, c.origin, n.path, n.qualname FROM claims c "
            "LEFT JOIN nodes n ON n.id = c.node_id "
            "WHERE c.plan_id = ? AND c.released IS NULL", (args.id,)).fetchall()
        spec = plan["spec_md"]
        if len(spec) > 4000 and sys.stderr.isatty():
            sys.stderr.write("loom: spec truncated to 4000 chars\n")
        print(f"{plan['id']}  {plan['status']}  agent={plan['agent']}  repo={plan['repo']}\n"
              f"title: {plan['title']}\nexpires: {iso(plan['ttl_expires'])}\n"
              + "".join(f"  {r['mode']:5s} {_ref(r)}"
                        + (" (expanded — this node only)" if r["origin"] == "expanded" else "")
                        + "\n" for r in rows) + f"\n{spec[:4000]}")
    else:
        node = conn.execute("SELECT * FROM nodes WHERE id = ?", (args.id,)).fetchone()
        if node is None:
            _die(f"unknown node {args.id}")
        rows = conn.execute(
            "SELECT c.mode, c.origin, c.plan_id, p.agent FROM claims c "
            "LEFT JOIN plans p ON p.id = c.plan_id "
            f"WHERE c.node_id = ? AND {ACTIVE}", (args.id, now_s())).fetchall()
        print(f"{node['id']}  {node['kind']}  {_ref(node)}\n"
              f"lines {node['start_line']}-{node['end_line']}  updated {node['updated']}\n"
              + "".join(f"  claimed {r['mode']} by {r['agent']} ({r['plan_id']}"
                        + (", expanded — this node only" if r["origin"] == "expanded" else "")
                        + ")\n" for r in rows))
    conn.close()


def cmd_release(args: argparse.Namespace) -> None:
    """The MCP `release` tool over the local database. Deliberately NOT its own copy of the
    ownership rules — a second spelling of "who may release what" is a second thing to keep
    true."""
    from loom.server.claims import release as claims_release

    _no_empty(args, "agent")
    conn = connect(_existing_db_of(args))
    try:
        with immediate(conn):
            result = claims_release(conn, args.plan_id, args.agent, "done", now_s())
    finally:
        conn.close()
    if not result["ok"]:
        _die(result["reason"])
    print(json.dumps(result))


VERBS = {
    "serve": (cmd_serve, "run the loom MCP + /gate server",
              [("--repo-root", {"required": True, "action": "append",
                                "help": "PATH or NAME=PATH; repeat for several repos"}),
               ("--repo", {"default": "", "help": "rename the single repo of a one-root server"}),
               ("--host", {"default": "0.0.0.0"}), ("--port", {"type": int, "default": 8790}),
               ("--db", {"help": "recommended when serving several repos"}),
               ("--token", {"help": "shared secret required on /gate, /state and /mcp; "
                                    "env LOOM_TOKEN is the fallback (flag wins)"})]),
    "init": (cmd_init, "register the PreToolUse gate in this repo",
             [("--server", {"required": True}), ("--agent", {}),
              ("--repo", {"help": "required when the server serves several repos"}),
              ("--repo-root", {"help": "defaults to the current directory"}),
              ("--token", {"help": "the server's shared secret; required iff /health "
                                   "reports auth=token"})]),
    "doctor": (cmd_doctor, "check this checkout's loom wiring end to end",
               [("--repo-root", {"help": "defaults to the current directory"})]),
    "index": (cmd_index, "index a repo into the loom graph",
              [("--repo-root", {"required": True}), ("--repo", {"default": ""}), ("--db", {}),
               # `default=False` is load-bearing: main() merges `{"default": None} | opts`, and
               # None means "auto" downstream, which made the flag a no-op. --full is its
               # opposite: rebuild every node row even on a warm database.
               ("--changed", {"action": "store_true", "default": False,
                              "help": "skip re-writing node rows for unchanged files"}),
               ("--full", {"action": "store_true", "default": False,
                           "help": "force a full rebuild instead of the automatic choice"})]),
    "ls": (cmd_ls, "list active claims", [("--db", {}), ("--json", {"action": "store_true"})]),
    "show": (cmd_show, "show a plan (lm-...) or a node (n-...)", [("id", {}), ("--db", {})]),
    "release": (cmd_release, "release a plan's claims (owner only)",
                [("plan_id", {}), ("--agent", {"required": True}), ("--db", {})]),
}


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom", description="loom — spec-driven coordination gate")
    ap.add_argument("--version", action="version", version=f"loom {__version__}")
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
