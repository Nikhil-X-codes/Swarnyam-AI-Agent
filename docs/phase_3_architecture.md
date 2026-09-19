# Phase 3: RAG Pipeline (Repo Understanding)

This document explains the architecture, flow, and component design of **Phase 3** in simple, plain language.

---

## What Does Phase 3 Do?

Phase 3 gives the multi-agent swarm the ability to **intelligently "read" and understand a target codebase** before making plans or writing code.

Instead of stuffing an entire repository into the LLM context window (which blows token limits, inflates costs, and introduces hallucinations), Phase 3 implements a local, semantic **Retrieval-Augmented Generation (RAG)** pipeline:

1. **AST-Aware Code Chunking:** Uses `tree-sitter` (via `tree-sitter-language-pack`) to parse source files into Abstract Syntax Trees (AST). Rather than slicing code at arbitrary character or line counts, it extracts complete functions, classes, and method definitions across multiple programming languages (Python, JavaScript, TypeScript, TSX, Java).
2. **Local CPU Embedding Generation:** Converts each code chunk into normalized dense vector embeddings using `sentence-transformers` (`all-MiniLM-L6-v2`). Runs strictly on CPU without any GPU dependencies.
3. **ChromaDB Vector Storage:** Stores code chunks, line numbers, file paths, AST node kinds, and dense vectors in a persistent ChromaDB collection (`repo_code`).
4. **Semantic Retrieval:** Given a natural language task description (e.g. *"handle division by zero in calculator"*), retrieves the top-$N$ most relevant code chunks ranked by cosine similarity.
5. **CLI Interface:** Provides `python main.py index --repo <path> [--db <path>] [--query <text>]` to index repos and test query retrieval.
6. **Benchmark Task Candidates:** Starts drafting candidate coding tasks in `eval/phase3_candidates.md` to feed the benchmark suite in Phase 4.5.

---

## Architecture Diagram

```mermaid
graph TB
    subgraph Ingestion ["1. Repo Ingestion & AST Parsing"]
        TargetRepo["Target Repository<br/>(Source Files: .py, .js, .ts, .java)"] --> Walker["File Walker<br/>(Excludes .git, checks extensions)"]
        Walker --> Chunker["tree-sitter Chunker<br/>(rag/chunker.py)"]
        Chunker --> AST["AST Node Identification<br/>function_definition / class_definition"]
        AST --> CodeChunks["Structured CodeChunks<br/>(Path, Lines, Kind, Source Code)"]
    end

    subgraph EmbeddingDB ["2. Vector Indexing & Storage"]
        CodeChunks --> Embedder["SentenceTransformer<br/>('all-MiniLM-L6-v2', CPU-only)"]
        Embedder --> Vectors["Dense Normalized Embeddings<br/>(384 dimensions)"]
        Vectors --> Chroma["ChromaDB Persistent Store<br/>(Collection: 'repo_code')"]
        CodeChunks -. Metadata & Text .-> Chroma
    end

    subgraph QueryRetrieval ["3. Query & Semantic Search"]
        TaskQuery["Task Description / Query<br/>e.g. 'handle zero division'"] --> QueryEmbed["Encode Query with Embedder"]
        QueryEmbed --> VectorSearch["ChromaDB Vector Search<br/>(Cosine / L2 Distance)"]
        Chroma --> VectorSearch
        VectorSearch --> TopChunks["Top-N Relevant Code Chunks<br/>+ File Paths, Line Numbers, Distance"]
    end

    subgraph SwarmConsumption ["4. Swarm Agent Consumption"]
        TopChunks --> RepoContext["agents/repo_context.py<br/>(Summarize & Format Chunks)"]
        RepoContext --> Planner["Planner Agent"]
        RepoContext --> Architect["Architect Agent"]
        RepoContext --> Coder["Coder Agent"]
    end

    classDef ingestion fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d47a1;
    classDef storage fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1b5e20;
    classDef query fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#bf360c;
    classDef agents fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#4a148c;

    class TargetRepo,Walker,Chunker,AST,CodeChunks ingestion;
    class Embedder,Vectors,Chroma storage;
    class TaskQuery,QueryEmbed,VectorSearch,TopChunks query;
    class RepoContext,Planner,Architect,Coder agents;
```

