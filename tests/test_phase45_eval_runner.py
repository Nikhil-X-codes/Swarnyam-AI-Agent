from types import SimpleNamespace

from eval.runner import BenchmarkTask, run_suite


def test_eval_is_stable_and_detects_regression(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [BenchmarkTask("one", "one", [{"path": "answer.txt", "contains": ["pass"]}])]

    def passing_solver(*args, **kwargs):
        sandbox = tmp_path / "sandbox-pass"
        sandbox.mkdir(exist_ok=True)
        (sandbox / "answer.txt").write_text("pass", encoding="utf-8")
        return SimpleNamespace(status="success", sandbox=str(sandbox))

    first = run_suite(repo, tasks=tasks, solver=passing_solver, results_dir=tmp_path / "results")
    second = run_suite(repo, tasks=tasks, solver=passing_solver, results_dir=tmp_path / "results")
    assert first["pass_rate"] == 1.0
    assert second["comparison"]["stable_pass_pattern"] is True

    def failing_solver(*args, **kwargs):
        sandbox = tmp_path / "sandbox-fail"
        sandbox.mkdir(exist_ok=True)
        (sandbox / "answer.txt").write_text("fail", encoding="utf-8")
        return SimpleNamespace(status="success", sandbox=str(sandbox))

    third = run_suite(repo, tasks=tasks, solver=failing_solver, results_dir=tmp_path / "results")
    assert third["pass_rate"] == 0.0
    assert third["comparison"]["pass_rate_delta"] == -1.0
