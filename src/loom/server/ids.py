"""Frozen artifact — BUILD-SPEC §3. ID minting for loom.

Portions derived from beads (https://github.com/steveyegge/beads), MIT License,
Copyright (c) 2025 Beads Contributors. See THIRD_PARTY_NOTICES.md.

stdlib only: this module is imported transitively by the PreToolUse hook budget path.
"""

from __future__ import annotations

import hashlib
import sqlite3

BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"

# beads table, incl. the 6/8 reuse (§3).
LENGTH_TO_BYTES = {3: 2, 4: 3, 5: 4, 6: 4, 7: 5, 8: 5}


def encode_base36(data: bytes, length: int) -> str:
    """Beads' EncodeBase36, ported exactly (beads §2.1.1).

    int.from_bytes(data, 'big') -> repeated divmod(n, 36) collecting BASE36[r] -> reverse;
    left-pad with '0' to `length`; if longer, keep the LAST `length` chars
    (least-significant).
    """
    n = int.from_bytes(data, "big")
    out: list[str] = []
    while n > 0:
        n, r = divmod(n, 36)
        out.append(BASE36[r])
    s = "".join(reversed(out))
    if len(s) < length:
        s = "0" * (length - len(s)) + s
    elif len(s) > length:
        s = s[-length:]
    return s


def beads_hash_id(
    prefix: str,
    title: str,
    description: str,
    creator: str,
    ts_ns: int,
    length: int,
    nonce: int,
) -> str:
    """content = f"{title}|{description}|{creator}|{ts_ns}|{nonce}", UTF-8, sha256;
    take digest[:LENGTH_TO_BYTES[length]]; encode_base36(_, length);
    return f"{prefix}-{short}".
    """
    content = f"{title}|{description}|{creator}|{ts_ns}|{nonce}"
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    short = encode_base36(digest[: LENGTH_TO_BYTES[length]], length)
    return f"{prefix}-{short}"


def node_ref(path: str, qualname: str = "") -> str:
    """'path::qualname' when qualname else 'path'. The display/agent-input form."""
    return f"{path}::{qualname}" if qualname else path


def split_ref(ref: str) -> tuple[str, str]:
    """Inverse of node_ref: rsplit('::', 1); ('path', '') when no '::'."""
    if "::" in ref:
        path, qual = ref.rsplit("::", 1)
        return path, qual
    return ref, ""


def node_id(repo: str, path: str, qualname: str = "") -> str:
    """DETERMINISTIC, content-addressed, NO timestamp/nonce (beads ADAPT 1).

    'n-' + encode_base36(sha256((repo + "\\x00" + node_ref(path, qualname)).encode())
    .digest()[:5], 8).  NUL separator = the repo salt boundary (beads C7: '|' is
    ambiguous under concatenation).  Length 8 => 36^8 ~ 2.8e12; birthday p~0.25 at
    ~1.2M nodes.  No collision loop for nodes: the UNIQUE(repo, path, qualname)
    constraint raises on a true hash collision, which we WANT to see.
    """
    payload = (repo + "\x00" + node_ref(path, qualname)).encode("utf-8")
    return "n-" + encode_base36(hashlib.sha256(payload).digest()[:5], 8)


def mint_plan_id(
    conn: sqlite3.Connection, title: str, spec_md: str, agent: str, now_ns: int
) -> str:
    """ENTROPIC (beads recipe kept whole): inside the caller's BEGIN IMMEDIATE tx.

    Nanosecond trap (beads §2.1.2): callers compute `now_ns` with `time.time_ns()`
    or integer arithmetic — NEVER `int(ts.timestamp() * 1e9)`.
    """
    for length in (6, 7, 8):
        for nonce in range(10):
            cand = beads_hash_id("lm", title, spec_md, agent, now_ns, length, nonce)
            if not conn.execute(
                "SELECT 1 FROM plans WHERE id=?", (cand,)
            ).fetchone():
                return cand
    raise RuntimeError("mint_plan_id: exhausted id space for this plan content")
