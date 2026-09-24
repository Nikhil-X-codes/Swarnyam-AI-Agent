# AGENTS.md

This file is the entry point for any AI coding agent (Claude Code, Cursor, Copilot Workspace, or a human) working on this repository. Read this in full before making changes. If something here conflicts with a comment in code, this file wins — update the code to match, or update this file in the same commit if the project's direction genuinely changed.

---

## What this project is

A multi-agent AI coding system (an "agent swarm") that takes a coding task + a target repository and autonomously plans, researches, writes, tests, and debugs code changes until they pass. Full architecture and phase-by-phase spec lives in `docs/AI-Coding-Agent-Swarm-Build-Doc.md` — treat that file as the source of truth for scope and sequencing. This file is the operational summary an agent needs without re-reading the whole spec every time.

**This repo is itself the swarm** — not a repo the swarm operates on. Sample/target repos the swarm indexes and patches live under a sandboxed workspace directory at runtime, never inside this repo's own source tree.

---

## Current build status

> Update this section every time a phase is completed or started. An agent should never have to grep commit history to figure out where the project actually is.

- **Current phase:** Phase 10 — Latency, Response Quality & Pipeline Optimization
- **Last completed phase:** Phase 9 — Docker Containerization (Dockerfile with CPU-only PyTorch, docker-compose.yml, volume isolation for target repos/models, internal sandbox routing via SWARM_WORK_ROOT, 5/5 Phase 9 unit tests passing, production documentation in docs/ and README.md)
- **Known broken / in-progress:** None. Swarnyam container and host workflows tested end-to-end.

Do not start work on a phase later than the current one. Phases build on each other in order — see the build doc for why (e.g. the eval harness in Phase 4.5 assumes a working Coder→Reviewer loop from Phase 4; don't build it early with nothing real to test).

---

## Tech stack (do not substitute without updating this file)

| Layer | Tool |
|---|---|
| Language | Python 3.10+ |
| Primary LLM | Groq API (`openai/gpt-oss-120b`) |
| Fallback chain | Groq → OpenRouter (free tier) → llama.cpp (local GGUF, 1.5B–3B) |
| Vector DB | ChromaDB |
| Embeddings | sentence-transformers (local, CPU-only — no GPU assumed anywhere in this project) |
| Code chunking | tree-sitter |
| Output validation | Pydantic |
| Testing/Linting | pytest, ruff |
| Diff handling | unidiff / git apply |
| Web search | duckduckgo-search |
| Logging | SQLite |
| Containerization | Docker |
| CI/CD | GitHub Actions |

**Explicitly not used:** vLLM, Hugging Face Transformers (both GPU-dependent, not viable on target hardware). Do not introduce a dependency that requires a GPU.

---

## Folder structure

```
agent-swarm/
├── agents/       # Planner, Researcher, Architect, Coder, Reviewer, Debugger
├── execution/    # sandbox workspace mgmt, diff application, guardrail scans
├── rag/          # repo indexer, tree-sitter chunking, ChromaDB retrieval
├── tools/
├── memory/       # SQLite logging
├── models/       # local llama.cpp fallback config ONLY — not a dumping ground
├── ui/
├── tests/        # tests for the swarm's own code
├── eval/         # benchmark task suite (Phase 4.5+) — NOT the same as tests/
├── docs/         # build doc + this file
├── .env          # secrets — NEVER commit
├── .gitignore
├── main.py
└── requirements.txt
```

`tests/` tests the swarm's own codebase (pytest, part of normal dev hygiene). `eval/` is the regression benchmark — 15–20 known coding tasks the swarm itself attempts to solve, used as a quality gate. Don't conflate the two.

---

## Non-negotiable rules for any agent working in this repo

These aren't style preferences — they're safety and cost boundaries specific to what this project does (an agent that writes and executes code).

1. **Never widen the sandbox boundary.** All diff application and code execution happens inside the sandboxed workspace copy. Any code that lets a diff touch paths outside the mounted workspace (`../` traversal, absolute paths outside sandbox root) is a bug, not a feature request — reject it, don't "fix" it by loosening the check.
2. **Never remove or weaken the guardrail scan.** The dangerous-pattern scan (`os.system`, `eval`, `subprocess(shell=True)`, unsandboxed file deletion, unexpected network calls) runs on every generated diff *before* it touches the sandbox. If a legitimate task trips a guardrail, the fix is a more precise pattern check — not disabling the scan for that call site.
3. **Never auto-finalize a diff that touches security-sensitive files** (auth, payments, config) or that the Coder flagged with low self-reported confidence. Route to human review instead. This logic lives in Phase 5 (Debugger loop) — don't bypass it elsewhere in the pipeline.
4. **Every LLM call goes through `call_llm()`.** Don't call the Groq/OpenRouter/llama.cpp clients directly from agent code. `call_llm()` is where cost/token/latency logging, backoff, and the fallback chain live — bypassing it silently breaks cost tracking and observability.
5. **Cap every retry loop.** Debugger retries, malformed-JSON retries, agent retries — all need an explicit max-iteration cap (3–5 is the working default). No open-ended `while True` retry logic anywhere in the pipeline.
6. **Local fallback (llama.cpp) is a safety net, not a target.** Don't spend effort tuning it to be a strong model — its only job is "keep the pipeline alive when Groq and OpenRouter are both unavailable."
7. **Structured outputs use Pydantic schemas, and schema changes are backward-compatible where possible.** E.g. the Coder's `confidence` field (added Phase 4) is relied on by Phase 5's confidence-gating — don't rename/remove fields other phases depend on without updating every consumer in the same change.
8. **Any change to swarm behavior should be run against the eval harness (`eval/`, once it exists from Phase 4.5 onward)** before being considered done. CI enforces this in Phase 10, but don't wait for CI to find out you regressed something.

---

## Conventions

- **Diffs, not full file rewrites.** The Coder agent outputs unified diffs (unidiff/git apply format), not entire regenerated files.
- **All agent system prompts live alongside their agent code**, not in a shared prompts-dump file — keep prompt and logic co-located so changing one doesn't require hunting for the other.
- **Every agent call is logged automatically** via the `call_llm()` wrapper — session id, agent name, prompt, response, backend used, tokens, cost, latency, timestamp. If you add a new agent, it gets this for free through `call_llm()`; don't hand-roll separate logging.
- **CLI commands are added to `main.py` via `click`**, one subcommand per capability (`plan`, `index`, `solve`, `eval`, `cost-report`, etc.) — keep the CLI surface as the single entry point rather than scattering standalone scripts.

---

## What "done" looks like per phase

Don't mark a phase complete from a vibe check — the build doc (`docs/AI-Coding-Agent-Swarm-Build-Doc.md`) has an explicit "Success Check" for every phase. An agent picking up work here should run that phase's Success Check before considering it finished or moving to the next phase.

---

## If context is missing

If you (the agent) are about to make a decision this file doesn't cover — e.g. which model to use for a new agent role, how to handle a new class of dangerous pattern, whether something belongs in `tools/` vs `execution/` — stop and either:
- check `docs/AI-Coding-Agent-Swarm-Build-Doc.md` for the relevant phase spec, or
- flag the ambiguity explicitly in your output rather than guessing silently.

Silent, unstated assumptions are exactly what this file exists to prevent.
