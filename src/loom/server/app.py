"""BUILD-SPEC §5.11 / §6 — MCPServer construction and the four plain-HTTP routes:
`GET /health`, `GET /` + `GET /state` (the dashboard and its poll feed), `POST /gate`.

The nine §5 tools live in `loom.server.tools` and are registered here. State (the ORDERED
repo-salt map and the one long-lived connection) is built in `build_server` and closed
over — no env-var singletons (specgate §3.6). The server never needs `repo_root` to judge:
it judges the graph, and every path on the wire is already repo-root-relative — the roots
carried in `repos` are there so one process can name (and, via the CLI, index) several
repos. The hook speaks ONLY `POST /gate` on this same port, never MCP (§11.14).

MULTIREPO-SPEC §2 (D2-D5) is the multi-repo layer: one db, N repo salts, `served[0]` as
the default everywhere a name is omitted — which is exactly how the single-repo behavior
of BUILD-SPEC survives unchanged.

ITERATION-2-SPEC adds two honest-about-itself layers: §2 (D7) makes `/state`'s caps visible
instead of silent, and ITERATION-2 §3 (D8-D9) puts an OPT-IN shared token in front of `/gate`,
`/state`
and `/mcp` while `/health` stays open and names the mode.
"""

from __future__ import annotations

import argparse
import hmac
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from loom.server.claims import gate_decision
from loom.server.db import connect, init_db, now_s
from loom.server.tools import register

# `events.actor` holds AGENTS plus these two SYSTEM writers: the indexer (walk.py) and
# `loom` itself, which owns the TTL sweep's `expired` rows (claims.py `sweep`). Neither is
# a teammate, so neither may become an agent chip on the dashboard — a swept plan used to
# grow a permanent phantom "loom" agent. They stay visible in the event feed regardless.
SYSTEM_ACTORS = ("indexer", "loom")

# ITERATION-2-SPEC §2 — the `/state` payload caps, as MODULE CONSTANTS on purpose: they are
# read at REQUEST time, so a test lowers them with one `monkeypatch.setattr` instead of
# seeding thousands of synthetic nodes to reach 600. Changing them changes only how much of
# the graph is SENT; `totals` and `truncated` stay honest either way.
STATE_NODE_CAP = 600
STATE_EDGE_CAP = 1500

# ITERATION-2-SPEC §3 — the exact request paths the opt-in token guards. `/health` is
# deliberately absent (it is how a client LEARNS a token is required) and so is `/`, which
# serves a static shell and no repo data.
PROTECTED_PATHS = ("/gate", "/state", "/mcp")

# U2 — how long one repo's mtime scan is reused. A module constant for the same reason the
# caps above are: a test lowers it with one `monkeypatch.setattr`. Staleness is a HINT, so
# a 5s-old answer is always good enough, and the poll loop must never turn the dashboard
# into an `os.walk` generator.
INDEX_AGE_TTL_S = 5.0

# `iso()` TRUNCATES to whole seconds, so an `indexed_at` read back out of `events` is up to
# 1s EARLIER than the moment the index actually ran — and every file written in that same
# second would read as dirty forever. One second of grace cancels the truncation exactly.
# The cost is the honest converse: an edit landing inside the index's own second is not
# reported until the next edit. `loom index --changed` compares sha256, never mtime, so it
# still picks that edit up; only the HINT is late.
_INDEX_AGE_GRACE_S = 1.0

