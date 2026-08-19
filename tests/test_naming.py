"""BUILD-SPEC §4 — canonical qualname convention string helpers."""

from __future__ import annotations

from loom.indexer.naming import (
    NAME_SEP,
    node_ref,
    norm_path,
    prefix_candidates,
    qualname,
    split_ref,
)


def test_name_sep_is_serenas_slash_not_a_dot() -> None:
    # Serena's real NAME_PATH_SEP; the plan's dotted `Class.method` form is dead in loom.
    assert NAME_SEP == "/"


def test_qualname_joins_with_slash() -> None:
    assert qualname(["DocumentParser", "_resolve_ref"]) == "DocumentParser/_resolve_ref"
    assert qualname(["decode_jwt_token"]) == "decode_jwt_token"
    assert qualname(["Outer", "Inner", "method"]) == "Outer/Inner/method"


def test_qualname_of_a_file_level_node_is_empty() -> None:
    assert qualname([]) == ""
    assert qualname(["", ""]) == ""


def test_norm_path_posix_normalizes() -> None:
    assert norm_path("src\\conduit\\core\\security.py") == "src/conduit/core/security.py"
    assert norm_path("src/./conduit//core/security.py") == "src/conduit/core/security.py"
    assert norm_path("README.md") == "README.md"


def test_norm_path_gives_one_unicode_spelling_one_identity() -> None:
    """break3 journey-J1: `café.py` had TWO coordination identities.

    NFD (`cafe` + U+0301) is what a macOS zip, a Finder copy or any non-precomposing tool
    leaves on disk and what the indexer then keyed the graph on; NFC (U+00E9) is what a
    keyboard, a fresh clone and every LLM emit. APFS opens both as one file, so an edit sent
    in the other spelling resolved to nothing — `new_path` — and was ALLOWED over a live
    foreign write claim. `norm_path` is the single place both sides pass through.
    """
    nfc, nfd = "caf\u00e9.py", "cafe\u0301.py"
    assert nfc != nfd                                    # byte-different, same file on disk
    assert norm_path(nfd) == norm_path(nfc) == nfc       # ...and one node key, the NFC one
    assert norm_path("src/\u0041\u030apen/caf\u00e9.py") == norm_path("src/\u00c5pen/cafe\u0301.py")
    assert norm_path("plain/ascii.py") == "plain/ascii.py"   # ASCII is untouched


def test_prefix_candidates_longest_first() -> None:
    assert prefix_candidates("A/b/c") == ["A/b/c", "A/b", "A"]
    assert prefix_candidates("decode_jwt_token") == ["decode_jwt_token"]


def test_prefix_candidates_strips_a_leading_slash() -> None:
    # Serena hands out absolute-looking name paths; the leading '/' is not part of the name.
    assert prefix_candidates("/DocumentParser/_resolve_ref") == [
        "DocumentParser/_resolve_ref",
        "DocumentParser",
    ]
    assert prefix_candidates("/") == []
    assert prefix_candidates("") == []


def test_node_ref_and_split_ref_are_the_ids_definitions() -> None:
    from loom.server import ids

    assert node_ref is ids.node_ref
    assert split_ref is ids.split_ref
    assert node_ref("services/parsing/document_parser.py", "DocumentParser/_resolve_ref") == (
        "services/parsing/document_parser.py::DocumentParser/_resolve_ref"
    )
    assert split_ref("src/conduit/models/document.py") == (
        "src/conduit/models/document.py",
        "",
    )


def test_full_ref_examples_from_the_spec() -> None:
    assert node_ref("src/conduit/core/security.py", "decode_jwt_token") == (
        "src/conduit/core/security.py::decode_jwt_token"
    )
    path, qual = split_ref("services/parsing/document_parser.py::DocumentParser/_resolve_ref")
    assert norm_path(path) == "services/parsing/document_parser.py"
    assert prefix_candidates(qual) == ["DocumentParser/_resolve_ref", "DocumentParser"]
