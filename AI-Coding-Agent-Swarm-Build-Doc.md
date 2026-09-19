# AI Coding Agent Swarm — Build Documentation (Final, Merged)

A phase-by-phase roadmap to build a production-aware multi-agent AI coding system using a hosted free API (Groq) as primary inference, with local llama.cpp as an offline fallback — no GPU required.

## Project Overview

**Goal:** Build a multi-agent system that takes a coding task + a target repository, and autonomously plans, researches, writes, tests, and debugs code changes until they pass — inspired by the KaggleCoder Swarm architecture, with production-grade safety, cost tracking, and quality-gating built in from the start rather than bolted on.

### Core Design Decisions

- Hosted API (Groq, free tier) as primary LLM — fast, strong quality
- llama.cpp as local fallback — used only if Groq is unavailable (rate limit, no internet, downtime)
- vLLM and Hugging Face Transformers are not used — GPU-dependent, not viable on your hardware
- Docker added later for sandboxing and portability
- CI/CD added last as a polish layer, but it enforces a real regression gate (eval harness), not just lint/test-pass on the swarm's own code
- Safety guardrails on generated code are **not optional** — they're built into Phase 4, not appended afterward

### Tech Stack Summary

| Layer | Tool |
|---|---|
| Language | Python 3.10+ |
| Primary LLM | Groq API (`openai/gpt-oss-120b`) |
| Fallback LLM | llama.cpp + GGUF quantized model (1.5B–3B) |
| Vector DB | ChromaDB |
| Embeddings | sentence-transformers (local, CPU) |
| Code chunking | tree-sitter |
| Output validation | Pydantic |
| Testing/Linting | pytest, ruff |
| Diff handling | unidiff / git apply |
| Web search | duckduckgo-search |
| Logging | SQLite |
| Containerization | Docker |
| CI/CD | GitHub Actions |

---

## Phase 0 — Environment Setup

**Objective:** Get a clean, working Python environment before writing any logic.

### Tasks

- Install Python 3.10+
- Install uv (or use pip + venv)
- Create project folder and initialize git
- Create virtual environment and activate it
- Create `.env` file for secrets (never commit this)
- Sign up for a free Groq account and generate an API key
- Install base dependencies

```bash
uv venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
uv pip install groq chromadb sentence-transformers tree-sitter tree-sitter-languages pydantic pytest ruff duckduckgo-search unidiff click python-dotenv
```

### Folder Structure to Create

```
agent-swarm/
├── agents/
├── execution/
├── rag/
├── tools/
├── memory/
├── models/           # only local fallback config lives here
├── ui/
├── tests/
├── eval/             # NEW — benchmark task suite lives here
├── .env
├── .gitignore
├── main.py
└── requirements.txt
```

### Deliverable

A repo that runs `python main.py --help` (even if it does nothing yet) with no import errors.

---

## Phase 1 — LLM Connection Layer (`call_llm`) + Cost Tracking

**Objective:** One reliable function every agent will call. Get this rock-solid before building any agent logic — and instrument it for cost from day one, since retrofitting cost tracking later means re-touching every call site.

### Tasks

- Write a `call_llm(prompt, system_prompt)` function using the Groq client
- Add timeout handling
- Add try/except for rate-limit and connection errors
- Add **exponential backoff with jitter** on rate-limit/timeout errors before falling back to anything else — a transient blip shouldn't immediately degrade you to a weaker path
- Log tokens in/out, estimated cost, and latency for every call (write to a simple local structure for now; wired into SQLite properly in Phase 8)
- Test it standalone with a simple prompt

### Deliverable

A script that sends one prompt to Groq and prints a real response, with basic error handling and per-call cost/latency logging in place.

### Success Check

Run it 5–10 times in a row without crashing, including at least one deliberate bad input (e.g., empty prompt) to confirm errors are caught, not fatal. Confirm cost/latency numbers are captured for each call.

---

## Phase 2 — Single Agent, No RAG Yet

**Objective:** Build the simplest possible working agent to validate the prompt → structured output pattern before adding complexity.

### Tasks

- Define a Planner agent: system prompt + `call_llm` call
- Use Pydantic to define the expected output shape (e.g., `Plan(steps: list[str], acceptance_criteria: list[str])`)
- Add JSON parsing + a retry-on-malformed-output loop
- Test: give it a plain-text task description, confirm it returns valid structured JSON

### Deliverable

`python main.py plan "Add input validation to the login form"` → prints a structured plan.

### Success Check

Run 5 different task descriptions through it. Structured output should parse successfully at least 4/5 times without manual fixing.

---

## Phase 3 — RAG Pipeline (Repo Understanding)

**Objective:** Let the system actually "read" a target codebase intelligently. This is also the right time to start drafting your eval benchmark tasks (used in Phase 4.5), since you'll already be assembling small sample repos here.

### Tasks

