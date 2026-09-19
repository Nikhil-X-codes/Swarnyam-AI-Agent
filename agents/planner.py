"""Phase 2 Planner agent: task text to validated structured JSON."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from tools.llm import call_llm


class Plan(BaseModel):
    steps: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)


class PlannerOutputError(ValueError):
    """Raised when the model cannot produce a valid Plan after retries."""


PLANNER_SYSTEM_PROMPT = """You are the Planner agent in a coding-agent swarm.
Return only valid JSON, with exactly these top-level fields:
{"steps": ["..."], "acceptance_criteria": ["..."]}
Both arrays must contain concrete, testable strings. Do not use Markdown fences
or explanatory text outside the JSON object."""


def _parse_plan(raw: str) -> Plan:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.removeprefix("```").removeprefix("json").removesuffix("```").strip()
    try:
        return Plan.model_validate(json.loads(candidate))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise PlannerOutputError(f"Planner returned invalid JSON: {exc}") from exc


def create_plan(task: str, *, max_retries: int = 3, llm: Any = call_llm) -> Plan:
    """Generate and validate a plan, retrying malformed model output."""
    try:
        from memory.logger import set_agent_context
        set_agent_context("planner")
    except ImportError:
        pass

    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    last_error: Exception | None = None
    prompt = f"Create an implementation plan for this coding task:\n\n{task.strip()}"
    for attempt in range(max_retries + 1):
        if attempt:
            prompt = (
                f"Your previous output was invalid. Return only valid JSON matching the schema.\n"
                f"Task: {task.strip()}"
            )
        try:
            raw, _ = llm(prompt, PLANNER_SYSTEM_PROMPT)
            return _parse_plan(raw)
        except PlannerOutputError as exc:
            last_error = exc
    raise PlannerOutputError(f"Planner failed after {max_retries + 1} attempts: {last_error}") from last_error
