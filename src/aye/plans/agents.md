Generate an implementation plan for adding AGENTS.md support to Aye Chat.

Context:

Aye Chat is a terminal-based AI coding tool (this codebase)

It does NOT support tool calling or command execution by the LLM.

AGENTS.md is treated as static instruction text that augments the system prompt.

Requirements:

Discovery logic:

First, check for .aye/AGENTS.md in the current working directory.

If not found, search upward through parent directories for AGENTS.md.

Stop searching when the filesystem root or repository root is reached.

At most one AGENTS.md should be applied (no merging).

Precedence:

.aye/AGENTS.md has higher priority than root-level AGENTS.md.

If both exist, only .aye/AGENTS.md is used.

Prompt integration:

The contents of AGENTS.md should be appended to the system prompt.

Clearly delimit the injected content - like the following:

--- SYSTEM CONTEXT - AGENTS.md (repo instructions)

<contents>

--- END AGENTS.md


Do not modify or reinterpret the contents.

No special handling for commands or tool instructions — treat all text as guidance only.

Scope:

This is per-project configuration, not global user configuration.

Behavior should be deterministic and transparent.

Deliverables:

Implementation plan, written into agents_implementation.md file

Do not propose additional features beyond the above.

