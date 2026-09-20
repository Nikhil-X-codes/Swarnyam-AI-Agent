from types import SimpleNamespace

from eval.runner import BenchmarkTask, run_suite
from main import build_parser


def test_eval_suite_stability_and_regression_detection(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = [
        BenchmarkTask("task-1", "task-1", [{"path": "result.txt", "contains": ["success"]}]),
        BenchmarkTask("task-2", "task-2", [{"path": "calc.py", "contains": ["def add"]}]),
    ]

    # Baseline run
    def good_solver(task_desc, *args, **kwargs):
        sandbox = tmp_path / "sandbox-good"
        sandbox.mkdir(exist_ok=True)
        (sandbox / "result.txt").write_text("success", encoding="utf-8")
        (sandbox / "calc.py").write_text("def add(a, b): return a + b", encoding="utf-8")
        return SimpleNamespace(status="success", sandbox=str(sandbox))

    run1 = run_suite(repo, tasks=tasks, solver=good_solver, results_dir=tmp_path / "results")
    assert run1["passed"] == 2
    assert run1["pass_rate"] == 1.0

    # Run 2: identical code -> stable
    run2 = run_suite(repo, tasks=tasks, solver=good_solver, results_dir=tmp_path / "results")
    assert run2["comparison"]["stable_pass_pattern"] is True
    assert run2["comparison"]["pass_rate_delta"] == 0.0

    # Run 3: deliberately broken prompt/output -> regression detected
    def broken_coder_solver(task_desc, *args, **kwargs):
        sandbox = tmp_path / "sandbox-broken"
        sandbox.mkdir(exist_ok=True)
        # Deliberately broken output that fails benchmark acceptance check
        (sandbox / "result.txt").write_text("broken output without required tokens", encoding="utf-8")
        return SimpleNamespace(status="success", sandbox=str(sandbox))

    run3 = run_suite(repo, tasks=tasks, solver=broken_coder_solver, results_dir=tmp_path / "results")
    assert run3["passed"] == 0
    assert run3["pass_rate"] == 0.0
    assert run3["comparison"]["stable_pass_pattern"] is False
    assert run3["comparison"]["pass_rate_delta"] == -1.0


def test_main_eval_cli_filters(tmp_path):
    parser = build_parser()
    args = parser.parse_args(["eval", "--limit", "2", "--task-ids", "calc-001", "calc-002"])
    assert args.command == "eval"
    assert args.limit == 2
    assert args.task_ids == ["calc-001", "calc-002"]
