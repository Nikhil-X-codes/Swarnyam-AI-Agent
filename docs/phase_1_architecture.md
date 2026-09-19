# Phase 1: LLM Connection Layer & Cost Tracking

This document explains the architecture and flow of **Phase 1** in simple, plain language.

---

## What Does Phase 1 Do?

Phase 1 provides a single, rock-solid gateway function called `call_llm()`. Every agent in the swarm (Planner, Researcher, Coder, etc.) uses this one function to communicate with the LLM. 

Instead of calling the AI provider directly, `call_llm()` acts like a smart middleman that:
1. **Validates input:** Rejects empty or invalid prompts immediately.
2. **Handles glitches automatically:** If there is a rate limit or timeout, it waits with exponential backoff and jitter, then retries.
3. **Tracks costs & latency:** Accurately measures token counts, estimated dollar cost, and response time in milliseconds for every single call.

---

## Flowchart: How `call_llm()` Works

```mermaid
flowchart TD
    Start(["Caller invokes call_llm(prompt)"]) --> CheckPrompt{"Is prompt empty or invalid?"}
    
    CheckPrompt -- "Yes" --> InputError["Raise LLMInputError (No network call wasted)"]
    
    CheckPrompt -- "No" --> StartTimer["Start latency timer & record start time"]
    
    StartTimer --> MakeCall["Send request to Groq API (openai/gpt-oss-120b)"]
    
    MakeCall --> CallResult{"Was call successful?"}
    
    CallResult -- "Network / Timeout / Rate Limit Error" --> CheckRetries{"Attempts left < max_retries?"}
    
    CheckRetries -- "Yes" --> Backoff["Sleep: Exponential backoff + random jitter"]
    Backoff --> MakeCall
    
    CheckRetries -- "No" --> FailError["Raise LLMCallError (All retries exhausted)"]
    
    CallResult -- "Success" --> StopTimer["Stop timer -> Calculate latency_ms"]
    
    StopTimer --> CountTokens["Extract input & output token counts"]
    
    CountTokens --> CalcCost["Calculate estimated cost (USD) based on rates"]
    
    CalcCost --> CreateUsage["Build LLMUsage object (tokens, cost, latency, status)"]
    
    CreateUsage --> ReturnResult(["Return: (response_text, usage)"])

    classDef success fill:#d4edda,stroke:#28a745,stroke-width:2px,color:#155724;
    classDef failure fill:#f8d7da,stroke:#721c24,stroke-width:2px,color:#721c24;
    classDef process fill:#e2e3e5,stroke:#383d41,stroke-width:2px,color:#383d41;
    
    class ReturnResult success;
    class InputError,FailError failure;
    class MakeCall,Backoff,CalcCost,CountTokens,CreateUsage process;
```

---

## Sequence Diagram: Step-by-Step Interaction

```mermaid
sequenceDiagram
    autonumber
    actor Caller as Agent / CLI (tools.llm_cli)
    participant Core as call_llm()
    participant Groq as Groq API (Cloud)
    participant Telemetry as LLMUsage & Logger

    Caller->>Core: call_llm("Write a poem")
    
    Note over Core: Validate prompt is non-empty
    Note over Core: Start stopwatch (latency_ms)

    Core->>Groq: POST /chat/completions (model, prompt, timeout)
    
    alt Transient Error (Rate limit 429 or Timeout)
        Groq-->>Core: RateLimitError / Timeout
        Note over Core: Wait base_backoff * 2^(attempt) + jitter
        Core->>Groq: Retry request
    end

    Groq-->>Core: 200 OK (completion text + token usage)
    
    Note over Core: Stop stopwatch -> Calculate latency
    Note over Core: Calculate estimated cost = (in_tokens * price_in) + (out_tokens * price_out)

    Core->>Telemetry: Record usage (in/out tokens, cost USD, latency ms)
    Core-->>Caller: Return (response_text, LLMUsage)
```

---

## Core Components Breakdown

| Component | What It Does | Why It Matters |
|---|---|---|
| **Input Validation** | Checks `prompt.strip()` | Prevents wasting API credits or failing silently on empty requests. |
| **Groq API Client** | Uses `openai/gpt-oss-120b` | Provides fast, hosted cloud inference without requiring local GPU hardware. |
| **Exponential Backoff + Jitter** | Exponential delay with random randomness | Prevents hammering the API when transient rate limits or network blips occur. |
| **`LLMUsage` Dataclass** | Holds tokens, cost, latency, status | Gives observability from Day 1, feeding directly into later reporting and logging phases. |
