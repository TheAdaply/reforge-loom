"""M3 — locator rules (§7.2) and the §4 granularity/block-statement laws, in-process."""

from __future__ import annotations

import os
from pathlib import Path

from loom.hook.locator import (
    UNSCOPED_TMPL,
    collect_symbol_spans,
    enclosing_qualname,
    locate,
)

SRC = '''"""demo."""

import os
from typing import TYPE_CHECKING


class Outer:
    if TYPE_CHECKING:

        def guarded(self) -> None:
            hidden = 1

    def method(self, x):
        def inner():
            return os.urandom(x)

        return inner()


def twin():
    return 1


def twin():
    return 2


if os.environ:

    def blocked():
        return 3
'''


def _write(tmp_path: Path, source: str = SRC) -> str:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "m.py").write_text(source, encoding="utf-8")
    return str(root)


def _edit(root: str, old: str, **extra) -> object:
    return locate("Edit", {"file_path": root + "/src/m.py", "old_string": old, **extra}, root)


def test_spans_follow_the_granularity_and_duplicate_rules() -> None:
    names = [q for q, _, _ in collect_symbol_spans(SRC)]
    assert names == ["Outer", "Outer/method", "twin", "twin[1]"]
    # Never minted: a closure, a def under a block statement, a guarded method (§4).
    assert not {"Outer/method/inner", "blocked", "Outer/guarded"} & set(names)


def test_syntax_error_yields_no_spans() -> None:
    assert collect_symbol_spans("def broken(:\n    pass\n") == []
    assert enclosing_qualname("def broken(:\n", 1, 1) is None


def test_edit_inside_a_method_gates_that_method(tmp_path: Path) -> None:
    loc = _edit(_write(tmp_path), "        return inner()")
    assert (loc.action, loc.path, loc.qualname) == ("gate", "src/m.py", "Outer/method")


def test_edit_inside_a_closure_rolls_up_to_the_enclosing_function(tmp_path: Path) -> None:
    loc = _edit(_write(tmp_path), "            return os.urandom(x)")
    assert loc.qualname == "Outer/method"


def test_edit_inside_a_guarded_method_falls_to_the_class(tmp_path: Path) -> None:
    loc = _edit(_write(tmp_path), "            hidden = 1")
    assert loc.qualname == "Outer"


def test_edit_inside_a_block_scoped_def_falls_to_file_level(tmp_path: Path) -> None:
    loc = _edit(_write(tmp_path), "        return 3")
    assert (loc.action, loc.qualname) == ("gate", None)


def test_ambiguous_replace_all_falls_to_file_level(tmp_path: Path) -> None:
    root = _write(tmp_path)
    assert _edit(root, "    return", replace_all=True).qualname is None
    # A unique old_string still locates its span; the twin suffix is the [i] counter.
    assert _edit(root, "    return 1").qualname == "twin"
    assert _edit(root, "    return 2").qualname == "twin[1]"


def test_absent_old_string_and_unparseable_file_fall_to_file_level(tmp_path: Path) -> None:
    root = _write(tmp_path)
    assert _edit(root, "nowhere in this file").qualname is None
    broken = _write(tmp_path / "b", "class Broken(:\n")
    assert _edit(broken, "class Broken(:").qualname is None


def test_camel_case_input_keys_are_honored(tmp_path: Path) -> None:
    root = _write(tmp_path)
    loc = locate("Edit", {"filePath": root + "/src/m.py", "oldString": "        return inner()"}, root)
    assert loc.qualname == "Outer/method"


def test_write_notebook_and_serena_file_tools_are_file_level(tmp_path: Path) -> None:
    root = _write(tmp_path)
    w = locate("Write", {"file_path": root + "/src/new.py"}, root)
    assert (w.action, w.path, w.qualname) == ("gate", "src/new.py", None)
    nb = locate("NotebookEdit", {"notebook_path": root + "/nb.ipynb"}, root)
    assert (nb.path, nb.qualname) == ("nb.ipynb", None)
    sf = locate("mcp__serena__create_text_file", {"relative_path": "src/new.py"}, root)
    assert (sf.action, sf.path, sf.qualname) == ("gate", "src/new.py", None)


