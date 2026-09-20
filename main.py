"""Command-line entry point for the AI Coding Agent Swarm.

Phase 0 intentionally provides only a bootstrap CLI. Agent capabilities are
added in later phases without changing this entry point's role as the single
CLI surface.
"""

import argparse
import json

from agents.planner import create_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-swarm",
        description="Multi-agent AI coding system.",
    )
    subparsers = parser.add_subparsers(dest="command")
    plan_parser = subparsers.add_parser("plan", help="Create a structured plan for a coding task.")
    plan_parser.add_argument("task")
    plan_parser.add_argument("--offline", action="store_true", help="Force local fallback (llama.cpp) without external API calls.")
    index_parser = subparsers.add_parser("index", help="Index a repository for semantic retrieval.")
    index_parser.add_argument("--repo", required=True)
    index_parser.add_argument("--db", default="memory/chroma")
    index_parser.add_argument("--query", help="Retrieve matching chunks after indexing.")
    solve_parser = subparsers.add_parser("solve", help="Plan, code, review, and debug a repository task.")
    solve_parser.add_argument("task")
    solve_parser.add_argument("--repo", required=True)
    solve_parser.add_argument("--db", default="work/chroma-phase3")
    solve_parser.add_argument("--max-attempts", type=int, default=3)
    solve_parser.add_argument("--confidence-threshold", type=float, default=0.7)
    solve_parser.add_argument("--sequential-context", action="store_true", help="Run context agents sequentially instead of in parallel.")
    solve_parser.add_argument("--offline", action="store_true", help="Force local fallback (llama.cpp) without external API calls.")
    eval_parser = subparsers.add_parser("eval", help="Run the regression benchmark suite.")
    eval_parser.add_argument("--repo", default="workspace/target-repo")
    eval_parser.add_argument("--db", default="work/chroma-phase3")
    eval_parser.add_argument("--max-attempts", type=int, default=3)
    eval_parser.add_argument("--results-dir", default="eval/results")
    eval_parser.add_argument("--task-ids", nargs="*", help="Filter benchmark to specific task IDs")
    eval_parser.add_argument("--limit", type=int, help="Limit number of benchmark tasks to run")
    eval_parser.add_argument("--tasks", help="Optional path to custom tasks JSON file.")
    eval_parser.add_argument("--offline", action="store_true", help="Force local fallback (llama.cpp) without external API calls.")
    cost_parser = subparsers.add_parser("cost-report", help="Show total spend, cost per agent role, and recent runs.")
    cost_parser.add_argument("--json", action="store_true", help="Output cost report as raw JSON.")
    last_run_parser = subparsers.add_parser("last-run", help="Show all steps from the last run or a specified run.")
    last_run_parser.add_argument("--run-id", help="Optional specific run ID to view (defaults to latest run).")
    last_run_parser.add_argument("--json", action="store_true", help="Output run steps as raw JSON.")
    return parser


