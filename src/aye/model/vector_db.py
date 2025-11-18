"""
This module encapsulates all interactions with the vector database (ChromaDB).

It uses a lightweight ONNX-based model for embeddings to avoid large dependencies
like PyTorch.
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any

import chromadb
# Use the lightweight ONNX embedding function included with chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

from aye.model.models import VectorIndexResult
from aye.model.ast_chunker import ast_chunker, get_language_from_file_path


@contextmanager
def suppress_stdout_stderr():
    """A context manager that redirects stdout and stderr to devnull"""
    with open(os.devnull, 'w') as fnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = fnull
        sys.stderr = fnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


def initialize_index(root_path: Path) -> Any:
    """
    Initializes a persistent ChromaDB client and gets or creates a collection.

    Args:
        root_path: The root directory of the project.

    Returns:
        The ChromaDB collection object, ready for use.
    """
    db_path = root_path / ".aye" / "chroma_db"
    db_path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(db_path))
    
    # Instantiate the lightweight ONNX embedding function.
    # This avoids pulling in PyTorch and is much smaller.
    # The first time this is called, it will download the ONNX model.
    # We suppress stdout/stderr to hide the download progress from the user.
    with suppress_stdout_stderr():
        embedding_function = ONNXMiniLM_L6_V2()

    # A collection is like a table in a traditional database
    collection = client.get_or_create_collection(
        name="project_code_index",
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"}  # Cosine similarity is good for text
    )
    return collection


def _chunk_file(content: str, chunk_size: int = 100, overlap: int = 10) -> List[str]:
    """
    Simple text chunker based on lines of code.

    TODO: This can be replaced with a more sophisticated, language-aware chunker.
    """
    lines = content.splitlines()
    if not lines:
        return []

    chunks = []
    for i in range(0, len(lines), chunk_size - overlap):
        chunk = "\n".join(lines[i:i + chunk_size])
        chunks.append(chunk)
    return chunks


def update_index_coarse(
    collection: Any, 
    files_to_update: Dict[str, str]
) -> None:
    """
    Performs a coarse, file-per-chunk update to the index.
    The document ID is the file path itself. This is for the fast, initial pass.
    """
    if not files_to_update:
        return

    ids = list(files_to_update.keys())
    documents = list(files_to_update.values())
    metadatas = [{"file_path": fp} for fp in ids]

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas
    )


def refine_file_in_index(collection: Any, file_path: str, content: str):
    """
    Refines the index for a single file by replacing its coarse chunk
    with fine-grained, AST-based chunks.
    """
    # 1. Delete the old coarse chunk, which used the file_path as its ID.
    collection.delete(ids=[file_path])

    # 2. Create and upsert the new fine-grained chunks.
    language_name = get_language_from_file_path(file_path)
    chunks = []
    if language_name:
        chunks = ast_chunker(content, language_name)

    # Fallback to line-based chunking if AST chunking fails or is not supported
    if not chunks:
        chunks = _chunk_file(content)

    if not chunks:
        return

    ids = [f"{file_path}:{i}" for i, _ in enumerate(chunks)]
    metadatas = [{"file_path": file_path} for _ in chunks]

    collection.upsert(
        documents=chunks,
        metadatas=metadatas,
        ids=ids
    )


def delete_from_index(collection: Any, deleted_files: List[str]):
    """
    Deletes all chunks associated with a list of file paths from the index.
    This handles both coarse (id=file_path) and fine-grained chunks.
    """
    if not deleted_files:
        return
    collection.delete(where={"file_path": {"$in": deleted_files}})


def query_index(
    collection: Any, 
    query_text: str, 
    n_results: int = 10,
    min_relevance: float = 0.0
) -> List[VectorIndexResult]:
    """
    Queries the vector index for context relevant to the user's prompt.

    Args:
        collection: The ChromaDB collection object.
        query_text: The user's prompt.
        n_results: The number of relevant chunks to retrieve.
        min_relevance: The minimum similarity score (0-1) for a chunk to be considered relevant.

    Returns:
        A list of VectorIndexResult objects. If filtering by `min_relevance` yields no results,
        it falls back to returning the top 10 most relevant chunks.
    """
    if not query_text:
        return []

    results = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        include=["metadatas", "documents", "distances"]
    )

    all_results = []
    # The result is a dictionary with lists for each requested field.
    # We need to iterate through them by index.
    ids = results.get('ids', [[]])[0]
    documents = results.get('documents', [[]])[0]
    metadatas = results.get('metadatas', [[]])[0]
    distances = results.get('distances', [[]])[0]

    for i in range(len(ids)):
        all_results.append(
            VectorIndexResult(
                file_path=metadatas[i].get("file_path", "unknown"),
                content=documents[i],
                score=1 - distances[i]  # Convert distance to similarity score
            )
        )
    
    if min_relevance > 0.0:
        filtered_results = [r for r in all_results if r.score >= min_relevance]

        # If filtering removes all results, but there were some initial matches,
        # fall back to returning the top 10 results regardless of score.
        # This ensures some context is always provided if anything is found at all.
        if not filtered_results and all_results:
            return all_results[:10]
        
        return filtered_results

    return all_results
