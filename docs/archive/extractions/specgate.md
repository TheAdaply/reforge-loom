# Extraction: specgate (our own prior MVP)

Source clone: `<specgate-clone>`
(verified working 2026-08-14; `mcp==2.0.0` pinned in its `uv.lock`).
Target: `loom/` per `loom/docs/PLAN-v1.md`.

This is the **only** cherry-pick source whose code we copy verbatim — it is ours.

---

## 1. LICENSE

**There is no LICENSE file in the specgate repo.** `ls -a` on the clone root returns:
`.git .gitignore .mcp.json.example .pytest_cache .python-version .venv AGENTS-SNIPPET.md demo pyproject.toml README.md src tests uv.lock` — no `LICENSE`, `LICENSE.md`, `COPYING`, or `NOTICE`.

Authorship, from `specgate/pyproject.toml:6-8`:

```toml
authors = [
    { name = "<author>" }
]
```

**Restriction that matters: none.** Same owner, same workspace, unpublished. Verbatim copy of any
specgate file into `loom/` is unrestricted. No attribution header, no "patterns only" constraint,
no rewrite-from-scratch requirement. This is the sole source in the §2 cherry-pick manifest for
which that is true — every other source (mcp_agent_mail, beads, FalkorDB, Serena, spec-kit) stays
patterns-only.

Transitive note: the one runtime dependency, `mcp>=2.0.0` (MIT, Anthropic), is a normal PyPI
dependency, not vendored code. Nothing to carry.

---

## 2. ADOPT

All excerpts below are **verbatim** from the clone. `file:line` is the line number in that file.

### 2.1 The mcp 2.0.0 server surface — copy this shape into `loom/server/app.py`

**Import** — `specgate/src/specgate/server.py:25`

```python
from mcp.server import MCPServer
```

Confirmed in the installed SDK: `mcp/server/__init__.py:4` does `from .mcpserver import MCPServer`,
and `:7` lists it in `__all__`. The class is defined at `mcp/server/mcpserver/server.py:147`.
**There is no `FastMCP` in mcp 2.0.0.**

**Construction** — `server.py:46-51` (module-level singleton, decorators register against it):

```python
mcp = MCPServer(
    "specgate",
    title="specgate — spec-time conflict gate",
    instructions=INSTRUCTIONS,
    version="0.1.0",
)
```

`INSTRUCTIONS` (`server.py:31-44`) is a plain module-level string containing the agent protocol; the
SDK ships it to clients in the initialize result. **loom should put the §4.4 protocol text here as
well as in `CLAUDE.snippet.md`** — it is free pull-through for any agent whose CLAUDE.md drifted.

Full ctor signature (`mcp/server/mcpserver/server.py:148-176`) — the kwargs loom may want:
`name, title, description, instructions, website_url, icons, version, auth_server_provider,
token_verifier, *, tools, resources, extensions, debug, log_level, warn_on_duplicate_tools,
dependencies, lifespan, auth, resource_security, request_state_security, cache_hints,
subscriptions, middleware`.

**Tool registration** — `server.py:127-139`, and identically at `:143`, `:183`, `:190`, `:207`:

```python
@mcp.tool()
def check_spec(
    agent: str,
    user: str,
    file_path: str,
    functions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Dry-run: check a planned spec for conflicts WITHOUT registering it.

    functions: list of {"qualname": "func_or_Class.method",
    "change_type": "modify|add|delete", "description": "what changes"}.
    Returns ok/conflicts/warnings/validation_errors/guidance."""
    _, result = _run_check(agent, file_path, functions)
    return result
```

Rules this proves, all load-bearing for loom:

- `@mcp.tool()` — **with** the empty parens. Decorator is `MCPServer.tool()` at
  `mcp/server/mcpserver/server.py:621`, signature
  `tool(name=None, title=None, description=None, annotations=None, icons=None, meta=None, structured_output=None)`.
  (Do **not** confuse it with `Apps.tool()` at `mcp/server/apps.py:91` — that is the UI-apps
  extension and requires a `ui://` `resource_uri`.)
- Tool functions are **plain sync `def`** — no async required. Name and description come from the
  function name and docstring.
- **The return type annotation is the schema.** `structured_output=None` (the default) auto-detects
  from the annotation (`server.py:629`); `-> dict[str, Any]` yields a structured tool, which is what
  makes `result.structured_content` populated on the client. **Every loom tool must keep an explicit
  return annotation or clients silently lose `structured_content`.**
