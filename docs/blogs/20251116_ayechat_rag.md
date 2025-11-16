# Building an Intelligent Coding Assistant: A Deep Dive into Aye Chat's RAG Implementation

## Executive Summary

This document details the design and implementation of the Retrieval-Augmented Generation (RAG) system in Aye Chat, an AI coding assistant. The RAG system addresses the core challenge of providing Large Language Models (LLMs) with project-specific context. Our solution prioritizes privacy, performance, and user experience by using a local-first approach. Key components include a lightweight, on-device ONNX embedding model and the embeddable ChromaDB vector database, which avoid dependencies on external APIs and heavy deep learning frameworks.

The architecture features a two-phase progressive indexing strategy: an initial, rapid coarse indexing of entire files for immediate availability, followed by a background process that refines the index with smaller, overlapping code chunks. To ensure a seamless user experience, all indexing is performed in low-priority, daemonized background threads, with CPU usage limits and interruptible processing that saves progress to disk. Finally, we intelligently pack the most relevant code into the LLM prompt, managing context window size with soft and hard limits, while also providing an escape hatch to include all files when needed. The result is a powerful, context-aware, and non-intrusive coding assistant for the terminal.

Large Language Models (LLMs) are incredibly powerful, but they have a fundamental limitation when it comes to helping with software development: they are stateless and lack awareness of your project's codebase. You can paste code into the prompt, but this is manual, cumbersome, and limited by context window sizes. How can a coding assistant provide truly helpful, context-aware answers about *your* code?

The answer is **Retrieval-Augmented Generation (RAG)**. At its core, RAG is a technique that enhances an LLM's prompt with relevant, externally retrieved information. For Aye Chat, this means automatically finding the most relevant source code from your project and including it with your question, giving the LLM the context it needs to provide accurate, insightful responses.

This blog post is a deep dive into how we designed and built the RAG intelligence inside Aye Chat. We'll explore the key decisions, the technical challenges, and the nuances that make the system both powerful and user-friendly.

## Part 1: Choosing the Right Tools for the Job

A RAG system has two primary components: an **embedding model** to convert text into numerical representations (vectors) and a **vector database** to store and efficiently search these vectors.

### The Brains: The Embedding Model

