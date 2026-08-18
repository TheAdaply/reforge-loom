# Security

## What loom is not

loom is **not an access-control system**. It is advisory coordination for a team that already
trusts each other. It stops two cooperating agents from editing the same function at the same time.
It does not stop anything that does not want to be stopped, and it is not designed to.

If your threat model includes an adversary with network access to the server or shell access to a
teammate's machine, loom does not help. Use the platform's own controls.

## The tradeoffs, stated plainly

**The gate fails open.** If the server is unreachable, slow, or answers anything malformed, the
PreToolUse hook prints a warning and allows the edit. This is deliberate: a coordination layer that
can brick an edit is worse than no coordination layer. The consequence is that anyone who can make
the server unreachable can disable coordination, silently as far as the agents are concerned.

**Identity is caller-asserted.** Every tool call carries an `agent` string and loom believes it.
Any caller can declare a plan as anyone, widen anyone's plan, or release anyone's claims. This is
spec-conformant and it means a claim is a *statement of intent by a cooperating peer*, never a
permission boundary.

**The token is one shared team secret, in plaintext.** `--token` (or `LOOM_TOKEN`) puts a single
bearer secret in front of `/gate`, `/state` and `/mcp`. It authenticates the transport, not the
agent name in the payload — everyone on the team holds the same string, it is written to each
user's `.claude/loom.toml`, and it goes over the wire in the clear unless you put TLS in front of
the server. Pair it with HTTPS or a private network on any hostile path. Per-user tokens are a v2
line, not a thing loom does today.

**The server binds `0.0.0.0` by default.** That is the useful default for "one server, several
laptops" and the wrong one on a network you do not control. Pass `--host 127.0.0.1` when every
agent is on the same machine.

**`LOOM_BYPASS=1` disarms the gate for one process.** It exists so a human is never locked out of
their own repository. Every use is appended to `~/.loom/gate-audit.jsonl`. No agent-facing message
mentions it, and it should stay out of any prompt, `CLAUDE.md` or automation an agent can read —
an agent that learns of an override will use it.

**`/health` is always open**, even on a tokened server, so that `loom init` and `loom doctor` can
report "this server wants a token" instead of a bare 401. It discloses the served repository names,
the auth mode and the version.

## Deploying it sensibly

- Same machine for everyone: `--host 127.0.0.1`. Nothing else is needed.
- Several machines: a private network or a tailnet, plus `--token`. Do not expose the port to the
  internet.
- Untrusted network path: terminate TLS in front of the server (nginx, Caddy, a tailnet with
  HTTPS), and still use `--token`.
- The SQLite database contains every plan's spec text. Treat it with the same care as the source
  code it describes; back it up to somewhere with the same access controls.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through this repository's GitHub Security
Advisories ("Report a vulnerability" on the Security tab). Include the version (`loom --version`)
and enough detail to reproduce. We will acknowledge within a week.