- Write a repo indexer: walk target repo, read files
- Integrate tree-sitter to chunk code by function/class, not raw character splits
- Generate embeddings for each chunk using sentence-transformers
- Store chunks + embeddings in ChromaDB
- Write a retrieval function: given a task description, return top-N relevant chunks
- Build `python main.py index --repo <path>` CLI command
- Start a running draft list of candidate benchmark tasks in `eval/` as you build sample repos (don't build the harness yet — just capture task ideas)

### Deliverable

Running `index` on a small sample repo, then querying it, returns genuinely relevant code snippets for a given task description.

### Success Check

Manually verify: for 3 different queries against a known repo, the top retrieved chunk is actually relevant to what you asked.

---

## Phase 4 — Coder + Reviewer Loop, With Guardrails Built In

**Objective:** Build the heart of the system — code generation plus automated verification — with safety guardrails as a first-class part of the loop, not an afterthought. Real AI coding agents (Cursor, Copilot Workspace, etc.) all treat this as core, not optional.

### Tasks

- Build Coder agent: takes plan + retrieved context → outputs a unified diff
  - Include a `confidence` field in the Coder's structured output (needed later for Phase 5's confidence-gated auto-apply — add it now rather than retrofitting)
- Build diff application logic (unidiff or git apply) into a sandboxed workspace copy, not the real repo
- **Before applying any diff:**
  - Scan for dangerous patterns (`os.system`, `eval`, `subprocess` with `shell=True`, file deletion outside the sandbox, unexpected network calls)
  - Enforce the sandbox boundary explicitly — diffs can only touch files inside the mounted workspace, never `../` outside it
  - Reject and log any diff that fails these checks; do not attempt to auto-fix a rejected diff, surface it as a hard failure
- Build Reviewer step: run ruff (lint) and pytest (tests) against the patched sandbox
- Capture and structure the test/lint output into a pass/fail report

### Deliverable

Given a plan + a real small repo, the system generates a diff, runs it through the guardrail scan, applies it to a sandbox copy if it passes, and reports whether tests pass.

### Success Check

- Intentionally give it a task with an easy, verifiable outcome (e.g., "add a function that returns True") and confirm the loop correctly reports pass/fail.
- Intentionally craft (or prompt-inject) a diff containing a dangerous pattern and confirm the guardrail rejects it before it ever touches the sandbox.

---

## Phase 4.5 — Eval Harness (Regression Gate)

**Objective:** Now that a real Coder → Reviewer loop exists, build a repeatable benchmark to measure whether changes to the swarm itself make it better or worse at solving tasks. Building this before Phase 4 existed would mean writing benchmarks with nothing real to test them against — this is the right point to formalize it.

### Tasks

- Turn your Phase 3 draft task list into 15–20 known coding tasks with expected pass/fail outcomes (e.g., "add input validation" should make a specific test pass)
- Build a runner that executes the full suite against the current swarm and reports pass rate, cost, and time
- Store results so you can diff "this run" against "last run"

### Deliverable

`python main.py eval` runs all benchmark tasks and reports a pass/fail summary with aggregate cost and time.

### Success Check

Run the suite twice with no code changes in between — results should be stable (same pass/fail pattern). Then deliberately break something in the Coder prompt and confirm the eval pass rate drops.

---

## Phase 5 — Debugger + Self-Correction Loop, With Confidence Gating

**Objective:** Close the loop — when Reviewer reports failure, feed it back for a fix instead of stopping. Add a middle state between "pass" and "fail" so risky changes don't silently auto-merge.

### Tasks

- Build Debugger agent: takes failing test output/lint errors → produces a fix plan
- Wire the loop: Coder → guardrail scan → apply diff → Reviewer verifies → if fail → Debugger → back to Coder
- Add a max-iteration cap (e.g., 3–5 attempts) to prevent infinite loops
- **Confidence-gated auto-apply:** if Reviewer passes but the diff touches security-sensitive files (auth, payments, config) OR the Coder's self-reported confidence (from the field added in Phase 4) is below a threshold, don't auto-finalize — flag the run for human review instead of silently merging
- Log each iteration's outcome, including any confidence-gate flags

### Deliverable

`python main.py solve "<task>" --repo <path>` runs the full plan → code → test → fix loop end-to-end and either succeeds, flags for human review, or reports a clear final failure after max attempts.

### Success Check

- Deliberately give it a task likely to fail on the first try; confirm it self-corrects and eventually passes (or exits cleanly after max attempts with a useful failure summary).
- Give it a task that touches a file you've marked security-sensitive; confirm it flags for review instead of auto-finalizing even if tests pass.

---

## Phase 6 — Researcher + Architect (Parallel Context Agents)

**Objective:** Add the remaining context-gathering agents and run them concurrently to save time.

### Tasks

- Build Researcher agent: uses duckduckgo-search to pull relevant docs/info for the task
- Build Repo Context agent: summarizes retrieved ChromaDB chunks into a digestible form
- Build Architect agent: proposes module boundaries/design approach, flags risks
- Run Researcher, Repo Context, and Architect concurrently (e.g., `asyncio.gather` or a thread pool)
- Merge their outputs into a single "design context" object passed to the Coder

### Deliverable

Full 6-agent pipeline: Planner → (Researcher + Repo Context + Architect in parallel) → Coder → Reviewer → Debugger loop.

### Success Check

Compare total run time before/after parallelizing this stage — should be noticeably faster than running the three sequentially.

---

