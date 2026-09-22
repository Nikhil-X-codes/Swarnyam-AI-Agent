import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memory.logger import (
    SwarmLogger,
    get_agent_context,
    get_run_context,
    set_agent_context,
)
from memory.report import generate_run_report
from tools.llm import LLMUsage, call_llm


@pytest.fixture
def temp_logger(tmp_path, monkeypatch):
    db_path = tmp_path / "test_swarm.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_path))
    SwarmLogger.reset_instance()
    logger = SwarmLogger.get_instance(db_path)
    yield logger
    SwarmLogger.reset_instance()


def test_sqlite_schema_initialization(tmp_path):
    db_path = tmp_path / "schema_test.db"
    _ = SwarmLogger(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "runs" in tables
        assert "llm_calls" in tables


def test_run_lifecycle_and_steps(temp_logger):
    run_id = temp_logger.start_run("solve", "Fix bug in auth", repo="repo/test")
    assert run_id.startswith("run-")
    assert get_run_context() == run_id

    # Log two LLM calls
    usage1 = LLMUsage(
        session_id="s1",
        timestamp="2026-09-19T10:00:00Z",
        model="llama3-70b",
        prompt_chars=50,
        response_chars=100,
        input_tokens=10,
        output_tokens=20,
        estimated_cost_usd=0.0001,
        latency_ms=150.0,
        attempts=1,
        status="success",
        backend="groq",
        agent_name="planner",
    )
    temp_logger.log_call(
        usage=usage1,
        prompt="Plan prompt",
        response='{"plan": "step 1"}',
        system_prompt="You are planner",
        agent_name="planner",
    )

    usage2 = LLMUsage(
        session_id="s2",
        timestamp="2026-09-19T10:00:02Z",
        model="qwen2.5-coder-1.5b",
        prompt_chars=120,
        response_chars=200,
        input_tokens=30,
        output_tokens=40,
        estimated_cost_usd=0.0,
        latency_ms=250.0,
        attempts=1,
        status="success",
        backend="local",
        agent_name="coder",
    )
    temp_logger.log_call(
        usage=usage2,
        prompt="Coder prompt",
        response="diff content",
        system_prompt="You are coder",
        agent_name="coder",
    )

    temp_logger.finish_run(run_id, status="success", summary="All tests passed")
    assert get_run_context() is None

    run = temp_logger.get_run(run_id)
    assert run is not None
    assert run["status"] == "success"
    assert run["command"] == "solve"
    assert run["task"] == "Fix bug in auth"
    assert run["total_cost_usd"] == pytest.approx(0.0001)
    assert run["total_latency_ms"] == pytest.approx(400.0)

    steps = temp_logger.get_run_steps(run_id)
    assert len(steps) == 2
    assert steps[0]["agent_name"] == "planner"
    assert steps[0]["backend"] == "groq"
    assert steps[1]["agent_name"] == "coder"
    assert steps[1]["backend"] == "local"


def test_cost_report_aggregation(temp_logger):
    run_id = temp_logger.start_run("eval", "Test suite benchmark")

    temp_logger.log_call(
        usage={
            "session_id": "c1",
            "backend": "groq",
            "model": "gpt-oss-120b",
            "input_tokens": 100,
            "output_tokens": 50,
            "estimated_cost_usd": 0.005,
            "latency_ms": 500.0,
            "status": "success",
        },
        agent_name="planner",
        run_id=run_id,
    )
    temp_logger.log_call(
        usage={
            "session_id": "c2",
            "backend": "openrouter",
            "model": "deepseek-coder",
            "input_tokens": 200,
            "output_tokens": 100,
            "estimated_cost_usd": 0.002,
            "latency_ms": 700.0,
            "status": "success",
        },
        agent_name="coder",
        run_id=run_id,
    )
    temp_logger.finish_run(run_id, status="success")

    report = temp_logger.cost_report()
    grand = report["grand_total"]
    assert grand["total_cost"] == pytest.approx(0.007)
    assert grand["total_calls"] == 2
    assert grand["total_input_tokens"] == 300
    assert grand["total_output_tokens"] == 150

    by_agent = report["by_agent"]
    assert "planner" in by_agent
    assert by_agent["planner"]["cost"] == pytest.approx(0.005)
    assert "coder" in by_agent
    assert by_agent["coder"]["cost"] == pytest.approx(0.002)

    by_backend = report["by_backend"]
    assert "groq" in by_backend
    assert "openrouter" in by_backend


def test_markdown_run_report_generation(temp_logger, tmp_path):
    run_id = temp_logger.start_run("solve", "Add login validation", repo="/workspace/auth")
    temp_logger.log_call(
        usage={
            "session_id": "step-1",
            "backend": "groq",
            "model": "openai/gpt-oss-120b",
            "input_tokens": 120,
            "output_tokens": 80,
            "estimated_cost_usd": 0.0015,
            "latency_ms": 320.0,
            "status": "success",
        },
        agent_name="planner",
        prompt="Generate plan",
        response="Plan JSON",
        run_id=run_id,
    )
    temp_logger.finish_run(run_id, status="success", summary="Successfully solved")

    report_path = generate_run_report(
        run_id,
        logger=temp_logger,
        output_dir=tmp_path,
        plan={"summary": "Add login checks", "steps": [{"title": "Check password", "description": "Verify hash"}]},
        diff="--- a/auth.py\n+++ b/auth.py\n@@ -1,1 +1,2 @@\n+def validate(): pass\n",
        review_output="2 passed in 0.12s",
        confidence=0.92,
    )

    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert f"# Swarm Run Report: `{run_id}`" in content
    assert "Add login validation" in content
    assert "0.92" in content
    assert "Check password" in content
    assert "diff" in content
    assert "validate(): pass" in content
    assert "2 passed in 0.12s" in content
    assert "Spend by Agent Role" in content or "By Agent Role" in content


def test_call_llm_auto_logs_to_sqlite(temp_logger, monkeypatch):
    """Ensure call_llm automatically logs to SQLite SwarmLogger without explicit intervention."""
    mock_groq = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Mocked LLM Answer"
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 25
    mock_usage.completion_tokens = 15
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = mock_usage
    mock_groq.chat.completions.create.return_value = mock_response

    run_id = temp_logger.start_run("test-command", "Auto-logging test")
    set_agent_context("researcher")

    monkeypatch.setattr("tools.llm._pace_groq_request", lambda: None)
    monkeypatch.setenv("GROQ_API_KEY", "dummy-key")

    resp, _ = call_llm("What is Python?", client=mock_groq)
    assert resp == "Mocked LLM Answer"

    steps = temp_logger.get_run_steps(run_id)
    assert len(steps) == 1
    assert steps[0]["agent_name"] == "researcher"
    assert steps[0]["prompt"] == "What is Python?"
    assert steps[0]["response"] == "Mocked LLM Answer"
    assert steps[0]["input_tokens"] == 25
    assert steps[0]["output_tokens"] == 15


def test_solve_task_success_generates_report_and_trace(tmp_path, monkeypatch):
    """Test solve_task end-to-end with mock LLM produces SQLite records + report file."""
    # Setup mock target repo
    target_repo = tmp_path / "mini_repo"
    target_repo.mkdir()
    (target_repo / "calculator.py").write_text("def add(a, b): return a\n", encoding="utf-8")
    (target_repo / "tests").mkdir()
    (target_repo / "tests" / "test_calc.py").write_text(
        "from calculator import add\ndef test_add(): assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    db_file = tmp_path / "solve_test.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_file))
    SwarmLogger.reset_instance()
    logger = SwarmLogger.get_instance(db_file)

    def mock_llm(prompt: str, system_prompt: str = "", **kwargs):
        from memory.logger import SwarmLogger
        from tools.llm import LLMUsage
        agent = get_agent_context()
        if agent == "planner":
            body = json.dumps({"summary": "Fix add function", "steps": ["Fix return value to a + b"], "acceptance_criteria": ["assert add(1, 2) == 3"]})
        elif agent == "architect":
            body = json.dumps({"design_approach": "simple fix", "module_boundaries": [], "risks": []})
        elif agent == "coder":
            body = json.dumps({
                "files": {"calculator.py": "def add(a, b):\n    return a + b\n"},
                "summary": "Fix return value",
                "confidence": 0.95,
                "touched_files": ["calculator.py"],
            })
        else:
            body = "OK"
        usage = LLMUsage(
            session_id="mock-sess",
            timestamp="2026-09-19T10:00:00Z",
            model="mock-model",
            prompt_chars=len(prompt),
            response_chars=len(body),
            input_tokens=10,
            output_tokens=10,
            estimated_cost_usd=0.0001,
            latency_ms=50.0,
            attempts=1,
            status="success",
            agent_name=agent,
        )
        SwarmLogger.get_instance().log_call(usage, prompt=prompt, response=body, system_prompt=system_prompt, agent_name=agent)
        return body, usage

    # Mock indexer context and review
    monkeypatch.setattr("agents.solve._context", lambda repo, db, task: "calc context")
    from agents.reviewer import ReviewReport
    monkeypatch.setattr(
        "agents.solve.review_sandbox",
        lambda sb: ReviewReport(True, True, True, "1 passed in 0.05s", "All checks passed!"),
    )

    from agents.solve import solve_task

    result = solve_task(
        "Fix add function in calculator",
        target_repo,
        llm=mock_llm,
        work_root=tmp_path / "sandboxes",
    )

    assert result.status == "success"
    assert result.run_id is not None
    assert result.report_path is not None
    assert Path(result.report_path).exists()

    run = logger.get_run(result.run_id)
    assert run is not None
    assert run["status"] == "success"

    steps = logger.get_run_steps(result.run_id)
    assert len(steps) >= 3  # planner, context/architect, coder


