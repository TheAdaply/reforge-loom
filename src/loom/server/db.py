"""Frozen artifact — BUILD-SPEC §2. SQLite schema, connection pragmas, transaction law.

Every SQL statement that JUDGES a claim lives in `db.py` / `claims.py`, so the v2 Postgres
flip is a localized rewrite (§11.11). Three callers carry their own SQL by design and are
the whole list: the indexer's node/edge writes (`indexer/walk.py`), the dashboard's read
payload (`server/app.py`), and the CLI admin verbs (`cli/main.py`). stdlib `sqlite3` only —
no SQLAlchemy.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from time import time

DDL = """
CREATE TABLE IF NOT EXISTS nodes (
  id         TEXT PRIMARY KEY,               -- "n-" + 8 base36 chars, §3
  repo       TEXT NOT NULL,
  path       TEXT NOT NULL,                  -- repo-root-relative, POSIX separators
  qualname   TEXT NOT NULL DEFAULT '',       -- '' = file-level node; else 'Class/method' (§4)
  kind       TEXT NOT NULL,                  -- 'File' | 'Class' | 'Function'
  body_hash  TEXT NOT NULL DEFAULT '',       -- sha256 hex of node source (file content for File)
  sig_hash   TEXT NOT NULL DEFAULT '',       -- sha256 hex of def/class header line(s); v2 impact needs it, add NOW (papers 5.3)
  start_line INTEGER,                        -- 1-based, non-identifying (locator aid)
  end_line   INTEGER,
  updated    TEXT NOT NULL,                  -- ISO-8601 UTC
  UNIQUE (repo, path, qualname)
);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_qualname ON nodes(repo, qualname);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_path     ON nodes(repo, path);

CREATE TABLE IF NOT EXISTS edges (
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  kind TEXT NOT NULL,                        -- 'CALLS' | 'IMPORTS' | 'CONTAINS'; plain TEXT, NO
                                             -- CHECK constraint (v2 adds EXTENDS/USES without a
                                             -- migration — papers 5.1). CONTAINS is canonical,
                                             -- never DEFINES (falkordb C3).
                                             -- Direction (frozen, GATE-2 edit 7):
                                             --   CALLS    src = caller node,          dst = callee node
                                             --   IMPORTS  src = importing File node,  dst = imported File node
                                             --   CONTAINS src = container (File|Class), dst = contained (Class|Function)
  PRIMARY KEY (src, dst, kind)
);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, kind);

CREATE TABLE IF NOT EXISTS plans (
  id          TEXT PRIMARY KEY,              -- "lm-" + >=6 base36 chars, §3
  agent       TEXT NOT NULL,
  repo        TEXT NOT NULL,
  branch      TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  spec_md     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',-- 'active' | 'done' | 'expired' | 'superseded'
  created     TEXT NOT NULL,                 -- ISO-8601 UTC
  updated     TEXT NOT NULL,
  ttl_expires REAL NOT NULL                  -- unix seconds UTC (float)
);
CREATE INDEX IF NOT EXISTS idx_plans_repo_status  ON plans(repo, status);
CREATE INDEX IF NOT EXISTS idx_plans_agent_status ON plans(agent, status);

CREATE TABLE IF NOT EXISTS claims (
  node_id  TEXT NOT NULL,
  plan_id  TEXT NOT NULL,
  mode     TEXT NOT NULL,                    -- 'write' | 'read'
  origin   TEXT NOT NULL DEFAULT 'target',   -- 'target' | 'expanded' (BC3-1 authority model)
  created  TEXT NOT NULL,
  released TEXT,                             -- tombstone, never DELETE (agent-mail §2.1)
  PRIMARY KEY (node_id, plan_id, mode)
);
CREATE INDEX IF NOT EXISTS idx_claims_node ON claims(node_id, released);
CREATE INDEX IF NOT EXISTS idx_claims_plan ON claims(plan_id, released);

