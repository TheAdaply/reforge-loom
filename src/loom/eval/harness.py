"""M4 — BUILD-SPEC §9.1/§9.3: the eval harness and THE scripted two-actor collision demo.

`--demo` boots the REAL server (subprocess, `python -m loom.server.app`) on a throwaway db
over a throwaway copy of `tests/fixtures/pyrepo`, indexes it through the real CLI, and then
drives the headline sequence with two in-process MCP clients plus the real `loom-gate`
PreToolUse hook as a subprocess:

    A declares  ->  B collides (and receives A's spec inline)  ->  B replans around it
    ->  B's edit on A's node is DENIED by the gate  ->  B's edit on its own node is ALLOWED
    ->  A releases

Every step carries an inline assert: **the demo IS the test** (specgate §2.6). Nothing is
mocked — the conflict comes out of SQLite, the deny text comes off the wire, and the exit
codes come from the console script a real Claude Code session would run.

Import discipline (§9.2, M4 row): the mcp *client*, `subprocess`, and `server.db` for
READ-ONLY inspection of the throwaway database. The indexer, the claims engine and the hook
are reached only as subprocesses or over the wire — exactly as a real deployment reaches them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from mcp import Client

from loom.eval.metrics import Hunk, compute_metrics
from loom.server.db import connect

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "tests", "fixtures", "pyrepo")

# The §7.4 no-override law: no deny surface may name a way around a claim. Asserted here on
# live wire text, not just on M2's templates and M3's relay.
FORBIDDEN = ("force", "bypass", "override", "unclaim", "release(", "--force")

A_AGENT, B_AGENT = "aria", "bo"
A_TARGET = "svc.py::AuthService/authenticate"
A_ASSUME = "models.py::AuthResult"
B_FIRST = "sub/sibling.py::blend"          # collides with A only AFTER one-hop expansion
B_REPLAN = "sub/deep.py::Digger/dig"       # a disjoint call component
B_ASSUME = "up.py::surface"
SHARED_NEIGHBOUR = "lib/util.py::helper"   # what both plans actually reach

# --- the synthetic §2.5 hunks the demo scores (real conduit runs are post-MVP, §9.3 M4) ---
# A's work is identical in both arms; only B's changes, which is the whole comparison.
_A_HUNKS = [
    Hunk("svc.py", 44, 3, 44, 6, ("cached = _CACHE.get(email)", "if cached is not None:",
                                  "return cached", "token = lu.make_token(email)"),
         ("token = lu.make_token(email)",)),
    Hunk("svc.py", 1, 0, 1, 2, ("_CACHE: dict[str, str] = {}",), ()),
    Hunk("lib/util.py", 12, 2, 12, 3, ("def make_token(seed, *, salted=True):",
                                       "return helper(seed) if salted else seed"),
         ("def make_token(seed):", "return helper(seed)")),
]
_B_HUNKS_LOOM = [                                  # B after replanning: a disjoint component
    Hunk("sub/deep.py", 9, 2, 9, 4, ("def dig(self, depth=1):", "for _ in range(depth):",
                                     "sibling.probe()", "return sibling.probe()"),
         ("def dig(self):", "return sibling.probe()")),
]
_B_HUNKS_UNCOORDINATED = [                         # the counterfactual: B ignored the claim
    Hunk("lib/util.py", 12, 2, 12, 3, ("def make_token(seed, prefix=''):",
                                       "return prefix + helper(seed)"),
         ("def make_token(seed):", "return helper(seed)")),
]


def wait_for_port(port: int, timeout: float = 15.0) -> None:
    """Block until something accepts TCP on `port`, or raise. No sleeps in the happy path."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.25).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on 127.0.0.1:{port} after {timeout}s")


def free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _drain(stream, sink: list[str]) -> None:
    """Keep the pipe empty so a chatty server can never deadlock on a full 64K buffer."""
    for line in iter(stream.readline, ""):
        sink.append(line.rstrip("\n"))
    stream.close()


