from unittest.mock import patch

import pytest

from agents.coder import CoderOutput
from agents.context import DesignContext
from agents.planner import Plan
from agents.reviewer import ReviewReport
from agents.solve import solve_task


@pytest.fixture(autouse=True)
def mock_context_stage():
    fake_ctx = DesignContext(
        research_notes="test research",
        repo_summary="test repo",
        architecture_guidance="test arch",
        risks=["test risk"],
    )
    with patch("agents.solve.gather_design_context", return_value=fake_ctx):
        yield


@pytest.fixture
def mock_target_repo(tmp_path):
    repo = tmp_path / "target_repo"
    repo.mkdir()
    src = repo / "src"
    src.mkdir()
    tests = repo / "tests"
    tests.mkdir()
    (src / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tests / "test_calc.py").write_text("from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    return repo


def test_solve_self_corrects_with_debugger(mock_target_repo, tmp_path):
    """Success Check 1: Solver fails on attempt 1, Debugger generates fix plan, attempt 2 passes."""
    plan = Plan(steps=["Add divide"], acceptance_criteria=["divide(4, 2) == 2", "raises ZeroDivisionError"])

    attempt_counter = 0

    def mock_create_code_change(task, plan_arg, context, base_path, max_retries, llm):
        nonlocal attempt_counter
        attempt_counter += 1
        if attempt_counter == 1:
            # First attempt: intentional bug (returns 0 instead of division)
            diff = (
                "--- a/src/calc.py\n"
                "+++ b/src/calc.py\n"
                "@@ -2,1 +2,3 @@\n"
                "     return a + b\n"
                "+def divide(a, b):\n"
                "+    return 0\n"
            )
            return CoderOutput(files={"src/calc.py": "def add(a, b):\n    return a + b\n\ndef divide(a, b):\n    return 0\n"}, diff=diff, summary="buggy divide", confidence=0.9, touched_files=["src/calc.py"])
        else:
            # Second attempt: corrected implementation
            assert "Debugger Feedback" in context
            diff = (
                "--- a/src/calc.py\n"
                "+++ b/src/calc.py\n"
                "@@ -2,1 +2,3 @@\n"
                "     return a + b\n"
                "+def divide(a, b):\n"
                "+    return a / b\n"
            )
            return CoderOutput(files={"src/calc.py": "def add(a, b):\n    return a + b\n\ndef divide(a, b):\n    return a / b\n"}, diff=diff, summary="fixed divide", confidence=0.95, touched_files=["src/calc.py"])

    def mock_review_sandbox(sandbox_path):
        if attempt_counter == 1:
            return ReviewReport(passed=False, tests_passed=False, lint_passed=True, test_output="AssertionError: 0 != 2", lint_output="")
        return ReviewReport(passed=True, tests_passed=True, lint_passed=True, test_output="1 passed", lint_output="")

    def mock_debugger(task, diagnostic, *args, **kwargs):
        return "Fix divide to return a / b instead of 0."

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value="calculator code"), \
         patch("agents.solve.create_code_change", side_effect=mock_create_code_change), \
         patch("agents.solve.review_sandbox", side_effect=mock_review_sandbox), \
         patch("agents.solve.create_debug_plan", side_effect=mock_debugger):

        result = solve_task("Add divide function", mock_target_repo, work_root=tmp_path / "work", max_attempts=3)

        assert result.status == "success"
        assert result.attempts == 2
        assert len(result.iterations) == 2
        assert result.iterations[0].status == "failed_attempt"
        assert "Fix divide to return a / b" in (result.iterations[0].debug_feedback or "")
        assert result.iterations[1].status == "success"


def test_solve_security_sensitive_file_routes_to_human_review(mock_target_repo, tmp_path):
    """Success Check 2: Touches security-sensitive file -> routes to human review even if tests pass."""
    plan = Plan(steps=["Update config"], acceptance_criteria=["config loaded"])
    diff = (
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -0,0 +1,1 @@\n"
        "+API_TIMEOUT = 30\n"
    )
    coder_out = CoderOutput(files={"config.py": "API_TIMEOUT = 30\n"}, diff=diff, summary="update config", confidence=0.95, touched_files=["config.py"])

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value=""), \
         patch("agents.solve.create_code_change", return_value=coder_out), \
         patch("agents.solve.apply_diff"), \
         patch("agents.solve.review_sandbox", return_value=ReviewReport(passed=True, tests_passed=True, lint_passed=True, test_output="ok", lint_output="")):

        result = solve_task("Update config", mock_target_repo, work_root=tmp_path / "work")

        assert result.status == "human_review"
        assert result.security_sensitive is True
        assert "security-sensitive" in result.message.lower()
        assert len(result.iterations) == 1
        assert result.iterations[0].status == "human_review"


