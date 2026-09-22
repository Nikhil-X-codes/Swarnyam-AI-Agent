from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
_global_active_run_id: str | None = None
_current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="unknown")


def set_run_context(run_id: str | None) -> None:
    global _global_active_run_id
    _global_active_run_id = run_id
    _current_run_id.set(run_id)


def get_run_context() -> str | None:
    val = _current_run_id.get()
    return val if val is not None else _global_active_run_id


def set_agent_context(agent_name: str) -> None:
    _current_agent_name.set(agent_name)


def get_agent_context() -> str:
    return _current_agent_name.get()


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "swarm_log.db"


class SwarmLogger:
    """SQLite logger for swarm agent observability, traces, and cost reports."""

    _instance: SwarmLogger | None = None

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            env_path = os.getenv("SWARM_DB_PATH")
            self.db_path = Path(env_path) if env_path else DEFAULT_DB_PATH
        else:
            self.db_path = Path(db_path) if str(db_path) != ":memory:" else ":memory:"

        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn_str = str(self.db_path)
        else:
            self._db_conn_str = ":memory:"

        self._init_db()

    @classmethod
    def get_instance(cls, db_path: Path | str | None = None) -> SwarmLogger:
        if cls._instance is None or db_path is not None:
            cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self._db_conn_str)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    task TEXT NOT NULL,
                    repo TEXT DEFAULT '',
                    status TEXT DEFAULT 'started',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    total_cost_usd REAL DEFAULT 0.0,
                    total_latency_ms REAL DEFAULT 0.0,
                    summary TEXT DEFAULT ''
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    session_id TEXT NOT NULL,
                    agent_name TEXT DEFAULT 'unknown',
                    prompt TEXT,
                    response TEXT,
                    system_prompt TEXT,
                    backend TEXT DEFAULT 'unknown',
                    model TEXT DEFAULT '',
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0.0,
                    latency_ms REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'success',
                    error_type TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_run_id ON llm_calls(run_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_agent ON llm_calls(agent_name)"
            )
            conn.commit()

    def start_run(self, command: str, task: str, repo: str = "") -> str:
        """Start and record a new run, returning its run_id and setting run context."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO runs (run_id, command, task, repo, status, started_at)
                VALUES (?, ?, ?, ?, 'started', ?)
                """,
                (run_id, command, task, str(repo), now),
            )
            conn.commit()
        set_run_context(run_id)
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        summary: str = "",
        cost: float | None = None,
        latency: float | None = None,
    ) -> None:
        """Finalize a run with its status, totals, and summary."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Calculate aggregations from llm_calls if not explicitly provided
            if cost is None or latency is None:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(estimated_cost_usd), 0.0), COALESCE(SUM(latency_ms), 0.0)
                    FROM llm_calls WHERE run_id = ?
                    """,
                    (run_id,),
                )
                row = cursor.fetchone()
                calc_cost, calc_latency = row[0], row[1]
                if cost is None:
                    cost = calc_cost
                if latency is None:
                    latency = calc_latency

            cursor.execute(
                """
                UPDATE runs
                SET status = ?, finished_at = ?, total_cost_usd = ?, total_latency_ms = ?, summary = ?
                WHERE run_id = ?
                """,
                (status, now, cost, latency, summary, run_id),
            )
            conn.commit()

        if get_run_context() == run_id:
            set_run_context(None)

    def log_call(
        self,
        usage: Any,
        prompt: str = "",
        response: str = "",
        system_prompt: str = "",
        agent_name: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Record an LLM call to SQLite."""
        target_run_id = run_id or get_run_context()
        target_agent = agent_name or get_agent_context()

        # Prompt/response full vs truncated check
        store_full = os.getenv("SWARM_LOG_FULL_TEXT", "true").lower() in ("true", "1")
        logged_prompt = prompt if store_full else prompt[:500]
        logged_response = response if store_full else response[:500]
        logged_system = system_prompt if store_full else system_prompt[:500]

        # Extract values from usage (either LLMUsage object or dict)
        if hasattr(usage, "session_id"):
            session_id = usage.session_id
            backend = getattr(usage, "backend", "unknown")
            model = getattr(usage, "model", "")
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)
            cost = getattr(usage, "estimated_cost_usd", 0.0)
            latency = getattr(usage, "latency_ms", 0.0)
            status = getattr(usage, "status", "success")
            error_type = getattr(usage, "error_type", None)
            timestamp = getattr(usage, "timestamp", datetime.now(timezone.utc).isoformat())
        elif isinstance(usage, dict):
            session_id = usage.get("session_id", str(uuid.uuid4()))
            backend = usage.get("backend", "unknown")
            model = usage.get("model", "")
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = usage.get("estimated_cost_usd", 0.0)
            latency = usage.get("latency_ms", 0.0)
            status = usage.get("status", "success")
            error_type = usage.get("error_type")
            timestamp = usage.get("timestamp", datetime.now(timezone.utc).isoformat())
        else:
            session_id = str(uuid.uuid4())
            backend = "unknown"
            model = ""
            input_tokens = 0
            output_tokens = 0
            cost = 0.0
            latency = 0.0
            status = "unknown"
            error_type = None
            timestamp = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO llm_calls (
                    run_id, session_id, agent_name, prompt, response, system_prompt,
                    backend, model, input_tokens, output_tokens, estimated_cost_usd,
                    latency_ms, status, error_type, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_run_id,
                    session_id,
                    target_agent,
                    logged_prompt,
                    logged_response,
                    logged_system,
                    backend,
                    model,
                    input_tokens,
                    output_tokens,
                    cost,
                    latency,
                    status,
                    error_type,
                    timestamp,
                ),
            )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch run summary by run_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_last_run(self) -> dict[str, Any] | None:
        """Fetch the most recent run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_run_steps(self, run_id: str) -> list[dict[str, Any]]:
        """Get all LLM calls associated with a run in chronological order."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, run_id, session_id, agent_name, backend, model,
                       input_tokens, output_tokens, estimated_cost_usd, latency_ms,
                       status, error_type, timestamp,
                       SUBSTR(prompt, 1, 100) AS prompt_preview,
                       SUBSTR(response, 1, 100) AS response_preview,
                       prompt, response, system_prompt
                FROM llm_calls
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent runs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def cost_report(self) -> dict[str, Any]:
        """Aggregate total spend, spend per agent role, and spend per backend."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Grand totals
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS total_cost,
                    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                    COALESCE(SUM(latency_ms), 0.0) AS total_latency,
                    COUNT(*) AS total_calls
                FROM llm_calls
                """
            )
            grand = dict(cursor.fetchone())

            # Per agent
            cursor.execute(
                """
                SELECT
                    agent_name,
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS cost,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COUNT(*) AS calls,
                    COALESCE(SUM(latency_ms), 0.0) AS latency
                FROM llm_calls
                GROUP BY agent_name
                ORDER BY cost DESC
                """
            )
            by_agent = {row["agent_name"]: dict(row) for row in cursor.fetchall()}

            # Per backend
            cursor.execute(
                """
                SELECT
                    backend,
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS cost,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COUNT(*) AS calls,
                    COALESCE(SUM(latency_ms), 0.0) AS latency
                FROM llm_calls
                GROUP BY backend
                ORDER BY cost DESC
                """
            )
            by_backend = {row["backend"]: dict(row) for row in cursor.fetchall()}

            # Recent runs list
            cursor.execute(
                """
                SELECT run_id, command, task, status, total_cost_usd, total_latency_ms, started_at
                FROM runs
                ORDER BY started_at DESC
                LIMIT 20
                """
            )
            recent_runs = [dict(row) for row in cursor.fetchall()]

            return {
                "grand_total": grand,
                "by_agent": by_agent,
                "by_backend": by_backend,
                "recent_runs": recent_runs,
            }
