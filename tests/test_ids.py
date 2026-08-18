"""BUILD-SPEC §3 — ID minting. The golden vector is ported verbatim from the spec."""

from __future__ import annotations

import pytest

from loom.server.ids import (
    BASE36,
    LENGTH_TO_BYTES,
    beads_hash_id,
    encode_base36,
    mint_plan_id,
    node_id,
    node_ref,
    split_ref,
)

# §3 golden vector inputs (frozen).
GOLDEN_TS_NS = 1704164645006000000  # 2024-01-02T03:04:05.006Z
GOLDEN = {
    3: "bd-vju",
    4: "bd-8d8e",
    5: "bd-bi3tk",
    6: "bd-8bi3tk",
    7: "bd-r5sr6bm",
    8: "bd-8r5sr6bm",
}


@pytest.mark.parametrize("length", sorted(GOLDEN))
def test_golden_vector_all_six_lengths(length: int) -> None:
    got = beads_hash_id(
        prefix="bd",
        title="Fix login",
        description="Details",
        creator="jira-import",
        ts_ns=GOLDEN_TS_NS,
        length=length,
        nonce=0,
    )
    assert got == GOLDEN[length]


def test_golden_vector_as_a_whole_set() -> None:
    got = {
        length: beads_hash_id("bd", "Fix login", "Details", "jira-import", GOLDEN_TS_NS, length, 0)
        for length in LENGTH_TO_BYTES
    }
    assert got == GOLDEN


def test_length_to_bytes_table_is_frozen() -> None:
    assert LENGTH_TO_BYTES == {3: 2, 4: 3, 5: 4, 6: 4, 7: 5, 8: 5}
    assert BASE36 == "0123456789abcdefghijklmnopqrstuvwxyz"


def test_encode_base36_left_pads_short_values() -> None:
    assert encode_base36(b"\x00", 5) == "00000"
    assert encode_base36(b"\x01", 4) == "0001"
    assert encode_base36((35).to_bytes(1, "big"), 3) == "00z"
    assert encode_base36((36).to_bytes(1, "big"), 3) == "010"


def test_encode_base36_keeps_the_last_chars_when_too_long() -> None:
    data = b"\xff\xff\xff\xff\xff"  # 2**40 - 1, which needs 8 base36 chars
    padded = encode_base36(data, 12)
    assert len(padded) == 12
    assert padded.startswith("0000")
    truncated = encode_base36(data, 3)
    assert len(truncated) == 3
    assert padded.endswith(truncated)  # least-significant chars are the ones kept


def test_node_ref_and_split_ref_round_trip() -> None:
    assert node_ref("src/a.py", "Klass/method") == "src/a.py::Klass/method"
    assert node_ref("README.md") == "README.md"
    assert node_ref("README.md", "") == "README.md"
    assert split_ref("src/a.py::Klass/method") == ("src/a.py", "Klass/method")
    assert split_ref("README.md") == ("README.md", "")
    for path, qual in [("src/a.py", "K/m"), ("README.md", ""), ("a/b/c.py", "f")]:
        assert split_ref(node_ref(path, qual)) == (path, qual)


def test_node_id_is_deterministic_and_shaped() -> None:
    a = node_id("conduit", "src/conduit/core/security.py", "decode_jwt_token")
    b = node_id("conduit", "src/conduit/core/security.py", "decode_jwt_token")
    assert a == b
    assert a.startswith("n-")
    assert len(a) == 10  # "n-" + 8 base36 chars
    assert all(ch in BASE36 for ch in a[2:])


def test_node_id_nul_salt_separates_repo_from_ref() -> None:
    # The NUL separator is the repo salt boundary (beads C7): a '|' or '' joiner would
    # make these two identities collide under concatenation.
    assert node_id("a", "b/c", "") != node_id("a/b", "c", "")


def test_node_id_distinguishes_file_level_from_symbol() -> None:
    assert node_id("r", "src/a.py", "") != node_id("r", "src/a.py", "f")
    assert node_id("r1", "src/a.py", "f") != node_id("r2", "src/a.py", "f")


def _insert_plan(conn, plan_id: str) -> None:
    conn.execute(
        "INSERT INTO plans (id, agent, repo, branch, title, spec_md, status, created, updated,"
        " ttl_expires) VALUES (?, 'aria', 'demo', '', 't', 's', 'active', 'now', 'now', 0.0)",
        (plan_id,),
    )


def test_mint_plan_id_shape_and_collision_walk(conn) -> None:
    minted = mint_plan_id(conn, "harden authenticate", "# Spec", "aria", 1704164645006000000)
    assert minted.startswith("lm-")
    assert len(minted) == 3 + 6
    assert all(ch in BASE36 for ch in minted[3:])

    # Occupy the first candidate: the next mint must walk to a different id.
    _insert_plan(conn, minted)
    second = mint_plan_id(conn, "harden authenticate", "# Spec", "aria", 1704164645006000000)
    assert second != minted
    assert second.startswith("lm-")


def test_mint_plan_id_raises_when_the_space_is_exhausted(conn) -> None:
    ts_ns = 1704164645006000000
    for length in (6, 7, 8):
        for nonce in range(10):
            _insert_plan(conn, beads_hash_id("lm", "dup", "spec", "aria", ts_ns, length, nonce))
    with pytest.raises(RuntimeError):
        mint_plan_id(conn, "dup", "spec", "aria", ts_ns)
