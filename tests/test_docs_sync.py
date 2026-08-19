"""Documentation claims that a reader can falsify in one command must not go stale.

The README shipped "298 tests" against 316, and "Nine checks" against ten, for two
iterations. Both are numbers a human maintains by hand, and neither had anything watching
it. These tests watch the ones that are cheap to watch; the rest of the fix was to stop
writing numbers into prose that a command already answers.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from loom.cli import main as cli_main
from loom.server import claims, tools

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_doctor_prints_the_number_of_checks_the_docs_claim() -> None:
    """`cmd_doctor` calls `row(...)` once per check. The README and its own docstring both
    say how many there are, in words."""
    body = inspect.getsource(cli_main.cmd_doctor)
    calls = body.count("\n    row(")
    assert calls == 10, f"cmd_doctor now prints {calls} rows"
    assert "ten checks" in body.lower()
    assert "Ten checks" in _read("README.md")
    assert "prints ten rows" in _read("docs/troubleshooting.md")


def test_the_readme_hardcodes_no_test_count() -> None:
    """The count changes on every pass that adds a test; prose cannot track it."""
    readme = _read("README.md")
    assert "tests" in readme
    for stale in ("298 tests", "316 tests", "319 tests"):
        assert stale not in readme


def test_every_mcp_tool_is_named_in_the_protocol_reference() -> None:
    """`docs/protocol.md` is the only current reference for the tool surface, so a tool
    added without a paragraph there is invisible to the agents that must call it."""
    names = [line.split("def ")[1].split("(")[0]
             for line in inspect.getsource(tools.register).splitlines()
             if line.strip().startswith("def ") and "    def " in line]
    # `pick`/`unknown` are the shared repo-resolution helpers; an underscore prefix marks
    # any other nested non-tool helper (e.g. list_claims' busy-tolerant `_sweep`).
    names = [n for n in names if n not in ("pick", "unknown") and not n.startswith("_")]
    assert len(names) == 9, f"the tool surface changed: {names}"
    documented = _read("docs/protocol.md")
    missing = [n for n in names if f"`{n}(" not in documented]
    assert not missing, f"tools missing from docs/protocol.md: {missing}"


def test_every_gate_case_is_named_in_the_protocol_reference() -> None:
    documented = _read("docs/protocol.md")
    for case in ("in_plan", "foreign_claim", "out_of_scope", "no_plan", "new_path", "unindexed"):
        assert f"`{case}`" in documented, f"case {case} is undocumented"


def test_every_placeholder_stem_is_in_the_shipped_template() -> None:
    """A stem the template does not contain validates nothing — it is dead weight that
    reads like a rule."""
    template = _read("src/loom/templates/spec.md")
    missing = [s for s in claims._PLACEHOLDERS if s not in template]
    assert not missing, f"placeholder stems absent from templates/spec.md: {missing}"