---

## Detailed Process Flowchart

```mermaid
flowchart TD
    Start(["CLI: python main.py index --repo <path>"]) --> CheckRepo{"Does repo path exist and is dir?"}
    
    CheckRepo -- "No" --> ErrRepo["Raise ValueError: Repository path does not exist"]
    CheckRepo -- "Yes" --> InitDB["Initialize ChromaDB client & SentenceTransformer embedder"]
    
    InitDB --> ScanFiles["Scan repo with rglob('*')"]
    ScanFiles --> FilterFile{"Is file, not in .git, and supported extension?"}
    
    FilterFile -- "Skip" --> NextFile["Move to next file"]
    FilterFile -- "Yes" --> TreeSitterParse["Parse file AST with tree-sitter-language-pack"]
    
    TreeSitterParse --> TraverseAST["Traverse AST root for functions/classes"]
    TraverseAST --> HasNodes{"Any function/class nodes found?"}
    
    HasNodes -- "No" --> RootFallback["Fallback to whole file (root_node)"]
    HasNodes -- "Yes" --> ExtractChunks["Extract text, start_line, end_line, kind"]
    RootFallback --> ExtractChunks
    
    ExtractChunks --> CollectChunks["Append to list of CodeChunks"]
    CollectChunks --> HasMoreFiles{"More files in repo?"}
    HasMoreFiles -- "Yes" --> ScanFiles
    HasMoreFiles -- "No" --> CheckEmpty{"Any chunks extracted?"}
    
    CheckEmpty -- "No" --> ReturnZero["Return 0 (no code indexed)"]
    CheckEmpty -- "Yes" --> BatchEmbed["Batch encode documents with SentenceTransformer (CPU)"]
    
    BatchEmbed --> ChromaUpsert["collection.upsert(ids, documents, embeddings, metadatas)"]
    ChromaUpsert --> OutputIndexed["Print indexed chunks count JSON"]
    
    OutputIndexed --> HasQuery{"Was --query provided?"}
    HasQuery -- "No" --> Done(["Finish Execution"])
    HasQuery -- "Yes" --> CheckQueryEmpty{"Is query non-empty?"}
    
    CheckQueryEmpty -- "No" --> ErrQuery["Raise ValueError: query must be non-empty"]
    CheckQueryEmpty -- "Yes" --> EmbedQuery["Embed query text with embedder"]
    
    EmbedQuery --> ChromaQuery["collection.query(query_embeddings, n_results=top_n)"]
    ChromaQuery --> FormatResults["Format documents, metadatas, and distances"]
    FormatResults --> PrintResults(["Output formatted JSON to stdout"])
    
    classDef success fill:#d4edda,stroke:#28a745,stroke-width:2px,color:#155724;
    classDef failure fill:#f8d7da,stroke:#721c24,stroke-width:2px,color:#721c24;
    classDef process fill:#e2e3e5,stroke:#383d41,stroke-width:2px,color:#383d41;

    class OutputIndexed,PrintResults,Done success;
    class ErrRepo,ErrQuery failure;
    class CheckRepo,InitDB,ScanFiles,FilterFile,TreeSitterParse,TraverseAST,ExtractChunks,BatchEmbed,ChromaUpsert,EmbedQuery,ChromaQuery process;
```

---

