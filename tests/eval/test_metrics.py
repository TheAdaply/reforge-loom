"""M4 — the pure-function cases from BUILD-SPEC §9.3 / papers §2.5.

`compute_metrics` takes parsed hunks, not a repo, so the entire scoring rule is testable
without agents, without git, and without a server. These tests pin the FORMULA (charge-min
on duplicates, charge-max-per-cluster on overlaps, disjoint buckets, both invariants) —
they are the specification of loom's headline number in executable form.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from loom.eval.metrics import (
    DUP_JACCARD,
    MERGE_TREE_MIN,
    Hunk,
    compute_metrics,
    git_version,
    merge_conflict_files,
    normalize,
    parse_hunks,
)


def hunk(path: str, base_start: int, added: list[str], deleted: list[str] | None = None,
         base_len: int | None = None) -> Hunk:
    """Synthetic hunk whose base-side length defaults to its deleted-line count."""
    deleted = deleted or []
    return Hunk(path=path, base_start=base_start,
                base_len=len(deleted) if base_len is None else base_len,
                new_start=base_start, new_len=len(added),
                added=tuple(added), deleted=tuple(deleted))


BODY = ["def authenticate(self, email):", "    token = make_token(email)", "    return token"]


# ---------------------------------------------------------------- duplicated work

def test_identical_hunks_are_matched_and_charged_the_redundant_copy_only() -> None:
    """Identical work on both sides: J == 1.0, and the charge is min(a, b) — ONE copy.

    NOTE (reported as a spec contradiction): §9.3's test list says "identical hunks -> rate
    1.0", but the frozen formula it shares a document with — §9.1 "dup charges min(a,b)",
    papers §2.5 "charge only the redundant copy, not both sides" — caps the rate at exactly
    0.5 for identical single hunks (L / 2L). Two exact statements beat one loose summary, so
    the formula wins and this test pins what the formula actually yields.
    """
    a = [hunk("svc.py", 10, BODY)]
    b = [hunk("svc.py", 10, BODY)]
    m = compute_metrics(a, b, [])

    assert m["total_lines"] == 6 and m["total_hunks"] == 2
    assert m["dup_lines"] == 3
    assert m["duplicate_work_rate"] == 0.5
    assert m["conflict_lines"] == 0, "duplicate beats conflict; the pair is charged once"
    assert m["wasted_work_share"] == 0.5
    assert m["useful_lines"] == 3
    assert len(m["dup_pairs"]) == 1
    assert m["dup_pairs"][0]["jaccard"] == 1.0
    assert m["dup_pairs"][0]["charged_lines"] == 3


def test_near_identical_hunks_above_the_threshold_still_count_as_duplicate() -> None:
    """One line differing out of five keeps J = 4/6 ... below 0.8; four of five clears it."""
    a = [hunk("svc.py", 1, ["x = 1", "y = 2", "z = 3", "w = 4"])]
    b = [hunk("svc.py", 1, ["x = 1", "y = 2", "z = 3", "w = 5"])]
    j = 3 / 5                                    # |int| = 3, |union| = 5
    assert j < DUP_JACCARD
    assert compute_metrics(a, b, [])["dup_lines"] == 0

    b2 = [hunk("svc.py", 1, ["x = 1", "y = 2", "z = 3", "w = 4", "v = 5"])]
    assert 4 / 5 >= DUP_JACCARD
    assert compute_metrics(a, b2, [])["dup_lines"] == 4


def test_formatting_only_differences_do_not_break_the_match() -> None:
    """Normalization is what stops a reformat from reading as independent work."""
    a = [hunk("svc.py", 1, [normalize(x) or x for x in BODY])]
    b = [hunk("svc.py", 1, [normalize(x) or x for x in
                            ["def  authenticate(self,   email):   ",
                             "    token =  make_token(email)",
                             "    return token"]])]
    m = compute_metrics(a, b, [])
    assert m["dup_pairs"] and m["dup_pairs"][0]["jaccard"] == 1.0


def test_pure_deletions_fall_back_to_the_deleted_line_jaccard() -> None:
    a = [hunk("svc.py", 5, [], ["old_call()", "legacy = True"])]
    b = [hunk("svc.py", 5, [], ["old_call()", "legacy = True"])]
    m = compute_metrics(a, b, [])
    assert m["dup_lines"] == 2 and m["conflict_lines"] == 0


# ---------------------------------------------------------------- disjoint work

def test_disjoint_files_waste_nothing() -> None:
    a = [hunk("svc.py", 10, ["a = 1", "b = 2"])]
    b = [hunk("models.py", 10, ["c = 3", "d = 4"])]
    m = compute_metrics(a, b, [])

    assert m["total_lines"] == 4
    assert m["dup_lines"] == 0 and m["conflict_lines"] == 0
    assert m["duplicate_work_rate"] == 0.0
    assert m["wasted_work_share"] == 0.0
    assert m["useful_lines"] == 4
    assert m["files_shared"] == 0


def test_same_file_but_non_overlapping_base_ranges_waste_nothing() -> None:
    """The whole point of loom: two agents in one file, in different regions, is FINE."""
    a = [hunk("svc.py", 10, ["a = 1"], ["A"], base_len=1)]
    b = [hunk("svc.py", 90, ["b = 2"], ["B"], base_len=1)]
    m = compute_metrics(a, b, [])
    assert m["files_shared"] == 1
    assert m["wasted_work_share"] == 0.0


# ---------------------------------------------------------------- conflicting work

def test_overlapping_but_different_hunks_are_conflict_only_never_duplicate() -> None:
    a = [hunk("svc.py", 10, ["cache = TTLCache(60)", "return cache.get(k)"],
              ["return compute(k)"], base_len=5)]
    b = [hunk("svc.py", 12, ["audit.log(k)", "return compute(k, trace=True)"],
              ["return compute(k)"], base_len=5)]
    m = compute_metrics(a, b, [])

    assert m["dup_lines"] == 0, "different added lines: not duplicate work"
    assert m["conflict_lines"] == max(3, 3) == 3
    assert m["wasted_work_share"] == 0.5
    assert len(m["overlap_clusters"]) == 1
    assert m["overlap_clusters"][0]["charged_lines"] == 3


def test_a_cluster_is_charged_max_side_once_not_per_pair() -> None:
    """Per-pair charging would count the A hunk twice and could exceed 1.0; clusters do not."""
    a = [hunk("svc.py", 10, ["a1", "a2", "a3", "a4"], base_len=20)]
    b = [hunk("svc.py", 11, ["b1", "b2"], base_len=2),
         hunk("svc.py", 20, ["b3", "b4"], base_len=2)]
    m = compute_metrics(a, b, [])

    assert len(m["overlap_clusters"]) == 1, "both B hunks land in the A hunk's cluster"
    cluster = m["overlap_clusters"][0]
    assert cluster["a_lines"] == 4 and cluster["b_lines"] == 4
    assert m["conflict_lines"] == 4, "max(4, 4), charged once — not 4 + 4"
    assert m["wasted_work_share"] == 0.5


def test_merge_conflict_files_are_reported_but_do_not_change_the_line_math() -> None:
    """git's verdict is reported alongside overlap; overlap is computable even on a clean merge."""
    a = [hunk("svc.py", 10, ["a = 1"], base_len=1)]
    b = [hunk("models.py", 10, ["b = 2"], base_len=1)]
    m = compute_metrics(a, b, ["svc.py", "models.py"])

    assert m["merge_conflict_files"] == 2
    assert m["conflicted_paths"] == ["svc.py", "models.py"]
    assert m["merge_conflict_hunks"] is None, "per-hunk counts are v2 (papers §2.5 Step 3)"
    assert m["conflict_lines"] == 0, "the line math is base-range overlap, independent of git"


