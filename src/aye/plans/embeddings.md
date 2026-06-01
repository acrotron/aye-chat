# Embedding Backend Migration: ChromaDB + ONNX → model2vec + sqlite-vec

Replace the ChromaDB/ONNX Runtime embedding and vector storage backend with
**model2vec** (static embeddings, numpy-only) and **sqlite-vec** (C-extension
for SQLite) to achieve Python 3.14 compatibility.

---

## Motivation

- **ChromaDB** is blocked on Python 3.14 because its default embedding function
  depends on **onnxruntime**, which does not yet support 3.14.
- **model2vec** produces embeddings using only numpy (no PyTorch, no ONNX
  Runtime). Total dependency footprint: < 5 MB + numpy.
- **sqlite-vec** is a pure-C SQLite extension with minimal Python surface.
  Virtually no Python version risk.

## Dependency Changes

| Current              | Replacement       | Size       |
|----------------------|-------------------|------------|
| `chromadb`           | `sqlite-vec`      | ~1 MB      |
| `onnxruntime` (transitive via chromadb) | `model2vec` | ~1 MB + numpy |

Embedding model: `minishlab/potion-base-8M` (~30 MB download, cached
locally in `~/.cache/model2vec/`).

---

## Current Touchpoints (ChromaDB surface area)

| File | Usage |
|------|-------|
| `model/vector_db.py` | All ChromaDB operations: `initialize_index()`, `update_index_coarse()`, `refine_file_in_index()`, `delete_from_index()`, `query_index()` |
| `model/onnx_manager.py` | ONNX model download, cache-dir detection, `chromadb.utils.embedding_functions` |
| `model/index_manager.py` | Stores `self.collection` (ChromaDB collection object) |
| `model/index_manager_state.py` | `InitializationCoordinator` calls `vector_db.initialize_index()`, corruption recovery references `.aye/chroma_db` directory |
| `model/index_manager_executor.py` | Passes `collection` to `vector_db.*` functions |
| `controller/repl.py` | `db` command: `collection.count()`, `collection.peek()` |
| `controller/commands.py` | Imports `vector_db` (initialization) |

No other module imports `chromadb` directly.

---

## Architecture

### Before (ChromaDB)

```
index_manager  ──▸  vector_db.py (free functions)  ──▸  chromadb.Collection
                                                    ──▸  onnx_manager.py
```

### After (sqlite-vec + model2vec)

```
index_manager  ──▸  VectorStore (protocol)  ──▸  SqliteVecStore (class)
                                             ──▸  Embedder (protocol)  ──▸  Model2VecEmbedder
```

Key design decisions:
- Introduce a **`VectorStore` protocol** so the backend is swappable.
- The `Embedder` is injected into `VectorStore` at construction time.
- `collection` (ChromaDB object) is replaced by `VectorStore` instance
  throughout `index_manager*` modules.

---

## Implementation Steps

### Step 1 — Define `Embedder` protocol and `Model2VecEmbedder`

**New file:** `model/embedder.py`

```python
from typing import Protocol, List
import numpy as np


class Embedder(Protocol):
    """Protocol for text → embedding conversion."""

    @property
    def dimension(self) -> int:
        """Dimensionality of the output embedding vectors."""
        ...

    def embed(self, texts: List[str]) -> np.ndarray:
        """Embed a batch of text strings.

        Returns:
            numpy array of shape (len(texts), dimension), dtype float32.
        """
        ...


class Model2VecEmbedder:
    """Embedder backed by model2vec static embeddings."""

    MODEL_NAME = "minishlab/potion-base-8M"

    def __init__(self):
        from model2vec import StaticModel
        self._model = StaticModel.from_pretrained(self.MODEL_NAME)
        # model2vec models expose .dim
        self._dimension = self._model.dim

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: List[str]) -> np.ndarray:
        return self._model.encode(texts)
```

The `model2vec.StaticModel.from_pretrained()` auto-downloads and caches
the model on first use. No manual download step required.

**Risk:** Low. Additive new file, nothing depends on it yet.

---

### Step 2 — Define `VectorStore` protocol

**New file:** `model/vector_store.py`

```python
from typing import Protocol, List, Dict, Any, Optional
from aye.model.models import VectorIndexResult


class VectorStore(Protocol):
    """Abstract interface for vector index storage."""

    def upsert(
        self,
        ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> None:
        """Insert or update documents by ID."""
        ...

    def delete(self, ids: List[str]) -> None:
        """Delete documents by ID."""
        ...

    def delete_by_prefix(self, prefix: str) -> None:
        """Delete all documents whose ID starts with prefix."""
        ...

    def query(
        self,
        query_text: str,
        n_results: int = 10,
        min_relevance: float = -1.0,
    ) -> List[VectorIndexResult]:
        """Search for similar documents."""
        ...

    def count(self) -> int:
        """Total number of indexed documents."""
        ...

    def peek(self, limit: int = 5) -> Dict[str, Any]:
        """Return a sample of stored documents (for `db` command).

        Returns a dict with keys: 'ids', 'metadatas', 'documents'.
        """
        ...
```