- Complex params arrive as plain JSON (`list[dict[str, Any]]`) and are validated by hand
  (`server.py:69-70`: `FunctionChange.model_validate(f)`). loom can instead annotate with pydantic
  models directly; specgate's hand-validate path is the fallback if schema generation misbehaves.
- Tools return dicts with an explicit `ok`/`approved` boolean rather than raising — errors are data
  (`server.py:196-202`). Keep this: a raised exception becomes `CallToolResult(is_error=True)` with
  only a text blob (`mcp/server/mcpserver/server.py:415-424`), which the hook cannot parse.

**Run / entrypoint** — `server.py:229-247` verbatim:

```python
def main() -> None:
    global _store, _repo_root
    parser = argparse.ArgumentParser(description="specgate MCP server (streamable HTTP)")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (0.0.0.0 so the other machine can reach it)")
    parser.add_argument("--db", default=os.environ.get("SPECGATE_DB", "specgate.sqlite3"))
    parser.add_argument(
        "--repo-root",
        default=os.environ.get("SPECGATE_REPO_ROOT"),
        help="path to a checkout of the shared repo; enables AST validation of specs",
    )
    args = parser.parse_args()
    _store = SpecStore(args.db)
    _repo_root = args.repo_root
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

`run()` is **synchronous** and blocks (`mcp/server/mcpserver/server.py:387-408`; it calls
`anyio.run(...)` internally). The `streamable-http` overload (`:372-385`) accepts
`host, port, streamable_http_path, json_response, stateless_http, event_store, retry_interval,
max_request_body_size, transport_security`. **`streamable_http_path` defaults to `/mcp`**
(`:1060`), which is why every client URL is `http://host:port/mcp`.

`--host 0.0.0.0` is deliberate and must survive into `loom serve`: the whole point is the other
machine reaching it.

**Client config file** — `specgate/.mcp.json.example` verbatim; `loom init` writes this shape:

```json
{
  "mcpServers": {
    "specgate": {
      "type": "http",
      "url": "http://REPLACE-WITH-SERVER-HOST:8776/mcp"
    }
  }
}
```

### 2.2 The mcp 2.0.0 client surface — copy into `loom/hook/gate.py` and the eval harness

**Import** — `specgate/demo/run_demo.py:26`

```python
from mcp import Client
```

(`mcp/__init__.py:65` re-exports `Client` from `mcp.client.client`; also importable as
`from mcp.client import Client`.)

**Session + call** — `run_demo.py:53-77`, verbatim:

```python
async def scenario() -> None:
    async with Client(URL) as agent_a, Client(URL) as agent_b:
        # ---------------------------------------------------------------- 1
        banner("STEP 1 — Agent A (akash @ mbp-akash) submits its spec for process_payment")
        r = await agent_a.call_tool("submit_spec", {
            "agent": "claude-code-A",
            "user": "akash",
            "machine": "mbp-akash",
            "title": "Idempotency keys for charges",
            "intent": (
                "Add an idempotency_key parameter to process_payment; duplicate keys "
                "return the original result instead of double-charging. No signature "
                "changes elsewhere."
            ),
            "file_path": "payments.py",
            "functions": [{
                "qualname": "process_payment",
                "change_type": "modify",
                "description": "add idempotency_key param + dedupe lookup before charging",
            }],
        })
        a1 = r.structured_content
        show("submit_spec ->", a1)
        assert a1["approved"] is True, "step 1 should approve"
        spec_a = a1["spec"]["spec_id"]
```

with the URL constant at `run_demo.py:30-31`:

```python
PORT = 8776
URL = f"http://127.0.0.1:{PORT}/mcp"
```

Rules:

- `Client(url_string)` — a bare URL string selects the streamable-HTTP transport automatically
  (`mcp/client/client.py:286-292`). No transport object to build.
- `async with Client(...)` performs connect + initialize; **two separate `Client` objects are two
  independent MCP sessions**, which is exactly how the demo simulates two machines from one process.
- `await client.call_tool(name, args_dict)` returns a `CallToolResult`
  (`mcp/client/client.py:751-761`); `.structured_content` is the parsed dict (present only because
  the tool has a return annotation — see 2.1).
- `Client` is a dataclass with `KW_ONLY` extras, including **`read_timeout_seconds: float | None`**
  (`client.py:300-301`), and `call_tool` takes a per-call `read_timeout_seconds` too — see §5.

### 2.3 The check-then-act lock lesson (adopt the *lesson*, replace the *mechanism*)