def test_solve_low_confidence_routes_to_human_review(mock_target_repo, tmp_path):
    """Low self-reported confidence (< threshold) routes to human review."""
    plan = Plan(steps=["Risky refactor"], acceptance_criteria=["tests pass"])
    diff = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-def add(a, b):\n"
        "+def add(a: float, b: float):\n"
    )
    # Confidence is 0.5, below default threshold of 0.7
    coder_out = CoderOutput(files={"src/calc.py": "def add(a: float, b: float):\n    return a + b\n"}, diff=diff, summary="uncertain refactor", confidence=0.5, touched_files=["src/calc.py"])

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value=""), \
         patch("agents.solve.create_code_change", return_value=coder_out), \
         patch("agents.solve.apply_diff"), \
         patch("agents.solve.review_sandbox", return_value=ReviewReport(passed=True, tests_passed=True, lint_passed=True, test_output="ok", lint_output="")):

        result = solve_task("Risky refactor", mock_target_repo, work_root=tmp_path / "work", confidence_threshold=0.7)

        assert result.status == "human_review"
        assert result.confidence == 0.5
        assert "confidence" in result.message.lower()


def test_solve_max_attempts_cap(mock_target_repo, tmp_path):
    """Exhausting max attempts stops cleanly with status failed."""
    plan = Plan(steps=["Unsolvable"], acceptance_criteria=["pass"])
    coder_out = CoderOutput(files={"src/calc.py": "def broken(): pass\n"}, diff="--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n+pass\n", summary="attempt", confidence=0.8, touched_files=["src/calc.py"])

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value=""), \
         patch("agents.solve.create_code_change", return_value=coder_out), \
         patch("agents.solve.apply_diff"), \
         patch("agents.solve.review_sandbox", return_value=ReviewReport(passed=False, tests_passed=False, lint_passed=False, test_output="FAIL", lint_output="FAIL")), \
         patch("agents.solve.create_debug_plan", return_value="Try again"):

        result = solve_task("Unsolvable task", mock_target_repo, work_root=tmp_path / "work", max_attempts=3)

        assert result.status == "failed"
        assert result.attempts == 3
        assert len(result.iterations) == 3
        assert "Maximum attempts" in result.message


def test_solve_guardrail_rejection(mock_target_repo, tmp_path):
    """Diffs containing dangerous patterns are rejected immediately."""
    plan = Plan(steps=["Dangerous task"], acceptance_criteria=["none"])
    diff = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,1 +1,1 @@\n"
        "+eval('print(1)')\n"
    )
    coder_out = CoderOutput(files={"src/calc.py": "eval('print(1)')\n"}, diff=diff, summary="unsafe", confidence=0.9, touched_files=["src/calc.py"])

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value=""), \
         patch("agents.solve.create_code_change", return_value=coder_out):

        result = solve_task("Unsafe task", mock_target_repo, work_root=tmp_path / "work")

        assert result.status == "rejected"
        assert "dangerous pattern" in result.message
        assert len(result.iterations) == 1
        assert result.iterations[0].status == "rejected"


def test_solve_passes_failed_diff_to_debugger_and_resets_sandbox(mock_target_repo, tmp_path):
    """Debugger receives failed_diff and sandbox is reset between attempts."""
    plan = Plan(steps=["Bug fix"], acceptance_criteria=["passes"])
    diff1 = "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n+# buggy\n"
    diff2 = "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n+# fixed\n"

    calls = 0
    received_diff = None

    def mock_coder(task, plan_arg, context, base_path, max_retries, llm):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CoderOutput(files={"src/calc.py": "# buggy\n"}, diff=diff1, summary="buggy", confidence=0.8, touched_files=["src/calc.py"])
        return CoderOutput(files={"src/calc.py": "# fixed\n"}, diff=diff2, summary="fixed", confidence=0.9, touched_files=["src/calc.py"])

    def mock_debugger(task, diagnostic, *, failed_diff="", llm=None):
        nonlocal received_diff
        received_diff = failed_diff
        return "Fix the bug."

    def mock_review(sandbox_path):
        if calls == 1:
            return ReviewReport(passed=False, tests_passed=False, lint_passed=True, test_output="FAIL", lint_output="")
        return ReviewReport(passed=True, tests_passed=True, lint_passed=True, test_output="PASS", lint_output="")

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value=""), \
         patch("agents.solve.create_code_change", side_effect=mock_coder), \
         patch("agents.solve.apply_diff"), \
         patch("agents.solve.review_sandbox", side_effect=mock_review), \
         patch("agents.solve.create_debug_plan", side_effect=mock_debugger):

        result = solve_task("Fix bug", mock_target_repo, work_root=tmp_path / "work", max_attempts=2)

        assert result.status == "success"
        assert received_diff == diff1
