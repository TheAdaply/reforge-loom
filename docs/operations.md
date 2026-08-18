# Operations

Running a loom server for a team. Read [architecture.md](architecture.md) first if you want to know
why any of this is shaped the way it is.

## Where the database lives

`--db` names the SQLite file. Without it, the database lands at `<first --repo-root>/.loom.sqlite3`.

That default is fine for a single repository and a trap for several: "beside the first root" is a
rule every operator has to remember, and the CLI verbs that read the database (`ls`, `show`,
`release`) default to `<cwd>/.loom.sqlite3`, which will not be the same file. **Pass `--db`
explicitly whenever you serve more than one root**, and pass the same `--db` to `loom index`.

```bash
loom serve --repo-root api=/srv/checkouts/api --repo-root web=/srv/checkouts/web \
           --db /srv/loom/loom.sqlite3 --port 8790
```

A missing database is reported rather than created: `loom ls --db /wrong/path` says
`no loom database at ... — is 'loom serve' running with this --db?` instead of printing
"0 active claims".

## Host and port

`--host` defaults to `0.0.0.0` and `--port` to `8790`. `0.0.0.0` is the useful default for the
"one server, several laptops" case and the wrong one if the machine is on a network you do not
control. When every agent runs on the same box, use `--host 127.0.0.1`. When they do not, put the
server on a private network or a tailnet, and read [SECURITY.md](../SECURITY.md) before anything
else.

## Backups

The database is one SQLite file and everything in it is reconstructible except the plans and
claims — the graph is rebuilt from the repository on every boot. Back it up with SQLite's own
online backup, which is safe against a running server:

```bash
sqlite3 /srv/loom/loom.sqlite3 ".backup '/srv/loom/backup-$(date +%F).sqlite3'"
```

Copying the file with `cp` while the server is running can capture a torn write-ahead log. If you
lose the database entirely, restart `loom serve`: the graph re-indexes at boot and every agent
re-declares. Nothing is lost except in-flight claims, which expire in 30 minutes anyway.

## Re-indexing

`loom serve` indexes every root at boot and prints one `loom: indexed {...}` line per repository.
After that, the graph does not move on its own: a new function is invisible to loom until someone
re-indexes.

```bash
# one repository, from the machine that has the database
loom index --repo api --repo-root /srv/checkouts/api --db /srv/loom/loom.sqlite3 --changed
```

`--changed` skips re-writing node rows for files whose content hash is unchanged. It does **not**
skip the parse, and it does not skip the edge rebuild — an incremental index produces exactly the
same graph as a cold one, which is a property with a test on it. Expect an incremental run to cost
roughly 90% of a cold one; a 200-file repository indexes in about 0.15 seconds either way.

`loom doctor`'s "index staleness" row tells a user when the graph has fallen behind their working
tree. It is a WARN, never a FAIL.

Re-indexing while the server is live is supported, and it is done inside one transaction so the
gate never sees a half-written graph.

## Keeping `serve` alive

`loom serve` is an ordinary foreground process that writes to stdout. Run it under whatever
supervisor you already use. A systemd unit:

```ini
[Unit]
Description=loom coordination gate
After=network.target

[Service]
ExecStart=/srv/loom/.venv/bin/loom serve --repo-root api=/srv/checkouts/api \
          --db /srv/loom/loom.sqlite3 --host 127.0.0.1 --port 8790
Restart=always
User=loom
Environment=LOOM_TOKEN=<shared secret>

[Install]
WantedBy=multi-user.target
```

On macOS, a launchd `plist` with `KeepAlive` does the same job.

Restarting is cheap and safe: claims live in the database, not in memory, so a restart costs one
re-index and nothing else.

## Turning on the shared token

```bash
loom serve --repo-root /srv/checkouts/api --token "$LOOM_TOKEN" --db /srv/loom/loom.sqlite3
```

`LOOM_TOKEN` in the environment is the fallback; the flag wins. Every user must then re-run
`loom init --token "$LOOM_TOKEN"` in their checkout. `/health` stays open and advertises
`auth=token`, which is how `init` and `doctor` produce a useful message instead of a bare 401.

The dashboard cannot read a tokened server — the browser polls `/state` with no credential. That is
a known gap, not a configuration mistake.

## What the server logs

One line per repository at boot, then uvicorn's request log. Every gate decision is recorded in the
database's `events` table rather than on stdout, and the dashboard's decision feed is a view of it.
Human uses of the `LOOM_BYPASS` escape hatch are appended to `~/.loom/gate-audit.jsonl` on the
machine where the bypass happened — see [troubleshooting.md](troubleshooting.md).

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `LOOM_TOKEN` | `loom serve` | Shared secret, when `--token` is absent. |
| `LOOM_CONFIG` | the hook, `loom doctor` | Replaces config discovery with an explicit path. |
| `LOOM_AGENT` | the hook | Overrides the configured agent identity for this process. |
| `LOOM_BYPASS` | the hook | `1` makes the gate pass this process through. Audited. Humans only. |
| `LOOM_AGENT_MODE` | `loom ls` | `1` suppresses the human-facing count line. |
| `LOOM_ARM` | the server | `claims_only` blanks spec text everywhere it is surfaced. An evaluation control arm, not a production setting. |
