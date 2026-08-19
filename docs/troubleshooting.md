# Troubleshooting

Start here:

```bash
uv run --directory <loom> loom doctor --repo-root "$PWD"
```

It prints ten rows and exits 0 unless one of them says FAIL. The table always prints in full,
because the row that explains a breakage is usually not the first one to fail. Rows are ordered by
dependency: fix the highest FAIL first and re-run, since one root cause routinely paints several
rows red.

---

## The ten rows

### 1. `config` — which config file the gate will use

```
FAIL  config  no usable config at ~/.loom/config.toml — run `loom init --server URL` in this repo
```

The gate looks for, in order: `$LOOM_CONFIG`, then `.claude/loom.toml` walked up from the current
directory, then `~/.loom/config.toml`. A file that is missing a key is treated as absent.

**Fix:** run `loom init --server http://<host>:8790 --repo-root "$PWD"` from inside your checkout.
If you are running several agents under one OS user, give each one its own `LOOM_CONFIG` or
`LOOM_AGENT`.

### 2. `server` — is anything listening

```
FAIL  server  http://host:8790 unreachable (URLError) — is `loom serve` running?
```

**Fix:** start the server, or correct `server_url` in the config file named by row 1. If the server
is up but unreachable, check that it is not bound to `127.0.0.1` on a different machine. `/health`
is open even on a tokened server, so this row failing is never an auth problem.

### 3. `auth` — does this checkout have the credential the server wants

```
FAIL  auth  server requires a token — re-run loom init with --token
```

Probed by calling `/state`, not by trusting `/health`'s advertisement: a token that is present but
**wrong** looks identical in the config file and produces the same 401 as no token at all.

**Fix:** `loom init --server ... --token "$LOOM_TOKEN" --repo-root "$PWD"`. Ask whoever runs the
server for the secret.

### 4. `repo match` — is your repository name one the server serves

```
FAIL  repo match  config repo 'api' is not served (web, docs)
```

**This is the silent failure worth understanding.** A gate whose repository name the server does
not know is answered `allow` with case `unindexed`, forever. Everything looks healthy; nothing is
ever blocked.

**Fix:** re-run `loom init` with `--repo <one of the served names>`. If the name is right and the
server disagrees, the server was started with a different `--repo-root` basename — either rename
with `loom serve --repo NAME` or match it in your config.

### 5. `gate binary` — is `loom-gate` on PATH

```
FAIL  gate binary  `loom-gate` is not on PATH — install loom (uv sync) and re-run loom init
```

`uv sync` installs `loom-gate` into `<loom>/.venv/bin`, which is on PATH only inside `uv run`. The
hook is executed by Claude Code, not by you, so it needs the name to resolve in *that* environment.

**Fix:** either `export PATH="<loom>/.venv/bin:$PATH"` in the shell you launch your agent from, or
re-run `loom init` from inside `uv run --directory <loom>` so the registered hook command is an
absolute path into the venv.

### 6. `hook registered` — is the PreToolUse hook in `.claude/settings.json`

```
FAIL  hook registered  no loom-gate PreToolUse hook in <no .claude/settings.json> — run loom init
```

The check matches the command *substring* `loom-gate`, not today's resolved path, so moving your
virtualenv does not make an installed hook read as absent. It does make the hook stale — the
command will fail to execute, and the gate will fail open.

**Fix:** `loom init` again. It merges rather than overwrites, so your own hooks survive.

### 7. `mcp registered` — can the agent call the tools

```
FAIL  mcp registered  mcpServers.loom.url is absent in .mcp.json, expected http://host:8790/mcp
```

Without this the agent is told by the protocol to call `declare_plan` and has no way to do it —
every edit gets denied with `no_plan` and there is no path forward.

**Fix:** `loom init`. If the URL is present but different, the server moved; re-run `init` with the
new `--server`.

### 8. `gate round-trip` — does the whole chain actually work

```
FAIL  gate round-trip  exit 0: loom: WARNING — gate failed open (URLError); coordination degraded
```