# (repo, repo_root) -> (expires_at, payload). Plain dict on purpose: two threads racing here
# recompute the same answer and the loser overwrites with an equal value. No lock (§2).
_index_age_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _template(name: str) -> str:
    """Absolute path of a shipped template; the package dir is the only source of truth."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", name)


# The CLAUDE.snippet protocol text (§8.2), read from the ONE file `loom init` also appends
# to CLAUDE.md — free pull-through for agents whose CLAUDE.md drifted (specgate §2.1).
# Read once at import: a second copy inlined here is a copy that silently drifts.
with open(_template("CLAUDE.snippet.md"), encoding="utf-8") as _fh:
    INSTRUCTIONS = _fh.read()


def connection_factory(db_path: str) -> Callable[[], sqlite3.Connection]:
    """One long-lived connection PER THREAD, opened once and reused (specgate §3.2).

    The SDK runs every sync `@mcp.tool()` through `anyio.to_thread.run_sync`, so tool bodies
    execute concurrently in worker threads and CANNOT share one `sqlite3.Connection`: the
    second `BEGIN IMMEDIATE` raises "cannot start a transaction within a transaction", and
    worse, two threads' statements would interleave inside one transaction. Per-thread
    connections restore §2's law as written — SQLite's write lock serializes the racers,
    `busy_timeout=5000` makes the loser queue — with no Python lock and no per-call
    connect/close (which would blow the check budget). `threading.local` is a slot, not a
    lock; nothing here ever blocks a thread in Python.
    """
    local = threading.local()

    def get() -> sqlite3.Connection:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = local.conn = connect(db_path)
        return conn

    return get


def _named(root: str) -> tuple[str, str]:
    """Split `NAME=PATH` into (name, path); a bare or path-shaped value gives ('', root).

    A `=` that appears AFTER a separator belongs to the path (`/tmp/a=b` is one directory),
    so only a separator-free head counts as a name — and an explicitly empty head is the
    beads empty-flag error, not a silent fallback to the basename.
    """
    head, sep, tail = root.partition("=")
    if not sep or "/" in head or os.sep in head:
        return "", root
    if not head.strip():
        raise ValueError(f"--repo-root {root!r} has an empty NAME; pass NAME=PATH")
    return head.strip(), tail


def parse_repo_roots(roots: list[str], repo: str = "") -> dict[str, str]:
    """MULTIREPO-SPEC §1 — `--repo-root` values to an ORDERED `{name: abs_root}` map.

    Each value is `NAME=PATH` or a bare `PATH` (name defaults to the basename). Order is
    the flag order and it is load-bearing: `served[0]` is the default repo for `/state`,
    for `""` tool arguments, and for `/health`'s back-compat `"repo"` key.

    `--repo NAME` keeps its BUILD-SPEC meaning — rename the one repo — so it is legal only
    with a single un-prefixed root; combining it with several roots (or with an explicit
    `NAME=`) is ambiguous and therefore a hard error, never a silent pick. Duplicate names
    are a hard error too: two roots under one salt would share every node id.

    Raises `ValueError` with the operator-facing text; callers turn that into an exit.
    """
    if not roots:
        raise ValueError("--repo-root is required; pass PATH or NAME=PATH")
    if repo and (len(roots) > 1 or _named(roots[0])[0]):
        raise ValueError("--repo names the single repo of a one-root server; with several "
                         "--repo-root flags (or a NAME=PATH form) use NAME=PATH instead")
    out: dict[str, str] = {}
    for root in roots:
        name, path = _named(root)
        if not path.strip():
            raise ValueError(f"--repo-root {root!r} has no path; pass a real value")
        abs_path = os.path.abspath(path.strip())
        name = repo or name or os.path.basename(abs_path.rstrip("/")) or "repo"
        if name in out:
            raise ValueError(f"duplicate repo name {name!r}: {out[name]} and {abs_path} "
                             "cannot share one salt; give each root its own NAME=PATH")
        out[name] = abs_path
    return out


def resolve_token(flag: str | None) -> str:
    """ITERATION-2-SPEC §3 — the shared secret from `--token SECRET` or `LOOM_TOKEN`.

    The flag wins when it is present at all. The two empty cases are deliberately NOT
    symmetric (ITERATION-2 §5a refines its §3 one-liner):

    - `--token ""` is a HARD ERROR. beads §2.3 applied to the one flag where the wildcard
      reading is dangerous: `--token "$UNSET_VAR"` from a broken shell expansion would
      otherwise unlock the server quietly, and the operator TYPED the intent to secure it.
    - `LOOM_TOKEN=""` (set but empty) reads as UNSET, i.e. an OPEN server. An exported-empty
      var is how shells and CI runners spell "no value"; refusing to boot on it would make
      every untokened server hostage to an ambient export nobody meant as a secret.

    Raises `ValueError` with the operator-facing text; callers turn that into an exit.
    """
    if flag is not None:
        if not flag.strip():
            raise ValueError("--token was given an empty value; pass a real secret "
                             "or omit the flag to run an open server")
        return flag
    env = os.environ.get("LOOM_TOKEN")
    return env if env and env.strip() else ""


class TokenAuth:
    """ITERATION-2-SPEC §3 — the 401 wall in front of `/gate`, `/state` and `/mcp`.

    A PLAIN ASGI wrapper, not a `BaseHTTPMiddleware`: `/mcp` is a long-lived streaming
    endpoint and BaseHTTPMiddleware buffers the response body through a task group, which
    is exactly the shape that deadlocks an SSE stream. Non-`http` scopes — `lifespan` above
    all, which starts the session manager — pass through untouched.

    A wrong or missing token is answered `401 {"ok": false, "error": "unauthorized"}` even
    on `/gate`: a 401 is a TRANSPORT refusal, not a gate decision, and the hook treats any
    non-200 as fail-open — the correct advisory posture for a misconfigured client
    (ITERATION-2 §3).
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._want = f"Bearer {token}".encode()

    @staticmethod
    def guards(path: str) -> bool:
        return path in PROTECTED_PATHS or path.startswith("/mcp/")

    def _authorized(self, scope) -> bool:
        got = next((v for k, v in scope.get("headers") or () if k == b"authorization"), b"")
        return hmac.compare_digest(got, self._want)

    async def __call__(self, scope, receive, send) -> None:
        if (scope.get("type") == "http" and self.guards(scope.get("path") or "")
                and not self._authorized(scope)):
            refusal = JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _epoch(stamp: str) -> float | None:
    """Inverse of `db.iso` — `None` for anything that is not a loom timestamp."""
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except (TypeError, ValueError):                                  # pragma: no cover
        return None