CREATE TABLE IF NOT EXISTS events (
  ts     TEXT NOT NULL,                      -- ISO-8601 UTC
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,                      -- declared|denied|allowed|released|expired|rescoped|renewed|bypass|indexed
  detail TEXT NOT NULL DEFAULT '',
  repo   TEXT NOT NULL DEFAULT ''            -- MULTIREPO-SPEC §3 (D1); '' = pre-migration
                                             -- history, shown in EVERY repo's feed.
);
"""

# MULTIREPO-SPEC §3 (D1). One server now serves many repo salts, so the audit feed has to
# say WHICH repo an event belongs to — otherwise the dashboard shows every repo's traffic
# at once. `nodes`/`plans` were repo-keyed from day one; `events` is the one table that
# was not, so it is the one table that needs a migration. Guarded by PRAGMA table_info so
# it is idempotent and so a fresh db (which gets the column from the DDL above) skips it.
MIGRATIONS = (
    ("events", "repo", "ALTER TABLE events ADD COLUMN repo TEXT NOT NULL DEFAULT ''"),
    # BC3-1: how a claim was acquired decides how much authority it grants. 'target' =
    # the agent named this node; 'expanded' = §5.3's CALLS hop swept it in. Pre-migration
    # rows default to 'target' — the generous reading, correct for old single-writer dbs.
    ("claims", "origin", "ALTER TABLE claims ADD COLUMN origin TEXT NOT NULL DEFAULT 'target'"),
)


def connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with EVERY pragma of §2 set (none are optional).

    `isolation_level=None` puts the driver in autocommit so `immediate()`'s explicit
    BEGIN IMMEDIATE / COMMIT is the ONLY transaction control (transaction law, §2).
    """
    con = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")      # persists in the file; harmless to repeat
    con.execute("PRAGMA busy_timeout=5000")     # REQUIRED: losers of a write race must queue, not error
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")    # correct pairing with WAL
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> None:
    """Create the schema, apply `MIGRATIONS`, close the bootstrap connection. Idempotent."""
    con = connect(db_path)
    try:
        con.executescript(DDL)
        for table, column, ddl in MIGRATIONS:
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                con.execute(ddl)
    finally:
        con.close()


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE / COMMIT, ROLLBACK on raise.

    The write lock is taken BEFORE the first read, so every read->judge->write cycle
    (`declare_plan`, `rescope`, `renew`, `release`, plan-ID minting, the sweep) is one
    atomic check-then-act. There is no `threading.Lock` anywhere in the server.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        _abort(conn)
        raise
    else:
        try:
            conn.execute("COMMIT")
        except BaseException:
            # A failed COMMIT leaves the transaction open, and `app.connection_factory`
            # hands out ONE long-lived connection per worker thread — so without this the
            # thread's every later BEGIN IMMEDIATE fails forever, with no self-heal path.
            _abort(conn)
            raise


def _abort(conn: sqlite3.Connection) -> None:
    """Best-effort ROLLBACK that never displaces the exception being propagated.

    SQLite rolls back implicitly on the SQLITE_FULL / SQLITE_IOERR / SQLITE_BUSY /
    SQLITE_NOMEM error class, and an explicit ROLLBACK after that raises "cannot rollback -
    no transaction is active" — which would replace the caller's real error with plumbing
    noise in every incident report of that class.
    """
    with suppress(sqlite3.OperationalError):
        conn.execute("ROLLBACK")


def log_event(
    conn: sqlite3.Connection, actor: str, action: str, detail: str = "", repo: str = ""
) -> None:
    """Append one audit row. Callers inside `immediate()` get it in the same tx.

    `repo` is ADDITIVE (MULTIREPO-SPEC §3) and defaults to '' so a caller that has no repo
    in hand still writes a row — an event that reaches no feed would be worse than one
    that reaches every feed, which is exactly how '' is read by `/state`.
    """
    conn.execute(
        "INSERT INTO events (ts, actor, action, detail, repo) VALUES (?, ?, ?, ?, ?)",
        (iso(now_s()), actor, action, detail, repo),
    )


def now_s() -> float:
    """Unix seconds UTC (float) — the storage form for `plans.ttl_expires`."""
    return time()


def iso(ts: float) -> str:
    """ISO-8601 UTC with a literal `Z` suffix (§5 timestamp convention)."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
