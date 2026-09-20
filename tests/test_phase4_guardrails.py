import pytest

from execution.guardrails import scan_diff
from execution.sandbox import DiffApplyError, apply_diff, create_sandbox


def test_guardrail_rejects_dangerous_added_code():
    result = scan_diff("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+eval(user_input)\n")
    assert not result.passed
    assert any("eval" in reason for reason in result.reasons)


def test_guardrail_rejects_path_traversal():
    result = scan_diff("--- a/../secret.txt\n+++ b/../secret.txt\n@@ -0,0 +1 @@\n+bad\n")
    assert not result.passed


def test_safe_diff_applies_only_inside_sandbox(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    sandbox = create_sandbox(repo, tmp_path / "sandbox")
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n"
    apply_diff(sandbox, diff)
    assert "return 2" in (sandbox / "app.py").read_text(encoding="utf-8")


def test_rejected_diff_never_applies(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("safe\n", encoding="utf-8")
    sandbox = create_sandbox(repo, tmp_path / "sandbox")
    with pytest.raises(DiffApplyError):
        apply_diff(sandbox, "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-safe\n+os.system('bad')\n")
    assert (sandbox / "app.py").read_text(encoding="utf-8") == "safe\n"