def test_success_check_failure_reconstruction(tmp_path, monkeypatch):
    """Success Check: Intentionally cause a failure mid-run, then reconstruct exactly
    what happened from logs alone.
    """
    target_repo = tmp_path / "fail_repo"
    target_repo.mkdir()
    (target_repo / "app.py").write_text("def run(): pass\n", encoding="utf-8")

    db_file = tmp_path / "failure_audit.db"
    monkeypatch.setenv("SWARM_DB_PATH", str(db_file))
    SwarmLogger.reset_instance()
    logger = SwarmLogger.get_instance(db_file)

    def failing_llm(prompt: str, system_prompt: str = "", **kwargs):
        from memory.logger import SwarmLogger
        from tools.llm import LLMUsage
        agent = get_agent_context()
        if agent == "planner":
            body = json.dumps({"summary": "Plan", "steps": ["Step 1"], "acceptance_criteria": ["passes"]})
            usage = LLMUsage(
                "s-plan", "2026-09-19T10:00:00Z", "mock", 10, 10, 5, 5, 0.0001, 50.0, 1, "success", agent_name="planner"
            )
            SwarmLogger.get_instance().log_call(usage, prompt=prompt, response=body, system_prompt=system_prompt, agent_name="planner")
            return body, usage
        # Fail on coder step
        usage = LLMUsage("s-fail", "2026-09-19T10:00:01Z", "mock", 10, 0, 5, 0, 0.0, 50.0, 1, "error", error_type="RuntimeError", agent_name="coder")
        SwarmLogger.get_instance().log_call(usage, prompt=prompt, response="", system_prompt=system_prompt, agent_name="coder")
        raise RuntimeError("Simulated Coder Provider Crash mid-run!")

    monkeypatch.setattr("agents.solve._context", lambda repo, db, task: "")

    from agents.solve import solve_task

    with pytest.raises(RuntimeError, match="Simulated Coder Provider Crash"):
        solve_task("Failing task", target_repo, llm=failing_llm, work_root=tmp_path / "sb")

    # Now verify we can reconstruct what happened purely from SQLite
    last_run = logger.get_last_run()
    assert last_run is not None
    assert last_run["status"] == "failed"
    assert "Simulated Coder Provider Crash" in last_run["summary"]

    steps = logger.get_run_steps(last_run["run_id"])
    # Planner succeeded before the crash
    assert any(s["agent_name"] == "planner" and s["status"] == "success" for s in steps)

    # Check report was generated
    report_file = Path("outputs") / f"{last_run['run_id']}.md"
    assert report_file.exists()
    report_content = report_file.read_text(encoding="utf-8")
    assert "Simulated Coder Provider Crash mid-run!" in report_content


def test_cli_cost_report_and_last_run(temp_logger, capsys):
    import sys

    from main import main

    run_id = temp_logger.start_run("solve", "CLI test task")
    temp_logger.log_call(
        usage={
            "session_id": "c-cli",
            "backend": "groq",
            "model": "model-1",
            "input_tokens": 50,
            "output_tokens": 25,
            "estimated_cost_usd": 0.0005,
            "latency_ms": 100.0,
            "status": "success",
        },
        agent_name="planner",
        run_id=run_id,
    )
    temp_logger.finish_run(run_id, status="success")

    # Test cost-report
    with patch.object(sys, "argv", ["main.py", "cost-report"]):
        ret = main()
        assert ret == 0
        captured = capsys.readouterr()
        assert "Swarm Cost & Observability Report" in captured.out
        assert "planner" in captured.out

    # Test last-run
    with patch.object(sys, "argv", ["main.py", "last-run"]):
        ret = main()
        assert ret == 0
        captured = capsys.readouterr()
        assert "Run Details:" in captured.out
        assert "CLI test task" in captured.out
        assert "planner" in captured.out
