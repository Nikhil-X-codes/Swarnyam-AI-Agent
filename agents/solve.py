"""Phase 6 end-to-end 6-agent solve loop with parallel context gathering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agents.coder import CoderOutput, create_code_change
from agents.context import DesignContext, gather_design_context
from agents.debugger import create_debug_plan
from agents.planner import create_plan
from agents.reviewer import ReviewReport, review_sandbox
from execution.guardrails import scan_diff
from execution.sandbox import DiffApplyError, apply_diff, create_sandbox
try:
    from memory.logger import SwarmLogger
except ImportError:
    SwarmLogger = None  # type: ignore

try:
    from memory.report import generate_run_report
except ImportError:
    generate_run_report = None  # type: ignore
from tools.llm import call_llm

_INDEXER_CACHE: dict[str, Any] = {}


@dataclass
class IterationLog:
    attempt: int
    confidence: float | None
    guardrails_passed: bool
    security_sensitive: bool
    review_passed: bool | None
    status: str
    reasons: list[str] = field(default_factory=list)
    debug_feedback: str | None = None


@dataclass
class SolveResult:
    status: str
    attempts: int
    plan: Any
    confidence: float | None
    security_sensitive: bool
    sandbox: str | None
    message: str
    review: ReviewReport | None = None
    iterations: list[IterationLog] = field(default_factory=list)
    design_context: DesignContext | None = None
    diff: str = ""
    run_id: str | None = None
    report_path: str | None = None

    def as_dict(self) -> dict:
        value = asdict(self)
        if self.plan is not None and hasattr(self.plan, "model_dump"):
            value["plan"] = self.plan.model_dump()
        if self.design_context is not None and hasattr(self.design_context, "model_dump"):
            value["design_context"] = self.design_context.model_dump()
        return value


def _context(repo: str | Path, db_path: str | Path, task: str) -> str:
    from rag.indexer import RepoIndexer

    cache_key = str(Path(db_path).resolve())
    indexer = _INDEXER_CACHE.get(cache_key)
    if indexer is None:
        indexer = RepoIndexer(db_path)
        _INDEXER_CACHE[cache_key] = indexer
    if indexer.collection.count() == 0:
        indexer.index_repo(repo)
    return "\n\n".join(row["text"] for row in indexer.retrieve(task, top_n=5))


def solve_task(
    task: str,
    repo: str | Path,
    *,
    db_path: str | Path = "work/chroma-phase3",
    max_attempts: int = 3,
    confidence_threshold: float = 0.7,
    coder_retries: int | None = None,
    parallel_context: bool = True,
    offline: bool = False,
    llm: Any = call_llm,
    work_root: str | Path = "work/sandboxes",
) -> SolveResult:
    import os
    if offline:
        os.environ["OFFLINE_MODE"] = "true"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    eff_work_root = Path(os.getenv("SWARM_WORK_ROOT", str(work_root)))

    logger = SwarmLogger.get_instance() if SwarmLogger is not None else None
    run_id = logger.start_run("solve", task, str(repo)) if logger is not None else None

    plan: Any = None
    design_context: DesignContext | None = None
    iterations: list[IterationLog] = []
    last_coder: CoderOutput | None = None
    last_review: ReviewReport | None = None
    sandbox: Path | None = None

    def _finalize(
        status: str,
        attempts: int,
        confidence: float | None,
        security_sensitive: bool,
        sandbox_path: str | None,
        message: str,
        review: ReviewReport | None = None,
        confidence_flags: list[str] | None = None,
        error_msg: str | None = None,
    ) -> SolveResult:
        if logger is not None and run_id is not None:
            logger.finish_run(run_id, status, summary=message)
        diff_text = last_coder.diff if last_coder else ""
        rev_output = ""
        if review:
            rev_output = f"Tests Output:\n{review.test_output}\nLint Output:\n{review.lint_output}"
        report_path_str = None
        if generate_run_report is not None and logger is not None and run_id is not None:
            try:
                report_file = generate_run_report(
                    run_id,
                    logger=logger,
                    plan=plan,
                    diff=diff_text,
                    review_output=rev_output,
                    confidence=confidence,
                    confidence_flags=confidence_flags,
                    error_message=error_msg,
                )
                report_path_str = str(report_file)
            except Exception:
                report_path_str = None

        return SolveResult(
            status=status,
            attempts=attempts,
            plan=plan,
            confidence=confidence,
            security_sensitive=security_sensitive,
            sandbox=sandbox_path,
            message=message,
            review=review,
            iterations=iterations,
            design_context=design_context,
            diff=diff_text,
            run_id=run_id,
            report_path=report_path_str,
        )

    try:
        # 1. Planner Agent
        plan = create_plan(task, llm=llm)

        # 2. Parallel Context Gathering (Researcher + Repo Context + Architect)
        raw_context = _context(repo, db_path, task)
        design_context = gather_design_context(
            task,
            plan,
            raw_context,
            parallel=parallel_context,
            llm=llm,
        )
        context = design_context.as_prompt_context()

        sandbox = create_sandbox(repo, eff_work_root / "active")
        feedback = ""

        # 3. Coder -> Guardrails -> Reviewer -> Debugger Loop
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                sandbox = create_sandbox(repo, eff_work_root / "active")
            retries = 2 if coder_retries is None else max(0, coder_retries)
            coder = create_code_change(
                task,
                plan,
                f"{context}\n{feedback}".strip(),
                base_path=sandbox,
                max_retries=retries,
                llm=llm,
            )
            last_coder = coder
            guardrails = scan_diff(coder.diff)

            if not guardrails.passed:
                iterations.append(
                    IterationLog(
                        attempt=attempt,
                        confidence=coder.confidence,
                        guardrails_passed=False,
                        security_sensitive=guardrails.security_sensitive,
                        review_passed=None,
                        status="rejected",
                        reasons=guardrails.reasons,
                    )
                )
                return _finalize(
                    "rejected",
                    attempt,
                    coder.confidence,
                    guardrails.security_sensitive,
                    str(sandbox),
                    "; ".join(guardrails.reasons),
                    confidence_flags=guardrails.reasons,
                )

            try:
                apply_diff(sandbox, coder.diff)
            except DiffApplyError as exc:
                iterations.append(
                    IterationLog(
                        attempt=attempt,
                        confidence=coder.confidence,
                        guardrails_passed=True,
                        security_sensitive=guardrails.security_sensitive,
                        review_passed=None,
                        status="rejected",
                        reasons=[str(exc)],
                    )
                )
                return _finalize(
                    "rejected",
                    attempt,
                    coder.confidence,
                    guardrails.security_sensitive,
                    str(sandbox),
                    str(exc),
                )

            last_review = review_sandbox(sandbox)

            if last_review.passed:
                if guardrails.security_sensitive:
                    iterations.append(
                        IterationLog(
                            attempt=attempt,
                            confidence=coder.confidence,
                            guardrails_passed=True,
                            security_sensitive=True,
                            review_passed=True,
                            status="human_review",
                            reasons=["Security-sensitive file touched (requires human authorization)"],
                        )
                    )
                    return _finalize(
                        "human_review",
                        attempt,
                        coder.confidence,
                        True,
                        str(sandbox),
                        "Review passed but changes touch security-sensitive files; routed to human review.",
                        review=last_review,
                        confidence_flags=["Security-sensitive file touched"],
                    )

                if coder.confidence < confidence_threshold:
                    reasons = [f"Coder confidence {coder.confidence} is below threshold {confidence_threshold}"]
                    iterations.append(
                        IterationLog(
                            attempt=attempt,
                            confidence=coder.confidence,
                            guardrails_passed=True,
                            security_sensitive=False,
                            review_passed=True,
                            status="human_review",
                            reasons=reasons,
                        )
                    )
                    return _finalize(
                        "human_review",
                        attempt,
                        coder.confidence,
                        False,
                        str(sandbox),
                        f"Review passed but self-reported confidence ({coder.confidence}) is below threshold ({confidence_threshold}); routed to human review.",
                        review=last_review,
                        confidence_flags=reasons,
                    )

                iterations.append(
                    IterationLog(
                        attempt=attempt,
                        confidence=coder.confidence,
                        guardrails_passed=True,
                        security_sensitive=False,
                        review_passed=True,
                        status="success",
                        reasons=["Tests and lint passed"],
                    )
                )
                return _finalize(
                    "success",
                    attempt,
                    coder.confidence,
                    False,
                    str(sandbox),
                    "Tests and lint passed successfully.",
                    review=last_review,
                )

            # Review failed: generate debug feedback if more attempts remain
            if attempt < max_attempts:
                diagnostic_output = f"Test Failure Output:\n{last_review.test_output}\nLint Output:\n{last_review.lint_output}"
                debug_plan = create_debug_plan(task, diagnostic_output, failed_diff=coder.diff, llm=llm)
                iterations.append(
                    IterationLog(
                        attempt=attempt,
                        confidence=coder.confidence,
                        guardrails_passed=True,
                        security_sensitive=guardrails.security_sensitive,
                        review_passed=False,
                        status="failed_attempt",
                        reasons=["Tests or lint failed"],
                        debug_feedback=debug_plan,
                    )
                )
                feedback = f"\nDebugger Feedback from Attempt {attempt}:\n{debug_plan}\n"
            else:
                iterations.append(
                    IterationLog(
                        attempt=attempt,
                        confidence=coder.confidence,
                        guardrails_passed=True,
                        security_sensitive=guardrails.security_sensitive,
                        review_passed=False,
                        status="failed",
                        reasons=["Tests or lint failed on final attempt"],
                    )
                )

        return _finalize(
            "failed",
            max_attempts,
            last_coder.confidence if last_coder else None,
            False,
            str(sandbox),
            f"Maximum attempts ({max_attempts}) reached without passing review.",
            review=last_review,
        )
    except Exception as exc:
        _finalize(
            "failed",
            len(iterations),
            last_coder.confidence if last_coder else None,
            False,
            str(sandbox) if sandbox else None,
            f"Solve task encountered an error: {exc}",
            review=last_review,
            error_msg=str(exc),
        )
        raise
