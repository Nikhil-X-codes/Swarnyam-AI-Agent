# Phase 2: Single Agent, No RAG Yet (Planner Agent)

This document explains the architecture and flow of **Phase 2** in simple, plain language.

---

## What Does Phase 2 Do?

Phase 2 builds the simplest possible working agent in the swarm: the **Planner Agent**.
The objective is to validate the prompt -> structured output pattern before adding downstream complexity (like RAG, coding, and review loops).

The Planner agent:
1. **Takes a task description:** Accepts a plain-text coding task from the user or CLI (`python main.py plan "<task>"`).
2. **Enforces a strict output contract:** Uses Pydantic (`Plan`) to guarantee the output is formatted as valid JSON containing concrete `steps` and `acceptance_criteria`.
3. **Implements error recovery:** If the model returns malformed JSON or invalid types, it automatically re-prompts the model with corrective guidance in a capped retry loop (`max_retries=3`).
4. **Interfaces with Phase 1:** Calls `call_llm()` for robust API execution, timeout/backoff handling, and cost/token tracking.

---

## Flowchart: How the Planner Agent Works

```mermaid
flowchart TD
    Start(["CLI: python main.py plan '<task>'"]) --> ValidateInput{"Is task non-empty string?"}
    
    ValidateInput -- "No" --> InputErr["Raise ValueError: task must be non-empty"]
    ValidateInput -- "Yes" --> SetLogger["Set agent context: 'planner' in memory logger"]
    
    SetLogger --> InitRetry["Initialize retry loop (attempt = 0, max_retries = 3)"]
    
    InitRetry --> CheckAttempt{"Is this a retry attempt?"}
    CheckAttempt -- "No (attempt = 0)" --> InitialPrompt["Build initial prompt: Create plan for task"]
    CheckAttempt -- "Yes (attempt > 0)" --> ErrorPrompt["Build corrective prompt: Previous output was invalid JSON"]
    
    InitialPrompt --> CallLLM["Invoke call_llm(prompt, PLANNER_SYSTEM_PROMPT)"]
    ErrorPrompt --> CallLLM
    
    CallLLM --> ExtractText["Extract raw response text from call_llm tuple"]
    
    ExtractText --> StripFences["Strip markdown code fences (```json ... ```)"]
    
    StripFences --> ParseJSON{"json.loads(candidate)"}
    
    ParseJSON -- "Decode Error" --> CatchErr["Catch json.JSONDecodeError"]
    ParseJSON -- "Valid JSON" --> PydanticVal{"Plan.model_validate(data)"}
    
    PydanticVal -- "Schema Error" --> CatchErr2["Catch pydantic.ValidationError"]
    PydanticVal -- "Valid Plan" --> SuccessPlan["Return Plan(steps, acceptance_criteria)"]
    
    SuccessPlan --> PrintCLI(["CLI prints formatted JSON dump to stdout"])
    
    CatchErr --> CheckLimit{"attempt < max_retries?"}
    CatchErr2 --> CheckLimit
    
    CheckLimit -- "Yes" --> IncrementAttempt["attempt += 1"]
    IncrementAttempt --> CheckAttempt
    
    CheckLimit -- "No" --> MaxAttemptsErr["Raise PlannerOutputError (Max retries exhausted)"]

    classDef success fill:#d4edda,stroke:#28a745,stroke-width:2px,color:#155724;
    classDef failure fill:#f8d7da,stroke:#721c24,stroke-width:2px,color:#721c24;
    classDef process fill:#e2e3e5,stroke:#383d41,stroke-width:2px,color:#383d41;
    
    class SuccessPlan,PrintCLI success;
    class InputErr,MaxAttemptsErr failure;
    class SetLogger,InitRetry,InitialPrompt,ErrorPrompt,CallLLM,ExtractText,StripFences,IncrementAttempt process;
```

---

## Sequence Diagram: Step-by-Step Interaction

```mermaid
sequenceDiagram
    autonumber
    actor User as User / CLI
    participant Main as main.py (plan command)
    participant Planner as agents/planner.py (create_plan)
    participant LLM as tools/llm.py (call_llm)
    participant Model as Groq Cloud API
    participant Logger as memory/logger.py

    User->>Main: python main.py plan "Add validation"
    Main->>Planner: create_plan("Add validation")
    Planner->>Logger: set_agent_context("planner")
    
    rect rgb(245, 245, 255)
        note over Planner,Model: Retry Loop (max_retries = 3)
        Planner->>LLM: call_llm(prompt, PLANNER_SYSTEM_PROMPT)
        LLM->>Model: POST /chat/completions (model, system_prompt, user_prompt)
        Model-->>LLM: Response text + token usage
        LLM-->>Planner: (raw_text, LLMUsage)
        
        alt Malformed JSON or Schema Violation
            Planner->>Planner: _parse_plan() raises PlannerOutputError
            Note over Planner: Increment attempt, build corrective prompt
            Planner->>LLM: call_llm(corrective_prompt, PLANNER_SYSTEM_PROMPT)
            LLM->>Model: POST /chat/completions
            Model-->>LLM: Valid JSON response
            LLM-->>Planner: (raw_text, LLMUsage)
        end
        
        Planner->>Planner: Plan.model_validate(json_dict)
    end
    
    Planner-->>Main: Plan(steps=[...], acceptance_criteria=[...])
    Main-->>User: Structured JSON output printed to console
```

---

## Core Components Breakdown

| Component | Location | What It Does | Why It Matters |
|---|---|---|---|
| **`Plan` Pydantic Model** | [agents/planner.py](file:///d:/Projects/Swarm%20Agent/agents/planner.py) | Defines `steps: list[str]` and `acceptance_criteria: list[str]` with `min_length=1`. | Enforces strong schema guarantees for downstream agents (Architect, Coder). |
| **`PLANNER_SYSTEM_PROMPT`** | [agents/planner.py](file:///d:/Projects/Swarm%20Agent/agents/planner.py) | Instructs the model to output raw JSON adhering to the exact schema. | Keeps prompts localized with agent logic (rule from AGENTS.md). |
| **`_parse_plan()`** | [agents/planner.py](file:///d:/Projects/Swarm%20Agent/agents/planner.py) | Strips Markdown code blocks and invokes Pydantic validator. | Tolerates common LLM markdown formatting while enforcing valid JSON. |
| **`create_plan()`** | [agents/planner.py](file:///d:/Projects/Swarm%20Agent/agents/planner.py) | Executes the bounded retry loop on malformed outputs. | Guarantees reliability without open-ended loops (`while True` prohibited). |
| **CLI `plan` Subcommand** | [main.py](file:///d:/Projects/Swarm%20Agent/main.py) | Entry point CLI command (`python main.py plan "<task>"`). | Provides single developer interface for running the planner. |
| **Test Suite** | [tests/test_phase2.py](file:///d:/Projects/Swarm%20Agent/tests/test_phase2.py) | Unit tests with mock LLM for parsing, retries, and bounded failures. | Validates Phase 2 correctness and prevents regression. |
