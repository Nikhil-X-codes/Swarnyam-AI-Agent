"""Tests for Phase 3: RAG Pipeline (Repo Understanding)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag.chunker import CodeChunk, chunk_file
from rag.indexer import RepoIndexer


class FakeEmbedder:
    """Fast in-memory mock embedder for deterministic, offline testing."""

    def __init__(self, dim: int = 8):
        self.dim = dim

    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        import numpy as np

        vectors = []
        for text in texts:
            # Deterministic pseudo-embedding based on hash of text
            val = float(sum(ord(c) for c in text) % 100) / 100.0
            vec = np.zeros(self.dim, dtype=np.float32)
            vec[0] = val
            vec[1] = 1.0 - val
            vectors.append(vec)
        return np.array(vectors)


def test_chunk_file_python(tmp_path: Path):
    sample_code = (
        "def calculate_total(items):\n"
        "    return sum(items)\n\n"
        "class InvoiceCalculator:\n"
        "    def apply_discount(self, amount, discount):\n"
        "        return amount * (1.0 - discount)\n"
    )
    code_file = tmp_path / "calc.py"
    code_file.write_text(sample_code, encoding="utf-8")

    chunks = chunk_file(code_file, tmp_path)
    assert len(chunks) >= 2

    kinds = [c.kind for c in chunks]
    assert any("function" in k for k in kinds)
    assert any("class" in k for k in kinds)

    for chunk in chunks:
        assert isinstance(chunk, CodeChunk)
        assert chunk.path == "calc.py"
        assert chunk.language == "python"
        assert chunk.start_line >= 1
        assert chunk.end_line >= chunk.start_line
        assert chunk.text.strip() != ""


def test_chunk_file_unsupported_extension(tmp_path: Path):
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("just some text", encoding="utf-8")

    chunks = chunk_file(txt_file, tmp_path)
    assert chunks == []


def test_repo_indexer_indexing_and_retrieval(tmp_path: Path):
    repo_dir = tmp_path / "sample_repo"
    repo_dir.mkdir()
    (repo_dir / "math_utils.py").write_text(
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        raise ZeroDivisionError('cannot divide by zero')\n"
        "    return a / b\n\n"
        "def multiply(a, b):\n"
        "    return a * b\n",
        encoding="utf-8",
    )
    (repo_dir / "ignored.txt").write_text("ignored file", encoding="utf-8")

    db_dir = tmp_path / "chroma_db"
    fake_embedder = FakeEmbedder()

    indexer = RepoIndexer(db_path=db_dir, embedder=fake_embedder)
    indexed_count = indexer.index_repo(repo_dir)
    assert indexed_count >= 2

    # Query retrieval
    results = indexer.retrieve("divide numbers with zero division check", top_n=2)
    assert len(results) >= 1
    assert "text" in results[0]
    assert "metadata" in results[0]
    assert "distance" in results[0]
    assert results[0]["metadata"]["path"] == "math_utils.py"


def test_repo_indexer_empty_query_raises_value_error(tmp_path: Path):
    db_dir = tmp_path / "chroma_db"
    indexer = RepoIndexer(db_path=db_dir, embedder=FakeEmbedder())

    with pytest.raises(ValueError, match="query must be non-empty"):
        indexer.retrieve("   ")


def test_repo_indexer_nonexistent_repo_raises_value_error(tmp_path: Path):
    db_dir = tmp_path / "chroma_db"
    indexer = RepoIndexer(db_path=db_dir, embedder=FakeEmbedder())

    with pytest.raises(ValueError, match="Repository path does not exist"):
        indexer.index_repo(tmp_path / "nonexistent_dir")


def test_main_cli_index_command(tmp_path: Path, monkeypatch, capsys):
    from unittest.mock import MagicMock, patch
    import main

    mock_indexer = MagicMock()
    mock_indexer.index_repo.return_value = 3
    mock_indexer.retrieve.return_value = [{"text": "def add(): pass", "metadata": {}, "distance": 0.1}]

    with patch("rag.indexer.RepoIndexer", return_value=mock_indexer):
        monkeypatch.setattr(
            "sys.argv",
            ["main.py", "index", "--repo", str(tmp_path), "--query", "add"]
        )
        main.main()

    captured = capsys.readouterr()
    assert '"indexed_chunks": 3' in captured.out
    assert "def add(): pass" in captured.out
    mock_indexer.index_repo.assert_called_once_with(str(tmp_path))
    mock_indexer.retrieve.assert_called_once_with("add")

