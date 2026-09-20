"""Repeatable benchmark runner with persisted run comparisons."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TASKS_PATH = Path(__file__).with_name("tasks.json")


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    description: str
    checks: list[dict[str, Any]]


def load_tasks(path: str | Path = DEFAULT_TASKS_PATH) -> list[BenchmarkTask]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [BenchmarkTask(item["id"], item["description"], item["checks"]) for item in data]


def verify_sandbox(sandbox: str | Path | None, checks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    if not sandbox:
        return False, ["solver returned no sandbox"]
    root = Path(sandbox)
    failures = []
    for check in checks:
        path = root / check["path"]
        if not path.is_file():
            failures.append(f"missing file: {check['path']}")
            continue
        content = path.read_text(encoding="utf-8")
        for expected in check.get("contains", []):
            if expected not in content:
                failures.append(f"{check['path']} missing: {expected}")
    return not failures, failures


def _usage_cost() -> float:
    path = Path(os.getenv("LLM_USAGE_LOG", "memory/llm_usage.jsonl"))
    if not path.exists():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            total += float(json.loads(line).get("estimated_cost_usd", 0.0))
        except (ValueError, json.JSONDecodeError):
            continue
    return round(total, 8)


def _load_previous(results_dir: Path) -> dict | None:
    runs = sorted(results_dir.glob("run-*.json"), key=lambda item: item.stat().st_mtime)
    if not runs:
        return None
    return json.loads(runs[-1].read_text(encoding="utf-8"))


def _solver_task(task: BenchmarkTask) -> str:
    """Give the solver the benchmark's explicit, testable acceptance contract."""

    requirements = []
    for check in task.checks:
        path = check["path"]
        for expected in check.get("contains", []):
            requirements.append(f"- {path} must contain exactly this required text: {expected!r}")
    contract = "\n".join(requirements) or "- Satisfy the task and its repository tests."
    return (
        f"{task.description}\n\n"
        "Benchmark acceptance contract (all items are mandatory; do not omit any):\n"
        f"{contract}\n"
        "Implement the requested change and verify every contract item is present "
        "in the final files before responding."
    )


def run_suite(
    repo: str | Path,
    *,
    db_path: str | Path = "work/chroma-phase3",
    results_dir: str | Path = "eval/results",
    tasks: list[BenchmarkTask] | None = None,
    solver: Callable[..., Any] | None = None,
    max_attempts: int = 3,
) -> dict:
    from agents.solve import solve_task

    selected_tasks = tasks or load_tasks()
    run_solver = solver or solve_task
    output_dir = Path(results_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    previous = _load_previous(output_dir)
    started = time.perf_counter()
    cost_before = _usage_cost()
    task_results = []
    for task in selected_tasks:
        task_started = time.perf_counter()
        solver_prompt = _solver_task(task)
        result = None
        checks_passed = False
        failures: list[str] = []
        status = "failed"
        attempts_used = 1
        try:
            # Let the solver execute its internal retry loop (Coder -> Reviewer -> Debugger)
            # without re-running the expensive Planner and Context agents on every retry attempt.
            result = run_solver(solver_prompt, repo, db_path=db_path, max_attempts=max_attempts)
            sandbox_path = getattr(result, "sandbox", None)
            checks_passed, check_failures = verify_sandbox(sandbox_path, task.checks)
            status = getattr(result, "status", "failed")
            attempts_used = getattr(result, "attempts", 1)

            failures = list(check_failures)
            if status != "success" or not checks_passed:
                solver_msg = getattr(result, "message", None)
                if solver_msg and solver_msg not in failures:
                    failures.append(f"solver message: {solver_msg}")
                review = getattr(result, "review", None)
                if review:
                    if not getattr(review, "tests_passed", True):
                        test_out = getattr(review, "test_output", "").strip()
                        if test_out:
                            summary_line = test_out.splitlines()[-1] if test_out.splitlines() else test_out
                            failures.append(f"tests failed: {summary_line}")
                    if not getattr(review, "lint_passed", True):
                        lint_out = getattr(review, "lint_output", "").strip()
                        if lint_out:
                            failures.append(f"lint failed: {lint_out.splitlines()[0]}")
                iterations = getattr(result, "iterations", [])
                for it in iterations:
                    for reason in getattr(it, "reasons", []):
                        if reason and reason not in failures:
                            failures.append(reason)
                if not failures:
                    failures.append(f"task ended with status '{status}'")
        except Exception as exc:  # noqa: BLE001 - one task must not abort the suite
            checks_passed, failures, status, attempts_used = False, [f"solver exception: {type(exc).__name__}: {exc}"], "failed", 1
        passed = status == "success" and checks_passed
        task_results.append({
            "id": task.id,
            "status": status,
            "passed": passed,
            "attempts": attempts_used,
            "failures": failures,
            "elapsed_seconds": round(time.perf_counter() - task_started, 3),
        })
    passed_count = sum(item["passed"] for item in task_results)
    current = {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_count": len(task_results),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(task_results), 4) if task_results else 0.0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "estimated_cost_usd": round(_usage_cost() - cost_before, 8),
        "tasks": task_results,
    }
    if previous:
        old = {item["id"]: item["passed"] for item in previous.get("tasks", [])}
        current["comparison"] = {
            "previous_run_id": previous.get("run_id"),
            "stable_pass_pattern": all(old.get(item["id"]) == item["passed"] for item in task_results if item["id"] in old),
            "pass_rate_delta": round(current["pass_rate"] - previous.get("pass_rate", 0.0), 4),
        }
    output_path = output_dir / f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{current['run_id'][:8]}.json"
    output_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    current["result_path"] = str(output_path)
    return current
