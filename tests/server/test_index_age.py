"""U2 — `index_age`: staleness as a VERDICT on `/state`, never an error and never a block.

Posture adapted from graphify's PreToolUse staleness nudge — soften, never block; no code
copied, see CREDITS.md. The three things worth pinning are the three ways a
number like this goes wrong in production: it lies about a fresh index (the `iso()`
truncation trap), it lies about an absent one (never-indexed is not "behind"), and it
turns a 2s dashboard poll into an `os.walk` storm (the TTL).
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest
from conftest import REPO

import loom.server.app as app_mod
from loom.server.app import index_age, state_payload
from loom.server.db import iso, log_event, now_s


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """`_index_age_cache` is module state keyed by (repo, root); tmp roots differ per test,
    but a stray entry would still outlive its test. Clear it on both sides."""
    app_mod._index_age_cache.clear()
    ttl = app_mod.INDEX_AGE_TTL_S
    yield
    app_mod._index_age_cache.clear()
    app_mod.INDEX_AGE_TTL_S = ttl


@pytest.fixture()
def tree(tmp_path) -> str:
    """A minimal repo root: two .py files, both older than any index we will record."""
    old = time.time() - 600
    for name in ("a.py", "b.py"):
        p = tmp_path / name
        p.write_text("def f():\n    return 1\n", encoding="utf-8")
        os.utime(p, (old, old))
    return str(tmp_path)


def _indexed(conn, when: float) -> None:
    """Record an indexer run at `when` the way `walk.index_repo` does."""
    conn.execute("INSERT INTO events (ts, actor, action, detail, repo) VALUES (?,?,?,?,?)",
                 (iso(when), "indexer", "indexed", "seeded", REPO))
    conn.commit()


def test_a_repo_that_was_never_indexed_is_absent_not_stale(gconn, tree) -> None:
    """`loom doctor`'s freshness row owns the never-indexed case; this one must stay quiet
    or every fresh checkout would be told to refresh an index it does not have."""
    assert index_age(gconn, REPO, tree, now_s()) == {
        "indexed_at": None, "dirty_files": 0, "stale": False}


def test_an_index_newer_than_every_file_is_not_stale(gconn, tree) -> None:
    _indexed(gconn, now_s())

    age = index_age(gconn, REPO, tree, now_s())

    assert age["stale"] is False and age["dirty_files"] == 0
    assert age["indexed_at"] is not None


def test_files_touched_after_the_index_are_counted_and_flip_the_verdict(gconn, tree) -> None:
    _indexed(gconn, now_s())
    future = time.time() + 30
    os.utime(os.path.join(tree, "a.py"), (future, future))

    age = index_age(gconn, REPO, tree, now_s())

    assert (age["dirty_files"], age["stale"]) == (1, True)      # b.py is still older
    assert age["indexed_at"] is not None


def test_a_file_written_in_the_index_own_second_is_not_reported_stale(gconn, tree) -> None:
    """The `iso()` truncation trap: `indexed_at` reads back up to 1s EARLY, so a file
    written just BEFORE the index would look newer than it forever. One second of grace."""
    at = now_s()
    _indexed(gconn, at)
    just_before = int(at) + 0.9            # same wall-clock second the ISO stamp truncated to
    os.utime(os.path.join(tree, "a.py"), (just_before, just_before))

    assert index_age(gconn, REPO, tree, now_s())["stale"] is False


def test_the_answer_is_cached_for_the_ttl_and_recomputed_after_it(gconn, tree) -> None:
    """A 2s dashboard poll must not become an `os.walk` per repo per poll."""
    _indexed(gconn, now_s())
    now = now_s()
    assert index_age(gconn, REPO, tree, now)["stale"] is False

    future = time.time() + 30
    os.utime(os.path.join(tree, "b.py"), (future, future))

    assert index_age(gconn, REPO, tree, now)["stale"] is False           # still cached
    assert index_age(gconn, REPO, tree, now + app_mod.INDEX_AGE_TTL_S + 1)["stale"] is True


def test_state_payload_carries_the_verdict_and_defaults_to_no_root(gconn, tree) -> None:
    """The field ships on `/state` (dashboard + doctor read it there) and NOWHERE on
    `/gate`, whose five wire keys are frozen. A caller with no root — every unit-test call
    of `state_payload` — still gets a well-formed, honest answer."""
    _indexed(gconn, now_s())
    future = time.time() + 30
    os.utime(os.path.join(tree, "a.py"), (future, future))

    assert state_payload(gconn, REPO, [REPO], tree)["index_age"]["stale"] is True
    app_mod._index_age_cache.clear()
    rootless = state_payload(gconn, REPO, [REPO])["index_age"]
    assert rootless["stale"] is False and rootless["dirty_files"] == 0


def test_only_this_repos_own_index_events_set_the_clock(gconn, tree) -> None:
    """Multi-repo: another salt's index must not make this repo look fresh."""
    log_event(gconn, "indexer", "indexed", "other repo", "somewhere-else")
    gconn.commit()

    assert index_age(gconn, REPO, tree, now_s())["indexed_at"] is None
