"""Tree-sitter based source chunking."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".java": "java"}
CHUNK_NODE_TYPES = {"function_definition", "class_definition", "method_definition", "function_declaration", "class_declaration"}


@dataclass(frozen=True)
class CodeChunk:
    chunk_id: str
    path: str
    language: str
    kind: str
    start_line: int
    end_line: int
    text: str


def chunk_file(path: Path, repo_root: Path) -> list[CodeChunk]:
    language = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if not language:
        return []
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError as exc:
        raise RuntimeError("Install tree-sitter-language-pack to index source files") from exc

    source = path.read_bytes()
    parser = get_parser(language)
    tree = parser.parse(source)
    nodes = []

    def visit(node):
        if node.type in CHUNK_NODE_TYPES:
            nodes.append(node)
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    relative = path.relative_to(repo_root).as_posix()
    if not nodes:
        nodes = [tree.root_node]
    chunks = []
    for node in nodes:
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
        if not text:
            continue
        location = f"{relative}:{node.start_point[0] + 1}-{node.end_point[0] + 1}"
        chunks.append(CodeChunk(location, relative, language, node.type, node.start_point[0] + 1, node.end_point[0] + 1, text))
    return chunks
