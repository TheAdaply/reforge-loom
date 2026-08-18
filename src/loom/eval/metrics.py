"""M4 — BUILD-SPEC §9.1 `eval/metrics.py`, the papers §2.5 computation plan verbatim.

Work unit is a git *hunk* (`git diff --unified=0`). Three buckets, disjoint by construction:

  duplicated  two hunks on the same file whose normalized ADDED lines have Jaccard >= 0.8
              and which are matched one-to-one (greedy maximum-weight). Charged
              ``min(a.lines, b.lines)`` — only the redundant copy, mirroring grite counting
              the *extra* completion rather than the whole task.
  conflicting hunks left over after the duplicate bucket whose BASE-side ranges overlap,
              grouped into connected components (interval clusters) and charged
              ``max(A-side lines, B-side lines)`` per cluster. Per-cluster (not per-pair)
              charging is required: per-pair double-counts a hunk overlapping several
              opposing hunks and can push the composite above 1.
  useful      everything else.

``wasted_work_share = (dup_lines + conflict_lines) / total_lines`` is loom's own composite,
built after grite's separate components (§11.21) — grite's absolute numbers are simulated
N=32 and are never quoted as loom expectations.

`compute_metrics` is the pure boundary: it takes parsed hunks plus git's conflicted-file
verdict, never a repo, so the whole scoring rule is unit-testable without agents.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Jaccard at which two same-file hunks are called the same work (papers §2.5 Step 2).
# A run whose matched pairs are all `import` lines is a false positive, so every match is
# logged in `dup_pairs` for a human to eyeball; the appendix sweeps J in {0.7, 0.8, 0.9}.
DUP_JACCARD = 0.8

# `git merge-tree --write-tree` (§1 pin). Older git falls back to a scratch worktree merge.
MERGE_TREE_MIN = (2, 38)

# conduit-verify §2.2: four deterministic pre-existing failures on the eval target's main.
# Without deselecting them the `post_merge_test_failures` headline is wrong by +4 in EVERY
# arm. Ships as code + config; the real three-arm conduit runs are post-MVP (§9.3 M4).
BASELINE_DESELECT: tuple[str, ...] = (
    "tests/unit/test_config_pipeline.py::TestGenerateConfigurationPipeline"
    "::test_llm_path_with_augmentation",
    "tests/unit/test_document_upload_security.py::TestPathTraversalPrevention"
    "::test_path_traversal_filename_stripped",
    "tests/unit/test_production_hardening.py::TestGeminiClientLifecycle"
    "::test_get_llm_client_returns_singleton",
    "tests/unit/test_production_hardening.py::TestGeminiClientLifecycle"
    "::test_lifespan_closes_shared_client",
)
# `--no-cov`: the target forces `--cov` with `fail_under = 40` in addopts, so a plain run
# fails the coverage gate whenever an agent's edits drop coverage (conduit-verify §2.2).
PYTEST_BASE_ARGS: tuple[str, ...] = ("-q", "-p", "no:cacheprovider", "--no-cov")

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_COMMENT_RE = re.compile(r"^\s*#")
_SPACE_RUN_RE = re.compile(r" {2,}")


@dataclass(frozen=True)
class Hunk:
    """One contiguous change region, with its range on BOTH sides of the diff.

    Base-side ranges are what overlap detection uses: two agents editing the same original
    region collide there even when their new text is unrelated.
    """

    path: str
    base_start: int
    base_len: int
    new_start: int
    new_len: int
    added: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()

    @property
    def lines(self) -> int:
        """Primary weight: hunk boundaries are noisy, line counts are not."""
        return len(self.added) + len(self.deleted)

    @property
    def base_end(self) -> int:
        """Exclusive end of the base-side range; a zero-length hunk still occupies a point."""
        return self.base_start + max(self.base_len, 1)


def normalize(line: str) -> str | None:
    """Kill formatting-only false negatives; None = the line is not comparable work.

    Strip trailing whitespace, drop blank and comment-only lines, collapse internal runs of
    spaces. Deliberately does NOT lowercase and does NOT strip leading indentation —
    indentation is semantic in Python, so it stays in the compared text.
    """
    stripped = line.rstrip()
    if not stripped.strip() or _COMMENT_RE.match(stripped):
        return None
    body = stripped.lstrip(" ")
    indent = stripped[: len(stripped) - len(body)]
    return indent + _SPACE_RUN_RE.sub(" ", body)


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=120)


def git_version(repo: str = ".") -> tuple[int, ...]:
    """(major, minor) of the local git, for the `merge-tree --write-tree` capability check."""
    out = _git(repo, "--version").stdout.strip()
    nums = re.findall(r"\d+", out)
    return tuple(int(n) for n in nums[:2]) or (0, 0)


def parse_hunks(repo: str, base: str, branch: str) -> list[Hunk]:
    """`git diff --unified=0 <base> <branch>` -> normalized hunks (papers §2.5, work unit).

    `--unified=0` is what makes a hunk a *contiguous change region* rather than a region
    plus three lines of unrelated context that would smear overlap detection.
    """
    proc = _git(repo, "diff", "--unified=0", "--no-color", base, branch, "--")
    if proc.returncode != 0:
        raise RuntimeError(f"git diff {base}..{branch} failed ({proc.returncode}): {proc.stderr}")
    hunks: list[Hunk] = []
    path, old_path, pending, added, deleted = "", "", None, [], []

    def flush() -> None:
        if pending is not None:
            hunks.append(Hunk(path, *pending, tuple(added), tuple(deleted)))

    for line in proc.stdout.splitlines():
        # `diff --git` is the per-file boundary. Flushing HERE (not on `+++`) is what keeps
        # the `---`/`+++` headers of the next file out of the previous file's deleted lines.
        if line.startswith("diff --git "):
            flush()
            path, old_path, pending, added, deleted = "", "", None, [], []
        elif line.startswith("--- "):
            old = line[4:].strip()
            old_path = "" if old == "/dev/null" else re.sub(r"^a/", "", old)
        elif line.startswith("+++ "):
            new = line[4:].strip()
            # A deleted file has no b/ side: name it by the old side so the work still counts.
            path = old_path if new == "/dev/null" else re.sub(r"^b/", "", new)
        elif (m := _HUNK_RE.match(line)) is not None:
            flush()
            added, deleted = [], []
            pending = (int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1))
        elif pending is not None and line[:1] in ("+", "-"):
            norm = normalize(line[1:])
            if norm is not None:
                (added if line[0] == "+" else deleted).append(norm)
    flush()
    return hunks


def merge_conflict_files(repo: str, a: str, b: str) -> list[str]:
    """Git's authoritative conflict verdict; no working tree is dirtied.

    Exit status is a three-way verdict, not a count: **0 = clean, 1 = conflicts, >=2 = error**
    (papers C5.7). Treating >=2 as "some conflicts" would silently score a broken invocation
    as a bad arm, so it raises.
    """
    if git_version(repo) < MERGE_TREE_MIN:
        return _merge_conflict_files_worktree(repo, a, b)
    proc = _git(repo, "merge-tree", "--write-tree", "--name-only", a, b)
    if proc.returncode >= 2:
        raise RuntimeError(f"git merge-tree failed ({proc.returncode}): {proc.stderr.strip()}")
    if proc.returncode == 0:
        return []
    # stdout: <tree-oid>, then the conflicted paths, then a blank line + informational text.
    names: list[str] = []
    for line in proc.stdout.splitlines()[1:]:
        if not line.strip():
            break
        names.append(line.strip())
    return names


def _merge_conflict_files_worktree(repo: str, a: str, b: str) -> list[str]:
    """git < 2.38 fallback (§1): merge in a scratch worktree, then always `--abort`.

    Ships as code and is not exercised in CI — every supported dev box is well past 2.38.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        wt = f"{tmp}/wt"
        if _git(repo, "worktree", "add", "--detach", wt, a).returncode != 0:
            raise RuntimeError("scratch worktree could not be created")
        try:
            _git(wt, "merge", "--no-commit", "--no-ff", b)
            out = _git(wt, "diff", "--diff-filter=U", "--name-only")
            _git(wt, "merge", "--abort")
            return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        finally:
            _git(repo, "worktree", "remove", "--force", wt)


