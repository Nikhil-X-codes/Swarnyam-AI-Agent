"""Reviewer agent: run lint and tests inside the sandbox only."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReviewReport:
    passed: bool
    tests_passed: bool
    lint_passed: bool
    test_output: str
    lint_output: str


def _run(command: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode == 0, output


def review_sandbox(sandbox: str | Path, *, timeout_seconds: int = 60) -> ReviewReport:
    root = Path(sandbox).resolve()
    lint_passed, lint_output = _run([sys.executable, "-m", "ruff", "check", "."], root, timeout_seconds)
    tests_passed, test_output = _run([sys.executable, "-m", "pytest", "-q"], root, timeout_seconds)
    return ReviewReport(lint_passed and tests_passed, tests_passed, lint_passed, test_output, lint_output)