## Phase 7 — Local Fallback (llama.cpp) + Fallback Chain

**Objective:** Add offline resilience without making it your default path. Make the fallback a proper chain rather than a binary switch, so a single provider's rate limit doesn't fully degrade you to the weakest option.

### Tasks

- Download a small GGUF model (e.g., Qwen2.5-Coder-1.5B-Instruct-GGUF)
- Install llama-cpp-python
- Write `call_local_llm()` — load model once globally, reuse across calls
- Update `call_llm()` into a proper fallback chain: **Groq → OpenRouter free tier → llama.cpp**, using the backoff logic from Phase 1 before advancing down the chain
- Add `--offline` CLI flag to force local-only mode
- Log which backend (Groq / OpenRouter / local) handled each agent call

### Deliverable

Disconnecting your internet (or hitting a simulated rate-limit) causes the system to automatically continue down the fallback chain instead of crashing.

### Success Check

Run one task with `--offline` forced on, confirm it completes (slower, weaker output is expected and fine). Simulate a Groq rate-limit and confirm it tries OpenRouter before dropping to local.

---

## Phase 8 — Logging, Observability & Structured Run Reports

**Objective:** Make every run auditable and debuggable, and produce a human-readable artifact proving what the system did.

### Tasks

- Set up SQLite schema: session id, agent name, prompt, response, backend used, tokens in/out, cost, latency, timestamp
- Log every agent call automatically (wrap `call_llm`)
- Build a simple query/report script: "show me all steps from the last run"
- Add `python main.py cost-report` — total spend, cost per task, cost per agent role
- After every `solve` run, auto-generate a Markdown summary: what was attempted, which agents ran, the final diff, pass/fail status, any confidence-gate flags, cost, and time taken

### Deliverable

After any `solve` run, you can query SQLite and see the full trace of what each agent did, in order — and you get a standalone Markdown report as proof of the run.

### Success Check

Intentionally cause a failure mid-run, then reconstruct exactly what happened from the logs and the generated report alone, without re-running anything.

---

## Phase 9 — Docker Containerization

**Objective:** Sandbox agent-generated code execution and make the project portable.

### Tasks

- Write a Dockerfile for the Python app
- Mount the target repo and model files as volumes (not baked into the image)
- Ensure the sandbox workspace (where diffs get applied/tested) lives inside the container
- Test full solve run entirely inside Docker

### Deliverable

`docker build` + `docker run` executes a full task end-to-end with no local Python environment needed.

### Success Check

Run the exact same task on a clean machine (or after deleting your local venv) using only Docker, and get the same result.

---

## Phase 10 — CI/CD (Real Regression Gate)

**Objective:** Auto-validate your own project code on every change — using the eval harness as the actual quality gate, not just lint/test-pass on the swarm's own codebase.

### Tasks

- Write a GitHub Actions workflow: install deps, run `ruff check .`, run `pytest`
- Run the Phase 4.5 eval harness as part of the workflow — fail the build if pass rate drops below a set threshold vs. the last known-good run
- Trigger on push and pull request
- (Optional) Add a badge to your README showing build status

### Deliverable

Every push to GitHub automatically lints, tests, and runs the eval suite against your codebase, visible in the Actions tab — a real regression gate, not just style checking.

### Success Check

Deliberately push a lint error, a failing test, and a change that would drop eval pass rate; confirm CI catches all three and reports failure.

---

## Suggested Timeline (Part-Time Pace)

| Phase | Focus | Rough Effort |
|---|---|---|
| 0 | Setup | 1 day |
| 1 | LLM connection + cost tracking | 1–1.5 days |
| 2 | Single agent | 1–2 days |
| 3 | RAG pipeline | 3–4 days |
| 4 | Coder + Reviewer + guardrails | 3.5–4.5 days |
| 4.5 | Eval harness | 1–1.5 days |
| 5 | Debugger loop + confidence gating | 2.5–3.5 days |
| 6 | Researcher + Architect | 2–3 days |
| 7 | Local fallback + fallback chain | 1.5–2.5 days |
| 8 | Logging + run reports | 1–1.5 days |
| 9 | Docker | 1–2 days |
| 10 | CI/CD (real regression gate) | 0.5–1 day |

**Total:** roughly 3.5–4.5 weeks part-time, depending on debugging time — Phases 3, 4, 4.5, and 5 are the most involved and worth not rushing.

---

## Key Principles Throughout

- Get each phase fully working before moving to the next. A broken foundation makes every later phase harder to debug.
- Test with small, simple repos first before pointing the swarm at anything complex.
- Cap iteration loops everywhere (Debugger retries, agent retries) to avoid runaway costs or infinite hangs.
- Log early — cost, latency, and backend-used tracking is built into Phase 1, not retrofitted in Phase 8.
- Guardrails are not optional polish — they're part of Phase 4 because the swarm executes AI-generated code from the moment that phase exists.
- Local fallback is a safety net, not a target. Don't over-invest in llama.cpp tuning — its whole purpose is "keep working when Groq isn't available," not "be your main engine."
- The eval harness is your regression gate — once it exists (Phase 4.5), no later change to the swarm should ship without running against it.

---
