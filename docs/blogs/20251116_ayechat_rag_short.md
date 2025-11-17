# How We Built a Blazing-Fast, Privacy-First RAG for Our AI Coding Assistant

AI coding assistants are powerful, but they share a common flaw: they don't know your code. You can manually paste snippets, but it's a clunky workaround that breaks your flow. For our open-source terminal assistant, Aye Chat, we knew we needed to solve this by giving the AI full project context, but without compromising on privacy or performance.

Our solution is a custom-built Retrieval-Augmented Generation (RAG) system. This post is a high-level look at how we designed it to be smart, fast, and completely unobtrusive. (For a full technical deep-dive, see our main blog post).

## Part 1: Local, Private, and Lightweight by Design

From the start, privacy was our top priority. Sending your source code to a third-party service for embedding was a non-starter. This led us to a fully local, on-device approach. We chose a lightweight ONNX embedding model partialX a heavy framework like PyTorch, and paired it with ChromaDB, an embeddable vector database. The entire system runs on your machine, and your code never leaves it.

## Part 2: Progressive Indexing for Instant Gratification

No one wants to wait for a massive project to be indexed. To solve this, we developed a two-phase progressive indexing strategy.

1.  **Coarse Pass:** When you first launch the chat, Aye Chat performs a lightning-fast scan, creating a single vector for each new or modified file. This gives you a usable, if imprecise, index almost instantly.
2.  **Refinement Pass:** With the basics covered, a background process silently kicks in. It takes each of those coarsely-indexed files and replaces them with smaller, more precise, overlapping code chunks. 

This approach gives you the best of both worlds: immediate availability with search quality that improves over time, all without blocking you.

## Part 3: Staying Out of Your Way

A background process should never make the main application feel sluggish. We engineered our indexing system to be a polite background citizen:

*   **Background, Daemon Threads:** All indexing work happens in the background on daemonized threads, meaning the app can exit instantly without waiting for indexing to finish.
*   **Resource Limiting:** We limit the indexing to a fraction of your CPU cores and, on Linux/macOS, lower its process priority. This keeps the UI snappy and responsive.
*   **Interruptible and Resumable:** If you quit mid-index, Aye Chat saves its progress. The next time you start, it picks up right where it left off.

## Part 4: From Search to Intelligent Prompt

Once the RAG system finds relevant code chunks, it intelligently assembles the context for the LLM. It ranks files by relevance and packs them into the prompt, carefully managing soft and hard limits to stay within the context window. This ensures the LLM gets the most useful information possible. And for moments when you know better, the `/all` command lets you bypass RAG and send the entire project's code.

## The Result

By combining a local-first philosophy with a smart, progressive indexing strategy and careful resource management, we've built a RAG system that makes Aye Chat a powerful, context-aware coding partner. It gives you the power of AI on your own code, without ever compromising your privacy or getting in your way.

**Want the full technical breakdown? Check out our detailed blog post [link here]. You can also find the open-source project, Aye Chat, on GitHub.**