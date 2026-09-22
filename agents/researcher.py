"""Researcher agent: uses duckduckgo-search / ddgs to gather relevant documentation and info."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from tools.llm import call_llm

RESEARCHER_SYSTEM_PROMPT = (
    "You are the Researcher agent in an AI coding swarm. "
    "Given a coding task, any related plan details, and web search results, "
    "synthesize the key technical findings into concise, actionable implementation notes for the Coder. "
    "Focus on correct APIs, signatures, common edge cases, and best practices. "
    "Be direct and practical. Do not include fluff."
)


def _format_search_query(task: str, max_words: int = 6) -> str:
    """Format a clean, concise web search query from the task description."""
    cleaned = re.sub(r"[^\w\s-]", " ", task).strip()
    words = cleaned.split()[:max_words]
    query = " ".join(words) if words else task[:40].strip()
    return f"Python {query}" if "python" not in query.lower() else query


_search_cache: dict[str, list[dict[str, str]]] = {}


def _search_web(query: str, max_results: int = 3) -> list[dict[str, str]]:
    """Search web documentation with caching and graceful offline / rate-limit resilience."""
    if os.getenv("OFFLINE_MODE", "false").lower() in ("true", "1"):
        return []

    if query in _search_cache:
        return _search_cache[query]

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # type: ignore

        results = []
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("body", "") or item.get("snippet", ""),
                    "href": item.get("href", ""),
                })
        _search_cache[query] = results
        return results
    except Exception:
        # Graceful fallback if network or search provider is unavailable or rate-limited
        return []


def conduct_research(
    task: str,
    plan: Any = None,
    *,
    search_fn: Any = _search_web,
    llm: Any = call_llm,
) -> str:
    """Gather external documentation and return synthesized research notes."""
    from memory.logger import set_agent_context
    set_agent_context("researcher")
    plan_text = json.dumps(plan.model_dump() if hasattr(plan, "model_dump") else plan) if plan else "None"

    # Formulate clean search query without punctuation noise or paragraph bloat
    search_query = _format_search_query(task)
    raw_results = search_fn(search_query, max_results=3)

    formatted_results = ""
    if raw_results:
        for idx, res in enumerate(raw_results, 1):
            formatted_results += f"[{idx}] {res.get('title')}: {res.get('snippet')}\n"
    else:
        formatted_results = "No external search results retrieved (offline or no matches)."

    prompt = (
        f"Coding Task: {task}\n"
        f"Search Keywords Used: {search_query}\n"
        f"Plan Context: {plan_text}\n\n"
        f"Web Search Results:\n{formatted_results}\n\n"
        "Summarize the relevant syntax, function signatures, library usage, and best practices."
    )

    notes, _ = llm(prompt, RESEARCHER_SYSTEM_PROMPT)
    return notes.strip()