def run_server(db: str, repo_root: str, port: int) -> tuple[subprocess.Popen, list[str]]:
    """The real server as a subprocess, plus its live log buffer. Logs to PIPE (never
    devnull — specgate §2.6).

    The caller owns the try/finally terminate. Returning the buffer beats monkeypatching it
    onto the `Popen`, which needed a `type: ignore` at every read site. `LOOM_ARM` is
    scrubbed from the child's environment: a stray `claims_only` would blank every
    `spec_md` and quietly turn the demo's embedded-spec assertions into a mystery failure.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("LOOM_ARM", "LOOM_BYPASS")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "loom.server.app", "--repo-root", repo_root,
         "--db", db, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    logs: list[str] = []
    for stream in (proc.stdout, proc.stderr):
        threading.Thread(target=_drain, args=(stream, logs), daemon=True).start()
    return proc, logs


# ------------------------------------------------------------------ transcript helpers

_W = 78


def _rule(title: str = "") -> None:
    print(f"\n{'=' * _W}" + (f"\n{title}\n" + "=" * _W if title else ""))


def _step(n: int, title: str) -> None:
    print(f"\n--- step {n}: {title} " + "-" * max(0, _W - 12 - len(title)))


def _say(text: str, indent: str = "    ") -> None:
    for line in str(text).splitlines() or [""]:
        print(indent + line)


def _ok(text: str) -> None:
    print(f"  [ok] {text}")


def _quote(text: str, limit: int = 14) -> None:
    """Show the head of a long wire string; the demo is a transcript, not a dump."""
    lines = str(text).splitlines()
    _say("\n".join(lines[:limit]), "      | ")
    if len(lines) > limit:
        _say(f"... (+{len(lines) - limit} more lines)", "      | ")


def spec_md(title: str, agent: str, goal: str, targets: list[str], interfaces: list[str],
            assumes: list[str], out_of_scope: str) -> str:
    """A filled §8.1 spec: five mandatory headings, every bracket resolved (§5.10 validates)."""
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "none"

    return (f"# Spec: {title}\n\n"
            f"**Agent**: {agent}  **Plan**: (written back after declare_plan)  "
            f"**Repo/branch**: pyrepo / main\n\n"
            f"## Goal *(mandatory)*\n\n{goal}\n\n"
            f"## Write targets *(mandatory)*\n\n{bullets(targets)}\n\n"
            f"## New/changed interfaces *(mandatory)*\n\n{bullets(interfaces)}\n\n"
            f"## Assumes *(mandatory)*\n\n{bullets(assumes)}\n\n"
            f"## Out of scope *(mandatory)*\n\n{out_of_scope}\n")


SPEC_A = spec_md(
    "Cache authenticate() results", A_AGENT,
    "Add a 60s TTL cache in front of AuthService.authenticate so repeated logins skip the\n"
    "token round. Auth-heavy calls drop to a dict lookup on hit; behaviour is unchanged on miss.",
    [A_TARGET, "lib/util.py::make_token"],
    ["CHANGED `AuthService.authenticate(self, email, db=Depends(get_db), *, use_cache: bool = True)"
     "` (was `(self, email, db=Depends(get_db))`; return type unchanged)",
     "CHANGED `lib.util.make_token(seed, *, salted: bool = True) -> str` (was `(seed)`)",
     "ADDED `svc._CACHE: dict[str, str]` module-level, read-through only"],
    [f"{A_ASSUME} — relies on `AuthResult(tag)` staying constructible from one positional arg"],
    "Session handling, the Digger traversal, and everything under sub/ are untouched.")

SPEC_B_FIRST = spec_md(
    "Trim blended util output", B_AGENT,
    "Make sibling.blend strip and de-duplicate what it forwards to lib.util.helper so callers\n"
    "stop seeing padded values. Observable outcome: blend returns a stable normalized string.",
    [B_FIRST],
    ["CHANGED `sibling.blend(items, *, dedupe: bool = True) -> str` (was `(items)`)"],
    ["none"],
    "Authentication, token minting, and the svc.py service class are untouched.")

SPEC_B_REPLAN = spec_md(
    "Depth-limited Digger traversal", B_AGENT,
    "Give Digger.dig a depth argument so callers can probe more than one level without\n"
    "recursing by hand. Observable outcome: dig(depth=n) issues n probes and returns the last.",
    [B_REPLAN],
    ["CHANGED `Digger.dig(self, depth: int = 1) -> str` (was `(self)`; return type unchanged)",
     "UNCHANGED-BUT-LOAD-BEARING `sibling.probe() -> str`"],
    [f"{B_ASSUME} — relies on `surface() -> int` staying side-effect free"],
    "lib/util.py, svc.py and the authentication path are untouched — see plan by aria.")


# ------------------------------------------------------------------ the demo

def _structured(result: Any) -> dict:
    return result.structured_content


def _gate_cmd() -> list[str]:
    """The console script if it is installed, else the same module by path — same code."""
    which = shutil.which("loom-gate")
    return [which] if which else [sys.executable, "-m", "loom.hook.gate"]


def run_gate(home: str, repo_root: str, rel_path: str,
             old_string: str) -> subprocess.CompletedProcess[str]:
    """Run the REAL PreToolUse hook on a real Edit payload, exactly as Claude Code would.

    `HOME` is redirected at the throwaway config so the demo can never touch a developer's
    own `~/.loom/`, and `LOOM_BYPASS` is scrubbed so an ambient escape hatch cannot make the
    deny assertion pass vacuously.
    """
    payload = {"session_id": "loom-demo", "tool_name": "Edit",
               "tool_input": {"file_path": os.path.join(repo_root, rel_path),
                              "old_string": old_string, "new_string": old_string + "  # edited"}}
    env = {k: v for k, v in os.environ.items() if k != "LOOM_BYPASS"}
    env["HOME"] = home
    return subprocess.run(_gate_cmd(), input=json.dumps(payload), capture_output=True,
                          text=True, timeout=30, env=env)


def deny_stats(db_path: str) -> dict[str, Any]:
    """papers §2.5 Step 6 (starvation analog) straight off the server's `events` table.

    An arm that scores zero wasted work by blocking one agent into starvation must be
    VISIBLE, so the composite never ships without these two counters beside it. Rows are
    read in rowid order — `ts` has second resolution and ties inside a demo-length run.
    """
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT actor, action FROM events ORDER BY rowid").fetchall()
    finally:
        conn.close()
    runs: dict[str, int] = {}
    current: dict[str, int] = {}
    for r in rows:
        if r["action"] == "denied":
            current[r["actor"]] = current.get(r["actor"], 0) + 1
            runs[r["actor"]] = max(runs.get(r["actor"], 0), current[r["actor"]])
        elif r["action"] == "allowed":
            current[r["actor"]] = 0
    return {"deny_count": sum(1 for r in rows if r["action"] == "denied"),
            "max_consecutive_denies": max(runs.values(), default=0),
            "denies_by_agent": {k: v for k, v in sorted(runs.items())}}


async def _script(url: str, home: str, repo_root: str, db: str) -> None:
    """The scripted collision, with the assertions inline. Raises on the first broken claim."""
    t0 = time.monotonic()
    async with Client(url) as a, Client(url) as b:
        _step(1, f"{A_AGENT} declares a plan over {A_TARGET}")
        got = _structured(await a.call_tool("declare_plan", {
            "agent": A_AGENT, "title": "Cache authenticate() results", "spec_md": SPEC_A,
            "write_targets": [A_TARGET], "assumes": [A_ASSUME]}))
        assert got.get("ok") is True, got
        plan_a = got["plan_id"]
        refs = _structured(await a.call_tool("get_plan", {"plan_id": plan_a}))["plan"]
        _say(f"plan {plan_a}  expires {got['expires_iso']}")
        _say(f"write claims ({len(refs['write_claims'])}): {', '.join(refs['write_claims'])}")
        _say(f"read  claims ({len(refs['read_claims'])}): {', '.join(refs['read_claims'])}")
        _say(f"one-hop CALLS expansion: {json.dumps(got['expanded_from'])}")
        assert SHARED_NEIGHBOUR in refs["write_claims"], refs
        assert A_TARGET in refs["write_claims"] and A_ASSUME in refs["read_claims"], refs
        assert got["warnings"] == [], got
        n_claims_a = len(got["claimed_write"]) + len(got["claimed_read"])
        _ok(f"{A_TARGET} and its one-hop neighbours are claimed WRITE — including "
            f"{SHARED_NEIGHBOUR}, which {A_AGENT} never named (§5.3, GATE-2 edit 4)")

        _step(2, f"{B_AGENT} declares a plan that names NO symbol {A_AGENT} named")
        _say(f"write_targets = [{B_FIRST}]")
        clash = _structured(await b.call_tool("declare_plan", {
            "agent": B_AGENT, "title": "Trim blended util output", "spec_md": SPEC_B_FIRST,
            "write_targets": [B_FIRST]}))
        assert clash.get("ok") is False and clash["reason"] == "conflict", clash
        assert len(clash["conflicts"]) == 1, clash
        c = clash["conflicts"][0]
        _say(f"DENIED: {c['kind']} on {c['ref']} — owned by {c['owner_agent']} "
             f"under {c['owner_plan_id']} \"{c['owner_title']}\", expires {c['owner_expires_iso']}")
        assert c["kind"] == "write-write" and c["ref"] == SHARED_NEIGHBOUR, c
        assert c["owner_plan_id"] == plan_a and c["owner_agent"] == A_AGENT, c
        assert c["owner_spec_md"] == SPEC_A, "the FULL spec must ride the conflict, inline"
        _say("owner spec arrives embedded — no second call:")
        _quote(c["owner_spec_md"])
        _ok("both plans reach lib/util.py::helper through the call graph; loom caught a "
            "collision neither agent could see in its own diff")
        # Nothing partial landed: the loser claims nothing at all (§5.3 all-or-nothing).
        after = _structured(await b.call_tool("list_claims", {}))["claims"]
        assert {cl["agent"] for cl in after} == {A_AGENT}, after
        _ok(f"all-or-nothing held: {B_AGENT} claimed nothing ({len(after)} claims, all {A_AGENT}'s)")

        _step(3, f"{B_AGENT} replans against {A_AGENT}'s DECLARED interfaces")
        _say(f"write_targets = [{B_REPLAN}]   assumes = [{B_ASSUME}]")
        good = _structured(await b.call_tool("declare_plan", {
            "agent": B_AGENT, "title": "Depth-limited Digger traversal",
            "spec_md": SPEC_B_REPLAN, "write_targets": [B_REPLAN], "assumes": [B_ASSUME]}))
        assert good.get("ok") is True, good
        plan_b = good["plan_id"]
        refs_b = _structured(await b.call_tool("get_plan", {"plan_id": plan_b}))["plan"]
        _say(f"plan {plan_b}  write {refs_b['write_claims']}  read {refs_b['read_claims']}")
        assert good["warnings"] == [], "the replan is disjoint AFTER expansion, so: no warnings"
        assert not (set(refs_b["write_claims"]) & set(refs["write_claims"])), "post-expansion overlap"
        _ok("replan accepted with zero warnings — disjoint after one-hop expansion, not just "
            "on the names the agents typed")

        _step(4, f"{B_AGENT} tries to edit {A_AGENT}'s node — the REAL PreToolUse gate runs")
        _say("$ loom-gate  <<<  {\"tool_name\": \"Edit\", \"file_path\": \".../svc.py\", ...}")
        denied = run_gate(home, repo_root, "svc.py", "token = lu.make_token(email)")
        _say(f"exit {denied.returncode}   stdout {denied.stdout!r}")
        assert denied.returncode == 2, (denied.returncode, denied.stdout, denied.stderr)
        assert denied.stdout == "", "a deny speaks on stderr only (§7.3)"
        assert denied.stderr.startswith("loom: BLOCKED —"), denied.stderr
        assert A_TARGET in denied.stderr, denied.stderr
        assert "## New/changed interfaces" in denied.stderr, "A's spec must be IN the deny"
        assert "AuthService.authenticate(self, email, db=Depends(get_db)" in denied.stderr
        low = denied.stderr.lower()
        for bad in FORBIDDEN:
            assert bad not in low, f"deny text names an escape hatch: {bad!r}"
        _say("stderr (what the blocked agent actually reads):")
        _quote(denied.stderr, 18)
        _ok("edit blocked at the tool boundary, with the owner's interfaces to build against "
            "— and no force/override/bypass path named anywhere (§7.4 no-override law)")

        _step(5, f"{B_AGENT} edits its OWN node — same gate, same config")
        _say("$ loom-gate  <<<  {\"tool_name\": \"Edit\", \"file_path\": \".../sub/deep.py\", ...}")
        allowed = run_gate(home, repo_root, "sub/deep.py", "return sibling.probe()")
        _say(f"exit {allowed.returncode}   stdout {allowed.stdout!r}   stderr {allowed.stderr!r}")
        assert allowed.returncode == 0, (allowed.returncode, allowed.stderr)
        assert allowed.stdout == "" and allowed.stderr == "", "an allow is silent (§7.3)"
        _ok("in-plan edit passes silently — coordination is a gate, not a queue")

        _step(6, f"{A_AGENT} finishes and releases plan {plan_a}")
        rel = _structured(await a.call_tool("release", {"plan_id": plan_a, "agent": A_AGENT}))
        _say(json.dumps(rel))
        assert rel.get("ok") is True and rel["plan_status"] == "done", rel
        assert rel["released_claims"] == n_claims_a, (rel, n_claims_a)
        left = _structured(await b.call_tool("list_claims", {}))["claims"]
        _say(f"claims still standing: {[(cl['agent'], cl['ref']) for cl in left]}")
        assert {cl["agent"] for cl in left} == {B_AGENT}, left
        free = _structured(await b.call_tool("check", {"agent": B_AGENT, "node": A_TARGET}))
        _say(f"check({B_AGENT}, {A_TARGET}) -> {json.dumps(free)}")
        assert free["allow"] is False and free["case"] == "out_of_scope", free
        _ok(f"{A_AGENT}'s claims are gone; {B_AGENT} is now told to RESCOPE, not that the node "
            "is owned — the deny changed because the world changed")

    # ---------------------------------------------------------------- the metrics row
    _rule("metrics — one §2.5 row (synthetic hunks; real deny counters from `events`)")
    stats = deny_stats(db)
    row = {"task": "authenticate-vs-blend", "arm": "loom", "seed": 0,
           **compute_metrics(_A_HUNKS, _B_HUNKS_LOOM, []),
           "post_merge_test_failures": 0,
           "wall_clock_s": round(time.monotonic() - t0, 2),
           # No LLM agents in a scripted demo, so there is no token count to report. None,
           # never 0 — the same discipline the composite uses for an empty arm.
           "tokens_total": None, **stats}
    for verbose in ("dup_pairs", "overlap_clusters", "conflicted_paths"):
        row.pop(verbose)          # human-eyeball detail; the row stays one line (papers §2.5)
    print(json.dumps(row))

    counterfactual = compute_metrics(_A_HUNKS, _B_HUNKS_UNCOORDINATED, ["lib/util.py"])
    assert row["wasted_work_share"] == 0.0, row
    assert row["deny_count"] >= 1 and row["max_consecutive_denies"] >= 1, row
    assert counterfactual["wasted_work_share"] > 0.0, counterfactual
    print()
    _ok(f"wasted_work_share = {row['wasted_work_share']} — the two branches touch disjoint files")
    _ok(f"deny_count = {row['deny_count']}, max_consecutive_denies = "
        f"{row['max_consecutive_denies']} ({row['denies_by_agent']}) — nobody was starved into "
        "that zero (papers §2.5 Step 6)")
    _ok("counterfactual, same A work, B ignoring the claim: wasted_work_share = "
        f"{counterfactual['wasted_work_share']:.4f} over {counterfactual['merge_conflict_files']} "
        "conflicted file(s)")


def demo() -> None:
    """Boot the real stack on throwaway state and run the scripted collision end to end."""
    with tempfile.TemporaryDirectory(prefix="loom-demo-") as tmp:
        # The copy is named `pyrepo` on purpose: `loom index` and `loom.server.app` both mint
        # the repo salt from the repo-root basename, so one directory name gives one spelling.
        repo_root = os.path.join(tmp, "pyrepo")
        if not os.path.isdir(FIXTURE):
            raise SystemExit(
                f"loom demo: fixture repo not found at {FIXTURE}. The demo reads it from the "
                "source checkout, so run it from a clone rather than an installed wheel.")
        shutil.copytree(FIXTURE, repo_root)
        db = os.path.join(tmp, "loom.sqlite3")
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, ".loom"))
        port = free_port()

        _rule("loom demo — two actors, one repo, one shared symbol neither of them named")
        _say(f"repo copy   {repo_root}", "  ")
        _say(f"database    {db}  (throwaway)", "  ")
        _say(f"gate config {os.path.join(home, '.loom', 'config.toml')}  (HOME redirected)", "  ")

        indexed = subprocess.run(
            [sys.executable, "-m", "loom.cli.main", "index", "--repo-root", repo_root,
             "--db", db], capture_output=True, text=True, timeout=180)
        assert indexed.returncode == 0, indexed.stderr
        _say(f"indexed     {indexed.stdout.strip()}", "  ")

        with open(os.path.join(home, ".loom", "config.toml"), "w", encoding="utf-8") as fh:
            fh.write(f'server_url = "http://127.0.0.1:{port}"\nagent = "{B_AGENT}"\n'
                     f'repo = "pyrepo"\nrepo_root = "{repo_root}"\n')

        proc, server_logs = run_server(db, repo_root, port)
        try:
            try:
                wait_for_port(port)
            except TimeoutError:
                raise RuntimeError("server never came up:\n"
                                   + "\n".join(server_logs[-20:])) from None
            _say(f"server      http://127.0.0.1:{port}/mcp  (pid {proc.pid})", "  ")
            asyncio.run(_script(f"http://127.0.0.1:{port}/mcp", home, repo_root, db))
        except BaseException:
            print("\n--- server log tail ---", file=sys.stderr)
            print("\n".join(server_logs[-20:]), file=sys.stderr)
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:                      # pragma: no cover
                proc.kill()

        _rule()
        print("demo complete — every step above carried an inline assert, and all of them held.")


def main() -> None:
    ap = argparse.ArgumentParser(prog="loom.eval.harness", description="loom eval harness")
    ap.add_argument("--demo", action="store_true",
                    help="run the scripted two-actor collision against a real server")
    args = ap.parse_args()
    if not args.demo:
        ap.print_help(sys.stderr)
        raise SystemExit(2)
    demo()


if __name__ == "__main__":
    main()
