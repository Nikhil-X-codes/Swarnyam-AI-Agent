# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Prevent Python from writing .pyc files and force unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    SWARM_DB_PATH=/app/memory/swarm_log.db \
    SWARM_WORK_ROOT=/app/work/sandboxes \
    LOCAL_MODEL_PATH=/app/models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf

# Install compilation and runtime prerequisites:
# - build-essential & cmake: required for compiling llama-cpp-python and tree-sitter bindings
# - git: required for unidiff patch application and repository operations
# - curl: healthchecks and network tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Upgrade pip toolchain
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip setuptools wheel

# Install CPU-only PyTorch first:
# By default, sentence-transformers pulls the GPU/CUDA version of torch (3.5+ GB of nvidia-* packages).
# Installing the official CPU-only build first (~170 MB) prevents this massive download.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining project dependencies
ENV CMAKE_ARGS="-DGGML_CUDA=OFF"
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache SentenceTransformers embeddings into container image cache
# This ensures ChromaDB / RAG indexing functions completely offline without runtime model downloads
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy application source code
COPY . .

# Create internal directory structure:
# - /workspace/target-repo: mount point for target repositories
# - /app/models: mount point for local GGUF models (not baked into image)
# - /app/work/sandboxes: isolated runtime sandboxes (strictly inside container)
# - /app/outputs: mount point for generated Markdown run reports
# - /app/memory: persistent SQLite audit traces
RUN mkdir -p /workspace/target-repo \
             /app/models \
             /app/work/sandboxes \
             /app/outputs \
             /app/memory

# Expose default CLI entrypoint
ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