specgate's whole mutual-exclusion guarantee is one module-level lock. `server.py:56-60` verbatim:

```python
# Serializes check-then-claim so two concurrent submits can't both pass the
# check and both insert — first-spec-wins must hold by construction, not by
# scheduler accident (the exact race beads has at sync time).
_gate_lock = threading.Lock()
```

used in `submit_spec` — `server.py:161-180` verbatim:

```python
    with _gate_lock:
        files, result = _run_check(agent, file_path, functions, replaces_spec_id)
        if not result["ok"]:
            return {"approved": False, **result}
        store = _get_store()
        if replaces_spec_id:
            prior = store.get(replaces_spec_id)
            if prior and prior.agent == agent and prior.status == SpecStatus.active:
                store.set_status(replaces_spec_id, SpecStatus.withdrawn)
        spec = store.create(
            agent=agent,
            user=user,
            machine=machine,
            title=title,
            intent=intent,
            files=files,
            ttl_minutes=ttl_minutes,
        )
        return {"approved": True, "spec": _spec_summary(spec), **result}
```

and again in `complete_spec` (`:194`) and `withdraw_spec` (`:211`) — **every** mutating tool takes
the same lock, which is what makes it correct.

**The lesson to carry into loom:** conflict-check and claim-insert are one critical section. A
`check()` that returns "clear" followed by a separate `claim()` is a race, and the race is the exact
defect that capped the glue stack. `declare_plan` must never be two round trips.

**Why loom supersedes the mechanism:** see §3.1. `threading.Lock` only works because specgate is a
single process, holds one lock for all mutations, and stores everything in one JSON blob per spec.
loom's §4.1 model spreads a plan across `plans`, `claims`, and `events` rows, and §7 requires a
Postgres flip — a Python-level lock protects neither. loom uses one SQLite `BEGIN IMMEDIATE`
transaction.

Related, also superseded: specgate's TTL expiry is **lazy, on read**, inside `active()` —
`store.py:101-123` verbatim:

```python
    def active(self) -> list[Spec]:
        """Active, unexpired specs. Expired-but-still-'active' rows are lazily
        flipped to expired here — this is the dead-agent recovery path the MVP
        ships instead of heartbeats."""
        now = utcnow()
        con = self._connect()
        rows = con.execute("SELECT * FROM specs WHERE status = 'active'").fetchall()
        out: list[Spec] = []
        expired_ids: list[str] = []
        for row in rows:
            spec = self._row_to_spec(row)
            if spec.expires_at <= now:
                expired_ids.append(spec.id)
            else:
                out.append(spec)
        if expired_ids:
            con.executemany(
                "UPDATE specs SET status = 'expired' WHERE id = ?",
                [(i,) for i in expired_ids],
            )
            con.commit()
        con.close()
        return out
```

The **lazy-expiry idea is worth keeping as a belt-and-braces read filter** (`WHERE status='active'
AND ttl_expires > :now`) even after loom adds the §4.2 TTL sweeper: it makes correctness independent
of the sweeper thread being alive.

### 2.4 `collect_qualnames` — AST fallback for `loom/hook/locator.py`

`specgate/src/specgate/engine.py:131-149`, verbatim, copy as-is into loom:

```python
def collect_qualnames(source: str) -> set[str]:
    """Top-level functions as 'name', methods as 'Class.name' (nested classes as
    'Outer.Inner.name'). Nested (closure) functions are not collected — a spec
    should claim the enclosing function instead."""
    tree = ast.parse(source)
    out: set[str] = set()

    def visit(node: ast.AST, class_stack: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(".".join((*class_stack, child.name)))
                # do not descend: closures are covered by their parent
            elif isinstance(child, ast.ClassDef):
                visit(child, (*class_stack, child.name))
            else:
                visit(child, class_stack)

    visit(tree, ())
    return out
```

Needs `import ast` (`engine.py:18`).

Two properties worth preserving deliberately:

