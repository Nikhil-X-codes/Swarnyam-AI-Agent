"""Phase 9 Test Suite: Docker Containerization and Sandbox Isolation.

Verifies:
1. Dockerfile configuration (base image, dependencies, cache preloading, entrypoint, volumes).
2. .dockerignore exclusion rules (preventing model weights, venv, and local artifacts from leaking into build context).
3. docker-compose.yml specification (volume mounts, environment variables).
4. SWARM_WORK_ROOT container sandbox isolation in agents.solve and main.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.solve import solve_task
from main import build_parser


def test_dockerfile_structure():
    """Verify Dockerfile contains essential production layers and safety boundaries."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.exists(), "Dockerfile must exist at repository root"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Base image and Python version
    assert "python:3.11-slim" in content or "python:3.12-slim" in content

    # System compilation prerequisites
    assert "build-essential" in content
    assert "cmake" in content
    assert "git" in content

    # Pre-caching embedding model
    assert "SentenceTransformer" in content
    assert "all-MiniLM-L6-v2" in content

    # Mount points and internal directories
    assert "/workspace/target-repo" in content
    assert "/app/models" in content
    assert "/app/work/sandboxes" in content
    assert "/app/outputs" in content
    assert "/app/memory" in content

    # Entrypoint
    assert 'ENTRYPOINT ["python", "main.py"]' in content or "ENTRYPOINT [\"python\", \"main.py\"]" in content


def test_dockerignore_rules():
    """.dockerignore must exclude heavy model files, virtualenvs, local sandboxes, and secrets."""
    ignore_path = Path(".dockerignore")
    assert ignore_path.exists(), ".dockerignore must exist at repository root"

    lines = [line.strip() for line in ignore_path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

    # Heavy model weights
    assert any("models/*.gguf" in line or "*.gguf" in line for line in lines)

    # Virtualenvs
    assert any(".venv" in line for line in lines)

    # Secrets
    assert any(".env" in line for line in lines)

    # Local runtime sandboxes
    assert any("work/" in line or "work" in line for line in lines)
    assert any("workspace/" in line or "workspace" in line for line in lines)


def test_docker_compose_config():
    """docker-compose.yml must define required volume mounts and environment mappings."""
    compose_path = Path("docker-compose.yml")
    assert compose_path.exists(), "docker-compose.yml must exist at repository root"

    content = compose_path.read_text(encoding="utf-8")

    # Volume mounts
    assert "/workspace/target-repo" in content
    assert "/app/models" in content
    assert "/app/outputs" in content

    # Environment file
    assert ".env" in content


def test_swarm_work_root_environment_isolation(tmp_path, monkeypatch):
    """Verify solve_task creates sandboxes inside SWARM_WORK_ROOT rather than host directory."""
    custom_root = tmp_path / "container_sandboxes"
    monkeypatch.setenv("SWARM_WORK_ROOT", str(custom_root))

    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    (fake_repo / "main.py").write_text("print('hello')\n", encoding="utf-8")

    with patch("agents.solve.create_plan") as mock_plan, \
         patch("agents.solve.gather_design_context") as mock_context, \
         patch("agents.solve.create_code_change") as mock_coder, \
         patch("agents.solve.scan_diff") as mock_guardrails, \
         patch("agents.solve.apply_diff") as mock_apply, \
         patch("agents.solve.review_sandbox") as mock_review, \
         patch("agents.solve._context", return_value=""):

        mock_plan.return_value = MagicMock(steps=["step 1"])
        mock_context.return_value = MagicMock(as_prompt_context=lambda: "context")
        mock_coder.return_value = MagicMock(diff="diff", confidence=0.9, touched_files=["main.py"])
        mock_guardrails.return_value = MagicMock(passed=True, reasons=[], security_sensitive=False)
        mock_apply.return_value = True
        mock_review.return_value = MagicMock(passed=True, test_output="pass", lint_output="")

        result = solve_task("test task", fake_repo, max_attempts=1)

        # Confirm the sandbox was created inside the custom SWARM_WORK_ROOT path
        expected_active = custom_root / "active"
        assert expected_active.exists()
        assert Path(result.sandbox) == expected_active.resolve()


def test_main_cli_supports_work_root():
    """Verify main.py CLI accepts --work-root flag."""
    parser = build_parser()
    args = parser.parse_args(["solve", "task", "--repo", "sample", "--work-root", "/custom/work"])
    assert args.work_root == "/custom/work"
