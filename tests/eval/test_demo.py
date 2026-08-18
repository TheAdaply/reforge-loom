"""The README's headline command must actually run.

`python -m loom.eval.harness --demo` boots a real server on a throwaway db, indexes the
fixture repo, and scripts the declare/deny/gate/release sequence with an inline assert on
every step. It was broken for five commits because nothing ran it; this test is the thing
that would have caught that. It is slow (it starts a subprocess server) and that is the
price of covering the one command a first-time visitor types.
"""

from __future__ import annotations

import subprocess
import sys


def test_demo_runs_green() -> None:
    r = subprocess.run([sys.executable, "-m", "loom.eval.harness", "--demo"],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"demo exited {r.returncode}\n{r.stdout[-4000:]}\n{r.stderr[-4000:]}"
    assert "demo complete" in r.stdout
    # The headline claim: two agents working in one file is the point, so the sequence must
    # end with the conflict resolved and no assert having fired.
    assert "AssertionError" not in r.stderr
