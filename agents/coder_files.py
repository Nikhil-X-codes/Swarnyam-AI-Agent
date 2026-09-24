"""File-content Coder implementation with deterministic diff generation."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from unidiff import PatchSet

from tools.llm import call_llm


class CoderOutput(BaseModel):
    files: dict[str, str]
    diff: str = ""
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    touched_files: list[str] = Field(default_factory=list)


class CoderOutputError(ValueError):
    """Raised when the Coder returns invalid file content."""


CODER_SYSTEM_PROMPT = """You are the Coder agent. Return only valid JSON with this shape:
{"files":{"file.py":"complete new file content"},"summary":"...","confidence":0.0,"touched_files":["file.py"]}
IMPORTANT: Ensure JSON string values are valid and escaped (use \\n for line breaks, \\\" for inner double quotes, or single quotes for Python strings).
Return complete contents for every changed or new file in the files object.
Use exact repository-relative POSIX paths matching existing files or standard locations (do not invent nested directories like src/ if files are located at repository root).
Put production changes and tests in the correct separate files. Preserve all existing behavior and existing tests unless
the task explicitly asks for a behavior change. Treat every concrete noun,
function name, argument, value, type annotation, and test assertion in the task
as a required acceptance item. Before returning JSON, mentally check that every
requested item is present in the returned complete file contents.

Code Style & Linting Rules:
- Clean imports: Standard library first, a single blank line, then local imports.
- Never include unused imports (e.g. do not import pytest if using plain assert statements).
- Never use leading slashes or irregular spacing in imports.
- Do not include obsolete headers like `# -*- coding: utf-8 -*-`.

Never return a diff, Markdown fences, or prose outside JSON. The application computes the
unified diff deterministically."""


def _repair_truncated_json(s: str) -> str:
    """Repair truncated JSON caused by token limits or unclosed string quotes/braces."""
    s = s.strip()
    in_str = False
    esc = False
    braces = 0
    for ch in s:
        if ch == '"' and not esc:
            in_str = not in_str
        elif ch == '\\' and in_str:
            esc = not esc
            continue
        elif not in_str:
            if ch == '{':
                braces += 1
            elif ch == '}':
                braces = max(0, braces - 1)
        esc = False
    if in_str:
        s += '"'
    s += '}' * braces
    return s


def _parse_files(raw: str) -> tuple[dict[str, str], str, float, list[str]]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()

    data = None
    last_exc: Exception | None = None

    # 1. Direct parse with strict=False (allows unescaped control characters in multi-line code)
    try:
        data = json.loads(value, strict=False)
    except Exception as exc:
        last_exc = exc

    # 2. Extract outermost JSON object block
    match = None
    if data is None:
        match = re.search(r"(\{[\s\S]*\})", value)
        if match:
            try:
                data = json.loads(match.group(1), strict=False)
            except Exception as exc:
                last_exc = exc

    # 3. Heal truncated strings or unclosed braces
    if data is None:
        candidate = match.group(1) if match else value
        repaired = _repair_truncated_json(candidate)
        try:
            data = json.loads(repaired, strict=False)
        except Exception as exc:
            last_exc = exc

    if not isinstance(data, dict):
        raise CoderOutputError(f"Coder returned invalid file JSON: {last_exc or raw[:200]}")

    try:
        files = data["files"]
        summary = data.get("summary", "Automated code changes")
        confidence = float(data.get("confidence", 0.9))
        touched_files = data.get("touched_files", list(files))
        if not isinstance(files, dict) or not files or not all(isinstance(path, str) and isinstance(content, str) for path, content in files.items()):
            raise CoderOutputError("files must be a non-empty path-to-content object")
        for path in files:
            path_obj = Path(path)
            if path_obj.is_absolute() or ".." in path_obj.parts or path.startswith(("~", "\\")):
                raise CoderOutputError(f"file path escapes repository: {path}")
        if not isinstance(touched_files, list) or set(touched_files) != set(files):
            touched_files = list(files)
        return files, summary, confidence, touched_files
    except (KeyError, TypeError) as exc:
        raise CoderOutputError(f"Coder returned invalid file JSON: {exc}") from exc


def _generate_diff(files: dict[str, str], base_path: str | Path | None) -> str:
    root = Path(base_path).resolve() if base_path else None
    chunks = []
    for path, new_content in files.items():
        old_content = None
        if root:
            target = (root / path).resolve()
            if root not in target.parents:
                raise CoderOutputError(f"file path escapes repository: {path}")
            if target.exists():
                old_content = target.read_text(encoding="utf-8")
        old_lines = [] if old_content is None else old_content.splitlines(keepends=True)
        fromfile = "/dev/null" if old_content is None else f"a/{path}"
        diff_lines = difflib.unified_diff(
            old_lines,
            new_content.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=f"b/{path}",
            lineterm="\n",
        )
        chunks.append("".join(diff_lines))
    full_diff = "".join(chunk for chunk in chunks if chunk)
    try:
        patches = PatchSet(full_diff.splitlines(keepends=True))
    except Exception as exc:
        raise CoderOutputError(f"locally generated diff failed validation: {exc}") from exc
    if not patches or not any(len(patch) for patch in patches):
        raise CoderOutputError("locally generated diff contains no hunks")
    return full_diff


def create_code_change(task: str, plan: Any, context: str = "", *, base_path: str | Path | None = None, max_retries: int = 2, llm: Any = call_llm) -> CoderOutput:
    try:
        from memory.logger import set_agent_context
        set_agent_context("coder")
    except ImportError:
        pass
    plan_data = plan.model_dump() if hasattr(plan, "model_dump") else plan

    repo_files: list[str] = []
    if base_path:
        root = Path(base_path).resolve()
        if root.is_dir():
            for p in root.rglob("*"):
                if p.is_file() and not any(part.startswith(".") or part in {"__pycache__", "work", "chroma_db"} for part in p.parts):
                    try:
                        repo_files.append(p.relative_to(root).as_posix())
                    except ValueError:
                        pass
    files_info = ""
    if repo_files:
        files_info = f"\nExisting repository files:\n" + "\n".join(f"- {f}" for f in sorted(repo_files)) + "\n"

    base_prompt = (
        "Implement the task exactly, including all requested production changes "
        "and tests. Do not stop after making the code compile or after making "
        "existing tests pass.\n"
        f"Task: {task}\n"
        f"Plan and acceptance criteria: {json.dumps(plan_data)}\n"
        "Required completion checklist: every detail in the Task and every "
        "acceptance criterion must be reflected in the returned files; preserve "
        "unrelated existing content; put tests under tests/.\n"
        f"{files_info}"
        f"Repository context:\n{context}"
    )
    last_error = None
    for _ in range(max_retries + 1):
        prompt = base_prompt
        if last_error:
            prompt += f"\nIMPORTANT: Previous output was rejected: {last_error}\nReturn complete file contents in the files object."
        try:
            raw, _ = llm(prompt, CODER_SYSTEM_PROMPT)
            files, summary, confidence, touched_files = _parse_files(raw)
            diff = _generate_diff(files, base_path)
            return CoderOutput(files=files, diff=diff, summary=summary, confidence=confidence, touched_files=touched_files)
        except CoderOutputError as exc:
            last_error = str(exc)
    raise CoderOutputError(f"Coder failed after {max_retries + 1} attempts: {last_error}") from None
