from unittest.mock import MagicMock, patch

from agents.architect import ArchitectOutput, create_architectural_design
from agents.coder import CoderOutput
from agents.context import DesignContext, gather_design_context
from agents.planner import Plan
from agents.repo_context import summarize_repo_context
from agents.researcher import conduct_research
from agents.reviewer import ReviewReport
from agents.solve import solve_task


def test_researcher_agent():
    mock_search = MagicMock(return_value=[
        {"title": "Math API", "snippet": "Use math.sqrt for root calculations.", "href": "https://example.com"}
    ])
    mock_llm = MagicMock(return_value=("Use math.sqrt(x) with positive validation.", None))

    notes = conduct_research("calculate square root", search_fn=mock_search, llm=mock_llm)
    assert "math.sqrt" in notes
    mock_search.assert_called_once()
    mock_llm.assert_called_once()


def test_repo_context_agent():
    chunks = [
        {"metadata": {"filepath": "src/math.py"}, "text": "def add(a, b): return a + b"}
    ]
    mock_llm = MagicMock(return_value=("Existing math.py uses standard python typing and snake_case.", None))

    summary = summarize_repo_context("add divide", chunks, llm=mock_llm)
    assert "snake_case" in summary
    mock_llm.assert_called_once()


def test_architect_agent():
    json_response = (
        '{\n'
        '  "design_approach": "Create a new module under src/helpers.py with isolated arithmetic helpers",\n'
        '  "module_boundaries": ["src/helpers.py", "tests/test_helpers.py"],\n'
        '  "risks": ["Avoid circular dependencies with src/math.py"]\n'
        '}'
    )
    mock_llm = MagicMock(return_value=(json_response, None))

    plan = Plan(steps=["Add helper"], acceptance_criteria=["helper works"])
    arch = create_architectural_design("Add helper", plan=plan, repo_summary="math.py exists", llm=mock_llm)

    assert isinstance(arch, ArchitectOutput)
    assert "src/helpers.py" in arch.design_approach
    assert len(arch.module_boundaries) == 2
    assert "circular dependencies" in arch.risks[0]


def test_gather_design_context_parallel_and_sequential():
    def fake_search(query, max_results=3):
        return [{"title": "Docs", "snippet": "test snippet", "href": "url"}]

    def fake_llm(prompt, system_prompt):
        if "Architect" in system_prompt:
            return (
                '{"design_approach": "Clean architecture", "module_boundaries": ["src/a.py"], "risks": ["risk 1"]}',
                None,
            )
        if "Researcher" in system_prompt:
            return ("Research findings on libraries", None)
        return ("Repo pattern summary", None)

    plan = Plan(steps=["step 1"], acceptance_criteria=["criterion 1"])
    
    # Test parallel execution
    ctx_parallel = gather_design_context("task", plan, "raw chunks", parallel=True, llm=fake_llm, search_fn=fake_search)
    assert isinstance(ctx_parallel, DesignContext)
    assert ctx_parallel.is_parallel is True
    assert "Research findings" in ctx_parallel.research_notes
    assert "Repo pattern" in ctx_parallel.repo_summary
    assert "Clean architecture" in ctx_parallel.architecture_guidance
    assert "risk 1" in ctx_parallel.risks[0]
    prompt_str = ctx_parallel.as_prompt_context()
    assert "Technical Research Notes" in prompt_str
    assert "Repository Context" in prompt_str
    assert "Architectural Design" in prompt_str

    # Test sequential execution
    ctx_sequential = gather_design_context("task", plan, "raw chunks", parallel=False, llm=fake_llm, search_fn=fake_search)
    assert ctx_sequential.is_parallel is False
    assert "Research findings" in ctx_sequential.research_notes


def test_solve_task_incorporates_design_context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    plan = Plan(steps=["Add power"], acceptance_criteria=["power(2, 3) == 8"])
    coder_out = CoderOutput(
        files={"calc.py": "def add(a, b): return a + b\ndef power(a, b): return a ** b\n"},
        diff="--- a/calc.py\n+++ b/calc.py\n@@ -1,1 +1,2 @@\n+def power(a, b): return a ** b\n",
        summary="add power",
        confidence=0.95,
        touched_files=["calc.py"],
    )

    with patch("agents.solve.create_plan", return_value=plan), \
         patch("agents.solve._context", return_value="calc.py content"), \
         patch("agents.solve.gather_design_context") as mock_gather, \
         patch("agents.solve.create_code_change", return_value=coder_out) as mock_coder, \
         patch("agents.solve.apply_diff"), \
         patch("agents.solve.review_sandbox", return_value=ReviewReport(passed=True, tests_passed=True, lint_passed=True, test_output="ok", lint_output="")):

        fake_design_context = DesignContext(
            research_notes="Use ** operator",
            repo_summary="Single module calc.py",
            architecture_guidance="Add function directly",
            risks=["Overflow with large exponents"],
        )
        mock_gather.return_value = fake_design_context

        result = solve_task("Add power function", repo, work_root=tmp_path / "work")

        assert result.status == "success"
        assert result.design_context == fake_design_context
        mock_gather.assert_called_once()
        # Verify design context was injected into coder prompt
        coder_call_args = mock_coder.call_args[0]
        context_arg = coder_call_args[2]
        assert "Technical Research Notes" in context_arg
        assert "Use ** operator" in context_arg


def test_benchmark_phase6_timing():
    from eval.benchmark_phase6_timing import benchmark_timing

    # Benchmark run with 0.15s latency (avoids Windows timer granularity / thread overhead jitter)
    results = benchmark_timing(simulated_call_latency_seconds=0.15)
    assert results["success_check_passed"] is True
    assert results["parallel_is_faster"] is True
    assert results["parallel_seconds"] < results["sequential_seconds"]


def test_researcher_query_formatting():
    from agents.researcher import _format_search_query

    long_task = "Update slugify to normalize unicode accents like café to cafe and collapse multiple consecutive dashes without dropping accented characters"
    query = _format_search_query(long_task)
    assert query.startswith("Python")
    assert "slugify" in query
    assert len(query.split()) <= 7


def test_repo_context_budgeting_and_deduplication():
    from agents.repo_context import _budget_and_deduplicate_chunks

    duplicate_chunks = [
        {"metadata": {"filepath": "src/calc.py"}, "text": "def add(a, b):\n    return a + b\n" * 10},
        {"metadata": {"filepath": "src/calc.py"}, "text": "def add(a, b):\n    return a + b\n" * 10},  # Duplicate
        {"metadata": {"filepath": "src/math.py"}, "text": "def multiply(a, b):\n    return a * b\n"},
    ]
    # Verify deduplication
    result = _budget_and_deduplicate_chunks(duplicate_chunks, max_chars=5000)
    assert result.count("src/calc.py") == 1
    assert "src/math.py" in result

    # Verify character budgeting
    budgeted = _budget_and_deduplicate_chunks(duplicate_chunks, max_chars=120)
    assert len(budgeted) <= 180
    assert "truncated" in budgeted


def test_gather_design_context_future_timeout_handling():
    import time
    from agents.planner import Plan

    def slow_research(*args, **kwargs):
        time.sleep(0.5)
        return "slow research notes"

    plan = Plan(steps=["step 1"], acceptance_criteria=["criterion 1"])
    # Set timeout to 0.05s so slow_research triggers timeout handling
    ctx = gather_design_context("task", plan, "raw chunks", parallel=True, timeout_seconds=0.05, search_fn=slow_research)
    assert "timed out" in ctx.research_notes.lower()
    assert ctx.is_parallel is True