def index_age(c: sqlite3.Connection, repo: str, repo_root: str, now: float) -> dict:
    """U2 — how far the graph has fallen behind the working tree, as a VERDICT.

    `{"indexed_at": <unix s | None>, "dirty_files": <count>, "stale": <bool>}`.

    Posture adapted from graphify (Apache-2.0) — no code copied; see CREDITS.md. Its
    PreToolUse hook swaps an orientation nudge for a stale one when the target file's
    mtime beats the graph's: soften, never block. loom takes that posture and drops the
    coupling: this is reported on `/state` (dashboard + `loom doctor`) and NOWHERE on
    `/gate`, whose five wire keys are frozen and whose 1.5s budget cannot afford a tree
    scan. An agent is never blocked, slowed, or nagged by this number; a human sees it.

    STAT ONLY, never a re-hash: the answer is a hint, and `index_repo` remains the sole
    authority on what actually changed (it re-reads and re-hashes every file). A repo that
    has never been indexed is not stale, it is absent — `dirty_files` stays 0 and the
    doctor's `index freshness` row is the one that speaks.
    """
    key = (repo, repo_root)
    cached = _index_age_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    row = c.execute("SELECT MAX(ts) AS ts FROM events WHERE repo = ? AND actor = 'indexer' "
                    "AND action = 'indexed'", (repo,)).fetchone()
    at = _epoch(row["ts"]) if row and row["ts"] else None
    dirty = 0
    if at is not None and repo_root:
        from loom.indexer.walk import discover_files  # lazy: keeps tree-sitter off the
        cutoff = at + _INDEX_AGE_GRACE_S              # import path of a serve-only process
        for rel in discover_files(repo_root):
            try:
                if os.stat(os.path.join(repo_root, *rel.split("/"))).st_mtime > cutoff:
                    dirty += 1
            except OSError:                           # vanished mid-scan: the next index
                continue                              # will notice; this hint need not
    out = {"indexed_at": at, "dirty_files": dirty, "stale": dirty > 0}
    _index_age_cache[key] = (now + INDEX_AGE_TTL_S, out)
    return out


