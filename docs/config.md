# Configuration Reference

The **Swarnyam AI Agent** is configured through environment variables defined in a `.env` file at the repository root, or injected via container runtime flags (`--env-file .env`).

---

## Environment Variables Index

### 1. Primary Provider: Hosted Groq API

| Variable | Type | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | `string` | *(Required)* | Your Groq API key (`gsk_...`). |
| `GROQ_MODEL` | `string` | `openai/gpt-oss-120b` | Model identifier used for primary agent inference. |
| `GROQ_INPUT_COST_PER_MILLION` | `float` | `0.00` | Cost in USD per 1M prompt tokens for cost-report tracking. |
| `GROQ_OUTPUT_COST_PER_MILLION` | `float` | `0.00` | Cost in USD per 1M completion tokens for cost-report tracking. |
| `GROQ_BASE_URL` | `string` | `https://api.groq.com/openai/v1` | Optional override for mock testing or proxy routing. |

---

### 2. Secondary Provider: OpenRouter Fallback

| Variable | Type | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | `string` | `""` | Optional OpenRouter API key (`sk-or-v1-...`). |
| `OPENROUTER_MODEL` | `string` | `meta-llama/llama-3.2-3b-instruct:free` | Free-tier model to fail over to when Groq rate limits. |

---

### 3. Local Offline Fallback: `llama.cpp`

| Variable | Type | Default | Description |
|---|---|---|---|
| `OFFLINE_MODE` | `boolean` | `false` | When `true`, completely bypasses cloud networks and forces local GGUF execution. |
| `LOCAL_MODEL_PATH` | `string` | `models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` | Path to the quantized GGUF model file. |
| `LOCAL_LLM_TIMEOUT_SECONDS` | `float` | `180.0` | Execution timeout in seconds for CPU inference. |
| `LOCAL_LLM_CTX` | `int` | `4096` | Context window size for `llama-cpp-python`. |

---

### 4. Telemetry, Observability & Database

| Variable | Type | Default | Description |
|---|---|---|---|
| `SWARM_DB_PATH` | `string` | `memory/swarm_log.db` | Path to the SQLite database storing all run traces and LLM calls. |
| `SWARM_WORK_ROOT` | `string` | `work/sandboxes` | Directory where active sandbox repository clones are isolated. |
| `LLM_USAGE_LOG` | `string` | `memory/llm_usage.jsonl` | Append-only raw JSONL log for token usage and latencies. |
| `LLM_MAX_OUTPUT_TOKENS` | `int` | `4096` | Maximum generation tokens allocated per agent response. |

---

### 5. RAG & Vector Embeddings

| Variable | Type | Default | Description |
|---|---|---|---|
| `HF_HOME` | `string` | `work/hf-cache` | Directory where Hugging Face and SentenceTransformers caches models. |
| `HF_HUB_OFFLINE` | `int` | `0` | When set to `1` (or under `--offline`), forces SentenceTransformers to run from local disk cache. |
| `TRANSFORMERS_OFFLINE` | `int` | `0` | When set to `1`, disables external Hugging Face network requests. |

---

## Sample `.env` File

```ini
# Primary Cloud Provider
GROQ_API_KEY=gsk_your_actual_key_here
GROQ_MODEL=openai/gpt-oss-120b

# Secondary Remote Fallback
OPENROUTER_API_KEY=
OPENROUTER_MODEL=meta-llama/llama-3.2-3b-instruct:free

# Local Offline Inference
OFFLINE_MODE=false
LOCAL_MODEL_PATH=models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf
LOCAL_LLM_TIMEOUT_SECONDS=180.0

# Telemetry & Sandboxing
SWARM_DB_PATH=memory/swarm_log.db
SWARM_WORK_ROOT=work/sandboxes
LLM_USAGE_LOG=memory/llm_usage.jsonl
LLM_MAX_OUTPUT_TOKENS=4096

# Embedding Cache
HF_HOME=work/hf-cache
```
