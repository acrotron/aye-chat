# Stage 1 Implementation Plan: Send Relevant Fragments as Context

Based on `chunk_stitching.md` §2 and the current codebase.

## Goal

Reduce context size by sending only the retrieved chunks (with metadata)
instead of reading entire files from disk. **No changes to the edit flow** —
the LLM still outputs full files in `source_files`, and `apply_updates`
remains untouched.

---

## Current State (summary)

1. **Indexing pipeline:**
   `IndexManager` → `vector_db.refine_file_in_index()` → `ast_chunker()` or `_chunk_file()`.
   Each Chroma document stores:
   - `id`: `"<file_path>:<i>"`
   - `metadata`: `{ "file_path": file_path }`
   - `document`: chunk text.

2. **Retrieval:**
   `IndexManager.query()` → `vector_db.query_index()` →
   returns `VectorIndexResult(file_path, content, score)`.

3. **Context packing (`controller/llm_invoker.py`):**
   `_get_rag_context_files()` takes retrieved chunks, deduplicates by
   `file_path`, reads the **entire file** from disk, and sends that as
   `source_files`. All chunk-level granularity is lost.

---

## Step 1 — Extend `VectorIndexResult` with chunk metadata

**File:** `model/models.py`

Add three fields:

```python
@dataclass
class VectorIndexResult:
    file_path: str
    content: str
    score: float
    chunk_id: str = ""            # Chroma document id, e.g. "src/foo.py:3"
    start_line: Optional[int] = None   # 1-based start line
    end_line: Optional[int] = None     # 1-based end line
```

Purely additive. Existing consumers that only use `file_path`, `content`,
and `score` are unaffected.

**Risk:** Low — no downstream breakage.

---

## Step 2 — Make `ast_chunker` return line ranges

**File:** `model/ast_chunker.py`

Change `ast_chunker()` signature from:

```python
def ast_chunker(content: str, language_name: str) -> List[str]:
```

to:

```python
def ast_chunker(content: str, language_name: str) -> List[Tuple[str, int, int]]:
```

Each tuple: `(chunk_text, start_line, end_line)` — **1-based** line numbers.

Tree-sitter nodes already expose `node.start_point[0]` and
`node.end_point[0]` (0-based row). Add 1 to convert.

The whole-file fallback (when no AST captures match) returns:
`[(content, 1, len(content.splitlines()))]`.

Since the **only caller** is `vector_db.refine_file_in_index()`, a direct
change is safe — no backward-compat shim needed.

**Risk:** Low — single caller.

---

## Step 3 — Make `_chunk_file` return line ranges

**File:** `model/vector_db.py`

Change `_chunk_file()` from:

```python
def _chunk_file(content: str, chunk_size=100, overlap=10) -> List[str]:
```

to:

```python
def _chunk_file(content: str, chunk_size=100, overlap=10) -> List[Tuple[str, int, int]]:
```

Inside the loop you already have the slice indices `i` and
`i + chunk_size`. Record:

```python
start_line = i + 1                          # 1-based
end_line   = min(i + chunk_size, len(lines)) # 1-based inclusive
chunks.append((chunk_text, start_line, end_line))
```

**Risk:** Low — single caller (`refine_file_in_index`).

---

## Step 4 — Store `start_line` / `end_line` in Chroma metadata

**File:** `model/vector_db.py` → `refine_file_in_index()`

Currently metadata is just `{"file_path": file_path}`. Change to:

```python
chunks = ast_chunker(content, language_name)   # now List[Tuple[str,int,int]]
if not chunks:
    chunks = _chunk_file(content)               # also List[Tuple[str,int,int]]

if not chunks:
    return

texts      = [c[0] for c in chunks]
ids        = [f"{file_path}:{i}" for i in range(len(chunks))]
metadatas  = [
    {
        "file_path":   file_path,
        "chunk_index": i,
        "start_line":  c[1],
        "end_line":    c[2],
    }
    for i, c in enumerate(chunks)
]

collection.upsert(documents=texts, metadatas=metadatas, ids=ids)
```

`update_index_coarse()` keeps its current metadata — coarse chunks will
have `start_line = None` which is fine. They get replaced during
refinement.

**Risk:** Low — backward compatible with old chunks that lack the new
keys (they simply return `None` on `.get()`).

---

## Step 5 — Populate new fields in `query_index()`

**File:** `model/vector_db.py` → `query_index()`

When constructing `VectorIndexResult`, read from the Chroma results:

```python
VectorIndexResult(
    file_path  = metadatas[i].get("file_path", "unknown"),
    content    = documents[i],
    score      = 1 - distances[i],
    chunk_id   = ids[i],
    start_line = metadatas[i].get("start_line"),
    end_line   = metadatas[i].get("end_line"),
)
```

Old chunks without `start_line`/`end_line` return `None` —
downstream code must handle that gracefully.

**Risk:** Low.

---

## Step 6 — Replace `_get_rag_context_files` with snippet-based packing

**File:** `controller/llm_invoker.py`

Create a new function `_get_rag_context_snippets()` that replaces the
current whole-file approach:

```python
def _get_rag_context_snippets(
    prompt: str,
    conf: Any,
    verbose: bool,
) -> Dict[str, str]:
    """Retrieve chunk-level snippets for RAG context.

    Returns a dict of {snippet_key: snippet_text} packed up to
    CONTEXT_HARD_LIMIT bytes.
    """
    source_snippets: Dict[str, str] = {}

    retrieved_chunks = conf.index_manager.query(
        prompt,
        n_results=300,
        min_relevance=RELEVANCE_THRESHOLD,
    )
    if not retrieved_chunks:
        return {}

    current_size = 0
    for chunk in retrieved_chunks:
        # Build a human-readable key
        if chunk.start_line is not None and chunk.end_line is not None:
            snippet_key = f"{chunk.file_path} (lines {chunk.start_line}\u2013{chunk.end_line})"
        else:
            snippet_key = f"{chunk.file_path} [chunk]"

        snippet_text = chunk.content
        snippet_bytes = len(snippet_text.encode("utf-8"))

        if current_size + snippet_bytes > CONTEXT_HARD_LIMIT:
            break

        source_snippets[snippet_key] = snippet_text
        current_size += snippet_bytes

    return source_snippets
```

In `_determine_source_files`, for large projects (where `conf.use_rag`
is True), call `_get_rag_context_snippets` instead of
`_get_rag_context_files`.

**Risk:** **Medium** — this is the core behavioural change. The LLM now
sees fragments instead of full files. Test thoroughly with both reasoning
prompts (read-only) and editing prompts (LLM must still output complete
files in its response).

---

## Step 7 — Verify plugin message format

**Files:** `plugins/local_model.py`, `plugins/offline_llm.py`

Both plugins use the shared `build_user_message()` from
`model_plugin_utils.py` which appends source files as:

```
--- Source files are below. ---

** <key> **
\`\`\`
<content>
\`\`\`
```

Since the key now includes line range info
(e.g. `src/foo.py (lines 121–170)`), the LLM will understand these are
fragments. **No plugin code changes required** — it just works.

**Risk:** Low.

---

## Step 8 — Handle re-indexing of existing projects

Existing Chroma indexes won't have `start_line` / `end_line` in
metadata.

**Recommended approach: lazy migration.**

- Accept `None` for line fields gracefully everywhere.
- As files get re-refined (on modification or when the user deletes
  `.aye/chroma_db` and restarts), they'll get the new metadata.

No changes needed to `index_manager_executor.py` or
`index_manager_file_ops.py` — they don't touch chunk content or
metadata directly.

**Risk:** Low.

---

## Execution Order & Risk Summary

| Order | Step | Risk   | Notes |
|------:|------|--------|-------|
|     1 | Extend `VectorIndexResult`        | Low    | Additive, no breakage |
|     2 | `ast_chunker` returns tuples      | Low    | Single caller |
|     3 | `_chunk_file` returns tuples       | Low    | Single caller |
|     4 | Store line ranges in Chroma        | Low    | Backward compat with old data |
|     5 | Populate fields in `query_index`   | Low    | `None` for old chunks |
|     6 | Snippet-based RAG packing          | Medium | Core context change — test thoroughly |
|     7 | Verify plugin message format       | Low    | Should work automatically |
|     8 | Migration strategy                 | Low    | Lazy is safest |

Steps 1–5 can be landed as **one PR** (indexing infrastructure).
Step 6 is the behavioural change and should be a **separate PR**,
ideally behind a config toggle (`use_snippet_context`) during rollout.

---

## What NOT to Change in Stage 1

- **Edit flow stays full-file.** The LLM still outputs complete files
  in `source_files`. No patching.
- **`apply_updates` untouched.** Snapshot + write pipeline stays as-is.
- **Coarse indexing untouched.** Only refinement gets line metadata.
- **No new system prompt changes.** The LLM doesn't need special
  instructions yet — fragments are read-only context.
- **No changes to `index_manager_executor.py` or
  `index_manager_file_ops.py`** — they delegate to `vector_db` for
  actual chunk storage.