def state_payload(c: sqlite3.Connection, repo: str, served: list[str],
                  repo_root: str = "") -> dict:
    """The `/state` body for ONE repo (D5), caps and all — extracted from the route so the
    cap arithmetic is unit-testable without an HTTP client.

    ITERATION-2-SPEC §2: `counts` keeps its POST-LIMIT meaning, because frozen consumers
    (the dashboard's stat tiles, `loom doctor`'s freshness check) already read it that way.
    `totals` is the honest `COUNT(*)` for the repo and `truncated` says whether a cap bit.
    The totals-edges query MIRRORS the sent query's join exactly — a different join shape
    here would make `truncated` lie in both directions.
    """
    now = now_s()
    nodes = [dict(r) for r in c.execute(
        "SELECT id, path, qualname, kind FROM nodes WHERE repo = ? "
        "ORDER BY path, qualname LIMIT ?", (repo, STATE_NODE_CAP))]
    edges = [dict(r) for r in c.execute(
        "SELECT e.src, e.dst, e.kind FROM edges e JOIN nodes n ON n.id = e.src "
        "WHERE n.repo = ? LIMIT ?", (repo, STATE_EDGE_CAP))]
    total_nodes = c.execute("SELECT COUNT(*) FROM nodes WHERE repo = ?", (repo,)).fetchone()[0]
    total_edges = c.execute("SELECT COUNT(*) FROM edges e JOIN nodes n ON n.id = e.src "
                            "WHERE n.repo = ?", (repo,)).fetchone()[0]
    plans = [dict(r) for r in c.execute(
        "SELECT id, agent, title, created, ttl_expires FROM plans "
        "WHERE repo = ? AND status = 'active' AND ttl_expires > ? "
        "ORDER BY created", (repo, now))]
    active_ids = [p["id"] for p in plans]
    claims = []
    if active_ids:
        marks = ",".join("?" * len(active_ids))
        claims = [dict(r) for r in c.execute(
            f"SELECT c.node_id, c.plan_id, c.mode, p.agent FROM claims c "
            f"JOIN plans p ON p.id = c.plan_id "
            f"WHERE c.released IS NULL AND c.plan_id IN ({marks})", active_ids)]
    # repo = '' is pre-migration history (MULTIREPO §3): it belongs to no repo in particular,
    # so it is shown in every feed rather than disappearing from all of them.
    events = [dict(r) for r in c.execute(
        "SELECT ts, actor, action, detail FROM events WHERE repo = ? OR repo = '' "
        "ORDER BY rowid DESC LIMIT 50", (repo,))]
    agents = [p["agent"] for p in plans]
    agents += [e["actor"] for e in reversed(events) if e["actor"] not in SYSTEM_ACTORS]
    return {
        "ok": True, "repo": repo, "repos": served, "now": now,
        "counts": {"nodes": len(nodes), "edges": len(edges),
                   "plans": len(plans), "claims": len(claims)},
        "totals": {"nodes": total_nodes, "edges": total_edges},
        "truncated": {"nodes": len(nodes) < total_nodes, "edges": len(edges) < total_edges},
        "index_age": index_age(c, repo, repo_root, now),
        "nodes": nodes, "edges": edges, "plans": plans, "claims": claims,
        "events": events, "agents": list(dict.fromkeys(agents)),
    }


