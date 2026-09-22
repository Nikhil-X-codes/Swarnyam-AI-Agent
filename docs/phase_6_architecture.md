# Phase 6: Researcher + Architect (Parallel Context Agents)

This document provides a comprehensive architectural and operational breakdown of **Phase 6** of the multi-agent AI coding swarm.

---

## Executive Summary

Prior to Phase 6, the swarm relied solely on Planner step decomposition and raw ChromaDB code retrieval to guide the Coder. While functional for localized edits, this lacked:
1. **External Domain Knowledge:** Awareness of library documentation, API signatures, edge cases, and modern best practices outside the indexed repo.
2. **Digestible Codebase Conventions:** Raw code chunks could easily overflow context windows or present conflicting styles without an explicit architectural summary.
3. **System Boundaries & Risk Analysis:** Complex multi-file tasks risked introducing circular dependencies, architectural coupling, or regression hazards.

Executing three separate context agents sequentially would add significant latency (3x LLM call times). **Phase 6 solves this by introducing parallel context agents:**
* **Researcher Agent:** Pulls live web documentation, API references, and idioms via DuckDuckGo.
* **Repo Context Agent:** Synthesizes raw ChromaDB retrieval chunks into a concise, structured codebase summary.
* **Architect Agent:** Evaluates system boundaries, defines module responsibilities, and flags architectural hazards.
* **Parallel Orchestrator (`agents/context.py`):** Runs the context-gathering agents concurrently in a worker pool, merging their outputs into a single unified `DesignContext` for the Coder.

---

## Architecture Overview

```mermaid
graph TB
    Start(["User Coding Task<br/><code>python main.py solve</code>"]) --> Planner["Planner Agent<br/>(agents/planner.py)"]
    
    Planner --> Plan["Structured Plan<br/>(steps + acceptance criteria)"]
    Plan --> FanOut{"Parallel Orchestrator<br/>(ThreadPoolExecutor)<br/><code>agents/context.py</code>"}

    subgraph ParallelContextStage ["Phase 6: Parallel Context Gathering"]
        FanOut -->|"Task + Plan"| Researcher["Researcher Agent<br/>(agents/researcher.py)"]
        FanOut -->|"Task + ChromaDB Chunks"| RepoContext["Repo Context Agent<br/>(agents/repo_context.py)"]
        FanOut -->|"Task + Plan"| Architect["Architect Agent<br/>(agents/architect.py)"]

        subgraph ExternalIO ["External & Database I/O"]
            Researcher -.->|"Web Query"| DDG[("DuckDuckGo Search<br/>duckduckgo-search")]
            RepoContext -.->|"Code Chunks"| Chroma[("ChromaDB Vector Store<br/>rag/indexer.py")]
            Architect -.->|"System Analysis"| LLMArch[("LLM Client<br/>tools/llm.py")]
        end

        Researcher --> ResOut["Research Notes<br/>(APIs, signatures, best practices)"]
        RepoContext --> RepoOut["Repo Digest<br/>(Patterns, style, module conventions)"]
        Architect --> ArchOut["Architect Output<br/>(Boundaries, interfaces, risks)"]

        ResOut --> Merge["Merge Outputs<br/><code>gather_design_context()</code>"]
        RepoOut --> Merge
        ArchOut --> Merge
    end

    Merge --> DesignContextObj["Unified Design Context<br/>(agents/context.py: DesignContext)"]
    DesignContextObj --> Coder["Coder Agent<br/>(agents/coder_files.py)"]

    subgraph SelfCorrectionLoop ["Self-Correction & Gating Loop"]
        Coder --> GuardrailScan{"Guardrails Scan<br/>(execution/guardrails.py)"}
        GuardrailScan -- "Passed" --> Sandbox["Apply to Sandbox & Run Tests<br/>(execution/sandbox.py)"]
        Sandbox --> Reviewer["Reviewer Agent<br/>(pytest + ruff)"]
        Reviewer -- "Fail" --> Debugger["Debugger Agent<br/>(agents/debugger.py)"]
        Debugger -->|"Fix Plan"| Coder
    end

    Reviewer -- "Pass" --> Finish(["Success / Human Review<br/>(SolveResult)"])

    classDef start fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#01579b;
    classDef parallel fill:#ede7f6,stroke:#512da8,stroke-width:2px,color:#311b92;
    classDef agent fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#4a148c;
    classDef io fill:#f5f5f5,stroke:#616161,stroke-width:2px,color:#212121;
    classDef success fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20;

    class Start,Planner,Plan start;
    class FanOut,Merge,DesignContextObj parallel;
    class Researcher,RepoContext,Architect,Coder,Reviewer,Debugger agent;
    class DDG,Chroma,LLMArch,Sandbox io;
    class Finish success;
```

---

## Sequence & Concurrency Timing