# ---------------------------------------------------------------- matching invariants

def test_one_hunk_duplicated_by_two_opposing_hunks_stays_one_to_one() -> None:
    """The A hunk may be charged as duplicate ONCE; the loser falls through to conflict."""
    a = [hunk("svc.py", 10, BODY, base_len=3)]
    b = [hunk("svc.py", 10, BODY, base_len=3),
         hunk("svc.py", 11, BODY, base_len=3)]
    m = compute_metrics(a, b, [])

    assert len(m["dup_pairs"]) == 1, "one-to-one matching: A cannot be charged twice"
    assert m["dup_pairs"][0]["b_index"] == 0, "ties break deterministically on index"
    assert m["dup_lines"] == 3
    # The unmatched B hunk has no surviving A hunk to overlap with, so nothing is charged twice.
    assert m["conflict_lines"] == 0
    assert m["total_lines"] == 9
    assert m["useful_lines"] == 6


def test_the_greedy_matching_prefers_the_higher_jaccard_pair() -> None:
    a = [hunk("svc.py", 1, ["p", "q", "r", "s"])]
    b = [hunk("svc.py", 1, ["p", "q", "r", "zzz"]),          # J = 3/5 — below threshold
         hunk("svc.py", 1, ["p", "q", "r", "s"])]            # J = 1.0
    m = compute_metrics(a, b, [])
    assert len(m["dup_pairs"]) == 1
    assert m["dup_pairs"][0]["b_index"] == 1 and m["dup_pairs"][0]["jaccard"] == 1.0


@pytest.mark.parametrize("a_hunks,b_hunks", [
    ([hunk("svc.py", 1, BODY, base_len=3)], [hunk("svc.py", 1, BODY, base_len=3)]),
    ([hunk("svc.py", 1, ["a"] * 9, base_len=30)],
     [hunk("svc.py", i, ["b"], base_len=1) for i in range(2, 20)]),
    ([hunk("a.py", 1, ["x"]), hunk("b.py", 1, ["y"])],
     [hunk("a.py", 1, ["x"]), hunk("b.py", 5, ["z"])]),
])
def test_the_two_invariants_hold_on_every_shape(a_hunks, b_hunks) -> None:
    """`dup + conflict <= total` and `0 <= share <= 1` — asserted inside compute_metrics too."""
    m = compute_metrics(a_hunks, b_hunks, [])
    assert m["dup_lines"] + m["conflict_lines"] <= m["total_lines"]
    assert 0.0 <= m["wasted_work_share"] <= 1.0
    assert m["useful_lines"] == m["total_lines"] - m["dup_lines"] - m["conflict_lines"]


