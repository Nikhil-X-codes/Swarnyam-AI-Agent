from agents.planner import Plan, PlannerOutputError, create_plan


def test_planner_parses_structured_output():
    def fake_llm(prompt, system_prompt):
        return '{"steps":["inspect form","add validation"],"acceptance_criteria":["invalid input is rejected"]}', None

    result = create_plan("Add validation", llm=fake_llm)
    assert isinstance(result, Plan)
    assert len(result.steps) == 2


def test_planner_retries_malformed_output():
    calls = 0

    def fake_llm(prompt, system_prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "not json", None
        return '{"steps":["make change"],"acceptance_criteria":["tests pass"]}', None

    result = create_plan("Make a change", llm=fake_llm)
    assert result.steps == ["make change"]
    assert calls == 2


def test_planner_fails_after_retry_cap():
    def fake_llm(prompt, system_prompt):
        return "nope", None

    try:
        create_plan("Bad output", max_retries=2, llm=fake_llm)
    except PlannerOutputError as exc:
        assert "3 attempts" in str(exc)
    else:
        raise AssertionError("expected PlannerOutputError")
