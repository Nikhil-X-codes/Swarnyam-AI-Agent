# CLI Reference

The **Swarnyam AI Agent** CLI (`main.py`) provides a unified, production-ready interface for all operations.

---

## Global Synopsis

```bash
python main.py [COMMAND] [OPTIONS]
```

### Global Commands

| Command | Summary |
|---|---|
| [`plan`](#plan) | Decomposes a natural language task into concrete steps and acceptance criteria. |
| [`index`](#index) | Parses a code repository via Tree-Sitter AST and indexes chunks into ChromaDB. |
| [`solve`](#solve) | Executes the autonomous 6-agent loop (Plan &rarr; Context &rarr; Code &rarr; Review &rarr; Debug). |
| [`eval`](#eval) | Runs the repeatable regression test harness against a benchmark task suite. |
| [`cost-report`](#cost-report) | Summarizes spend, token usage, and latencies grouped by agent role and backend. |
| [`last-run`](#last-run) | Inspects the step-by-step audit trace from the latest or specified run. |

---

## `plan`

Generate a structured execution plan and acceptance criteria without modifying files.

### Syntax

```bash
python main.py plan <TASK> [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `<TASK>` | `string` | *(Required)* | Natural language description of the coding goal. |
| `--offline` | `flag` | `False` | Force execution using the local GGUF model (`llama.cpp`) with no cloud APIs. |

### Example

```bash
python main.py plan "Add an exponential backoff retry helper"
```

??? example "Sample Output (JSON)"
    ```json
    {
      "steps": [
        "Create src/retry.py defining retry_with_backoff(max_retries, base_delay)",
        "Implement exponential wait loop with jitter handling exceptions",
        "Add unit tests in tests/test_retry.py asserting max retry caps"
      ],
      "acceptance_criteria": [
        "Function retries transient exceptions up to max_retries",
        "Delay scales exponentially: base_delay * (2 ** attempt)",
        "All unit tests pass with pytest"
      ]
    }
    ```

---

## `index`

Parse and index repository source code into a ChromaDB vector store.

### Syntax

```bash
python main.py index --repo <PATH> [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--repo` | `string` | *(Required)* | Path to the target code repository. |
| `--db` | `string` | `memory/chroma` | Destination directory for ChromaDB embeddings. |
| `--query` | `string` | `None` | Optional query to immediately test similarity retrieval after indexing. |

### Example

```bash
python main.py index --repo workspace/sample-calc --query "where is add defined?"
```

??? example "Sample Output (JSON)"
    ```json
    {
      "indexed_chunks": 4,
      "repo": "workspace/sample-calc"
    }
    ```

---

## `solve`

Plan, gather context, write code, review, and debug a task against a repository.

### Syntax

```bash
python main.py solve <TASK> --repo <PATH> [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `<TASK>` | `string` | *(Required)* | The feature request, bug fix, or refactor task. |
| `--repo` | `string` | *(Required)* | Path to the target repository. |
| `--db` | `string` | `work/chroma-phase3` | Path to ChromaDB index used by the Repo Context agent. |
| `--max-attempts` | `int` | `3` | Maximum Coder &rarr; Reviewer &rarr; Debugger iteration loops. |
| `--confidence-threshold` | `float` | `0.7` | Confidence gate threshold. Runs below this require human review. |
| `--sequential-context` | `flag` | `False` | Run context agents sequentially instead of in parallel. |
| `--offline` | `flag` | `False` | Force local offline fallback (`llama.cpp`) for all agent calls. |
| `--work-root` | `string` | `work/sandboxes` | Directory for internal sandbox workspaces (e.g. inside Docker). |

### Example

```bash
python main.py solve "Add a power function in src/calc.py" --repo workspace/sample-calc
```

??? example "Sample Output (JSON)"
    ```json
    {
      "status": "success",
      "attempts": 1,
      "confidence": 0.95,
      "security_sensitive": false,
      "message": "Task completed successfully",
      "run_id": "run-81ea8b69ba83",
      "report_path": "outputs/run-81ea8b69ba83.md"
    }
    ```

### Exit Codes

* `0`: Task succeeded or was routed to `human_review`.
* `1`: Task failed, hit maximum retries, or was rejected by security guardrails.

---

## `cost-report`

Display an aggregated financial and token usage summary across all runs.

### Syntax

```bash
python main.py cost-report [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--json` | `flag` | `False` | Output raw aggregated JSON instead of formatted ASCII tables. |

### Example

```bash
python main.py cost-report
```

??? example "Sample Output (Formatted)"
    ```text
    === Swarm Cost & Observability Report ===
    Total Spend:         $0.00000
    Total LLM Calls:     34
    Total Tokens:        57591 (33544 prompt / 24047 completion)

    --- Spend by Agent Role ---
    Agent           Calls    Tokens       Latency (ms)    Cost ($)  
    --------------------------------------------------------------
    architect       12       3623         10931.6         $0.00000   
    coder           8        35954        191592.0        $0.00000   
    debugger        4        7949         50114.6         $0.00000   
    planner         3        1719         27328.0         $0.00000   
    repo_context    4        4808         13747.6         $0.00000   
    researcher      2        3526         5663.6          $0.00000   

    --- Spend by Backend ---
    Backend         Calls    Tokens       Cost ($)  
    --------------------------------------------------
    groq            28       57304        $0.00000   
    local           5        262          $0.00000   
    openrouter      1        25           $0.00000   
    ```

---

## `last-run`

Query the chronological execution trace and prompts/responses from SQLite.

### Syntax

```bash
python main.py last-run [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--run-id` | `string` | `None` | Specific run ID to inspect. If omitted, shows the latest run. |
| `--json` | `flag` | `False` | Output complete step trace including prompts and responses as JSON. |

### Example

```bash
python main.py last-run
```

??? example "Sample Output (Formatted)"
    ```text
    === Run Details: run-81ea8b69ba83 ===
    Command:     solve
    Task:        Add a power function in src/calc.py
    Status:      success
    Started:     2026-09-22T10:28:39.648233+00:00
    Finished:    2026-09-22T10:30:15.112000+00:00
    Total Cost:  $0.00000
    Total Steps: 5

    --- Execution Steps ---
    Step  Agent           Backend      Model                     Tokens     Cost ($)   Status    
    ------------------------------------------------------------------------------------------
    1     planner         groq         openai/gpt-oss-120b       92/595     $0.00000   success   
    2     researcher      groq         openai/gpt-oss-120b       180/320    $0.00000   success   
    3     repo_context    groq         openai/gpt-oss-120b       210/180    $0.00000   success   
    4     architect       groq         openai/gpt-oss-120b       290/240    $0.00000   success   
    5     coder           groq         openai/gpt-oss-120b       680/410    $0.00000   success   
    ```

---

## `eval`

Execute the repeatable regression benchmark harness.

### Syntax

```bash
python main.py eval [OPTIONS]
```

### Options

| Flag | Type | Default | Description |
|---|---|---|---|
| `--repo` | `string` | `workspace/target-repo` | Target repository for evaluation checks. |
| `--tasks` | `string` | `eval/tasks.json` | Path to benchmark tasks definition JSON file. |
| `--task-ids` | `list` | `None` | Filter evaluation to specific task IDs (e.g. `calc-001`). |
| `--limit` | `int` | `None` | Maximum number of tasks to run. |
| `--results-dir` | `string` | `eval/results` | Directory where benchmark pass/fail comparisons are saved. |
| `--offline` | `flag` | `False` | Run evaluation suite using local fallback model. |