Our first major decision was whether to use a public API for embeddings (like OpenAI's) or a local, on-device model. We chose the local-first approach for several key reasons:

1.  **Privacy:** Your source code is your intellectual property. Sending it to a third-party service for embedding raises privacy concerns. A local model ensures your code never leaves your machine.
2.  **Cost & Rate-Limiting:** API-based embeddings can become expensive, especially for large projects that require frequent re-indexing. They are also subject to rate limits, which can slow down the initial indexing process.
3.  **Simplicity:** We wanted Aye Chat to be a self-contained, easy-to-install command-line tool. Adding dependencies on external APIs for core functionality complicates the setup and user experience.

With the local approach decided, we needed a model that was effective but also lightweight. A major goal was to avoid forcing users to install heavy frameworks like PyTorch or TensorFlow, which can be a significant barrier. This led us to select `ONNXMiniLM_L6_V2`, a model available in the ONNX (Open Neural Network Exchange) format. As seen in `aye/model/vector_db.py`, this model is conveniently packaged with ChromaDB and runs on a lightweight ONNX runtime, sidestepping the need for multi-gigabyte deep learning libraries.

```python
# aye/model/vector_db.py

# Use the lightweight ONNX embedding function included with chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

# ...

    # Instantiate the lightweight ONNX embedding function.
    # This avoids pulling in PyTorch and is much smaller.
    embedding_function = ONNXMiniLM_L6_V2()
```

### The Memory Palace: The Vector Database

Next, we needed a vector database. The requirements were clear: it had to be embeddable, run locally, be easy to install (`pip install ...`), and perform well.

We evaluated several options:

*   **Milvus Lite:** While promising, it felt more like a stepping stone to the full, distributed Milvus. For a purely local, single-user CLI tool, it seemed like overkill and potentially more complex to manage.
*   **LanceDB:** A strong, modern contender built on the Lance file format. It's very fast and efficient.
*   **ChromaDB:** An open-source, embeddable vector database that has gained significant popularity.

We ultimately chose **ChromaDB**. The decision was based on its excellent balance of features, ease of use, and maturity. The `chromadb.PersistentClient` allows us to create a self-contained database right inside the project's `.aye/` directory, requiring no external services. Furthermore, its seamless integration with the `ONNXMiniLM_L6_V2` embedding function was a significant advantage, simplifying the implementation. We configured it to use cosine similarity, a standard metric for measuring the similarity between text vectors.

```python
# aye/model/vector_db.py

def initialize_index(root_path: Path) -> Any:
    db_path = root_path / ".aye" / "chroma_db"
    db_path.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(db_path))
    
    embedding_function = ONNXMiniLM_L6_V2()

    collection = client.get_or_create_collection(
        name="project_code_index",
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"}  # Cosine similarity is good for text
    )
    return collection
```

## Part 2: The Architecture of Intelligence

With the tools selected, we designed the indexing and search process. The `IndexManager` class in `aye/model/index_manager.py` is the heart of this system.

### Creating the Index: A Two-Phase Approach

Indexing an entire project can be time-consuming. We didn't want users to wait minutes before they could start using the chat. To solve this, we implemented a **two-phase progressive indexing strategy**.

**Phase 1: Coarse Indexing**
When Aye Chat starts, it performs a quick scan of the project to find new or modified files. For each of these files, it creates a *single* vector for the *entire file content* and adds it to the index. The document ID is simply the file path.

```python
# aye/model/vector_db.py

def update_index_coarse(
    collection: Any, 
    files_to_update: Dict[str, str]
) -> None:
    # ...
    ids = list(files_to_update.keys())
    documents = list(files_to_update.values())
    # ...
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
```

This process is extremely fast. It gives the user a usable, albeit imprecise, search index almost immediately. Searching at this stage can identify which *files* are relevant, even if it can't pinpoint the exact lines of code.

**Phase 2: Refined Indexing**
After the coarse pass is complete, a background process kicks in. It takes each file that was coarsely indexed and replaces its single entry with multiple, fine-grained chunks. We use a simple line-based chunker for this, which splits files into smaller, overlapping segments.

```python
# aye/model/vector_db.py

def refine_file_in_index(collection: Any, file_path: str, content: str):
    # 1. Delete the old coarse chunk
    collection.delete(ids=[file_path])

    # 2. Create and upsert the new fine-grained chunks.
    chunks = _chunk_file(content)
    # ...
    collection.upsert(documents=chunks, metadatas=metadatas, ids=ids)
```

This progressive approach provides the best of both worlds: immediate availability and progressively improving search quality, all without blocking the user.

### The Search: Finding Needles in a Haystack

When the user enters a prompt, the `llm_invoker` calls the `query` method of the `IndexManager`. This in turn calls ChromaDB's `query` function. We ask for a generous 300 results to ensure we have a wide pool of potentially relevant code chunks. ChromaDB returns the chunks and their "distance" from the query. We convert this to a more intuitive similarity score (where 1.0 is a perfect match) by calculating `1 - distance`.

## Part 3: Engineering for a Seamless User Experience

A powerful RAG system is useless if it makes the host application slow or unreliable. We invested significant effort into making the indexing process as unobtrusive as possible.

### Working in the Shadows: Background Processing

All indexing work happens in a background thread, as initiated in `aye/controller/repl.py`. A standard `ThreadPoolExecutor` would create non-daemon threads, which would prevent the application from exiting until the indexing was complete. To fix this, we implemented a custom `DaemonThreadPoolExecutor` in `aye/model/index_manager.py`. This small but critical change ensures that background indexing is automatically terminated when the user quits the chat.

### Playing Nice: Limiting CPU Impact

Calculating embeddings is CPU-intensive. To prevent Aye Chat from hogging system resources and causing UI lag, we implemented two key constraints:

1.  **Worker Count:** We limit the number of background indexing threads to half the available CPU cores, with a maximum of 4. This leaves plenty of CPU cycles for the main application and other user tasks.
2.  **Process Priority:** On POSIX-compliant systems (like Linux and macOS), we use `os.nice(5)` to lower the priority of the background worker threads. This tells the operating system to prioritize other processes (like the user's terminal) over our indexing work.

```python
# aye/model/index_manager.py

def _set_low_priority():
    if hasattr(os, 'nice'):
        os.nice(5)

# ...
MAX_WORKERS = min(4, max(1, CPU_COUNT // 2))
```

### Never Starting Over: Robust, Interruptible Indexing

Initial indexing of a large project can still take time. If the user quits halfway through, they shouldn't have to start from scratch next time. We built robustness into the process by regularly saving the state of our file hash index to disk. The `IndexManager` saves its progress to `.aye/file_index.json` after every 20 files (`SAVE_INTERVAL`). If the process is interrupted, the next run will pick up right where it left off, only needing to process the remaining files.

## Part 4: From Search Results to LLM Prompt

Once the vector search returns a ranked list of code chunks, the final step is to assemble the context to be sent with the prompt. This is handled in `aye/controller/llm_invoker.py`.

First, we create a unique, ranked list of *files* from the returned chunks. A file that appears multiple times is ranked by its highest-scoring chunk.

Then, we iterate through this list of files, adding their full content to the context. This is where we manage the context window size with a system of soft and hard limits:

*   **Soft Limit (`CONTEXT_TARGET_SIZE`):** We aim to pack about 100KB of context. The loop continues adding files as long as the total size is below this threshold.
*   **Hard Limit (`CONTEXT_HARD_LIMIT`):** To prevent API errors from a payload that is too large, we have a hard limit of 200KB. Before adding a file, we check if it would push the total size over this limit. If so, we skip that file and try the next, smaller one in the ranked list.

This logic ensures we prioritize the most relevant files while respecting API limitations.

```python
# aye/controller/llm_invoker.py

# Stop if we've already packed enough context (soft limit).
if current_size > CONTEXT_TARGET_SIZE:
    break

# ...

# Check if adding this file would exceed the hard limit.
if current_size + file_size > CONTEXT_HARD_LIMIT:
    continue # Skip this file and try the next one.
```

Finally, for full user control, we added the `/all` command. If a user's prompt starts with `/all`, we bypass RAG entirely and include every single file in the project that matches the file mask. This is a powerful escape hatch for when the user knows better than the search algorithm.

## Conclusion

Building the RAG system for Aye Chat was a journey of careful trade-offs. We prioritized user experience, privacy, and performance at every step. By choosing a lightweight local model and database, implementing a progressive background indexing strategy, and carefully managing system resources, we've created a feature that provides powerful, context-aware assistance without getting in the user's way. The result is a smarter, more helpful coding companion for the command line.

---
## About Aye Chat

Aye Chat is an open-source, AI-powered terminal workspace that brings the power of AI directly into your command-line workflow. Edit files, run commands, and chat with your codebase without ever leaving the terminal.

Find the project on GitHub: [https://github.com/acrotron/aye-chat](https://github.com/acrotron/aye-chat)