def _jaccard(a: Hunk, b: Hunk) -> float:
    """Similarity over normalized ADDED lines; both-empty falls back to DELETED (pure deletes)."""
    sa, sb = set(a.added), set(b.added)
    if not sa and not sb:
        sa, sb = set(a.deleted), set(b.deleted)
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _match_duplicates(hunks_a: list[Hunk], hunks_b: list[Hunk]) -> list[dict]:
    """Greedy maximum-weight ONE-TO-ONE matching over same-file pairs with J >= DUP_JACCARD.

    One-to-one is the invariant that matters: without it a single hunk duplicated by two
    opposing hunks would be charged twice and the composite could exceed 1.
    """
    cands = []
    for i, a in enumerate(hunks_a):
        for j, b in enumerate(hunks_b):
            if a.path == b.path and (j_score := _jaccard(a, b)) >= DUP_JACCARD:
                cands.append((j_score, i, j))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))          # weight desc, then deterministic
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs = []
    for score, i, j in cands:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append({"path": hunks_a[i].path, "a_index": i, "b_index": j,
                      "jaccard": round(score, 4),
                      "charged_lines": min(hunks_a[i].lines, hunks_b[j].lines)})
    return pairs


def _overlap_clusters(rest_a: list[tuple[int, Hunk]],
                      rest_b: list[tuple[int, Hunk]]) -> tuple[int, list[dict]]:
    """Base-range interval clusters over the hunks the duplicate bucket did not claim.

    Charges `max(A-side, B-side)` per connected component: we do not know which side loses
    the merge, so we charge the larger — a deliberate, documented upper bound.
    """
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    touched: set[tuple[str, int]] = set()
    for i, a in rest_a:
        for j, b in rest_b:
            if a.path != b.path:
                continue
            if a.base_start < b.base_end and b.base_start < a.base_end:
                union(("a", i), ("b", j))
                touched |= {("a", i), ("b", j)}
    if not touched:
        return 0, []

    by_a = dict(rest_a)
    by_b = dict(rest_b)
    groups: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for key in sorted(touched):
        groups.setdefault(find(key), []).append(key)

    total, detail = 0, []
    for members in groups.values():
        a_lines = sum(by_a[i].lines for side, i in members if side == "a")
        b_lines = sum(by_b[i].lines for side, i in members if side == "b")
        cost = max(a_lines, b_lines)
        total += cost
        detail.append({"path": by_a[members[0][1]].path if members[0][0] == "a"
                       else by_b[members[0][1]].path,
                       "a_hunks": sorted(i for s, i in members if s == "a"),
                       "b_hunks": sorted(i for s, i in members if s == "b"),
                       "a_lines": a_lines, "b_lines": b_lines, "charged_lines": cost})
    return total, detail


