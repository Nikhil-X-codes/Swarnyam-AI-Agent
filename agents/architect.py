"""Architect agent: proposes module boundaries, design approach, and flags risks."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from tools.llm import call_llm


class ArchitectOutput(BaseModel):
    design_approach: str
    module_boundaries: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


ARCHITECT_SYSTEM_PROMPT = """You are the Architect agent in an AI coding swarm.
Given the coding task and plan, analyze the system architecture and return only valid JSON with this shape:
{
  "design_approach": "concise description of the recommended architecture and interfaces",
  "module_boundaries": ["file or module boundary guidelines..."],
  "risks": ["potential architectural, compatibility, or coupling risks..."]
}
Never include markdown fences or prose outside JSON. Be practical and focused."""


def create_architectural_design(
    task: str,
    plan: Any = None,
    repo_summary: str = "",
    *,
    llm: Any = call_llm,
) -> ArchitectOutput:
    """Analyze architecture and propose boundaries and risk mitigations."""
    from memory.logger import set_agent_context
    set_agent_context("architect")
    plan_text = json.dumps(plan.model_dump() if hasattr(plan, "model_dump") else plan) if plan else "None"
    prompt = (
        f"Task: {task}\n"
        f"Plan Context: {plan_text}\n"
        f"Existing Codebase Patterns:\n{repo_summary}\n\n"
        "Evaluate the design approach, module boundaries, and architectural risks. Return JSON only."
    )

    raw, _ = llm(prompt, ARCHITECT_SYSTEM_PROMPT)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```").removeprefix("json").removesuffix("```").strip()

    try:
        data = json.loads(raw)
        return ArchitectOutput.model_validate(data)
    except Exception:
        # Fallback if raw response did not validate strictly as JSON
        return ArchitectOutput(
            design_approach=raw,
            module_boundaries=["Keep changes modular and localized"],
            risks=["Ensure backward compatibility with existing tests"],
        )


analyze_architecture = create_architectural_design
