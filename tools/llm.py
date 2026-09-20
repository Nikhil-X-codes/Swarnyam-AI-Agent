"""Centralized Groq LLM access with bounded retries and local telemetry."""

from __future__ import annotations

import atexit
import json
import os
import random
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from groq import APIConnectionError, APITimeoutError, Groq, RateLimitError

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF_SECONDS = 0.5
_last_request_at = 0.0
_request_events: list[float] = []


class LLMInputError(ValueError):
    """Raised when a caller provides an invalid prompt."""


class LLMCallError(RuntimeError):
    """Raised after the bounded retry policy is exhausted."""


@dataclass(frozen=True)
class LLMUsage:
    session_id: str
    timestamp: str
    model: str
    prompt_chars: int
    response_chars: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: float
    attempts: int
    status: str
    error_type: str | None = None
    backend: str = "groq"
    agent_name: str = "unknown"


def _number_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _log_path() -> Path:
    path = Path(os.getenv("LLM_USAGE_LOG", "memory/llm_usage.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    input_rate = _number_env("GROQ_INPUT_COST_PER_MILLION", 0.0)
    output_rate = _number_env("GROQ_OUTPUT_COST_PER_MILLION", 0.0)
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 8)


def _write_usage(
    usage: LLMUsage,
    prompt: str = "",
    response: str = "",
    system_prompt: str = "",
    agent_name: str | None = None,
) -> None:
    record = json.dumps(asdict(usage), sort_keys=True) + "\n"
    try:
        with _log_path().open("a", encoding="utf-8") as handle:
            handle.write(record)
    except PermissionError:
        # Some managed checkouts allow source edits but deny runtime writes.
        # Keep telemetry local and auditable rather than dropping it.
        fallback = Path(tempfile.gettempdir()) / "agent-swarm" / "llm_usage.jsonl"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with fallback.open("a", encoding="utf-8") as handle:
            handle.write(record)

    # SQLite auto-logging
    try:
        from memory.logger import SwarmLogger, get_agent_context
        eff_agent = agent_name or getattr(usage, "agent_name", None) or get_agent_context()
        SwarmLogger.get_instance().log_call(
            usage=usage,
            prompt=prompt,
            response=response,
            system_prompt=system_prompt,
            agent_name=eff_agent,
        )
    except Exception:
        pass


_pace_lock = threading.Lock()


def _pace_groq_request() -> None:
    """Stay within the configured rolling Groq request budget in a thread-safe manner."""
    global _last_request_at
    request_limit = int(_number_env("GROQ_REQUESTS_PER_MINUTE", 30))
    min_interval = _number_env("GROQ_MIN_REQUEST_INTERVAL_SECONDS", 2.1)
    while True:
        with _pace_lock:
            now = time.perf_counter()
            _request_events[:] = [timestamp for timestamp in _request_events if now - timestamp < 60]
            wait_for = max(0.0, min_interval - (now - _last_request_at))
            if len(_request_events) >= request_limit:
                wait_for = max(wait_for, 60 - (now - _request_events[0]))
            if wait_for <= 0:
                _last_request_at = time.perf_counter()
                _request_events.append(_last_request_at)
                return
        time.sleep(wait_for)


def _call_openrouter(
    prompt: str,
    system_prompt: str,
    *,
    model: str,
    timeout: float,
    max_output_tokens: int,
) -> tuple[str, int, int]:
    """Call OpenRouter's OpenAI-compatible endpoint as a bounded fallback."""

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMCallError("OPENROUTER_API_KEY is not configured")
    payload = json.dumps({
        "model": os.getenv("OPENROUTER_MODEL", model),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_output_tokens,
        "temperature": _number_env("LLM_TEMPERATURE", 0.0),
    }).encode("utf-8")
    request = Request(
        os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"),
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost"),
            "X-Title": "AI Coding Agent Swarm",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 429 or exc.code >= 500:
            raise APIConnectionError(f"OpenRouter transient HTTP {exc.code}: {detail}") from exc
        raise LLMCallError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise APIConnectionError(f"OpenRouter connection failed: {exc}") from exc
    choices = data.get("choices") or []
    if not choices:
        raise LLMCallError(f"OpenRouter returned no choices: {data}")
    message = choices[0].get("message", {}).get("content") or ""
    usage = data.get("usage") or {}
    return message, int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)


_local_server_process: subprocess.Popen | None = None


def _stop_local_server() -> None:
    """Terminate the background llama-server process on exit."""
    global _local_server_process
    if _local_server_process is not None:
        try:
            _local_server_process.terminate()
            _local_server_process.wait(timeout=2.0)
        except Exception:
            try:
                _local_server_process.kill()
            except Exception:
                pass
        _local_server_process = None


atexit.register(_stop_local_server)


def _is_server_healthy(url: str, timeout: float = 0.8) -> bool:
    """Check if the local llama-server health endpoint responds."""
    try:
        health_url = url.replace("/v1/chat/completions", "/health")
        if not health_url.endswith("/health"):
            health_url = "http://127.0.0.1:8080/health"
        req = Request(health_url, headers={"User-Agent": "agent-swarm"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_local_server(timeout: float = 8.0) -> str:
    """Ensure local llama-server is running and return its chat completions endpoint."""
    global _local_server_process
    chat_url = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
    if _is_server_healthy(chat_url):
        return chat_url

    model_path = Path(os.getenv("LOCAL_MODEL_PATH", "models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"))
    if not model_path.exists():
        raise LLMCallError(f"Local model not found at {model_path}")

    server_bin = Path(os.getenv("LLAMA_SERVER_BIN", "models/bin/llama-server.exe"))
    if not server_bin.exists():
        server_bin = Path("models/bin/llama-server.exe")

    if server_bin.exists():
        try:
            cmd = [
                str(server_bin.resolve()),
                "-m", str(model_path.resolve()),
                "--port", "8080",
                "--host", "127.0.0.1",
                "-np", "4",
                "-c", "8192",
                "--no-warmup",
            ]
            _local_server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.perf_counter() + timeout
            while time.perf_counter() < deadline:
                if _is_server_healthy(chat_url, timeout=0.4):
                    return chat_url
                time.sleep(0.25)
        except Exception:
            _stop_local_server()

    return chat_url


def _call_local_llama_cli(
    prompt: str,
    system_prompt: str,
    *,
    model_path: Path,
    timeout: float = 180.0,
    max_tokens: int = 1024,
) -> tuple[str, int, int]:
    """Fallback runner using llama-cli.exe directly when server is unavailable."""
    cli_bin = Path(os.getenv("LLAMA_CPP_BIN", "models/bin/llama-cli.exe"))
    if not cli_bin.exists():
        raise LLMCallError(f"Local llama CLI not found at {cli_bin}")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tf:
        tf.write(prompt)
        prompt_file = tf.name

    try:
        cmd = [
            str(cli_bin.resolve()),
            "-m", str(model_path.resolve()),
            "-sys", system_prompt,
            "-f", prompt_file,
            "-n", str(max_tokens),
            "--temp", "0.0",
            "-c", "4096",
            "-no-cnv",
            "--no-display-prompt",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = proc.stdout.strip()
        in_tokens = max(1, len(prompt) // 4)
        out_tokens = max(1, len(output) // 4)
        return output, in_tokens, out_tokens
    finally:
        try:
            os.remove(prompt_file)
        except OSError:
            pass


_local_llama_instance: Any = None
_local_llama_lock = threading.Lock()


def get_local_model(model_path: Path | None = None) -> Any:
    """Load local llama-cpp model once globally and reuse across calls."""
    global _local_llama_instance
    if _local_llama_instance is None:
        with _local_llama_lock:
            if _local_llama_instance is None:
                target_path = model_path or Path(os.getenv("LOCAL_MODEL_PATH", "models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"))
                if not target_path.exists():
                    raise LLMCallError(f"Local model not found at {target_path}")
                try:
                    from llama_cpp import Llama

                    threads = max(1, min(os.cpu_count() or 4, 8))
                    _local_llama_instance = Llama(
                        model_path=str(target_path.resolve()),
                        n_ctx=int(_number_env("LOCAL_LLM_CTX", 4096)),
                        n_threads=threads,
                        verbose=False,
                    )
                except Exception as exc:
                    _local_llama_instance = None
                    raise LLMCallError(f"Failed to initialize llama-cpp-python: {exc}") from exc
    return _local_llama_instance


def call_local_llm(
    prompt: str,
    system_prompt: str = "You are a helpful coding assistant.",
    *,
    timeout: float = 180.0,
    max_output_tokens: int = 1024,
    model_path: Path | None = None,
) -> tuple[str, int, int]:
    """Execute local LLM inference using a persistent llama-cpp instance,
    falling back to local server or CLI if needed."""
    target_path = model_path or Path(os.getenv("LOCAL_MODEL_PATH", "models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"))
    if not target_path.exists():
        raise LLMCallError(f"Local model not found at {target_path}")

    local_timeout = max(timeout, _number_env("LOCAL_LLM_TIMEOUT_SECONDS", 180.0))

    # 1. Primary path: persistent in-process llama_cpp model
    try:
        model = get_local_model(target_path)
        with _local_llama_lock:
            response = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_output_tokens,
                temperature=_number_env("LLM_TEMPERATURE", 0.0),
            )
        choices = response.get("choices") or []
        if choices:
            msg = choices[0].get("message", {}).get("content") or ""
            usage = response.get("usage") or {}
            in_toks = int(usage.get("prompt_tokens", 0) or 0)
            out_toks = int(usage.get("completion_tokens", 0) or 0)
            return msg, in_toks, out_toks
    except Exception:
        pass

    # 2. Secondary path: local server
    try:
        chat_url = _ensure_local_server(timeout=10.0)
        if _is_server_healthy(chat_url, timeout=0.5):
            payload = json.dumps({
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_output_tokens,
                "temperature": _number_env("LLM_TEMPERATURE", 0.0),
            }).encode("utf-8")
            req = Request(chat_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(req, timeout=local_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices") or []
            if choices:
                msg = choices[0].get("message", {}).get("content") or ""
                usage = data.get("usage") or {}
                return msg, int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
    except Exception:
        pass

    # 3. Direct CLI invocation fallback
    return _call_local_llama_cli(
        prompt,
        system_prompt,
        model_path=target_path,
        timeout=local_timeout,
        max_tokens=max_output_tokens,
    )


def _call_local_llama(
    prompt: str,
    system_prompt: str,
    *,
    timeout: float = 180.0,
    max_output_tokens: int = 1024,
) -> tuple[str, int, int]:
    """Route internal caller to call_local_llm."""
    return call_local_llm(
        prompt,
        system_prompt,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
    )


_global_groq_client: Groq | None = None
_groq_client_lock = threading.Lock()


def _get_groq_client(timeout: float) -> Groq:
    """Get or create a reusable Groq client with connection pooling."""
    global _global_groq_client
    if _global_groq_client is None:
        with _groq_client_lock:
            if _global_groq_client is None:
                _global_groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=timeout)
    return _global_groq_client


def _close_groq_client() -> None:
    """Cleanly close the global Groq client and its pooled SSL connections."""
    global _global_groq_client
    if _global_groq_client is not None:
        try:
            _global_groq_client.close()
        except Exception:
            pass
        _global_groq_client = None


atexit.register(_close_groq_client)


def call_llm(
    prompt: str,
    system_prompt: str = "You are a helpful coding assistant.",
    *,
    model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: Any | None = None,
    offline: bool = False,
    agent_name: str | None = None,
) -> tuple[str, LLMUsage]:
    """Call LLM with fallback chain: Groq -> OpenRouter -> Local llama.cpp."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise LLMInputError("prompt must be a non-empty string")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise LLMInputError("system_prompt must be a non-empty string")

    from memory.logger import get_agent_context
    eff_agent = agent_name or get_agent_context()

    load_dotenv()
    is_offline = offline or os.getenv("OFFLINE_MODE", "false").lower() in ("true", "1")
    selected_model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)
    timeout = timeout_seconds or _number_env("LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    retries = _number_env("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES) if max_retries is None else max(0, max_retries)
    retries = int(retries)
    base_backoff = _number_env("LLM_BASE_BACKOFF_SECONDS", DEFAULT_BASE_BACKOFF_SECONDS)
    started = time.perf_counter()
    session_id = str(uuid.uuid4())
    max_output_tokens = int(_number_env("LLM_MAX_OUTPUT_TOKENS", 2040))
    local_timeout = max(timeout, _number_env("LOCAL_LLM_TIMEOUT_SECONDS", 180.0))

    # Explicit offline mode skips all remote APIs
    if is_offline:
        try:
            message, input_tokens, output_tokens = _call_local_llama(
                prompt, system_prompt, timeout=local_timeout, max_output_tokens=max_output_tokens,
            )
            usage = LLMUsage(
                session_id, datetime.now(timezone.utc).isoformat(), "local:qwen2.5-coder-1.5b",
                len(prompt), len(message), input_tokens, output_tokens, 0.0,
                round((time.perf_counter() - started) * 1000, 2), 1, "success",
                backend="local",
                agent_name=eff_agent,
            )
            _write_usage(usage, prompt=prompt, response=message, system_prompt=system_prompt, agent_name=eff_agent)
            return message, usage
        except Exception as exc:
            usage = LLMUsage(
                session_id, datetime.now(timezone.utc).isoformat(), "local:qwen2.5-coder-1.5b",
                len(prompt), 0, 0, 0, 0.0,
                round((time.perf_counter() - started) * 1000, 2), 1, "error", type(exc).__name__,
                backend="local",
                agent_name=eff_agent,
            )
            _write_usage(usage, prompt=prompt, response="", system_prompt=system_prompt, agent_name=eff_agent)
            raise LLMCallError(f"Local offline LLM call failed: {exc}") from exc

    groq_client = client or _get_groq_client(timeout)
    attempts = 0

    try:
        while True:
            attempts += 1
            try:
                if client is None:
                    _pace_groq_request()
                response = groq_client.chat.completions.create(
                    model=selected_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=timeout,
                    max_tokens=max_output_tokens,
                    temperature=_number_env("LLM_TEMPERATURE", 0.0),
                )
                message = response.choices[0].message.content or ""
                usage_data = getattr(response, "usage", None)
                input_tokens = int(getattr(usage_data, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage_data, "completion_tokens", 0) or 0)
                usage = LLMUsage(
                    session_id, datetime.now(timezone.utc).isoformat(), selected_model,
                    len(prompt), len(message), input_tokens, output_tokens,
                    _estimate_cost(input_tokens, output_tokens),
                    round((time.perf_counter() - started) * 1000, 2), attempts, "success",
                    backend="groq",
                    agent_name=eff_agent,
                )
                _write_usage(usage, prompt=prompt, response=message, system_prompt=system_prompt, agent_name=eff_agent)
                return message, usage
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                if attempts > retries:
                    # Cascade 1: Try OpenRouter
                    openrouter_error = None
                    if os.getenv("OPENROUTER_API_KEY"):
                        try:
                            message, input_tokens, output_tokens = _call_openrouter(
                                prompt, system_prompt, model=selected_model,
                                timeout=timeout, max_output_tokens=max_output_tokens,
                            )
                            usage = LLMUsage(
                                session_id, datetime.now(timezone.utc).isoformat(),
                                f"openrouter:{os.getenv('OPENROUTER_MODEL', selected_model)}",
                                len(prompt), len(message), input_tokens, output_tokens,
                                _estimate_cost(input_tokens, output_tokens),
                                round((time.perf_counter() - started) * 1000, 2), attempts, "success",
                                backend="openrouter",
                                agent_name=eff_agent,
                            )
                            _write_usage(usage, prompt=prompt, response=message, system_prompt=system_prompt, agent_name=eff_agent)
                            return message, usage
                        except Exception as fallback_exc:
                            openrouter_error = fallback_exc

                    # Cascade 2: Try Local llama.cpp
                    try:
                        message, input_tokens, output_tokens = _call_local_llama(
                            prompt, system_prompt, timeout=local_timeout, max_output_tokens=max_output_tokens,
                        )
                        usage = LLMUsage(
                            session_id, datetime.now(timezone.utc).isoformat(),
                            "local:qwen2.5-coder-1.5b", len(prompt), len(message),
                            input_tokens, output_tokens, 0.0,
                            round((time.perf_counter() - started) * 1000, 2), attempts, "success",
                            backend="local",
                            agent_name=eff_agent,
                        )
                        _write_usage(usage, prompt=prompt, response=message, system_prompt=system_prompt, agent_name=eff_agent)
                        return message, usage
                    except Exception as local_exc:
                        details = [f"Groq failed ({exc})"]
                        if openrouter_error:
                            details.append(f"OpenRouter failed ({openrouter_error})")
                        details.append(f"Local llama failed ({local_exc})")
                        raise LLMCallError(f"All backends in fallback chain failed: {'; '.join(details)}") from local_exc

                delay = base_backoff * (2 ** (attempts - 1)) + random.uniform(0, base_backoff)
                time.sleep(delay)
            except Exception as exc:
                raise LLMCallError(f"Groq call failed: {exc}") from exc
    except Exception as exc:
        usage = LLMUsage(
            session_id, datetime.now(timezone.utc).isoformat(), selected_model,
            len(prompt), 0, 0, 0, 0.0,
            round((time.perf_counter() - started) * 1000, 2), attempts, "error", type(exc).__name__,
            backend="groq",
            agent_name=eff_agent,
        )
        _write_usage(usage, prompt=prompt, response="", system_prompt=system_prompt, agent_name=eff_agent)
        raise
