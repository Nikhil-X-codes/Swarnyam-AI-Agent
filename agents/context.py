"""Parallel context-gathering stage: merges Researcher, Repo Context, and Architect outputs."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, Field

try:
    from agents.architect import create_architectural_design
    from agents.repo_context import summarize_repo_context
    from agents.researcher import conduct_research
except ImportError:
    create_architectural_design = None  # type: ignore
    summarize_repo_context = None  # type: ignore
    conduct_research = None  # type: ignore
from tools.llm import call_llm


class DesignContext(BaseModel):
    research_notes: str = ""
    repo_summary: str = ""
    architecture_guidance: str = ""
    risks: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    is_parallel: bool = True

    def as_prompt_context(self) -> str:
        """Format the merged context for injection into the Coder prompt."""
        risks_text = "\n".join(f"- {r}" for r in self.risks) if self.risks else "None noted."
        return (
            f"### Technical Research Notes (External Docs/Best Practices):\n"
            f"{self.research_notes}\n\n"
            f"### Repository Context & Established Patterns:\n"
            f"{self.repo_summary}\n\n"
            f"### Architectural Design & Module Boundaries:\n"
            f"{self.architecture_guidance}\n\n"
            f"### Architectural Risks to Avoid:\n"
            f"{risks_text}"
        )


def gather_design_context(
    task: str,
    plan: Any = None,
    raw_chunks: list[dict[str, Any]] | str = "",
    *,
    parallel: bool = True,
    llm: Any = call_llm,
    search_fn: Any = None,
) -> DesignContext:
    """Run Researcher, Repo Context, and Architect agents either concurrently or sequentially."""
    if create_architectural_design is None or summarize_repo_context is None or conduct_research is None:
        raw_text = str(raw_chunks) if isinstance(raw_chunks, str) else ""
        return DesignContext(
            research_notes="",
            repo_summary=raw_text,
            architecture_guidance="",
            risks=[],
            elapsed_seconds=0.0,
            is_parallel=False,
        )

    started = time.perf_counter()

    kwargs_research = {"llm": llm}
    if search_fn is not None:
        kwargs_research["search_fn"] = search_fn

    if parallel:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_research = executor.submit(conduct_research, task, plan, **kwargs_research)
            future_repo = executor.submit(summarize_repo_context, task, raw_chunks, llm=llm)
            future_architect = executor.submit(create_architectural_design, task, plan, "", llm=llm)

            research_notes = future_research.result()
            repo_summary = future_repo.result()
            architect_out = future_architect.result()
    else:
        research_notes = conduct_research(task, plan, **kwargs_research)
        repo_summary = summarize_repo_context(task, raw_chunks, llm=llm)
        architect_out = create_architectural_design(task, plan, repo_summary, llm=llm)

    elapsed = round(time.perf_counter() - started, 3)

    return DesignContext(
        research_notes=research_notes,
        repo_summary=repo_summary,
        architecture_guidance=architect_out.design_approach,
        risks=architect_out.risks,
        elapsed_seconds=elapsed,
        is_parallel=parallel,
    )
