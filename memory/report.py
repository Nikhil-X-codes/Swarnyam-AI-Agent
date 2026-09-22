from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memory.logger import SwarmLogger


def generate_run_report(
    run_id: str,
    *,
    logger: SwarmLogger | None = None,
    output_dir: Path | str = "outputs",
    plan: Any = None,
    diff: str = "",
    review_output: str = "",
    confidence: float | None = None,
    confidence_flags: list[str] | None = None,
    error_message: str | None = None,
) -> Path:
    """Generate a structured Markdown report for a given run and save to outputs/run-<run_id>.md."""
    if logger is None:
        logger = SwarmLogger.get_instance()

    run = logger.get_run(run_id) or {
        "run_id": run_id,
        "command": "solve",
        "task": "Unknown",
        "repo": "",
        "status": "unknown",
        "started_at": "",
        "finished_at": "",
        "total_cost_usd": 0.0,
        "total_latency_ms": 0.0,
        "summary": "",
    }

    steps = logger.get_run_steps(run_id)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report_name = f"{run_id}.md" if run_id.startswith("run-") else f"run-{run_id}.md"
    report_file = out_path / report_name

    # Compute step stats
    total_input = sum(s.get("input_tokens", 0) for s in steps)
    total_output = sum(s.get("output_tokens", 0) for s in steps)
    total_cost = run.get("total_cost_usd", 0.0) or sum(s.get("estimated_cost_usd", 0.0) for s in steps)
    total_latency_ms = run.get("total_latency_ms", 0.0) or sum(s.get("latency_ms", 0.0) for s in steps)

    lines: list[str] = []
    lines.append(f"# Swarm Run Report: `{run_id}`\n")

    # Overview Table
    lines.append("## Overview\n")
    lines.append("| Property | Value |")
    lines.append("|---|---|")
    lines.append(f"| **Task** | {run.get('task', 'N/A')} |")
    lines.append(f"| **Repo** | `{run.get('repo') or 'N/A'}` |")
    lines.append(f"| **Status** | **{run.get('status', 'unknown').upper()}** |")
    lines.append(f"| **Started At** | {run.get('started_at', 'N/A')} |")
    lines.append(f"| **Finished At** | {run.get('finished_at', 'N/A')} |")
    lines.append(f"| **Total Cost** | ${total_cost:.5f} |")
    lines.append(f"| **Total LLM Latency** | {total_latency_ms / 1000:.2f}s ({total_latency_ms:.0f} ms) |")
    lines.append(f"| **Total Tokens** | {total_input + total_output} ({total_input} in / {total_output} out) |")
    lines.append(f"| **Agent Calls** | {len(steps)} |")
    lines.append("")

    # Status / Errors
    if error_message:
        lines.append("## Failure / Error Details\n")
        lines.append(f"> [!CAUTION]\n> **Error encountered during execution:**\n> ```\n> {error_message}\n> ```\n")

    # Confidence & Safety Gating
    lines.append("## Safety & Confidence Gating\n")
    if confidence is not None:
        lines.append(f"- **Coder Confidence:** `{confidence:.2f}`")
    if confidence_flags:
        lines.append("- **Flags Triggered:**")
        for flag in confidence_flags:
            lines.append(f"  - `{flag}`")
    else:
        lines.append("- **Flags Triggered:** None (Passed all automated gates)")
    lines.append("")

    # Plan
    if plan:
        lines.append("## Implementation Plan\n")
        if isinstance(plan, dict):
            summary = plan.get("summary")
            steps_list = plan.get("steps", [])
            if summary:
                lines.append(f"**Summary:** {summary}\n")
            if steps_list:
                lines.append("**Steps:**")
                for i, st in enumerate(steps_list, 1):
                    if isinstance(st, dict):
                        lines.append(f"{i}. **{st.get('title', 'Step')}**: {st.get('description', '')}")
                    else:
                        lines.append(f"{i}. {st}")
        elif hasattr(plan, "summary") and hasattr(plan, "steps"):
            lines.append(f"**Summary:** {plan.summary}\n")
            lines.append("**Steps:**")
            for i, st in enumerate(plan.steps, 1):
                if hasattr(st, "title"):
                    lines.append(f"{i}. **{st.title}**: {getattr(st, 'description', '')}")
                else:
                    lines.append(f"{i}. {st}")
        else:
            lines.append(f"```json\n{json.dumps(plan, default=str, indent=2)}\n```")
        lines.append("")

    # Agent Timeline Table
    lines.append("## Agent Execution Timeline\n")
    if steps:
        lines.append("| Step | Agent | Backend | Model | Tokens (In/Out) | Cost ($) | Latency (ms) | Status |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for idx, step in enumerate(steps, 1):
            s_agent = step.get("agent_name", "unknown")
            s_backend = step.get("backend", "unknown")
            s_model = step.get("model", "")
            s_in = step.get("input_tokens", 0)
            s_out = step.get("output_tokens", 0)
            s_cost = step.get("estimated_cost_usd", 0.0)
            s_lat = step.get("latency_ms", 0.0)
            s_status = step.get("status", "success")
            lines.append(
                f"| {idx} | `{s_agent}` | {s_backend} | `{s_model}` | {s_in} / {s_out} | ${s_cost:.5f} | {s_lat:.0f} | {s_status} |"
            )
    else:
        lines.append("_No LLM calls recorded for this run._")
    lines.append("")

    # Review & Test Output
    if review_output:
        lines.append("## Test & Review Output\n")
        lines.append("```\n" + review_output.strip() + "\n```\n")

    # Final Diff
    if diff:
        lines.append("## Final Unified Diff\n")
        lines.append("```diff\n" + diff.strip() + "\n```\n")

    # Cost Breakdown
    lines.append("## Cost Breakdown\n")
    cost_by_agent: dict[str, dict[str, Any]] = {}
    for s in steps:
        ag = s.get("agent_name", "unknown")
        if ag not in cost_by_agent:
            cost_by_agent[ag] = {"calls": 0, "cost": 0.0, "tokens": 0}
        cost_by_agent[ag]["calls"] += 1
        cost_by_agent[ag]["cost"] += s.get("estimated_cost_usd", 0.0)
        cost_by_agent[ag]["tokens"] += s.get("input_tokens", 0) + s.get("output_tokens", 0)

    lines.append("### By Agent Role\n")
    lines.append("| Agent Role | Calls | Total Tokens | Cost ($) |")
    lines.append("|---|---|---|---|")
    for ag, st in sorted(cost_by_agent.items(), key=lambda x: x[1]["cost"], reverse=True):
        lines.append(f"| `{ag}` | {st['calls']} | {st['tokens']} | ${st['cost']:.5f} |")
    lines.append("")

    report_content = "\n".join(lines)
    report_file.write_text(report_content, encoding="utf-8")
    return report_file