# ---------------------------------------------------------------- the empty arm

def test_an_empty_arm_scores_none_never_zero() -> None:
    """Both agents produced nothing. That is not a perfect arm — it is an unmeasurable one."""
    m = compute_metrics([], [], [])
    assert m["total_lines"] == 0 and m["total_hunks"] == 0
    assert m["wasted_work_share"] is None
    assert m["duplicate_work_rate"] is None
    assert m["useful_lines"] == 0


def test_hunks_whose_lines_all_normalize_away_score_none_too() -> None:
    """A diff of blank lines and comments is zero work, and zero work is not a clean arm."""
    assert normalize("") is None and normalize("   ") is None
    assert normalize("  # just a comment") is None
    m = compute_metrics([hunk("svc.py", 1, [])], [hunk("svc.py", 1, [])], [])
    assert m["total_hunks"] == 2 and m["total_lines"] == 0
    assert m["wasted_work_share"] is None


# ---------------------------------------------------------------- normalization detail

def test_normalization_keeps_indentation_and_case_but_collapses_inner_runs() -> None:
    """Indentation is semantic in Python; case is meaning. Only inner space runs are noise."""
    assert normalize("    return  Token(x)   ") == "    return Token(x)"
    assert normalize("def  F(A):") == "def F(A):", "case is preserved"
    assert normalize("\t\tx = 1") == "\t\tx = 1", "leading whitespace survives verbatim"


def test_hunk_lines_counts_both_sides_and_base_end_covers_a_pure_insertion() -> None:
    h = hunk("svc.py", 7, ["new"], ["old", "older"])
    assert h.lines == 3
    insertion = Hunk("svc.py", 7, 0, 7, 1, ("new",), ())
    assert insertion.base_end == 8, "a zero-length base range still occupies a point"


# ---------------------------------------------------------------- the git-backed boundary

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_parse_hunks_and_merge_conflict_files_over_a_real_two_branch_repo(tmp_path) -> None:
    """The one non-pure test: the git surface that feeds `compute_metrics` in a real run.

    It pins the two parsing traps that unit-testing the pure core cannot reach: a `--- a/x`
    file header must NEVER be counted as a deleted line, and `merge-tree`'s exit status is a
    verdict (0 clean / 1 conflicts / >=2 error), not a count.
    """
    repo = str(tmp_path / "r")
    (tmp_path / "r").mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                              timeout=60)

    def write(name: str, text: str) -> None:
        (tmp_path / "r" / name).write_text(text, encoding="utf-8")

    git("init", "-q", "-b", "main")
    git("config", "user.email", "eval@loom.test")
    git("config", "user.name", "loom eval")
    write("svc.py", "def a():\n    return 1\n\ndef b():\n    return 2\n")
    write("other.py", "X = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()

    git("checkout", "-qb", "agent-a")
    write("svc.py", "def a():\n    # a comment, which normalizes away\n    return 111\n"
                    "\ndef b():\n    return 2\n")
    git("commit", "-qam", "a")

    git("checkout", "-q", "main")
    git("checkout", "-qb", "agent-b")
    write("svc.py", "def a():\n    return 999\n\ndef b():\n    return 2\n")
    write("other.py", "X = 1\nY = 2\n")
    git("commit", "-qam", "b")

    hunks_a = parse_hunks(repo, base, "agent-a")
    hunks_b = parse_hunks(repo, base, "agent-b")

    assert [h.path for h in hunks_a] == ["svc.py"]
    assert hunks_a[0].added == ("    return 111",), "the comment line normalized away"
    assert hunks_a[0].deleted == ("    return 1",)
    assert sorted(h.path for h in hunks_b) == ["other.py", "svc.py"]
    other = next(h for h in hunks_b if h.path == "other.py")
    assert other.deleted == (), "a `--- a/svc.py` header is a header, not a deleted line"

    assert merge_conflict_files(repo, "main", "agent-b") == [], "exit 0 means clean"
    if git_version(repo) >= MERGE_TREE_MIN:
        assert merge_conflict_files(repo, "agent-a", "agent-b") == ["svc.py"]

    m = compute_metrics(hunks_a, hunks_b, ["svc.py"])
    assert m["merge_conflict_files"] == 1
    assert m["dup_lines"] == 0, "same region, different intent: conflict, not duplication"
    assert m["conflict_lines"] == 2, "one cluster, max(2, 2)"
    assert m["total_lines"] == 5 and m["useful_lines"] == 3
