"""Phase 6 Success Check: Compare parallel vs sequential context gathering runtime."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.context import gather_design_context
from agents.planner import Plan


def benchmark_timing(simulated_call_latency_seconds: float = 0.5) -> dict:
    """Run context gathering sequentially vs in parallel and compare timing."""

    def simulated_llm(prompt, system_prompt):
        time.sleep(simulated_call_latency_seconds)
        if "Architect" in system_prompt:
            return (
                '{"design_approach": "Modular design", "module_boundaries": ["src/math.py"], "risks": ["compatibility"]}',
                None,
            )
        if "Researcher" in system_prompt:
            return ("Web research notes on best practices and signatures.", None)
        return ("Repository context summary of existing patterns.", None)

    def simulated_search(query, max_results=3):
        time.sleep(simulated_call_latency_seconds)
        return [{"title": "Python Docs", "snippet": "Example API doc", "href": "https://python.org"}]

    plan = Plan(steps=["Implement function", "Add tests"], acceptance_criteria=["all tests pass"])
    task = "Add power function that calculates base ** exponent"
    chunks = "def add(a, b): return a + b\ndef subtract(a, b): return a - b"

    # 1. Sequential Run
    start_seq = time.perf_counter()
    _ = gather_design_context(
        task,
        plan,
        chunks,
        parallel=False,
        llm=simulated_llm,
        search_fn=simulated_search,
    )
    seq_time = round(time.perf_counter() - start_seq, 3)

    # 2. Parallel Run
    start_par = time.perf_counter()
    ctx_par = gather_design_context(
        task,
        plan,
        chunks,
        parallel=True,
        llm=simulated_llm,
        search_fn=simulated_search,
    )
    par_time = round(time.perf_counter() - start_par, 3)

    speedup = round(seq_time / par_time, 2) if par_time > 0 else 0.0

    result = {
        "sequential_seconds": seq_time,
        "parallel_seconds": par_time,
        "speedup_factor": f"{speedup}x faster",
        "parallel_is_faster": par_time < seq_time,
        "merged_context_length_chars": len(ctx_par.as_prompt_context()),
        "success_check_passed": par_time < seq_time,
    }
    return result


if __name__ == "__main__":
    results = benchmark_timing()
    print(json.dumps(results, indent=2))