**Risk:** Low. Additive, no callers yet.

---

### Step 3 — Implement `SqliteVecStore`

**New file:** `model/sqlite_vec_store.py`

This is the core implementation. It wraps sqlite-vec behind the
`VectorStore` protocol.

```
class SqliteVecStore:
    """VectorStore implementation backed by sqlite-vec."""

    def __init__(self, db_path: str, embedder: Embedder):
        ...
```

**Schema (two tables):**

```sql
-- Metadata table (regular SQLite)
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    document    TEXT NOT NULL,
    file_path   TEXT,
    chunk_index INTEGER,
    start_line  INTEGER,
    end_line    INTEGER
);

-- Vector table (sqlite-vec virtual table)
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    id          TEXT PRIMARY KEY,
    embedding   float[<dim>]
);
```

Where `<dim>` is obtained from `embedder.dimension` at init time.

**Key methods:**

| Method | SQL |
|--------|-----|
| `upsert()` | `INSERT OR REPLACE INTO chunks ...` + `INSERT OR REPLACE INTO vec_chunks ...` (embed texts via `embedder.embed()`) |
| `delete()` | `DELETE FROM chunks WHERE id = ?` + `DELETE FROM vec_chunks WHERE id = ?` |
| `delete_by_prefix()` | `DELETE FROM chunks WHERE id LIKE ?` + corresponding vec delete |
| `query()` | Embed query text, then `SELECT ... FROM vec_chunks WHERE embedding MATCH ? ORDER BY distance LIMIT ?`, join with `chunks` for metadata |
| `count()` | `SELECT COUNT(*) FROM chunks` |
| `peek()` | `SELECT id, document, file_path, chunk_index, start_line, end_line FROM chunks LIMIT ?` |

**sqlite-vec query syntax:**

```sql
SELECT id, distance
FROM vec_chunks
WHERE embedding MATCH ?
ORDER BY distance
LIMIT ?
```

The `?` parameter is the query embedding as raw bytes (`embedding.tobytes()`).
sqlite-vec uses L2 distance by default. To get cosine similarity:
- Normalize embeddings to unit length before insert and query.
- Then L2 distance and cosine distance are monotonically related.
- Score: `1.0 - (distance / 2.0)` maps L2 on unit vectors to cosine similarity.

**Embedding normalization helper:**

```python
def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return vectors / norms
```

Call `_normalize()` in both `upsert()` and `query()`.

**DB file location:** `.aye/vector_index.db` (replaces `.aye/chroma_db/`).

**Risk:** Medium — this is the most code-heavy step. Needs thorough
testing of upsert/delete/query round-trips and edge cases (empty DB,
duplicates, corrupted DB recovery).

---

### Step 4 — Replace `onnx_manager.py` with `embedding_manager.py`

**Delete:** `model/onnx_manager.py`

**New file:** `model/embedding_manager.py`

This replaces the ONNX model management with model2vec setup:

```python
"""Embedding model management for model2vec."""
from pathlib import Path
from typing import Optional


def is_embedding_model_cached() -> bool:
    """Check if the model2vec model is already downloaded."""
    # model2vec caches in ~/.cache/model2vec/ by default
    cache_dir = Path.home() / ".cache" / "model2vec"
    # Check for the specific model directory
    model_dir = cache_dir / "minishlab_potion-base-8M"
    return model_dir.exists()


def ensure_embedding_model() -> None:
    """Download the embedding model if not cached.

    model2vec.StaticModel.from_pretrained() handles download
    automatically, so this is mostly a pre-check for UX messaging.
    """
    if not is_embedding_model_cached():
        from rich import print as rprint
        rprint("[cyan]Downloading embedding model (~30MB, one-time)...[/]")

    from model2vec import StaticModel
    StaticModel.from_pretrained("minishlab/potion-base-8M")
```

**Risk:** Low. Replaces ONNX-specific logic with simpler model2vec
equivalent.

---

### Step 5 — Refactor `vector_db.py` from free functions to `VectorStore` factory

**File:** `model/vector_db.py`

The current file exposes free functions that take `collection: Any`.
Refactor to:

1. Remove all `chromadb` imports.
2. Keep `initialize_index()` as a **factory function** that returns a
   `VectorStore` instance:

