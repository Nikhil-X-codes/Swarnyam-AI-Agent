"""Debugger agent that turns reviewer failures into a bounded fix plan."""

from __future__ import annotations

from typing import Any

from tools.llm import call_llm

DEBUGGER_SYSTEM_PROMPT = (
    "You are the Debugger agent in an AI coding swarm. "
    "Given the task description and failed test or linter output, "
    "identify the exact root cause of the failure and output a concise, "
    "step-by-step fix plan that the Coder agent can implement immediately. "
    "Focus only on the smallest necessary changes to make tests pass."
)


def create_debug_plan(task: str, review_output: str, *, failed_diff: str = "", llm: Any = call_llm) -> str:
    from memory.logger import set_agent_context
    set_agent_context("debugger")
    prompt = (
        f"Task:\n{task}\n\n"
        f"Reviewer Failure Diagnostic Output:\n{review_output}\n\n"
    )
    if failed_diff and failed_diff.strip():
        prompt += f"Failed Diff Applied:\n{failed_diff}\n\n"
    prompt += "Provide a concise analysis of the failure and specific instructions for the Coder to fix it."
    response, _ = llm(prompt, DEBUGGER_SYSTEM_PROMPT)
    return response

