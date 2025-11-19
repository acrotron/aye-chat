---
title: "A Design for a Local-First, Privacy-Focused RAG System for Codebases"
date: 2025-11-16
draft: false
summary: "A summary of Aye Chat's local-first RAG system design, covering the core stack, progressive indexing strategy, and resource management for a private, context-aware AI."
tags: ["rag", "ai", "summary", "design"]
---

The stateless nature of LLMs makes it difficult to apply them to an entire codebase. Manually providing context is inefficient. For our open-source terminal AI, Aye Chat, we developed a RAG system to address this, with the goals of ensuring privacy, performance, and a responsive user experience.

We're sharing our design and implementation details here. (A more detailed technical post is also available [here]({{< relref "2025-11-16-ayechat-rag-deep-dive.md" >}})).

### TL;DR:

*   **Goal:** Provide codebase context to an LLM without using external APIs.
*   **Stack:** Local-first using `ChromaDB` and the `ONNXMiniLM_L6_V2` embedding model to avoid heavy dependencies like PyTorch/TensorFlow.
*   **Indexing:** A two-phase strategy. An initial "coarse" pass indexes whole files, followed by a background process that refines the index with semantic, AST-based chunks (e.g., functions, classes) using `tree-sitter`.
*   **Performance:** Indexing runs in low-priority, daemonized background threads with CPU limits and is interruptible/resumable.

---

### Local-First Architecture

A primary design constraint was user privacy, so sending source code to a third-party embedding API was not an option. This led to a fully local, on-device architecture. We chose `ChromaDB` as an embeddable vector store and paired it with the `ONNXMiniLM_L6_V2` model. This stack is self-contained, runs on the ONNX runtime, and avoids dependencies on large deep learning frameworks.

### Progressive Indexing and Semantic Chunking

Waiting for a large project to be indexed is a poor user experience. We address this with a two-phase progressive indexing strategy that now incorporates semantic chunking:

1.  **Coarse Pass:** On launch, the system performs a rapid scan, creating a single vector for each new or modified file. This provides a usable, file-level index almost immediately.
2.  **Refinement Pass with `tree-sitter`:** A background process then replaces each whole-file entry with multiple, fine-grained chunks derived from the code's Abstract Syntax Tree (AST). Using `tree-sitter`, we parse the code and extract logical units like functions, classes, and methods. This creates semantically relevant chunks, leading to far more precise retrieval than simple line-based splitting. If a language isn't supported by our AST chunker, we fall back to a simple line-based approach.

This design provides immediate availability, with retrieval quality improving significantly as the refinement pass completes, all without blocking the user.

### Background Processing and Resource Management

To prevent the indexing process from impacting application performance, it was designed with several features:

*   **Daemon Threads:** All work is performed in daemonized threads, allowing the main application to exit immediately.
*   **Resource Limiting:** Indexing workers are limited to a fraction of CPU cores, and the process priority is lowered via `os.nice()` on POSIX systems.
*   **Interruptible State:** Indexing progress is saved to disk periodically, allowing the process to resume if interrupted.

### Retrieval and Prompt Assembly

After retrieving a ranked list of chunks, the final prompt context is assembled. Files are ranked based on their highest-scoring chunk, and their full content is packed into the prompt. We use soft (~100KB) and hard (~200KB) limits to manage the context window size. For cases where the user wants to override retrieval, an `/all` command can be used to include the entire project's code.

### Future Work

Our initial implementation focused on building a robust, local-first RAG pipeline. With the move to AST-based chunking, we've significantly improved the core retrieval quality. Here's what's next:

*   **Context Assembly:** The current strategy retrieves whole files based on their highest-scoring chunk. While effective, this can still introduce noise. We plan to refine this to assemble context from the most relevant *chunks* themselves, providing more targeted information to the LLM.
*   **Expanded Language Support:** While our `tree-sitter` implementation covers many popular languages, we plan to expand the set of supported languages and continuously refine the AST queries for even more accurate chunking across different coding patterns and edge cases.
*   **Advanced Retrieval:** We are exploring more advanced techniques, such as hybrid search and adding a re-ranking step, to further improve the relevance of the retrieved context before it's sent to the LLM.

Feedback on this approach and roadmap is welcome.

---

This design combines a local-first approach with progressive indexing and resource management to implement a RAG system for codebases. The goal is to provide relevant context to an LLM while maintaining user privacy and application responsiveness.

The project, **Aye Chat**, is open-source. Feedback is welcome.

**GitHub:** [https://github.com/acrotron/aye-chat](https://github.com/acrotron/aye-chat)
