"""Repo Context agent: summarizes retrieved ChromaDB chunks into a digestible form."""

from __future__ import annotations

from typing import Any

from tools.llm import call_llm

REPO_CONTEXT_SYSTEM_PROMPT = (
    "You are the Repo Context agent in an AI coding swarm. "
    "Given a coding task and raw code chunks retrieved from the target repository, "
    "summarize the codebase conventions, existing function/class patterns, "
    "typical imports, type-annotation styles, and module boundaries. "
    "Provide a concise, digestible brief to help the Coder integrate changes seamlessly."
)


def _budget_and_deduplicate_chunks(
    retrieved_chunks: list[dict[str, Any]] | str,
    max_chars: int = 12000,
) -> str:
    """Format, deduplicate, and budget code chunks within token boundaries."""
    if isinstance(retrieved_chunks, str):
        text = retrieved_chunks.strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n... [truncated for token budget]"
        return text

    seen_signatures: set[tuple[str, str]] = set()
    formatted_chunks: list[str] = []
    current_length = 0

    for chunk in retrieved_chunks:
        filepath = chunk.get("metadata", {}).get("filepath", "unknown")
        raw_text = (chunk.get("text") or "").strip()
        if not raw_text:
            continue

        # Deduplicate based on filepath and the first 80 characters of content
        dedup_key = (filepath, raw_text[:80])
        if dedup_key in seen_signatures:
            continue
        seen_signatures.add(dedup_key)

        block = f"File: {filepath}\n{raw_text}"
        block_len = len(block) + 8  # including separator overhead

        if current_length + block_len > max_chars:
            remaining = max(0, max_chars - current_length - 35)
            if remaining > 10:
                formatted_chunks.append(block[:remaining] + "\n... [truncated for token budget]")
            else:
                formatted_chunks.append(block[:max(20, max_chars)] + "\n... [truncated for token budget]")
            break

        formatted_chunks.append(block)
        current_length += block_len

    return "\n\n---\n\n".join(formatted_chunks)


def summarize_repo_context(
    task: str,
    retrieved_chunks: list[dict[str, Any]] | str,
    *,
    max_chars: int = 12000,
    llm: Any = call_llm,
) -> str:
    """Turn raw retrieved code chunks into a structured digest of repository patterns with token budgeting."""
    from memory.logger import set_agent_context
    set_agent_context("repo_context")

    chunk_text = _budget_and_deduplicate_chunks(retrieved_chunks, max_chars=max_chars)

    if not chunk_text.strip():
        chunk_text = "No prior code chunks retrieved from repository."

    prompt = (
        f"Coding Task: {task}\n\n"
        f"Retrieved Code Chunks from Repository:\n{chunk_text}\n\n"
        "Provide a concise summary of existing patterns, conventions, signatures, and relevant modules."
    )

    summary, _ = llm(prompt, REPO_CONTEXT_SYSTEM_PROMPT)
    return summary.strip()