def build_server(db_path: str, repos: dict[str, str], token: str = "") -> MCPServer:
    """Create the schema if needed, wire the per-thread connection factory, the §5 tools,
    and the four plain-HTTP routes. `repos` (name -> repo_root, MULTIREPO-SPEC §2/D3) and
    the connection factory are closed over by every route below — they are the whole of
    the server's state. `served[0]` is the default repo everywhere a name is omitted.

    `token` is only ever READ here, to answer `/health`'s `auth` field (D8); the enforcing
    is `TokenAuth`'s job, wrapped around the finished app in `serve`."""
    init_db(db_path)
    conn = connection_factory(db_path)
    served = list(repos)
    if not served:
        raise ValueError("build_server needs at least one repo")
    default = served[0]

    mcp = MCPServer(
        "loom",
        title="loom — spec-driven coordination gate",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    register(mcp, conn, served)

    def selected(request: Request) -> str:
        """`?repo=NAME` when served, else the default — a mistyped name must not blank
        the dashboard, it must show the repo the operator most likely meant."""
        asked = request.query_params.get("repo") or ""
        return asked if asked in repos else default

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(request: Request) -> Response:
        """Plain-HTTP liveness used by `loom init` to learn the repo salt (§11.19).

        `repos` is the multi-repo answer; `repo` is kept as served[0] so every already
        initialized checkout and every frozen test keeps reading the key it knows (D2).
        `auth` (D8) is how `loom init` and `loom doctor` learn, WITHOUT a credential, that
        this server wants one — which is why this route stays open when the others do not."""
        return JSONResponse({"ok": True, "repo": default, "repos": served,
                             "auth": "token" if token else "open"})

    @mcp.custom_route("/", methods=["GET"])
    async def dashboard_route(request: Request) -> Response:
        """The one-page read-only dashboard (PLAN §7 'visibility', brought into MVP)."""
        with open(_template("dashboard.html"), encoding="utf-8") as fh:
            return Response(fh.read(), media_type="text/html")

    @mcp.custom_route("/state", methods=["GET"])
    async def state_route(request: Request) -> Response:
        """Dashboard poll feed, scoped to `?repo=NAME` (D5). Fail-soft: under writer
        contention return ok:false with HTTP 200 — the page shows 'reconnecting', never a
        broken dashboard. Caps keep the payload sane on big repos — and, since D7, SAY SO:
        `totals`/`truncated` sit beside the post-LIMIT `counts`. spec_md is deliberately
        NOT shipped (8KB x poll). `repos` ships every served name so the page can render a
        switcher without a second endpoint. `index_age` (U2) is the one field computed off
        the FILESYSTEM rather than the db — `repos[repo]` is the root it scans."""
        try:
            repo = selected(request)
            return JSONResponse(state_payload(conn(), repo, served, repos[repo]))
        except sqlite3.OperationalError as exc:
            return JSONResponse({"ok": False, "error": type(exc).__name__})

    @mcp.custom_route("/gate", methods=["POST"])
    async def gate_route(request: Request) -> Response:
        """§6 wire contract. Always HTTP 200 with exactly the five frozen keys.

        `body.repo` picks WHICH served repo judges this edit (MULTIREPO-SPEC §2). A name
        we do not serve — or none at all — is answered advisory: a repo this server knows
        nothing about is never bricked (§6 case 2 spirit), it is simply not ours to judge.
        """
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError               # a JSON array or scalar has no .get
        except Exception:
            # P2-3: an unparseable body used to raise, so the route 500'd — the hook fails
            # open on that, but the "always 200, five keys" contract above was false and the
            # server logged a traceback for input it should have answered. An empty dict
            # falls into the unserved-repo branch below, which is exactly the right answer.
            body = {}
        asked = str(body.get("repo") or "")
        if asked not in repos:
            return JSONResponse({"decision": "allow", "case": "unindexed", "message": "",
                                 "node_id": None, "plan_id": None})
        q = body.get("qualname")
        d = gate_decision(conn(), repo=asked, agent=str(body.get("agent") or ""),
                          path=str(body.get("path") or ""),
                          # P2-3: a non-string qualname reaches `prefix_candidates` and
                          # explodes. Anything that is not a string is "no symbol named".
                          qualname=q if isinstance(q, str) else None,
                          now=now_s())
        return JSONResponse({k: d[k] for k in
                             ("decision", "case", "message", "node_id", "plan_id")})

    return mcp


def serve(host: str, port: int, db_path: str, repos: dict[str, str], token: str = "") -> None:
    """Run the streamable-http server; MCP endpoint is `/mcp`.

    The Starlette app is built here rather than left to `MCPServer.run(transport=...)`
    because `run` offers no seam to wrap: it builds the app and hands it to uvicorn in one
    breath. Everything below IS `run_streamable_http_async`, plus ITERATION-2 §3's optional
    guard. An
    open server (`token == ""`) is wrapped in nothing at all.
    """
    import uvicorn

    mcp = build_server(db_path, repos, token)
    app = mcp.streamable_http_app(streamable_http_path="/mcp", host=host)
    guarded = TokenAuth(app, token) if token else app
    uvicorn.Server(uvicorn.Config(guarded, host=host, port=port,
                                  log_level=mcp.settings.log_level.lower())).run()


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom.server.app", description="run the loom server")
    # Repeatable: `--repo-root PATH` (name = basename) or `--repo-root NAME=PATH`
    # (MULTIREPO §1).
    ap.add_argument("--repo-root", action="append", default=None)
    ap.add_argument("--repo", default="")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--db", default="")
    # `default=None` (not "") is load-bearing: `resolve_token` distinguishes "flag absent,
    # fall back to LOOM_TOKEN" from "flag given an empty value", which is a hard error.
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    # `--repo-root` survives as the DEFAULT source for the two values the server does use:
    # the repo salts (minted once, here, and echoed to every `loom init` — §11.19) and,
    # absent `--db`, the database location. Parsing lives with the server, not the CLI,
    # because this module is spawned directly as `python -m loom.server.app`; `loom serve`
    # imports the same `parse_repo_roots` so the two entry points cannot drift (§9.2 lets
    # the CLI reach into the server, never back).
    try:
        repos = parse_repo_roots(args.repo_root or ["."], args.repo)
        token = resolve_token(args.token)
    except ValueError as exc:
        raise SystemExit(f"loom: {exc}") from None
    db_path = args.db or os.path.join(next(iter(repos.values())), ".loom.sqlite3")
    serve(args.host, args.port, db_path, repos, token)


if __name__ == "__main__":
    main()
