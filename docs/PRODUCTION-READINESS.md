# PRODUCTION-READINESS — what stands between this MVP and a customer install

Written 2026-08-20, on the HEAD that passed the real two-laptop live fire (see BUILD-LOG tail
and `dashboard-two-laptops.png`). The coordination engine is proven: 360 tests, judged
benchmarks (64 concurrent agents, 0 errors), five red-team cycles closed, and a cross-machine
collision arbitrated live between two humans' Claude sessions. What follows is everything else
— ranked by what actually blocks money changing hands.

## P0 — blocks the first install (a customer cannot even start without these)

1. **`pip install` distribution.** Today installing loom means cloning this repo and running
   `uv sync` — fine for us, dead on arrival for a customer. Needs: a PyPI name (`loom` is
   almost certainly taken; decide `loom-coord` / `reforge-loom` / other — OWNER DECISION),
   version tag (`v0.1.0`), a release GitHub Action (build + publish on tag), and the README
   quickstart rewritten around `uvx <name> serve ...` / `uv tool install <name>`. Everything
   in the codebase is already src-layout + uv_build; this is a day of work plus the name.
2. **A server deployment recipe that survives a reboot.** One copy-pasteable page + files:
   systemd unit (Restart=always, the cron-driven `git pull && loom index --changed` sync of
   the server's checkout), a Dockerfile/compose variant, and the TLS story — Caddy reverse
   proxy for a public VM (2-line Caddyfile, auto-HTTPS), or Tailscale for a private team (no
   TLS config at all). Today the "server" is a laptop process started by hand; the live fire
   needed `caffeinate` to keep it alive. Customers get a box, not a laptop.
3. **Dashboard on tokened servers.** Proven live this week: with `--token` set the dashboard
   shows a banner instead of the board (§5a "for now"), so our own demo had to drop auth to
   show the fabric. Any real customer runs with a token. Smallest fix: the page accepts
   `?token=` once, keeps it in localStorage, sends it as the Bearer header on /state polls.
4. **A 10-minute CUSTOMER quickstart.** The current README is written for people building
   loom. The customer page is: install server → serve your repo → every dev runs `loom init`
   in their clone → restart Claude Code → done. Include the two footguns we hit ourselves in
   the live fire: `loom init` must be given `--repo-root` (or run from the repo root — see
   P1.6), and hooks/MCP load at Claude Code startup, so "restart your session" is a step,
   not a troubleshooting tip.

## P1 — first customer will hit these in week one

5. **Language positioning, stated loudly.** Symbol-level claims are Python-only; every other
   language gets file-level claims and gating (by design, bench E3). That is still valuable —
   file-level is what everyone else doesn't have either — but it must be on the tin, or the
   first TypeScript customer feels lied to. Fast-follow: tree-sitter TS/JS symbol support
   (the indexer's two-pass structure was built for added languages).
6. **`loom init` defaults to the git toplevel, not cwd.** The orchestrator itself mis-wired a
   repo during the live fire because `uv run --directory` changes cwd. `init` should default
   `--repo-root` to `git rev-parse --show-toplevel` (cwd as fallback outside git), and print
   the resolved root it is about to wire. Same class: init's closing output should say
   "restart Claude Code in this repo" explicitly.
7. **Backup + reset story for the coordination db.** `.loom.sqlite3` holds plans/claims/audit.
   Document: it is derived-plus-coordination state — safe to back up with one `sqlite3
   .backup`, safe to delete when the team is idle (re-index rebuilds the graph; active plans
   are lost, which is announced). One page, plus log rotation guidance for the server log.
8. **Token lifecycle — CONFIRMED leak path.** `loom init` writes the shared token as a
   plaintext Bearer header into `.mcp.json`, and `.mcp.json` is NOT gitignored (verified on
   the live-fire checkout) — a customer's `git add -A` commits the server secret. Immediate
   patch: when a token is present, `init` adds `.mcp.json` to .gitignore beside the identity
   file and says so (teams that want a committed .mcp.json can keep a tokenless variant).
   Proper fix: keep the header as `${LOOM_TOKEN}`-style env expansion with the secret in the
   per-user gitignored file. Plus the rotation two-step doc (restart server with new token →
   each dev re-runs `loom init --token`).
9. **Post-fix benchmark refresh.** research/benchmarks.md rows flagged "pre-fix" (claim
   counts moved by W2/BC3-1) must be re-run before any number is quoted to a customer. Sales
   claims and shipped behavior have to match to the digit.

## P2 — scale and polish (fine to ship the first install without)

10. **Windows.** Entirely untested (macOS + Linux proven). Hook subprocess, path handling and
    the gitignore writer all touch OS specifics. Either test or state "macOS/Linux only" in
    the README.
11. **Per-user identity/auth tiers** (v2 design exists): per-user tokens, claim ownership
    tied to authenticated identity rather than self-declared agent names.
12. **SQLite → Postgres flip** when a customer's fleet outgrows one writer (~dozens of
    simultaneous agents; the flip is localized to db.py/claims.py by design, §11.11).
13. **Known residual** (§11.45): on enormous trees the single edge-swap transaction can still
    exceed the hook budget once per re-index; busy verdicts surface as data. Revisit only if
    a real repo hits it.
14. **Dashboard multi-repo aggregate view + read-only sharing** (customer stakeholders want
    the fabric without shell access).

## What is already customer-grade (do not redo)

Advisory claims with TTL + owner-spec-embedded denies; PreToolUse enforcement that fails open
loudly and exits 0/2 only; multi-repo serve; `loom doctor` (10 checks, the support tool);
chunked indexing; origin-aware authority with every read surface showing it; 360-test suite
with a stateful fuzzer; frozen wire contract with a written delta ledger (§11) for every
behavior change since.