1. **Do-not-descend-into-functions** produces exactly the granularity §1 wants ("symbol granularity
   for claims") — closures are covered by their enclosing function, so an edit inside a nested `def`
   maps to the claimable parent.
2. **(Amended per GATE-1 fix 2.)** The `Outer.Inner.name` dotted form is specgate's internal
   spelling only — the plan's "`relative/path.py::Class.method` Serena convention" does not exist in
   Serena (serena.md C1: `NAME_PATH_SEP = "/"`). loom's canonical form is
   `relative/path.py::Class/method`. `collect_qualnames`' dotted output must be converted at the
   naming layer (`.` → `/` on the class-stack join) before it is compared with or emitted as a loom
   qualname; `indexer/naming.py` joins `path + "::" + qualname` with `/` separators inside the
   qualname.

Supporting helper, also reusable for `resolve_nodes`' "did you mean" — `engine.py:190-193`:

```python
def _closest(name: str, existing: set[str], n: int = 5) -> str:
    tail = name.rsplit(".", 1)[-1].lower()
    ranked = sorted(existing, key=lambda q: (tail not in q.lower(), q))
    return ", ".join(ranked[:n]) if ranked else "(file defines no functions)"
```

### 2.5 pyproject / uv shape — copy wholesale into `loom/pyproject.toml`

`specgate/pyproject.toml`, all 24 lines, verbatim:

```toml
[project]
name = "specgate"
version = "0.1.0"
description = "Spec-gated collaboration server: coding agents on different machines declare function-level specs before editing a shared repo; conflicts are caught at spec time, not merge time."
readme = "README.md"
authors = [
    { name = "<author>" }
]
requires-python = ">=3.12"
dependencies = [
    "mcp>=2.0.0",
]

[project.scripts]
specgate = "specgate.server:main"

[dependency-groups]
dev = [
    "pytest>=9.1.1",
]

[build-system]
requires = ["uv_build>=0.11.7,<0.12.0"]
build-backend = "uv_build"
```

Plus `specgate/.python-version` containing exactly `3.12`.

Facts a coder must not change:

- `uv_build` backend implies the **`src/<pkg>/` layout** (`src/specgate/…` with a `py.typed`
  marker) — uv_build discovers `src/<project-name>` by default. loom's §3 layout (`server/`,
  `indexer/`, `hook/`, `cli/` at repo root) must therefore become `src/loom/server/`,
  `src/loom/indexer/`, `src/loom/hook/`, `src/loom/cli/`, or the build backend must be swapped to
  hatchling. **Copy the layout, not just the toml.**
- `[project.scripts]` is the entire `uvx loom` / `pip install loom` story from §4.5:
  `loom = "loom.cli:main"`.
- `[dependency-groups] dev` (PEP 735) is uv-native — `uv run pytest` picks it up with no extras
  syntax.
- `requires-python = ">=3.12"` + `.python-version = 3.12` is the pinned pair.
- Everything runs as `uv run --directory <abs-path> …`; `demo/run_demo.py:11` documents
  `uv run python demo/run_demo.py`.

loom's dependency list adds (over specgate's single `mcp>=2.0.0`): `tree-sitter`,
`tree-sitter-python`, and `sqlalchemy` (§7 requires SQLAlchemy from day one so the Postgres flip is
a URL). `pydantic` is already transitive via `mcp` but should be declared explicitly since loom's
models depend on it directly.

### 2.6 Demo harness — reuse for M2 acceptance and M4

`demo/run_demo.py:34-41`, verbatim:

```python
def wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.15)
    raise RuntimeError(f"server did not open port {port} within {timeout}s")
```

`demo/run_demo.py:167-189`, verbatim:

```python
def main() -> None:
    db = Path(tempfile.mkdtemp(prefix="specgate-demo-")) / "specgate.sqlite3"
    env = dict(os.environ)
    server = subprocess.Popen(
        [sys.executable, "-m", "specgate.server",
         "--port", str(PORT), "--host", "127.0.0.1",
         "--db", str(db), "--repo-root", str(HERE / "shared_repo")],
        cwd=str(PKG_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_port(PORT)
        print(f"specgate server up on {URL} (db: {db})")
        asyncio.run(scenario())
    finally:
        server.terminate()
        server.wait(timeout=5)


if __name__ == "__main__":
    main()
```

with the path constants at `run_demo.py:28-31`:

```python
HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
PORT = 8776
URL = f"http://127.0.0.1:{PORT}/mcp"
```

The pattern, spelled out:

1. Fresh DB in `tempfile.mkdtemp()` — every run starts clean, no state leaks between arms.
2. `sys.executable -m <pkg>.server` — same interpreter, no shell, no `timeout` binary needed
   (**relevant on macOS, which has none**).
3. `wait_for_port` before any client connects — no `sleep(2)` flake.
4. `stdout/stderr=DEVNULL` so the demo transcript is only the scenario, not uvicorn logs. **For M4
   use `subprocess.PIPE` or a log file instead** — a server that dies at startup is otherwise
   invisible.
