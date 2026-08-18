# ITERATION-2-SPEC — big-repo dashboard, honest caps, opt-in token auth (frozen)

Orchestrator, 2026-08-18, on HEAD after multi-repo tryout. Found by real-repo tryout: 78-file
conduit renders the fabric as an unreadable smear; /state caps (600/1500) are silent; identity is
caller-asserted (top customer objection). Amends BUILD-SPEC/MULTIREPO-SPEC; conflicts resolve to
this file; deltas in §4.

## 1. Fabric focus mode (templates/dashboard.html ONLY)

- Threshold frozen: if the repo has > 12 files, render FOCUS view; ≤ 12 keeps today's full view.
- FOCUS shows threads for: (a) every file with ≥1 claimed node; (b) padded to a minimum of 3 and
  capped at 12 threads by adding the largest remaining files by bead count.
- Within a shown file: if > 14 beads, show all CLAIMED beads plus the first unclaimed by index up
  to 14 total, then one bead-less monospace note `+k more` at the thread's foot.
- Panel header gains a scope note when focused: `showing N of M files — files with active claims
  surface automatically` (exact copy). Zero-claims big repo: top-8 files by bead count + note.
- Edges drawn only between visible beads (positions map already enforces this).
- No new colors; the note text uses --ink-2.

## 2. Honest caps (/state + dashboard)

- /state adds: `"totals": {"nodes": <COUNT(*) for repo>, "edges": <COUNT(*) joined for repo>}`
  and `"truncated": {"nodes": bool, "edges": bool}` (sent-length < total). Existing `counts` keys
  keep their current post-LIMIT meaning (frozen consumers), documented inline.
- Dashboard: when either truncated flag is true, the fabric panel header appends
  ` · graph truncated to first 600 nodes` (monospace, --ink-2). Stat tile "nodes" uses totals.
  (Stat tiles today show counts.plans/claims/agents/denies — unchanged.)

## 3. Opt-in shared-token auth

- `loom serve ... --token SECRET` (or env LOOM_TOKEN; flag wins). Empty string = hard error.
- When set, the server requires `Authorization: Bearer SECRET` on: POST /gate, GET /state, and
  the MCP mount /mcp (starlette middleware on the app; MCPServer custom middleware or an ASGI
  wrapper — implementer's choice, but /health STAYS OPEN and gains `"auth": "token"` vs "open").
  Wrong/missing token → 401 JSON {"ok": false, "error": "unauthorized"} (/gate too: 401, NOT a
  200 gate-shape — the hook treats non-200 as fail-open, which is the correct advisory posture
  for a misconfigured client).
- `loom init --server URL --token SECRET ...`: token required iff /health says auth=token (die
  with a clear message if omitted); stored in loom.toml (`token = "..."`); written into
  .mcp.json as `"headers": {"Authorization": "Bearer SECRET"}` on the loom server entry.
- gate.py: if config has non-empty `token`, send the Authorization header on /gate calls.
  (~4 lines; fail-open behavior on 401 unchanged by design — doctor is the loud path.)
- `loom doctor`: server row reports auth mode; gate round-trip row must still pass WITH auth;
  new row `auth` — PASS when config token matches server requirement (probe /state), FAIL with
  "server requires a token — re-run loom init with --token" when 401.
- Threat model note for docs: shared team secret over the wire — pair with HTTPS/tailscale for
  hostile networks; per-user tokens are the v2 line. Honest, not oversold.

## 4. Deltas: D7 /state totals+truncated; D8 /health auth field; D9 --token/LOOM_TOKEN + 401
middleware; D10 loom.toml token key + .mcp.json headers; D11 doctor auth row. No changes to §7.4
templates, /gate 200-shape keys (401 is a transport refusal, not a gate shape), ids, claims.

## 5. Tests (the bar)

- Unit: token flag/env precedence + empty-token error; /state totals/truncated math (seed > cap
  via many synthetic nodes? too heavy — instead monkeypatch the LIMITs via module constants
  `STATE_NODE_CAP`/`STATE_EDGE_CAP` introduced by this spec, set low in the test).
- Integration (subprocess server WITH --token): /state 401 without header, 200 with; /gate 401
  without, routes with; mcp Client with headers succeeds (mcp.Client custom httpx client or
  headers param — use whatever the installed SDK supports, verified not recalled); init against
  token server without --token dies with the §3 message, with --token writes toml+mcp headers;
  doctor all-PASS on tokened rig, auth-FAIL when toml token wrong.
- Dashboard: focus-mode logic is pure JS — add a tiny node-count fixture test only if cheap via
  /state on pyrepo (12 files threshold not crossed → full view asserted by absence of the note);
  the conduit visual is the orchestrator's screenshot job, not CI's.
- Full suite ≥ 279 and green.

## 5a. Landing notes (orchestrator addendum — binding on the implementer AND the verify gate)

- `resolve_token`: if its signature reads `os.environ` internally (rather than injected env), the
  §5 flag/env-precedence unit test MUST monkeypatch `os.environ` — either design is acceptable,
  the test discipline is not optional.
- `tests/conftest.py` MUST gain an autouse `monkeypatch.delenv("LOOM_TOKEN", raising=False)`
  fixture: an ambient LOOM_TOKEN on the machine would arm auth inside every untokened subprocess
  rig and red the suite non-reproducibly. The verify gate checks this fixture exists.
- THREE existing /health exact-equality asserts break BY DESIGN when it gains `auth`:
  tests/server/test_multirepo.py (~:192, ~:320) AND
  tests/server/test_concurrency.py::test_the_plain_http_gate_and_health_routes_speak_the_frozen_wire
  — plus test_doctor.py's CHECKS tuple/row-count (9th `auth` row). Updating all four is in-scope
  for the server stage, not a regression; the suite CANNOT go green until they land.

- SDK fact (verified against installed mcp 2.0.0, not recalled): `mcp.Client` has NO `headers`
  parameter. The tokened-integration-test path is `create_mcp_http_client(headers=...)` from
  `mcp.shared._httpx_utils`, passed as `streamable_http_client(url, http_client=...)` (the SDK's
  own session_group.py:325 pattern) — and the caller must enter that http client as an async
  context itself (it is not lifecycle-managed by the transport).
- Dashboard-on-tokened-server UX (Dashboard stage, binding): when `/state` answers 401, the
  banner must read `this server requires a token — the dashboard reads only open servers for now`
  instead of the generic reconnecting text (the shell is open at `/` by spec, so without this the
  page spins forever). Detect via response.status === 401 before the json parse.
- Doctor row-count prose: README "Eight checks" line and `cmd_doctor`'s docstring must say nine
  when the auth row lands (staleness, in-scope for the server stage).
- Ambient-empty-token edge: `LOOM_TOKEN=""` (set but empty) must be treated as UNSET for serve
  (not a hard error) — the hard error is reserved for an explicit `--token ""`. Unit-test both.

## 6. Out of scope: per-user tokens/OAuth, HTTPS termination, rate limiting, Postgres.