## Sequence Diagram: Indexing and Retrieval Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Developer
    participant CLI as main.py
    participant Indexer as rag/indexer.py (RepoIndexer)
    participant Chunker as rag/chunker.py (chunk_file)
    participant TreeSitter as tree-sitter parser
    participant Embedder as SentenceTransformer
    participant Chroma as ChromaDB PersistentClient

    User->>CLI: python main.py index --repo ./sample-repo --query "division by zero"
    CLI->>Indexer: RepoIndexer(db_path="memory/chroma")
    Indexer->>Embedder: SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    Indexer->>Chroma: get_or_create_collection("repo_code")
    
    CLI->>Indexer: index_repo("./sample-repo")
    
    loop For each source file (.py, .js, .ts, .tsx, .java)
        Indexer->>Chunker: chunk_file(file_path, repo_root)
        Chunker->>TreeSitter: parse(file_bytes)
        TreeSitter-->>Chunker: AST node tree
        Chunker->>Chunker: Extract function/class definitions + line ranges
        Chunker-->>Indexer: list[CodeChunk]
    end
    
    Indexer->>Embedder: encode([chunk.text, ...], normalize_embeddings=True)
    Embedder-->>Indexer: list[embeddings] (float vectors)
    
    Indexer->>Chroma: collection.upsert(ids, documents, embeddings, metadatas)
    Chroma-->>Indexer: Success
    Indexer-->>CLI: count of indexed chunks
    
    rect rgb(240, 248, 255)
        note over CLI,Chroma: Query Execution Step
        CLI->>Indexer: retrieve("division by zero", top_n=5)
        Indexer->>Embedder: encode(["division by zero"])
        Embedder-->>Indexer: query_embedding
        Indexer->>Chroma: collection.query(query_embeddings, n_results=5)
        Chroma-->>Indexer: matched documents, metadatas, distances
        Indexer-->>CLI: list[dict] (matched chunks with scores)
    end
    
    CLI-->>User: JSON dump of indexed count and retrieved code snippets
```

---

## Key Components and Contracts

### 1. `CodeChunk` Data Structure ([rag/chunker.py](file:///d:/Projects/Swarm%20Agent/rag/chunker.py))

```python
@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str      # E.g. "calc.py:10-25"
    path: str          # Relative file path inside repo
    language: str      # python, javascript, typescript, java
    kind: str          # function_definition, class_definition, etc.
    start_line: int    # 1-indexed start line
    end_line: int      # 1-indexed end line
    text: str          # Source code snippet
```

### 2. Supported Languages & Node Types

| Language | File Extensions | Tree-Sitter Node Types Captured |
|---|---|---|
| **Python** | `.py` | `function_definition`, `class_definition` |
| **JavaScript** | `.js` | `function_declaration`, `class_declaration`, `method_definition` |
| **TypeScript / TSX**| `.ts`, `.tsx` | `function_declaration`, `class_declaration`, `method_definition` |
| **Java** | `.java` | `method_declaration`, `class_declaration` |

*Fallback:* If a file contains top-level procedural code without functions or classes, the entire root node is preserved as a single chunk rather than being dropped.

### 3. `RepoIndexer` API ([rag/indexer.py](file:///d:/Projects/Swarm%20Agent/rag/indexer.py))

* **`index_repo(repo_path: str | Path) -> int`**:
  * Traverses all supported files in `repo_path`.
  * Computes embeddings and upserts into ChromaDB.
  * Returns total chunks indexed.
* **`retrieve(query: str, top_n: int = 5) -> list[dict]`**:
  * Validates non-empty query.
  * Performs vector cosine nearest-neighbor search.
  * Returns list of dictionaries containing `text`, `metadata`, and `distance`.

---

## How to Run & Verify

### Run the CLI Indexer
```bash
# Index a target repository and query it
python main.py index --repo ./sample-repo --query "divide by zero validation"
```

### Run Phase 3 Automated Tests
```bash
# Execute Phase 3 unit tests
.venv\Scripts\python -m pytest tests/test_phase3.py -v
```

### Success Check Criteria
- Ingest a known repository.
- Execute 3 distinct natural language task queries.
- In each case, verify that the top retrieved chunk corresponds to the exact module/function responsible for that task.
