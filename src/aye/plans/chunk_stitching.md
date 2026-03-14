============================================================
1. Where you are now
============================================================

- Chunks are created and stored per file:
  - `IndexManager` → `vector_db.refine_file_in_index()` → `ast_chunker()` or `_chunk_file()`.
  - Each stored Chroma document has:
    - `id`: `"<file_path>:<i>"`
    - `metadata`: `{ "file_path": file_path }`
    - `document`: the chunk text.

- Retrieval:
  - `IndexManager.query()` → `vector_db.query_index()` → returns `VectorIndexResult(file_path, content, score)` for top chunks.

- But when preparing context for the LLM:
  - `_get_rag_context_files()` (in `controller/llm_invoker.py`) ignores the chunk text and just collects **unique file paths** from the retrieved chunks:
    ```python
    unique_files_ranked = []
    ... for chunk in retrieved_chunks: unique_files_ranked.append(chunk.file_path)
    ```
  - Then it reads the **entire file** from disk and sends that as `source_files`.

So: you already have chunk‑level retrieval, but the last step reverts to full‑file context.

============================================================
2. Stage 1: Send only relevant fragments as context
============================================================

Goal of this stage: reduce context size by sending only the retrieved chunks (maybe with a bit of padding), **without changing how edits are applied** (still full‑file
updates for now).

### 2.1. Represent chunk identity and location

Right now you lose chunk id and location when building `VectorIndexResult`.

**Changes (conceptual):**

1) Extend `VectorIndexResult` to carry chunk metadata:
   - Add fields like:
     - `chunk_id: str`  – Chroma doc id (currently `"file_path:i"`).
     - Optional: `start_line: int`, `end_line: int`.

2) Extend `vector_db.query_index()` to populate them:
   - You already have `ids` and `metadatas` returned from Chroma:
     ```python
     ids = results.get('ids', [[]])[0]
     metadatas = results.get('metadatas', [[]])[0]
     ```
   - Set:
     - `chunk_id = ids`
     - `start_line = metadatas.get("start_line")`
     - `end_line   = metadatas.get("end_line")`

3) Extend the index metadata when refining:

   - In `vector_db.refine_file_in_index()`:
     - For AST chunks: adjust `ast_chunker` to optionally return `(text, start_line, end_line)` per chunk and store those in `metadatas`.
     - For `_chunk_file` fallback: you already have slicing by lines, so you can compute `start_line`/`end_line` directly.

   Conceptually:
   ```python
   metadatas = [
       {
           "file_path": file_path,
           "chunk_index": i,
           "start_line": start_line,
           "end_line": end_line,
       }
       for i, (chunk_text, start_line, end_line) in enumerate(chunks)
   ]
   ```

You don’t have to implement this all at once; you can start with just `chunk_id` and add line ranges later for smarter stitching.

### 2.2. Change RAG packing to use chunks, not whole files

Instead of taking retrieved chunks → deduplicate by `file_path` → send full files, you want:

- Take retrieved chunks (already sorted by relevance).
- Optionally merge adjacent/overlapping chunks from the same file.
- Pack **only those snippets** into the context until you hit `CONTEXT_TARGET_SIZE`.

Conceptually, replace `_get_rag_context_files` with a “snippets” version:

```python
# PSEUDO‑CODE / ALGORITHM ONLY

def _get_rag_context_snippets(prompt: str, conf: Any, verbose: bool) -> Dict:
    source_snippets: Dict = {}

    retrieved_chunks: List[VectorIndexResult] = conf.index_manager.query(
        prompt, n_results=300, min_relevance=RELEVANCE_THRESHOLD
    )

    if not retrieved_chunks:
        return {}

    current_size = 0
    for chunk in retrieved_chunks:
        snippet_key = f"{chunk.file_path} "
        snippet_text = chunk.content

        snippet_bytes = len(snippet_text.encode("utf-8"))
        if current_size + snippet_bytes > CONTEXT_HARD_LIMIT:
            break

        source_snippets = snippet_text
        current_size += snippet_bytes

    return source_snippets
```

Then in `_determine_source_files`:
- For large projects, call `_get_rag_context_snippets` instead of `_get_rag_context_files`.

### 2.3. How the LLM sees these snippets

Your local/offline plugins build the user message like this:

- `local_model.py` → `_build_user_message`
- `offline_llm.py` → `_build_user_message`

They append each entry from `source_files` as:

```text
--- Source files are below. ---

** <key> **
```
<content>
```
```

If `key` includes file path and maybe line range, the LLM will understand it’s a fragment, e.g.:

```text
** src/foo.py  **
```
<fragment text>
```
```

No schema change is needed for **context‑only** use: you just label your fragments clearly.

At this stage:
- RAG gives you **fragments as read‑only context**.
- Editing still expects the LLM to output **full file contents** in the response `source_files` (as today).
- There’s no special stitching; you just use `apply_updates` as now.

This already drastically cuts context size for read‑only questions or reasoning‑style prompts.

============================================================
3. Stage 2: Chunk‑level edits with client‑side stitching
============================================================

This is what you asked explicitly: only send fragments for both reading **and** writing, then reconstruct full files on the client.

To do this robustly you need three pieces:

1) Stable chunk identifiers + location mapping on disk.
2) A clear patch‑style response format from the LLM.
3) A client‑side patching engine that converts patches → full updated files → `apply_updates`.

### 3.1. Stable chunk ids and locations

You already have predictable chunk ids in the vector DB (`"file_path:i"`). Extend that to carry location:

- For AST chunks (in `ast_chunker`):
  - Use `node.start_point` / `node.end_point` from tree‑sitter to get `(start_row, start_col)` and `(end_row, end_col)`.
  - Convert rows to **1‑based line numbers** and store in metadata.

- For line‑based `_chunk_file` fallback:
  - You’re already slicing on lines; record `start_line` and `end_line` when creating the chunk.

So your metadata per chunk becomes roughly:

```json
{
  "file_path": "src/foo.py",
  "chunk_index": 5,
  "start_line": 121,
  "end_line": 170
}
```

On retrieval, `VectorIndexResult` should expose:
- `file_path`
- `chunk_id` (Chroma id)
- `start_line`, `end_line`
- `content`

### 3.2. Patch‑style response format

Define a new response contract for the LLM:

- Request side: you send snippets as context, including chunk id and line range in the text:

  ```text
  Chunk ID: src/foo.py:5  (lines 121–170)
  Original code:
  ```python
  <chunk content>
  ```
  ```

- In the **system prompt** (or in the user message), tell the LLM explicitly:

  - Do **not** try to output full files in this mode.
  - Instead, output a JSON object with a `patches` array.

Example schema:

```json
{
  "answer_summary": "What you did / explanation",
  "patches": [
    {
      "file_path": "src/foo.py",
      "chunk_id": "src/foo.py:5",
      "start_line": 121,
      "end_line": 170,
      "new_content": "def updated_function(...):\n    ...\n"
    }
  ]
}
```

You can keep `source_files` in the response for backward compatibility (full‑file edits), but introduce `patches` as a preferred mechanism for this advanced mode.

### 3.3. Client‑side stitching algorithm

Implement a separate stage that converts `patches` → `updated_files` (full contents), and only then call `apply_updates`.

High‑level algorithm (conceptual):

```python
# PSEUDO‑CODE ONLY

def apply_patches_to_files(patches: list[Patch]) -> list:
    # group patches by file
    patches_by_file = defaultdict(list)
    for p in patches:
        patches_by_file.append(p)

    updated_files = []

    for file_path, file_patches in patches_by_file.items():
        # 1) load full file from disk
        text = Path(file_path).read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)

        # 2) sort patches by start_line descending so indices stay valid
        file_patches.sort(key=lambda p: p.start_line, reverse=True)

        for p in file_patches:
            s = p.start_line - 1
            e = p.end_line     # exclusive index when using Python slicing

            # replace that span with new_content split into lines
            new_lines = p.new_content.splitlines(keepends=True)
            lines = new_lines

        new_text = "".join(lines)
        updated_files.append({
            "file_name": file_path,
            "file_content": new_text,
        })

    return updated_files
```

Then in `controller/llm_handler.py`:

- Extend `LLMResponse` (in `model/models.py`) to optionally carry `patches`.
- Before calling `filter_unchanged_files` / `make_paths_relative` / `apply_updates`, do:

  ```python
  if response.patches:
      patched_files = apply_patches_to_files(response.patches)
      updated_files = patched_files
  else:
      updated_files = response.updated_files
  ```

Everything else (snapshots, `apply_updates`, etc.) can stay the same, because in the end you still operate on whole‑file contents.

### 3.4. Guard rails for safe patching

To make patching robust:

- Include the **original snippet** in each patch and verify it before applying, e.g.:
  ```json
  {
    "file_path": "src/foo.py",
    "chunk_id": "src/foo.py:5",
    "original_snippet": "...",
    "new_content": "..."
  }
  ```

- At apply time, assert that `original_snippet` is still present at (`start_line`, `end_line`) or at least in the file; if not, either:
  - skip that patch, or
  - search for it and adjust the location before applying.

This avoids corrupting files if the project changed between retrieval and applying patches.

============================================================
4. How to phase this in safely
============================================================

Given your current codebase, a pragmatic migration plan is:

1) **Step 1 (low risk):**
   - Keep the existing full‑file edit flow.
   - Change RAG to pass **snippets as context only** (Section 2).
   - No schema change required; you just change `_get_rag_context_files` + the way you form `source_files` keys.

2) **Step 2 (medium risk):**
   - Add chunk metadata (ids + line ranges) in the vector index and `VectorIndexResult`.
   - Start labeling snippets with chunk ids & line ranges in the prompt.

3) **Step 3 (higher risk / more power):**
   - Define a `patches` schema as above and teach the LLM (via `SYSTEM_PROMPT` and examples) to use it.
   - Extend response parsing (`_parse_api_response`, local/offline plugin parsers) to capture `patches`.
   - Implement `apply_patches_to_files` and integrate it into `llm_handler.process_llm_response`.

4) Once this is stable, you can **stop** sending full files for most operations and rely on chunk‑level editing plus patch merging.

============================================================
5. Summary
============================================================

- Right now you already have chunk‑level retrieval, but `_get_rag_context_files` collapses it back to files.
- First improvement: change RAG packing to send only retrieved chunks as context (no stitching required, minimal code changes).
- For true fragment‑only editing + client‑side stitching:
  - give chunks stable ids and line ranges,
  - design a `patches` JSON schema for LLM responses,
  - implement a patch‑application layer that converts patches into full file contents and then reuses your existing snapshot + `apply_updates` pipeline.

If you want, I can next sketch the exact changes to a small subset of files (e.g. `vector_db.py`, `index_manager.py`, `llm_invoker.py`, `llm_handler.py`) as concrete code
to get Stage 1 or Stage 2 working.