5. `try/finally: terminate(); wait(timeout=5)` — no orphaned server on assertion failure.
6. Two `Client`s in one `async with` = two agents.
7. **Assertions inside the demo** (`run_demo.py:76`, `:96-98`, `:120-121`, `:140`, `:147`, `:158`) —
   the demo *is* the acceptance test. M2's acceptance ("exactly one wins, the loser's response
   embeds the winner's spec") is written the same way.

Also reusable directly: the narration helpers `banner()` (`:44-45`) and `show()` (`:48-50`), and the
fixture `demo/shared_repo/payments.py` as a throwaway indexer target before the RealWorld submodule
lands.

---

## 3. ADAPT — what changes and why

### 3.1 `threading.Lock` → one `BEGIN IMMEDIATE` SQLite transaction

**Change.** Delete `_gate_lock` entirely. `declare_plan` and `rescope` run their whole
check-and-claim inside a single write transaction:

```python
con.execute("BEGIN IMMEDIATE")          # write lock acquired here, before any read
# 1. expand write_targets one hop over CALLS/IMPORTS
# 2. SELECT existing claims intersecting (expanded ∪ assumes)
# 3. if conflicts -> con.rollback(); return {"conflicts": [...]}  (with spec_md inline)
# 4. INSERT plan row, INSERT claim rows, INSERT event row
con.commit()
```

**Why.** Three reasons, all from the plan:

