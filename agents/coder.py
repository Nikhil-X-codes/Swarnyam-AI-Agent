"""Coder agent: structured unified-diff generation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from unidiff import PatchSet

# The production implementation returns complete files and computes diffs locally.
from agents import coder_files as _file_coder
from tools.llm import call_llm


class LegacyCoderOutput(BaseModel):
    diff: str
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    touched_files: list[str] = Field(default_factory=list)


class LegacyCoderOutputError(ValueError):
    """Raised when the Coder returns invalid structured output."""


CODER_SYSTEM_PROMPT = """You are the Coder agent. Return only valid JSON with:
{"diff":"unified diff","summary":"...","confidence":0.0,"touched_files":[]}
The diff must use git unified-diff format with a/ and b/ paths, including real
hunk ranges such as @@ -1,1 +1,1 @@. Put production changes and tests in the
correct separate files. Never include Markdown fences or prose outside JSON.
Make the smallest safe change."""


def _parse_legacy(raw: str) -> LegacyCoderOutput:
    value = raw.strip()
    if value.startswith("```"):
        value = value.removeprefix("```").removeprefix("json").removesuffix("```").strip()
    try:
        output = LegacyCoderOutput.model_validate(json.loads(value))
        patches = PatchSet(output.diff.splitlines(keepends=True))
        if not patches or not any(len(patch) for patch in patches):
            raise LegacyCoderOutputError("Coder diff is not a valid unified diff with hunks")
        return output
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise LegacyCoderOutputError(f"Coder returned invalid JSON: {exc}") from exc


def _legacy_create_code_change(task: str, plan: Any, context: str = "", *, max_retries: int = 2, llm: Any = call_llm) -> LegacyCoderOutput:
    from memory.logger import set_agent_context
    set_agent_context("coder")
    plan_data = plan.model_dump() if hasattr(plan, "model_dump") else plan
    prompt = f"Task: {task}\nPlan: {json.dumps(plan_data)}\nRepository context:\n{context}"
    last_error = None
    for attempt in range(max_retries + 1):
        if attempt:
            prompt += "\nReturn corrected JSON only; the prior response failed schema validation."
        try:
            raw, _ = llm(prompt, CODER_SYSTEM_PROMPT)
            return _parse_legacy(raw)
        except LegacyCoderOutputError as exc:
            last_error = exc
    raise LegacyCoderOutputError(f"Coder failed after {max_retries + 1} attempts: {last_error}") from last_error


CoderOutput = _file_coder.CoderOutput
CoderOutputError = _file_coder.CoderOutputError
create_code_change = _file_coder.create_code_change