This runs the real `loom-gate` binary on a synthetic edit to a path under your repo root, which
the hook has to ask the server about. A PASS means config discovery, the hook binary, the server,
auth and the decision all just worked end to end — it is the row that proves the others were not
lying. Anything less than a clean answer prints its own reason here: a fail-open (server down,
401, `/gate` erroring), a `LOOM_BYPASS` still exported in your shell, or a deny.

**Fix:** whatever rows 1–7 said. If they are all green and this is red, check `LOOM_BYPASS` in
your environment, then whether the hook binary on PATH is from a different loom checkout than the
one you are running.

### 9. `index freshness` — does this repository have a graph at all (WARN)

```
WARN  index freshness  'api' has no indexed nodes — run `loom index --repo api --repo-root PATH`
```

An unindexed repository gates nothing: every edit resolves to `unindexed` and is allowed. The
same row WARNs with `cannot tell — server or repo unknown` when there is no server to ask: this
check never FAILs, and the row that explains the outage is one of the ones above it.

**Fix:** `loom index --repo api --repo-root /path/to/checkout --db <the server's db>`. Note that
this writes to the database, so it must run on the machine that has it, with the same `--db` the
server uses.

### 10. `index staleness` — has the graph fallen behind the working tree (WARN)

```
WARN  index staleness  index behind working tree — 7 file(s) changed since the last index;
                       run `loom index --repo api --repo-root PATH --changed`
```

Symbols added since the last index are invisible: edits to them answer `new_path` and are allowed.

**Fix:** re-index as shown. `cannot tell — no /state answer to read an index age from` means the
server is older than this feature or unreachable, not that the index is stale.

---

## Symptoms `doctor` does not cover

### "My edits are never blocked"

Most often row 4: the repository name in your config is not one the server serves, so every
decision is `allow/unindexed`. Second most often row 9 or 10: the symbol you are editing is not in
the graph. Third: the file is in a skipped directory — the indexer never walks `build/`, `dist/`,
`node_modules/`, `site-packages/`, `.venv/`, `venv/`, `__pycache__/` or `.git/`, so **nothing
inside them is claimable**, by design and not yet configurable. `tests/` is *not* skipped.

Confirm with `loom ls` and by calling the `check` tool on the exact ref.

A rarer cause on macOS after an upgrade: paths with accented characters. Databases indexed by a
loom build older than the NFC path normalization hold decomposed (NFD) path keys, and NFC lookups
miss them — every decision on those files is `allow/unindexed` until one full re-index. Run
`loom index --repo NAME` once per served repo after upgrading; nothing else is needed.

### "The gate warns on every edit"

The hook is failing open and saying so. It could not reach the server within 1.5 seconds, or the
server answered something that was not a well-formed 200. Work continues but nothing is
coordinated. Check rows 2 and 3, and check whether the server is under a load that makes it slower
than the wall deadline.

### "The dashboard says reconnecting"

The server has a token. The dashboard polls `/state` from the browser with no credential, so a
tokened server answers 401 and the page never loads data. There is no workaround today; use
`loom ls` or the `list_claims` tool.

### "A plan vanished mid-session"

TTL. Plans last 30 minutes and renew implicitly on activity, but **an expired plan is never
renewed** — `renew` on it answers `{"renewed": 0, "reason": "expired"}`. That is a verdict:
declare a new plan, do not keep editing. An agent that sits idle past the deadline loses its
claims, which is the mechanism that stops a crashed agent from freezing the team.

---

## Breaking the glass

There is one escape hatch, and it is for humans:

```
Human escape hatch: LOOM_BYPASS=1 in your own shell makes the gate pass that
process through. Every use is written to ~/.loom/gate-audit.jsonl. Agents are
never told this exists — no deny message ever names a way around a claim.
```

Read the audit trail with:

```bash
cat ~/.loom/gate-audit.jsonl
```

The reason no deny message names this is deliberate: an agent told that an override exists will
use it, and a coordination gate an agent can talk itself around is not a gate. Keep it out of any
prompt, `CLAUDE.md`, or automation that an agent can read.

If you find yourself reaching for it routinely, the honest fix is usually `rescope` — or asking the
claim's owner, whose whole spec is in the message that blocked you.