```mermaid
sequenceDiagram
    autonumber
    participant Main as Solve Pipeline (solve.py)
    participant Planner as Planner Agent
    participant Orchestrator as Context Orchestrator (context.py)
    participant Res as Researcher Agent
    participant Repo as Repo Context Agent
    participant Arch as Architect Agent
    participant Coder as Coder Agent

    Main->>Planner: create_plan(task)
    Planner-->>Main: Plan (steps, criteria)

    Note over Main,Orchestrator: Phase 6 Concurrency Fan-Out
    Main->>Orchestrator: gather_design_context(task, plan, chunks, parallel=True)
    
    par Thread 1: Web Research
        Orchestrator->>Res: conduct_research(task, plan)
        Res-->>Orchestrator: research_notes (external docs & signatures)
    and Thread 2: Repository Patterns
        Orchestrator->>Repo: summarize_repo_context(task, chunks)
        Repo-->>Orchestrator: repo_summary (digest of existing conventions)
    and Thread 3: Architectural Design
        Orchestrator->>Arch: create_architectural_design(task, plan)
        Arch-->>Orchestrator: ArchitectOutput (boundaries & risks)
    end

    Note over Orchestrator: Concurrency Fan-In & Format
    Orchestrator-->>Main: DesignContext (merged prompt block)
    Main->>Coder: create_code_change(task, plan, DesignContext.as_prompt_context())
```

---

## Core Components Deep Dive

### 1. Researcher Agent (`agents/researcher.py`)

* **Purpose:** Queries live web search engines using `duckduckgo-search` / `ddgs` to retrieve relevant API documentation, function signatures, library usage patterns, and known caveats.
* **Offline Resilience:** Checks the `OFFLINE_MODE` environment variable and returns empty results gracefully without crashing when working in offline or air-gapped environments.
* **Caching:** Implements an in-memory `_search_cache` to prevent redundant network calls during iterative solves.
* **Output:** A concise, practical summary focusing strictly on APIs and best practices.

### 2. Repo Context Agent (`agents/repo_context.py`)

* **Purpose:** Digests raw code chunks retrieved from ChromaDB vector search into an organized architectural summary.
* **Role in Swarm:** Prevents raw chunk dumps from blowing context limits or confusing the Coder with irrelevant boilerplate. Identifies:
  - Codebase conventions (snake_case, type hinting, class hierarchies)
  - Existing function and class signatures
  - Shared utilities and common imports
  - Typical error handling conventions

### 3. Architect Agent (`agents/architect.py`)

* **Purpose:** Analyzes the task and plan against existing repo structure to establish modular design boundaries and identify risks before coding begins.
* **Structured Output Model (`ArchitectOutput`):**
  ```python
  class ArchitectOutput(BaseModel):
      design_approach: str
      module_boundaries: list[str] = Field(default_factory=list)
      risks: list[str] = Field(default_factory=list)
  ```
* **System Prompt:** Enforces pure JSON output with strict architectural guidelines.

### 4. Parallel Orchestrator (`agents/context.py`)

* **Data Container (`DesignContext`):**
  ```python
  class DesignContext(BaseModel):
      research_notes: str = ""
      repo_summary: str = ""
      architecture_guidance: str = ""
      risks: list[str] = Field(default_factory=list)
      elapsed_seconds: float = 0.0
      is_parallel: bool = True
  ```
* **Concurrency Engine:**
  - Uses `concurrent.futures.ThreadPoolExecutor(max_workers=3)` to dispatch `conduct_research`, `summarize_repo_context`, and `create_architectural_design` simultaneously.
  - Supports `parallel=False` fallback mode for environments with strict threading constraints or for debugging.
* **Prompt Formatter (`as_prompt_context()`):** Assembles a structured Markdown block injected into the Coder's prompt:
  ```markdown
  ### Technical Research Notes (External Docs/Best Practices):
  ...
  ### Repository Context & Established Patterns:
  ...
  ### Architectural Design & Module Boundaries:
  ...
  ### Architectural Risks to Avoid:
  ...
  ```

---

## Integration in the Pipeline

### Solve Loop (`agents/solve.py`)
In `solve_task()`, context gathering occurs immediately after planning:
```python
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
```

The resulting `DesignContext` is preserved on `SolveResult.design_context` for run inspection and structured run reporting.

### CLI Usage (`main.py`)
The `solve` command executes parallel context gathering by default and provides a toggle flag:
```bash
# Default: Parallel context gathering
python main.py solve "Add power calculation helper" --repo /path/to/repo

# Sequential context gathering
python main.py solve "Add power calculation helper" --repo /path/to/repo --sequential-context
```

---

## Success Check & Benchmark Results

The Phase 6 success check requires verifying that concurrent context gathering is measurably faster than sequential execution:

* **Benchmark Harness:** `eval/benchmark_phase6_timing.py`
* **Automated Test:** `tests/test_phase6.py::test_benchmark_phase6_timing`
* **Verification Command:**
  ```bash
  python eval/benchmark_phase6_timing.py
  ```
* **Sample Benchmark Output:**
  ```json
  {
    "sequential_seconds": 1.503,
    "parallel_seconds": 0.512,
    "speedup_factor": "2.94x faster",
    "parallel_is_faster": true,
    "merged_context_length_chars": 482,
    "success_check_passed": true
  }
  ```
An approximate **~3x speedup** is achieved during the context-gathering stage, reducing pipeline wall-clock time without dropping context quality.