- `BEGIN IMMEDIATE` takes SQLite's write lock **before the first read**, so the read that decides
  "no conflict" and the write that claims cannot be interleaved by another connection. A deferred
  transaction (Python's `sqlite3` default) would upgrade mid-transaction and can raise
  `SQLITE_BUSY`/lose to another writer — that reintroduces the exact race.
- specgate's lock is correct only under "one process, one lock, all mutations." loom's §7 wants one
  stateless server process (possibly restarted, possibly replicated) and a config flip to Postgres.
  Transaction semantics are identical across both; a Python lock is identical across neither.
- §1 states it outright: "check and claim become one transaction. This kills the race window."

**Also required, and missing in specgate:** `store.py:40-43` (`_connect`) sets no PRAGMAs at all.
§4.1 mandates WAL. loom's connection factory must set, on every connection:

```python
con.execute("PRAGMA journal_mode=WAL")     # once per DB, persists in the file
con.execute("PRAGMA busy_timeout=5000")    # per connection — required, or concurrent writers raise
con.execute("PRAGMA foreign_keys=ON")
con.execute("PRAGMA synchronous=NORMAL")   # correct pairing with WAL
```

`busy_timeout` is not optional: with WAL and two concurrent `declare_plan` calls, the loser needs to
*wait* for the winner's transaction, not error out. Without it the M2 acceptance test ("exactly one
wins") fails with an exception instead of a clean conflict response.

### 3.2 Connection lifecycle: per-call `connect/close` → one pooled connection (or SQLAlchemy)

specgate opens and closes a fresh `sqlite3.Connection` in **every** method
(`store.py:35-38, 67-84, 106-122, 126-129, 132-137`). That is fine at spec-submit frequency
(minutes apart) and fatal at `check()` frequency: §4.2 targets **sub-10ms warm** for the hook's fast
path, called on every single edit. Open a connection once per thread (`check_same_thread=False` +
a `threading.local`, or SQLAlchemy's pool per §7), and index `claims(node_id)` so `check` is a
single indexed lookup.

### 3.3 `collect_qualnames` → line-range-aware for `locator.py`

**Change.** `collect_qualnames` answers "what symbols exist"; `locator.py` must answer "which symbol
encloses lines N..M of this file" (§4.3). Extend the same visitor to emit spans instead of names:

```python
def collect_symbol_spans(source: str) -> list[tuple[str, int, int]]:
    """(qualname, lineno, end_lineno) for every claimable symbol, same
    granularity rule as collect_qualnames (closures roll up to their parent)."""
    tree = ast.parse(source)
    out: list[tuple[str, int, int]] = []

    def visit(node, class_stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((".".join((*class_stack, child.name)),
                            child.lineno, child.end_lineno))
            elif isinstance(child, ast.ClassDef):
                out.append((".".join((*class_stack, child.name)),
                            child.lineno, child.end_lineno))
                visit(child, (*class_stack, child.name))
            else:
                visit(child, class_stack)

    visit(tree, ())
    return out
```

Then the enclosing symbol for an edited range is the **narrowest** span containing it; nothing
contains it → file-level ID (§1's "file granularity as fallback for non-code files").
`ast.FunctionDef.end_lineno` exists on 3.8+, so 3.12 is safe. Note `ClassDef` now also emits a span
(specgate never claimed classes) because a class-body edit outside any method must still resolve.

**Role.** This AST path is the **fallback**, not the primary. M1 makes tree-sitter the indexer
(§1: "The indexer is tree-sitter, so more languages are added by adding capture queries"). The AST
version is Python-only, stdlib-only, zero-install, and is what `locator.py` uses if tree-sitter is
unavailable or the file fails to parse under the grammar. It also keeps the hook's dependency
surface small, which matters for hook latency.

### 3.4 Claim identity: `(path, qualname)` string pairs → indexed node IDs

specgate's engine compares normalized path strings and qualname strings
(`engine.py:31-32, 45-60`). loom compares `node_id` — a short hash of repo + qualname (§4.1) minted
by the indexer, resolved through `resolve_nodes` (§4.2). See §4.1 below for why.

Keep specgate's `_norm_path` verbatim as an input-normalization step for `resolve_nodes`
(`engine.py:31-32`):

```python
def _norm_path(p: str) -> str:
    return str(PurePosixPath(p.replace("\\", "/")))
```

`tests/test_engine.py:58-60` proves it matters (`src\payments.py` vs `src/payments.py` must collide).

### 3.5 Test harness: subprocess for M4, in-memory `Client` for M2

**Discovery in the SDK** (`mcp/client/client.py:261-292`): `Client` accepts a `Server` or
`MCPServer` **instance** and connects in-process. So M2's unit-level acceptance can be:

```python
from mcp import Client
from loom.server.app import mcp          # the MCPServer instance

async with Client(mcp) as a, Client(mcp) as b:
    ...
```

— no subprocess, no port, no `wait_for_port`, milliseconds per test, and still a real MCP round trip
through the same tool dispatch. Use this for the pytest suite.

**Keep the subprocess + `wait_for_port` + two-HTTP-`Client`s shape for M4's demo**, where crossing
the HTTP transport is precisely the thing being demonstrated (and for the "check answers under 10ms
warm" measurement, which must include real HTTP).

For true concurrency in M2's acceptance ("hit `declare_plan` concurrently on overlapping targets"),
in-process `asyncio` alone will not exercise the DB transaction across connections. Use
`asyncio.gather` of two HTTP `Client` calls against the subprocess server, or two OS threads each
with their own connection — otherwise you are testing the GIL, not `BEGIN IMMEDIATE`.

### 3.6 Config: env-var singletons → explicit CLI/config wiring

`server.py:53-66` uses module globals `_store`/`_repo_root` plus a lazy `_get_store()` reading
`os.environ["SPECGATE_DB"]`. This makes tests order-dependent and multi-repo (§7) impossible. loom
should build state in `main()`/`loom serve` and hand it to the tool layer via the SDK's `lifespan=`
kwarg (`MCPServer(..., lifespan=...)`, `mcp/server/mcpserver/server.py:170`), which is the supported
place to own a resource for the server's lifetime. Keep the argparse+env-default pattern for flags
(`server.py:231-241`) — it is good and `loom serve --repos config.yaml` (§7) is the same shape.

---

## 4. REJECT — specgate behaviors loom deliberately replaces

### 4.1 Claims keyed by caller-supplied `(file_path, qualname)` → claims by indexed node ID

**Rejected.** `check_spec`/`submit_spec` take `file_path: str` + `functions: [{qualname, ...}]`
(`server.py:127-153`) and the engine intersects raw strings (`engine.py:45-60`).

**Why.** Strings are unresolvable and unjoinable. There is no way to expand a claim one hop over
CALLS/IMPORTS (§4.2's atomic `declare_plan`), no way to key `edges`, and typos become silent
non-conflicts — two agents could claim the same function under `payments.py` and `./payments.py`
(specgate patches the worst case with `_norm_path` + AST validation; that is a symptom).

**Replacement.** `resolve_nodes(names_or_paths) -> node ids` (§4.2), `nodes(id, repo, path,
qualname, kind, body_hash, updated)` with `id` = short hash of repo + qualname (§4.1),
`claims(node_id, plan_id, mode)`. Agents plan in canonical IDs only. The one-hop graph expansion is
then a two-line SQL join instead of impossible.

### 4.2 The `warn` severity tier → `read` claims (assumes)

**Rejected.** `Severity.warn` (`models.py:59-61`) and the same-file/different-function warning
(`engine.py:77-92`), which fires purely on **path adjacency**:

```python
            if not overlap and other_fns and proposed[opath]:
                warnings.append(Conflict(severity=Severity.warn, ...))
```

**Why.** It is a proximity heuristic, not a semantic one. It fires on every unrelated edit to a
large file (pure noise at 10 users, §7) and stays silent on the case that actually breaks people —
agent A changing a function that agent B's plan *depends on* in a different file. specgate's own
docstring admits it: "the functions may still be semantically coupled (that is exactly what the
dependency graph will catch later)."

**Replacement.** §4.2's three-case conflict rule over declared `assumes` (read claims):
write-write **blocks**; my-write-vs-your-read and my-read-vs-your-write **warn with the owner's
spec attached**. The signal is a declared dependency, not a shared filename. §7: "Read claims are
what make 10 users safe."

Do keep the *shape* of `Conflict` (`models.py:64-71`: severity, path, qualname, with_spec_id,
with_agent, with_user, reason) as the response schema — only the rule that produces it changes.

### 4.3 `validate_against_repo` as a submit-time gate

**Rejected** as a step inside `declare_plan` (`engine.py:152-187`, wired at `server.py:112-122`).

**Why.** It re-parses files from disk on the write path (latency in the critical section), it is
Python-only, and it duplicates work the indexer already did. Its purpose — catching a spec that
names a function that does not exist — is fully served, earlier and canonically, by
`resolve_nodes`: an unresolvable name simply has no node ID, and `_closest()` (§2.4) supplies the
"did you mean" list. Move the logic to `resolve_nodes`, delete it from the claim path.

### 4.4 Caller-asserted identity

**Rejected.** Every tool takes `agent: str, user: str` as free-text parameters
(`server.py:128-129`) — the docstring at `server.py:14-15` calls it a known limitation. Two agents
can trivially impersonate each other, and `withdraw_spec(force=True)` (`server.py:207-227`) lets
anyone clear anyone's claim.

**Replacement.** §4.5: `loom init` mints an agent token per user; §7: token maps to a user record.
Identity comes from the token on the connection, never from a tool argument.

### 4.5 One file per spec

**Rejected.** `_run_check` hard-wraps a single path (`server.py:104-105`):

```python
    files = [FileChange(path=file_path, functions=_parse_functions(functions))]
```

The models already support many (`Spec.files: list[FileChange]`), so this was an MVP shortcut. loom
plans span files by construction — `write_targets[]` and `assumes[]` are flat node-ID lists (§4.2).

### 4.6 Storing the claim set as an opaque JSON blob

**Rejected.** `store.py:77` serializes all files/functions into one `files_json` column, so every
conflict check deserializes **every** active spec in Python (`engine.py:51-60` loops
`for other in active_specs`). Linear in active plans, un-indexable, and incompatible with the sub-10ms
`check` target. loom's `claims` table is one row per `(node_id, plan_id, mode)` with an index on
`node_id` — `check` becomes a single indexed lookup.

### 4.7 The name "spec" as the claim unit

specgate has one object: the Spec, which *is* the claim. loom splits it: `plans` (identity, agent,
branch, `spec_md`, status, TTL) and `claims` (per-node rows pointing at a plan). Same TTL and
lifecycle semantics, but one plan holds many claims and claims can be added by `rescope` without
re-minting the plan. Do not carry `replaces_spec_id` (`server.py:153`) — `rescope(plan_id,
add_targets[], add_assumes[])` replaces it, and it is additive rather than withdraw-and-recreate,
so the plan ID in a deny message stays stable.

---

## 5. CORRECTIONS to PLAN-v1.md

### 5.1 Plan note (b) is CONFIRMED, not stale — mark it resolved

PLAN-v1.md lines 6-8 flag the FastMCP → MCPServer correction as something to verify. Verified
against the installed SDK in `specgate/.venv` (`mcp==2.0.0`, per the `uv.lock` entry
`name = "mcp" / version = "2.0.0"`):

- `mcp/server/__init__.py:4,7` — `from .mcpserver import MCPServer`, exported in `__all__`.
- `mcp/server/mcpserver/server.py:147` — `class MCPServer(Generic[LifespanResultT])`.
- `:372-385` — the `run(transport: Literal["streamable-http"], *, host, port, streamable_http_path, json_response, stateless_http, event_store, retry_interval, max_request_body_size, transport_security)` overload.
- `:1060` — `streamable_http_path: str = "/mcp"`.
- No `FastMCP` symbol anywhere in the package.

The plan can drop the hedge and state the surface as fact.

### 5.2 §3's repo layout is incompatible with the `uv_build` backend we are copying

§3 shows `loom/server/`, `loom/indexer/`, `loom/hook/`, `loom/cli/` as top-level directories, while
§4.5 requires `pip install loom` / `uvx loom` and §2.5 above copies `build-backend = "uv_build"`,
which expects `src/<project-name>/`. Pick one: either the layout becomes `src/loom/{server,indexer,
hook,cli}/` (recommended — mirrors specgate exactly, and `[project.scripts] loom = "loom.cli:main"`
then works unchanged), or the backend changes to hatchling with an explicit `packages` list. As
written, §3 + §4.5 do not build.

### 5.3 The hook's fail-open timeout has a native mechanism — and a latency risk the plan misses

The MVP addendum makes "hook fail-open with ~2s timeout when the server is unreachable" mandatory
and does not say how. The SDK provides it directly:

- `Client(url, read_timeout_seconds=2.0)` — `mcp/client/client.py:300-301`.
- per-call: `await client.call_tool("check", args, read_timeout_seconds=2.0)` —
  `mcp/client/client.py:751-761`.

Wrap in `try/except Exception` → print the loud warning to stderr → `sys.exit(0)` (allow). Note that
`read_timeout_seconds` covers the *tool call*; a server that accepts TCP but never completes the
initialize handshake needs an outer `asyncio.wait_for` around the whole `async with Client(...)`
block. Budget both inside ~2s.

**Flagged risk, needs measurement, not a decision yet:** §4.2 targets **sub-10ms warm** for `check`,
but an MCP client performs an initialize handshake per session, and a PreToolUse hook is a fresh
process per edit — so every `check` would pay connect + initialize + call, which is very unlikely to
fit in 10ms over HTTP. Options to evaluate in M3: (a) a plain `POST /check` HTTP fast-path on the
same server, bypassing MCP for the hook only (MCP stays the agent-facing API); (b) a long-lived
local sidecar the hook talks to over a unix socket; (c) accept a higher hook budget and keep the
10ms target for the server-side handler only. **Measure first.** Do not let the plan's 10ms number
be read as an end-to-end hook budget.

### 5.4 §4.1 says WAL but nothing in the plan mentions `busy_timeout`

Add it to the schema/connection spec. WAL permits one writer plus concurrent readers; with two
`declare_plan` calls racing (M2's own acceptance test), the loser without `busy_timeout` gets
`sqlite3.OperationalError: database is locked` instead of a conflict response. `PRAGMA
busy_timeout=5000` on every connection, plus `BEGIN IMMEDIATE` for claim transactions. See §3.1.

### 5.5 Every loom MCP tool needs an explicit return type annotation

Not in the plan, and it silently breaks the hook and the eval harness if missed. `structured_output`
defaults to `None` = auto-detect from the return annotation
(`mcp/server/mcpserver/server.py:629,644-647`). An unannotated tool returns text content only, and
`result.structured_content` is `None` — every `r.structured_content["..."]` access in the harness
raises `TypeError`. Annotate `-> dict[str, Any]` (or a pydantic model) on every tool.

### 5.6 `mcp` 2.0.0's dependency set is heavier than "one dependency" implies

`specgate/uv.lock` resolves `mcp>=2.0.0` to 30+ packages including `starlette`, `uvicorn`,
`sse-starlette`, `pydantic`, `httpx2`, `jsonschema`, `cryptography`, `pyjwt`, `truststore`,
`opentelemetry-api`, `python-multipart`. Two consequences: (a) `uvx loom` cold-start is not
instantaneous — matters for §4.5's seamlessness claim, so warm the cache in `loom init`; (b)
**the hook must not import `mcp` if a plain-HTTP fast-path is chosen (§5.3)** — importing the whole
starlette/otel stack per PreToolUse invocation is itself a latency problem independent of the
network round trip.

### 5.7 The §5 milestone list has no acceptance-harness dependency ordering

M2's acceptance ("two simulated agents, scripted, hit `declare_plan` concurrently") and M4's demo
harness are **the same code** — `demo/run_demo.py` proves the shape works end to end. Build it once
in M2 as `eval/harness.py` (subprocess + `wait_for_port` + N `Client`s + inline asserts) and have M4
add arms and metrics to it, rather than writing two harnesses. This also means M4's shrunken scope
(MVP addendum: "harness skeleton + ONE scripted collision demo") is nearly free if M2 is done right.