def test_serena_symbol_tools_map_name_path_and_the_pattern_spelling(tmp_path: Path) -> None:
    root = _write(tmp_path)
    a = locate("mcp__serena__replace_symbol_body",
               {"relative_path": "src/m.py", "name_path": "/Outer/method"}, root)
    b = locate("mcp__any-key__safe_delete_symbol",
               {"relative_path": "src/m.py", "name_path_pattern": "Outer/method"}, root)
    assert (a.action, a.qualname) == ("gate", "Outer/method")
    assert (b.action, b.qualname) == ("gate", "Outer/method")


def test_replace_in_files_scoping(tmp_path: Path) -> None:
    root = _write(tmp_path)
    dry = locate("mcp__serena__replace_in_files", {"relative_path": "", "dry_run": True}, root)
    one = locate("mcp__serena__replace_in_files", {"relative_path": "src/m.py"}, root)
    wide = locate("mcp__serena__replace_in_files", {"relative_path": "src"}, root)
    assert dry.action == "pass"
    assert (one.action, one.path, one.qualname) == ("gate", "src/m.py", None)
    assert (wide.action, wide.message) == ("deny_local", UNSCOPED_TMPL)


def test_symlinked_repo_spelling_gates_identically(tmp_path: Path) -> None:
    """gate-F1: one file, one identity — a symlink-spelled repo must NOT slip through unchecked.

    `tmp_path` is already realpath'd on macOS, so the symlink has to be built explicitly:
    that is structurally why the rest of this suite cannot see the bug.
    """
    real = _write(tmp_path)  # <tmp>/repo, canonical spelling
    link = str(tmp_path / "repo_link")
    os.symlink(real, link)
    old = "        return inner()"
    # D1: repo_root recorded through the symlink, the tool sends the resolved real path.
    d1 = locate("Edit", {"file_path": real + "/src/m.py", "old_string": old}, link)
    # D2: repo_root recorded as the real path, the tool sends the symlinked spelling.
    d2 = locate("Edit", {"file_path": link + "/src/m.py", "old_string": old}, real)
    for loc in (d1, d2):
        assert (loc.action, loc.path, loc.qualname) == ("gate", "src/m.py", "Outer/method")


def test_dot_dot_alias_is_normalized_before_the_wire(tmp_path: Path) -> None:
    """gate-F2: an in-repo `..` round trip must reach the wire as the indexed spelling."""
    root = _write(tmp_path)
    loc = locate("Edit", {"file_path": root + "/src/../src/m.py",
                          "old_string": "        return inner()"}, root)
    assert (loc.action, loc.path, loc.qualname) == ("gate", "src/m.py", "Outer/method")


def test_dot_dot_escape_out_of_the_repo_passes(tmp_path: Path) -> None:
    """gate-F3 (§7.2): a file genuinely outside repo_root PASSes — never a hard local deny."""
    root = _write(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("def gone():\n    return 1\n", encoding="utf-8")
    escaped = locate("Edit", {"file_path": root + "/../outside.py",
                              "old_string": "def gone():"}, root)
    assert escaped.action == "pass"
    # Same file, spelled absolutely: identical verdict, no server round trip either way.
    assert locate("Edit", {"file_path": str(outside), "old_string": "def gone():"},
                  root).action == "pass"


def test_unknown_tools_and_out_of_repo_paths_pass(tmp_path: Path) -> None:
    root = _write(tmp_path)
    assert locate("Bash", {"command": "ls"}, root).action == "pass"
    assert locate("Edit", {"file_path": "/etc/hosts", "old_string": "x"}, root).action == "pass"
    assert locate("Edit", {}, root).action == "pass"
    assert locate("mcp__serena__rename_symbol", {"relative_path": "src/m.py"}, root).action == "pass"
