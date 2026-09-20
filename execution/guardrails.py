"""Safety checks that run before any generated diff is applied."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

SECURITY_SENSITIVE_PARTS = {"auth", "authentication", "payment", "payments", "config", "settings", ".env"}
_DANGEROUS_PATTERNS = {
    "os.system": re.compile(r"\bos\.system\s*\("),
    "eval": re.compile(r"\beval\s*\("),
    "exec": re.compile(r"\bexec\s*\("),
    "subprocess shell=True": re.compile(r"\bsubprocess\.[A-Za-z_]+\s*\([^\n]*\bshell\s*=\s*True\b"),
    "destructive file deletion": re.compile(r"\b(?:shutil\.rmtree|os\.(?:remove|unlink))\s*\("),
    "unexpected network call": re.compile(r"\b(?:requests\.(?:get|post|put|delete)|urllib\.request\.urlopen)\s*\("),
}

@dataclass(frozen=True)
class GuardrailResult:
    passed: bool
    reasons: list[str]
    touched_files: list[str]
    security_sensitive: bool

def _path_ok(path: str) -> bool:
    candidate = PurePosixPath(path)
    return not candidate.is_absolute() and ".." not in candidate.parts and not path.startswith("~")

def scan_diff(diff_text: str) -> GuardrailResult:
    """Reject unsafe content and paths before a diff reaches the sandbox."""
    reasons: list[str] = []
    touched: list[str] = []
    security_sensitive = False
    if not isinstance(diff_text, str) or not diff_text.strip():
        return GuardrailResult(False, ["diff is empty"], [], False)
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw in {"/dev/null", "dev/null"}:
                continue
            path = raw[2:] if raw.startswith(("a/", "b/")) else raw
            current_file = path
            if path not in touched:
                touched.append(path)
            if not _path_ok(path):
                reasons.append(f"sandbox boundary violation: {path}")
            lower_parts = {part.lower() for part in PurePosixPath(path).parts}
            if lower_parts & SECURITY_SENSITIVE_PARTS or PurePosixPath(path).name.lower() in {".env", "config.py", "settings.py"}:
                security_sensitive = True
        if line.startswith("+") and not line.startswith("+++"):
            for label, pattern in _DANGEROUS_PATTERNS.items():
                if pattern.search(line[1:]):
                    reasons.append(f"dangerous pattern '{label}' in {current_file or 'unknown file'}")
    return GuardrailResult(not reasons, reasons, touched, security_sensitive)
