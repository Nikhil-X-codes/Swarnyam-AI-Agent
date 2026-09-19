"""ChromaDB-backed repository index and semantic retrieval."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rag.chunker import SUPPORTED_EXTENSIONS, chunk_file


class RepoIndexer:
    def __init__(self, db_path: str | Path, *, embedding_model: str = "all-MiniLM-L6-v2", embedder: Any | None = None):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        os.environ.setdefault("HF_HOME", str(Path.cwd() / "work" / "hf-cache"))
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("Phase 3 requires chromadb") from exc
        self._chromadb = chromadb
        self.db_path = Path(db_path)
        if embedder is not None:
            self.embedder = embedder
        else:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("Phase 3 requires sentence-transformers") from exc
            
            hf_home = Path(os.environ["HF_HOME"])
            cache_dir = hf_home / "hub" / f"models--sentence-transformers--{embedding_model}"
            cached_locally = cache_dir.is_dir()
            local_only = os.getenv("OFFLINE_MODE", "false").lower() in ("true", "1") or cached_locally

            self.embedder = SentenceTransformer(embedding_model, device="cpu", local_files_only=local_only)
        self.client = chromadb.PersistentClient(path=str(self.db_path), settings=chromadb.config.Settings(anonymized_telemetry=False))
        self.collection = self.client.get_or_create_collection("repo_code")

    def index_repo(self, repo_path: str | Path) -> int:
        root = Path(repo_path).resolve()
        if not root.is_dir():
            raise ValueError(f"Repository path does not exist: {root}")
        chunks = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and ".git" not in path.parts:
                chunks.extend(chunk_file(path, root))
        if not chunks:
            return 0
        documents = [chunk.text for chunk in chunks]
        embeddings = self.embedder.encode(documents, normalize_embeddings=True).tolist()
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=documents,
            embeddings=embeddings,
            metadatas=[{"path": c.path, "language": c.language, "kind": c.kind, "start_line": c.start_line, "end_line": c.end_line} for c in chunks],
        )
        return len(chunks)

    def retrieve(self, query: str, *, top_n: int = 5) -> list[dict]:
        if not query.strip():
            raise ValueError("query must be non-empty")
        embedding = self.embedder.encode([query], normalize_embeddings=True).tolist()
        result = self.collection.query(query_embeddings=embedding, n_results=top_n)
        rows = []
        for i, document in enumerate(result.get("documents", [[]])[0]):
            rows.append({"text": document, "metadata": result["metadatas"][0][i], "distance": result["distances"][0][i]})
        return rows