```python
def initialize_index(db_dir: str, verbose: bool = False) -> VectorStore:
    """Create and return a VectorStore backed by sqlite-vec + model2vec."""
    from aye.model.embedder import Model2VecEmbedder
    from aye.model.sqlite_vec_store import SqliteVecStore

    db_path = os.path.join(db_dir, "vector_index.db")
    embedder = Model2VecEmbedder()
    return SqliteVecStore(db_path=db_path, embedder=embedder)
```

3. Remove `update_index_coarse()`, `refine_file_in_index()`,
   `delete_from_index()`, `query_index()` as free functions. Their
   logic moves into callers that use `VectorStore` methods directly.
   Alternatively, keep them as **thin wrappers** that delegate to
   `store.upsert()` / `store.delete()` / `store.query()` to minimize
   churn in `index_manager_executor.py`.

**Recommended approach (minimize churn):** Keep the free-function
signatures but change the first parameter from `collection: Any` to
`store: VectorStore`:

```python
def update_index_coarse(store: VectorStore, file_path: str, content: str) -> None:
    chunks = _chunk_file(content)
    ids = [f"{file_path}:{i}" for i in range(len(chunks))]
    metadatas = [{"file_path": file_path, "chunk_index": i} for i in range(len(chunks))]
    store.upsert(ids=ids, documents=chunks, metadatas=metadatas)

def refine_file_in_index(store: VectorStore, file_path: str, content: str) -> None:
    store.delete_by_prefix(f"{file_path}:")
    chunks = ast_chunker(content, _detect_language(file_path))
    if not chunks:
        chunks = _chunk_file(content)
    ids = [f"{file_path}:{i}" for i in range(len(chunks))]
    metadatas = [{"file_path": file_path, "chunk_index": i} for i in range(len(chunks))]
    store.upsert(ids=ids, documents=chunks, metadatas=metadatas)

def delete_from_index(store: VectorStore, file_path: str) -> None:
    store.delete_by_prefix(f"{file_path}:")

def query_index(store: VectorStore, query: str, n_results: int = 10, min_relevance: float = -1.0) -> List[VectorIndexResult]:
    return store.query(query, n_results=n_results, min_relevance=min_relevance)
```

This way, `index_manager_executor.py` changes are limited to passing
`store` instead of `collection`.

**Risk:** Medium — core plumbing change. All existing callers must
be updated.

---

### Step 6 — Update `index_manager*.py` modules

**Files:**
- `model/index_manager.py`
- `model/index_manager_state.py`
- `model/index_manager_executor.py`

Changes:

1. **`index_manager.py`**: Replace `self.collection` with `self.store: VectorStore`.
   All methods that currently pass `self.collection` to `vector_db.*`
   functions now pass `self.store`.

2. **`index_manager_state.py` (`InitializationCoordinator`):**
   - Change `vector_db.initialize_index()` call to use new factory
     signature.
   - Update corruption recovery path: instead of deleting
     `.aye/chroma_db/`, delete `.aye/vector_index.db`.
   - Return `VectorStore` instance instead of ChromaDB collection.

3. **`index_manager_executor.py` (`PhaseExecutor`):**
   - Replace `collection: Any` parameter/field with `store: VectorStore`.
   - Update all `vector_db.*()` calls to pass `store` instead of
     `collection`.

**Risk:** Medium — multiple files change but the diff is mechanical
(rename `collection` → `store`, update type hints).

---

### Step 7 — Update `db` command in `repl.py`

**File:** `controller/repl.py`

The `db` command currently calls:
```python
collection.count()
collection.peek(limit=5)
```

Replace with:
```python
store = index_manager.store   # VectorStore instance
count = store.count()
...
peek_data = store.peek(limit=5)
ids = peek_data.get('ids', [])
metadatas = peek_data.get('metadatas', [])
documents = peek_data.get('documents', [])
```

The `peek()` return format is identical to what ChromaDB returned,
so the display loop remains unchanged.

Also update the attribute check:
```python
# Before
if index_manager and hasattr(index_manager, 'collection') and index_manager.collection:

# After
if index_manager and hasattr(index_manager, 'store') and index_manager.store:
```

**Risk:** Low — small, localized change.

---

### Step 8 — Update `pyproject.toml` / `setup.cfg` dependencies

**Replace:**
```
chromadb>=0.4
```

**With:**
```
model2vec>=0.4
sqlite-vec>=0.1
```

Remove `onnxruntime` if it was listed as an explicit dependency
(it may only be a transitive dep of chromadb).

Verify that `numpy` is already in the dependency tree (it is — used by
multiple parts of the codebase).

**Risk:** Low.

---

### Step 9 — Migration: existing `.aye/chroma_db` → `.aye/vector_index.db`

When `initialize_index()` runs and finds no `.aye/vector_index.db` but
`.aye/chroma_db/` exists:

1. Log a message: `"Migrating vector index from ChromaDB to sqlite-vec..."`
2. Delete `.aye/chroma_db/` directory.
3. Clear `known_hashes.json` (forces full re-index).
4. Proceed with normal initialization.

This is a **destructive migration** — the old index is discarded and
rebuilt. This is acceptable because:
- Re-indexing is fast (~10s for medium projects).
- Embedding dimensions / models differ, so old vectors are useless.
- ChromaDB data cannot be read without `chromadb` installed.

**Risk:** Low. One-time operation, graceful fallback.

---

### Step 10 — Delete ChromaDB-specific files

**Delete:**
- `model/onnx_manager.py`

**Remove chromadb imports from:**
- `model/vector_db.py` (fully rewritten in Step 5)

**Risk:** Low (after all other steps are complete).

---

## Execution Order & Risk Summary

| Order | Step | Risk   | Notes |
|------:|------|--------|-------|
|     1 | `Embedder` protocol + `Model2VecEmbedder` | Low | New file, additive |
|     2 | `VectorStore` protocol                    | Low | New file, additive |
|     3 | `SqliteVecStore` implementation            | **Medium** | Most new code; needs tests |
|     4 | `embedding_manager.py` (replaces onnx_manager) | Low | Simpler than what it replaces |
|     5 | Refactor `vector_db.py`                    | **Medium** | Core plumbing change |
|     6 | Update `index_manager*.py`                 | Medium | Mechanical rename |
|     7 | Update `db` command in `repl.py`           | Low | Small change |
|     8 | Update dependencies                        | Low | pip-level change |
|     9 | Migration logic                            | Low | One-time, destructive |
|    10 | Delete `onnx_manager.py`                   | Low | Cleanup |

**Suggested PR split:**
- **PR 1 (Steps 1–3):** New files only. Add `Embedder`, `VectorStore`,
  `SqliteVecStore`. Include unit tests for `SqliteVecStore` in
  isolation (upsert, delete, query, count, peek).
- **PR 2 (Steps 4–7):** Wire it in. Replace chromadb usage throughout
  the codebase. Integration tests.
- **PR 3 (Steps 8–10):** Dependency swap, migration, cleanup.

---

## Testing Plan

### Unit Tests (Step 3)

- `test_sqlite_vec_store.py`:
  - `test_upsert_and_count`: upsert 10 chunks, verify `count() == 10`.
  - `test_upsert_idempotent`: upsert same ID twice, verify count stays
    the same and content is updated.
  - `test_delete_by_id`: upsert, delete, verify count drops.
  - `test_delete_by_prefix`: upsert `foo.py:0`, `foo.py:1`, `bar.py:0`.
    Delete prefix `foo.py:`, verify only `bar.py:0` remains.
  - `test_query_returns_results`: upsert code chunks, query with
    related text, verify top result is relevant.
  - `test_query_min_relevance`: verify low-relevance results are
    filtered out.
  - `test_query_empty_db`: query on empty DB returns `[]`.
  - `test_peek`: upsert 10 chunks, peek with limit=3, verify format.
  - `test_corrupted_db_recovery`: corrupt the .db file, verify
    `initialize_index()` recovers gracefully.

### Integration Tests (Step 6)

- Run the existing `index_manager` test suite (if any) against the new
  backend.
- Manual smoke test: `aye chat` on a real project, run `db` command,
  verify chunk counts and query behavior.

### Embedding Quality Spot-Check

- model2vec `potion-base-8M` is trained on general English. Code
  identifiers may embed differently than with the ONNX MiniLM model.
- Run a quick comparison: for 50 sample queries against a known
  codebase, compare top-5 retrieved files between old (ChromaDB/ONNX)
  and new (sqlite-vec/model2vec). Acceptable if >80% overlap.
- If quality drops significantly, consider `minishlab/potion-base-32M`
  (~120 MB) as a fallback model.

---

## What Does NOT Change

- **`ast_chunker.py`** — chunking logic is independent of storage.
- **`llm_invoker.py`** — calls `index_manager.query()` which is
  unchanged in signature.
- **`apply_updates` / snapshot pipeline** — completely unrelated.
- **Plugins** — no plugin touches vector storage.
- **`model/config.py`** — no config changes needed.
- **`index_manager_file_ops.py`** — file discovery / hashing is
  storage-agnostic.

---

## Rollback Plan

If model2vec or sqlite-vec proves problematic:
1. The `VectorStore` protocol allows swapping back to ChromaDB by
   implementing a `ChromaVecStore` class.
2. Dependencies are additive — chromadb can be re-added without
   breaking sqlite-vec.
3. Migration is not reversible (old ChromaDB data is deleted), but
   re-indexing with ChromaDB is trivial once the old code is restored.
