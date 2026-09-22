"""Unit tests for Phase 7 fallback chain and local llama.cpp fallback."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from groq import RateLimitError

import tools.llm as llm_module
from tools.llm import LLMCallError, call_llm, call_local_llm, get_local_model


class FailingGroqClient:
    """Simulates Groq API rate limit or outage."""
    def __init__(self):
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        resp = httpx.Response(429, request=req)
        raise RateLimitError("Simulated Groq 429 RateLimitError", response=resp, body={"error": "rate_limit"})


class SucceedingGroqClient:
    """Simulates standard Groq success."""
    def __init__(self, content: str = "groq response"):
        self.content = content
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        msg = type("Message", (), {"content": self.content})()
        choice = type("Choice", (), {"message": msg})()
        usage = type("Usage", (), {"prompt_tokens": 10, "completion_tokens": 8})()
        return type("Response", (), {"choices": [choice], "usage": usage})()


def test_primary_backend_is_groq(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    client = SucceedingGroqClient()
    resp, usage = call_llm("test prompt", client=client, max_retries=0)
    assert resp == "groq response"
    assert usage.backend == "groq"
    assert usage.model == "openai/gpt-oss-120b"


def test_offline_mode_forces_local_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    with patch("tools.llm._call_local_llama", return_value=("local response", 12, 6)) as mock_local:
        resp, usage = call_llm("test prompt", offline=True)
        assert resp == "local response"
        assert usage.backend == "local"
        assert "local:" in usage.model
        mock_local.assert_called_once()


def test_groq_failure_cascades_to_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-openrouter-key")
    client = FailingGroqClient()

    with patch("tools.llm._call_openrouter", return_value=("openrouter response", 15, 10)) as mock_or:
        resp, usage = call_llm("test prompt", client=client, max_retries=0)
        assert resp == "openrouter response"
        assert usage.backend == "openrouter"
        mock_or.assert_called_once()


def test_groq_and_openrouter_failure_cascades_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-openrouter-key")
    client = FailingGroqClient()

    with (
        patch("tools.llm._call_openrouter", side_effect=RuntimeError("OpenRouter 503 Outage")),
        patch("tools.llm._call_local_llama", return_value=("fallback to local", 18, 9)) as mock_local,
    ):
        resp, usage = call_llm("test prompt", client=client, max_retries=0)
        assert resp == "fallback to local"
        assert usage.backend == "local"
        mock_local.assert_called_once()


def test_all_backends_failing_raises_informative_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock-openrouter-key")
    client = FailingGroqClient()

    with (
        patch("tools.llm._call_openrouter", side_effect=RuntimeError("OpenRouter down")),
        patch("tools.llm._call_local_llama", side_effect=RuntimeError("Local model error")),
        pytest.raises(LLMCallError) as excinfo,
    ):
        call_llm("test prompt", client=client, max_retries=0)
    assert "All backends in fallback chain failed" in str(excinfo.value)


def test_call_local_llm_uses_in_process_model(tmp_path, monkeypatch):
    """call_local_llm should use the in-process llama_cpp model as primary path."""
    monkeypatch.setenv("LOCAL_MODEL_PATH", str(tmp_path / "fake.gguf"))
    (tmp_path / "fake.gguf").write_bytes(b"fake")

    mock_model = MagicMock()
    mock_model.create_chat_completion.return_value = {
        "choices": [{"message": {"content": "in-process response"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }

    with patch("tools.llm.get_local_model", return_value=mock_model):
        msg, in_tok, out_tok = call_local_llm("hello", model_path=tmp_path / "fake.gguf")
        assert msg == "in-process response"
        assert in_tok == 5
        assert out_tok == 3
        mock_model.create_chat_completion.assert_called_once()


def test_get_local_model_singleton_caching(tmp_path, monkeypatch):
    """get_local_model should load the model once and return the same instance."""
    # Reset the global singleton for this test
    original = llm_module._local_llama_instance
    llm_module._local_llama_instance = None
    try:
        monkeypatch.setenv("LOCAL_MODEL_PATH", str(tmp_path / "fake.gguf"))
        (tmp_path / "fake.gguf").write_bytes(b"fake")

        mock_llama_cls = MagicMock()
        mock_instance = MagicMock()
        mock_llama_cls.return_value = mock_instance

        with patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_cls)}):
            result1 = get_local_model(tmp_path / "fake.gguf")
            assert result1 is mock_instance
            assert mock_llama_cls.call_count == 1

            # Second call should return cached instance, not call constructor again
            result2 = get_local_model(tmp_path / "fake.gguf")
            assert result2 is mock_instance
            assert mock_llama_cls.call_count == 1  # still 1
    finally:
        llm_module._local_llama_instance = original


def test_offline_mode_via_env_var(tmp_path, monkeypatch):
    """OFFLINE_MODE=true env var should force local backend."""
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OFFLINE_MODE", "true")

    with patch("tools.llm._call_local_llama", return_value=("env offline", 10, 5)):
        resp, usage = call_llm("test prompt")
        assert resp == "env offline"
        assert usage.backend == "local"


def test_backend_logged_in_usage_file(tmp_path, monkeypatch):
    """Backend field should be written to the usage JSONL log."""
    log_file = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG", str(log_file))
    client = SucceedingGroqClient()
    call_llm("test prompt", client=client, max_retries=0)

    records = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    assert len(records) == 1
    assert records[0]["backend"] == "groq"
    assert records[0]["status"] == "success"


def test_groq_5xx_outage_triggers_fallback_chain(tmp_path, monkeypatch):
    """500/502/503 server outages should trigger the fallback cascade."""
    from groq import InternalServerError

    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp_obj = httpx.Response(503, request=req)

    class Failing503GroqClient:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": self})()

        def create(self, **kwargs):
            raise InternalServerError("Simulated 503 Service Unavailable", response=resp_obj, body=None)

    with patch("tools.llm._call_local_llama", return_value=("recovered locally from 503", 10, 5)):
        resp, usage = call_llm("test prompt", client=Failing503GroqClient(), max_retries=0)
        assert resp == "recovered locally from 503"
        assert usage.backend == "local"


def test_auto_discovery_of_gguf_models(tmp_path, monkeypatch):
    """Auto-discovery should locate *.gguf files when default path is missing."""
    from tools.llm import _resolve_local_model_path

    fake_models_dir = tmp_path / "models"
    fake_models_dir.mkdir()
    target_gguf = fake_models_dir / "custom-quant-q4_0.gguf"
    target_gguf.write_text("fake gguf content")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCAL_MODEL_PATH", raising=False)

    discovered = _resolve_local_model_path()
    assert discovered.name == "custom-quant-q4_0.gguf"


def test_planner_and_coder_robust_json_parsing():
    """Small models wrapping JSON in conversational prose should parse successfully."""
    from agents.planner import _parse_plan
    from agents.coder_files import _parse_files

    conversational_plan = (
        "Sure thing! Here is the implementation plan you requested:\n\n"
        "```json\n"
        '{\n  "steps": ["Step 1: do something"],\n  "acceptance_criteria": ["All tests pass"]\n}\n'
        "```\n"
        "Let me know if you need any adjustments!"
    )
    plan = _parse_plan(conversational_plan)
    assert plan.steps == ["Step 1: do something"]
    assert plan.acceptance_criteria == ["All tests pass"]

    conversational_coder = (
        "Here are the code changes:\n"
        '{"files": {"calc.py": "def add(a, b): return a + b\\n"}, "summary": "fix calc", "confidence": 0.95, "touched_files": ["calc.py"]}\n'
        "Hope this helps!"
    )
    files, summary, confidence, touched = _parse_files(conversational_coder)
    assert "calc.py" in files
    assert summary == "fix calc"
    assert confidence == 0.95


