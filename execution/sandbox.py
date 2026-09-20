"""Sandbox copy management and constrained unified-diff application."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from unidiff import PatchSet

from execution.guardrails import GuardrailResult, scan_diff


class DiffApplyError(RuntimeError):
    """Raised when a diff cannot be safely applied."""

def create_sandbox(repo_path: str | Path, root: str | Path | None = None) -> Path:
    source = Path(repo_path).resolve()
    if not source.is_dir():
        raise ValueError(f"Repository does not exist: {source}")
    destination = Path(root) if root else Path(tempfile.mkdtemp(prefix="agent-swarm-sandbox-"))
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination

def _apply_file_patch(sandbox: Path, patched_file) -> None:
    path = patched_file.path
    if path.startswith(("a/", "b/")):
        path = path[2:]
    target = (sandbox / path).resolve()
    if sandbox not in target.parents and target != sandbox:
        raise DiffApplyError(f"patch path escapes sandbox: {path}")
    if patched_file.is_removed_file:
        if target.exists():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    lines = original.splitlines(keepends=True)
    offset = 0
    for hunk in patched_file:
        old_start = max(0, hunk.source_start - 1 + offset)
        cursor = old_start
        replacement = []
        for line in hunk:
            value = line.value
            if line.is_context:
                if cursor >= len(lines) or lines[cursor].rstrip("\r\n") != value.rstrip("\r\n"):
                    raise DiffApplyError(f"context mismatch while patching {path}")
                replacement.append(lines[cursor])
                cursor += 1
            elif line.is_removed:
                if cursor >= len(lines) or lines[cursor].rstrip("\r\n") != value.rstrip("\r\n"):
                    raise DiffApplyError(f"removed line mismatch while patching {path}")
                cursor += 1
            elif line.is_added:
                replacement.append(value)
        lines[old_start:cursor] = replacement
        offset += len(replacement) - (cursor - old_start)
    target.write_text("".join(lines), encoding="utf-8")

def apply_diff(sandbox: str | Path, diff_text: str) -> GuardrailResult:
    result = scan_diff(diff_text)
    if not result.passed:
        raise DiffApplyError("guardrail rejected diff: " + "; ".join(result.reasons))
    try:
        patches = PatchSet(diff_text.splitlines(keepends=True))
        if not patches:
            raise DiffApplyError("diff contains no file patches")
        for patched_file in patches:
            _apply_file_patch(Path(sandbox).resolve(), patched_file)
    except DiffApplyError:
        raise
    except Exception as exc:
        raise DiffApplyError(f"could not apply diff: {exc}") from exc
    return result
