# Docker Containerization Guide

This guide covers running **Swarnyam AI Agent** inside an isolated Docker container with decoupled volume mounts, sandbox safety, and offline model execution.

---

## Why Containerize?

Executing AI-generated code directly on a host workstation presents safety and environment risks:
* **Host Contamination:** A generated script could accidentally modify host files or leak environment variables.
* **Dependency Drift:** Different developer machines might have mismatched Python, C++, or system libraries.
* **Sandbox Boundary:** By running inside a container, diff application and unit test execution are strictly jailed within container-internal storage (`/app/work/sandboxes`).

---

## Architecture & Volume Mounting Strategy

```
Host Machine                          Docker Container (/app)
┌───────────────────────────────┐     ┌────────────────────────────────────┐
│ ./workspace/sample-calc       │──v─▶│ /workspace/target-repo             │ (Target Code)
│ ./models/*.gguf               │──v─▶│ /app/models:ro                     │ (Read-Only Models)
│ ./outputs/                    │◀─v──│ /app/outputs/                      │ (Reports & Diffs)
│ ./memory/                     │◀─v──│ /app/memory/                       │ (SQLite Database)
│                               │     │                                    │
│ [Host Filesystem Protected]   │     │ /app/work/sandboxes/active [Clone] │ (Isolated Execution)
└───────────────────────────────┘     └────────────────────────────────────┘
```

---

## Quickstart

### 1. Build the Container Image

```bash
docker build -t swarm-agent:latest .
```

* Builds on `python:3.11-slim`.
* Pre-caches `all-MiniLM-L6-v2` SentenceTransformers weights inside the image.
* Compiles native C++ tree-sitter parsers and `llama-cpp-python` with `CMAKE_ARGS="-DGGML_CUDA=OFF"`.

---

### 2. Run a Plan Command

```bash
docker run --rm --env-file .env swarm-agent plan "Add an exponential backoff helper"
```

---

### 3. Index a Repository

```bash
docker run --rm --env-file .env \
  -v "${PWD}/workspace/sample-calc:/workspace/target-repo" \
  swarm-agent index --repo /workspace/target-repo
```

---

### 4. Solve a Repository Task

```bash
docker run --rm --env-file .env \
  -v "${PWD}/workspace/sample-calc:/workspace/target-repo" \
  -v "${PWD}/models:/app/models:ro" \
  -v "${PWD}/outputs:/app/outputs" \
  swarm-agent solve "Add a power function in src/calc.py" --repo /workspace/target-repo
```

---

### 5. Run Completely Offline (Local GGUF Fallback)

To run without internet access, mount the host `models/` folder into `/app/models:ro` and pass `--offline`:

```bash
docker run --rm \
  -v "${PWD}/workspace/sample-calc:/workspace/target-repo" \
  -v "${PWD}/models:/app/models:ro" \
  -v "${PWD}/outputs:/app/outputs" \
  swarm-agent solve "Add a power function in src/calc.py" --repo /workspace/target-repo --offline
```

---

## Docker Compose Reference

You can also use `docker-compose.yml` to simplify volume mounting:

```yaml
services:
  swarm:
    build: .
    image: swarm-agent:latest
    env_file:
      - .env
    volumes:
      - ./workspace/sample-calc:/workspace/target-repo
      - ./models:/app/models:ro
      - ./outputs:/app/outputs
      - ./memory:/app/memory
```

### Running with Docker Compose

```bash
# Execute plan
docker compose run --rm swarm

# Execute solve
docker compose run --rm swarm solve "Add a power function in src/calc.py" --repo /workspace/target-repo
```
