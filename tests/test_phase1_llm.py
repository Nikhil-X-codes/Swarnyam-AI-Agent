import json
from typing import ClassVar

import pytest

from tools.llm import LLMInputError, call_llm


class FakeUsage:
    prompt_tokens = 7
    completion_tokens = 5


class FakeResponse:
    class Choice:
        class Message:
            content = "ok"

        message = Message()

    choices: ClassVar = [Choice()]
    usage: ClassVar = FakeUsage()


class FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": self})()
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return FakeResponse()


def test_empty_prompt_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    with pytest.raises(LLMInputError):
        call_llm("   ")


def test_success_returns_text_and_logs_usage(tmp_path, monkeypatch):
    log_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG", str(log_path))
    response, usage = call_llm("hello", client=FakeClient(), max_retries=0)
    assert response == "ok"
    assert usage.input_tokens == 7
    assert usage.output_tokens == 5
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["status"] == "success"
