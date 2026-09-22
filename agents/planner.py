"""Phase 2 Planner agent: task text to validated structured JSON."""

from __future__ import annotations

import json
import re
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
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()

    # 1. Direct JSON parse
    try:
        return Plan.model_validate(json.loads(candidate))
    except Exception:
        pass

    # 2. Tolerant JSON block extraction across conversational prose
    json_match = re.search(r"(\{[\s\S]*\})", candidate)
    if json_match:
        try:
            return Plan.model_validate(json.loads(json_match.group(1)))
        except (json.JSONDecodeError, ValidationError, TypeError):
            pass

    raise PlannerOutputError(f"Planner returned invalid JSON: {raw[:200]}")


def create_plan(task: str, *, max_retries: int = 3, llm: Any = call_llm) -> Plan:
    """Generate and validate a plan, retrying malformed model output."""
    from memory.logger import set_agent_context
    set_agent_context("planner")

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