def main() -> int:
    import os

    args = build_parser().parse_args()
    if getattr(args, "offline", False):
        os.environ["OFFLINE_MODE"] = "true"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    if args.command == "plan":
        print(json.dumps(create_plan(args.task).model_dump(), indent=2))
    elif args.command == "index":
        from rag.indexer import RepoIndexer

        indexer = RepoIndexer(args.db)
        count = indexer.index_repo(args.repo)
        print(json.dumps({"indexed_chunks": count, "repo": args.repo}))
        if args.query:
            print(json.dumps(indexer.retrieve(args.query), indent=2))
    elif args.command == "solve":
        from agents.solve import solve_task

        result = solve_task(
            args.task,
            args.repo,
            db_path=args.db,
            max_attempts=args.max_attempts,
            confidence_threshold=args.confidence_threshold,
            parallel_context=not args.sequential_context,
            offline=args.offline,
        )
        print(json.dumps(result.as_dict(), indent=2, default=str))
        return 0 if result.status in {"success", "human_review"} else 1
    elif args.command == "eval":
        from eval.runner import load_tasks, run_suite

        tasks = load_tasks(args.tasks) if getattr(args, "tasks", None) else load_tasks()
        if args.task_ids:
            tasks = [t for t in tasks if t.id in args.task_ids]
        if args.limit:
            tasks = tasks[: args.limit]
        result = run_suite(args.repo, db_path=args.db, results_dir=args.results_dir, tasks=tasks, max_attempts=args.max_attempts)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["pass_rate"] == 1.0 else 1
    elif args.command == "cost-report":
        from memory.logger import SwarmLogger

        logger = SwarmLogger.get_instance()
        data = logger.cost_report()
        if args.json:
            print(json.dumps(data, indent=2, default=str))
            return 0

        grand = data.get("grand_total", {})
        total_cost = grand.get("total_cost", 0.0)
        total_calls = grand.get("total_calls", 0)
        total_in = grand.get("total_input_tokens", 0)
        total_out = grand.get("total_output_tokens", 0)

        print("\n=== Swarm Cost & Observability Report ===")
        print(f"Total Spend:         ${total_cost:.5f}")
        print(f"Total LLM Calls:     {total_calls}")
        print(f"Total Tokens:        {total_in + total_out} ({total_in} prompt / {total_out} completion)\n")

        print("--- Spend by Agent Role ---")
        by_agent = data.get("by_agent", {})
        if by_agent:
            print(f"{'Agent':<15} {'Calls':<8} {'Tokens':<12} {'Latency (ms)':<15} {'Cost ($)':<10}")
            print("-" * 62)
            for agent, stats in by_agent.items():
                toks = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                lat = stats.get("latency", 0.0)
                cost = stats.get("cost", 0.0)
                calls = stats.get("calls", 0)
                print(f"{agent:<15} {calls:<8} {toks:<12} {lat:<15.1f} ${cost:<10.5f}")
        else:
            print("No agent calls recorded.")

        print("\n--- Spend by Backend ---")
        by_backend = data.get("by_backend", {})
        if by_backend:
            print(f"{'Backend':<15} {'Calls':<8} {'Tokens':<12} {'Cost ($)':<10}")
            print("-" * 50)
            for backend, stats in by_backend.items():
                toks = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
                cost = stats.get("cost", 0.0)
                calls = stats.get("calls", 0)
                print(f"{backend:<15} {calls:<8} {toks:<12} ${cost:<10.5f}")
        else:
            print("No backend calls recorded.")

        print("\n--- Recent Runs ---")
        recent = data.get("recent_runs", [])
        if recent:
            print(f"{'Run ID':<20} {'Command':<8} {'Status':<12} {'Cost ($)':<10} {'Task Preview':<30}")
            print("-" * 82)
            for r in recent:
                r_id = r.get("run_id", "")
                cmd = r.get("command", "")
                stat = r.get("status", "")
                c = r.get("total_cost_usd", 0.0)
                t_prev = (r.get("task") or "")[:28]
                print(f"{r_id:<20} {cmd:<8} {stat:<12} ${c:<10.5f} {t_prev:<30}")
        else:
            print("No runs recorded.")
        print()
        return 0
    elif args.command == "last-run":
        from memory.logger import SwarmLogger

        logger = SwarmLogger.get_instance()
        if args.run_id:
            run = logger.get_run(args.run_id)
            run_id = args.run_id
        else:
            run = logger.get_last_run()
            run_id = run.get("run_id") if run else None

        if not run or not run_id:
            print("No runs found in database.")
            return 1

        steps = logger.get_run_steps(run_id)

        if args.json:
            print(json.dumps({"run": run, "steps": steps}, indent=2, default=str))
            return 0

        print(f"\n=== Run Details: {run_id} ===")
        print(f"Command:     {run.get('command')}")
        print(f"Task:        {run.get('task')}")
        print(f"Status:      {run.get('status')}")
        print(f"Started:     {run.get('started_at')}")
        print(f"Finished:    {run.get('finished_at')}")
        print(f"Total Cost:  ${run.get('total_cost_usd', 0.0):.5f}")
        print(f"Total Steps: {len(steps)}\n")

        print("--- Execution Steps ---")
        if steps:
            print(f"{'Step':<5} {'Agent':<15} {'Backend':<12} {'Model':<25} {'Tokens':<10} {'Cost ($)':<10} {'Status':<10}")
            print("-" * 90)
            for i, s in enumerate(steps, 1):
                ag = s.get("agent_name", "unknown")
                bk = s.get("backend", "unknown")
                md = (s.get("model") or "")[:24]
                toks = f"{s.get('input_tokens', 0)}/{s.get('output_tokens', 0)}"
                c = s.get("estimated_cost_usd", 0.0)
                st = s.get("status", "success")
                print(f"{i:<5} {ag:<15} {bk:<12} {md:<25} {toks:<10} ${c:<10.5f} {st:<10}")
        else:
            print("No steps recorded for this run.")
        print()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