def compute_metrics(hunks_a: list[Hunk], hunks_b: list[Hunk],
                    merge_conflicts: list[str] | None = None) -> dict:
    """The pure scoring boundary: parsed hunks + git's verdict -> one §2.5 metrics row.

    Bucket law (papers §2.5 Step 3): every hunk is charged AT MOST ONCE and **duplicate beats
    conflict** — matched hunks are removed from both sides before overlap runs. That is what
    makes `dup_lines + conflict_lines <= total_lines` true, and both invariants are asserted.
    """
    conflicts = list(merge_conflicts or [])
    total_lines = sum(h.lines for h in hunks_a) + sum(h.lines for h in hunks_b)
    total_hunks = len(hunks_a) + len(hunks_b)

    dup_pairs = _match_duplicates(hunks_a, hunks_b)
    dup_lines = sum(p["charged_lines"] for p in dup_pairs)
    matched_a = {p["a_index"] for p in dup_pairs}
    matched_b = {p["b_index"] for p in dup_pairs}
    rest_a = [(i, h) for i, h in enumerate(hunks_a) if i not in matched_a]
    rest_b = [(j, h) for j, h in enumerate(hunks_b) if j not in matched_b]
    conflict_lines, clusters = _overlap_clusters(rest_a, rest_b)

    files_a = {h.path for h in hunks_a}
    files_b = {h.path for h in hunks_b}
    row = {
        "total_lines": total_lines, "total_hunks": total_hunks,
        "files_touched_a": len(files_a), "files_touched_b": len(files_b),
        "files_shared": len(files_a & files_b),
        "dup_lines": dup_lines,
        "duplicate_work_rate": (dup_lines / total_lines) if total_lines else None,
        "dup_pairs": dup_pairs,
        "merge_conflict_files": len(conflicts),
        # v2 refines to per-hunk counts by reading the conflicted stage blobs and counting
        # `<<<<<<<` marker regions; MVP reports the file list only (papers §2.5 Step 3).
        "merge_conflict_hunks": None,
        "conflicted_paths": conflicts,
        "overlap_lines": conflict_lines, "conflict_lines": conflict_lines,
        "overlap_clusters": clusters,
        # An arm where both agents produced nothing is not a perfect arm: None, never 0.0.
        "wasted_work_share": ((dup_lines + conflict_lines) / total_lines) if total_lines else None,
        "useful_lines": total_lines - dup_lines - conflict_lines,
    }
    if total_lines:
        assert dup_lines + conflict_lines <= total_lines, row
        assert 0.0 <= row["wasted_work_share"] <= 1.0, row
    return row


def assert_baseline_green(worktree: str) -> None:
    """Pre-flight: the target's suite must be green MINUS the frozen quarantine list.

    PLAN §6's headline is `post_merge_test_failures`; it assumes a green baseline and the eval
    target's main is 1039 passed / 4 failed. Asserting here means a red arm is the agents'
    doing, never the repo's. Ships as code; the real conduit runs are post-MVP (§9.3 M4).
    """
    deselect = [arg for nodeid in BASELINE_DESELECT for arg in ("--deselect", nodeid)]
    proc = subprocess.run(["uv", "run", "--directory", worktree, "pytest",
                           *PYTEST_BASE_ARGS, *deselect],
                          capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
        raise AssertionError(f"baseline is NOT green in {worktree} "
                             f"(pytest exit {proc.returncode}):\n{tail}")
