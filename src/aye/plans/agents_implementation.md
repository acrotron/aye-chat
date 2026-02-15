# Implementation Plan: Add `AGENTS.md` Support to Aye Chat

## Goal
Add support for a per-project `AGENTS.md` file whose raw contents are appended verbatim to the LLM system prompt for every user request.

This support must:
- Discover **at most one** applicable `AGENTS.md` (no merging)
- Prefer `.aye/AGENTS.md` in the **current working directory** (CWD)
- Otherwise search upward for `AGENTS.md` until the **filesystem root** or **repository root** is reached
- Append the contents to the system prompt with clear delimiters
- Treat the file as **static guidance text** only (no tool/command execution behavior)
- Be deterministic and transparent

## Non-Goals
Do **not** add:
- Multiple file merging
- Special parsing/interpretation of instructions
- New CLI commands or configuration toggles
- Global user-level `AGENTS.md`

---

## Current Relevant Architecture
- The system prompt is determined inside `aye/controller/llm_invoker.py` in `invoke_llm()`:
  - `system_prompt = conf.ground_truth ... else SYSTEM_PROMPT`
  - Then passed to:
    - `plugin_manager.handle_command("local_model_invoke", {..., "system_prompt": system_prompt, ...})`
    - `cli_invoke(..., system_prompt=system_prompt, ...)`

This is the best insertion point: one place affects both local and API LLM paths.

The repository root concept in this codebase is currently derived via:
- `aye/controller/util.py::find_project_root()` using the marker `.aye/file_index.json`.
- `initialize_project_context()` stores it as `conf.root`.

`cd` command currently sets `conf.root = Path.cwd()`.

---

## Step 1: Implement `AGENTS.md` Discovery

### 1.1 New helper function
Add a helper that returns either:
- `None` (no agents file found)
- A tuple: `(agents_path: Path, agents_text: str)`

Recommended location:
- `aye/controller/util.py` (keeps “walk upward” filesystem logic near `find_project_root()`), OR
- a small new module `aye/controller/agents.py` (clean separation).

Either is acceptable; keep it small and deterministic.

### 1.2 Discovery requirements translated to algorithm
Inputs:
- `cwd: Path` (actual current working directory, i.e., `Path.cwd().resolve()`)
- `repo_root: Path` (the repository root boundary; use `conf.root.resolve()`)

Algorithm (must be deterministic):

1) **Highest precedence check (CWD-local `.aye/AGENTS.md`)**
   - Check `cwd / ".aye" / "AGENTS.md"`
   - If it exists and is a file: select it and stop.

2) **Upward search for `AGENTS.md`**
   - Start from `search_dir = cwd`
   - Loop:
     - Check `search_dir / "AGENTS.md"`
     - If found: select it and stop
     - Stop conditions:
       - If `search_dir == repo_root`: stop (do not move above repo root)
       - If `search_dir.parent == search_dir`: stop (filesystem root)
     - Else: `search_dir = search_dir.parent`

3) If nothing found: return `None`.

Notes:
- **No merging**: first match wins.
- **Precedence**: `.aye/AGENTS.md` in CWD is always preferred over any root/parent `AGENTS.md`.
- **Stop at repo root**: ensures per-project scope.

### 1.3 Repo root boundary details
Use `conf.root` as the “repository root” boundary because:
- It is already treated as the project root throughout the app
- It is what indexing and file collection use

Edge case: if the user has `cd`’d into a subdirectory but `conf.root` was changed by `cd` command.
- **Implementation plan decision:** treat the *current `conf.root`* as repo root boundary (consistent with “session context”).
- This is deterministic and consistent with how Aye currently changes scope on `cd`.

---

## Step 2: Integrate `AGENTS.md` into the System Prompt

### 2.1 Prompt injection format
When an agents file is found, append this block to the base system prompt:

```text

--- SYSTEM CONTEXT - AGENTS.md (repo instructions)

<contents>

--- END AGENTS.md
```

Requirements:
- **Append** to the system prompt (do not prepend)
- Do not modify the contents
- Preserve exact file text as read (other than whatever newline boundaries are required by concatenation)

### 2.2 Where to integrate
Modify `aye/controller/llm_invoker.py::invoke_llm()`:

Current:
- `system_prompt = conf.ground_truth ... else SYSTEM_PROMPT`

New flow:
1) Compute `base_system_prompt` the same way as today
2) Discover agents instructions:
   - `cwd = Path.cwd().resolve()`
   - `repo_root = Path(conf.root).resolve()`
   - call discovery helper
3) If found:
   - `system_prompt = base_system_prompt + injected_block`
4) Else:
   - `system_prompt = base_system_prompt`

Then pass `system_prompt` unchanged to:
- local model plugin invocation
- `cli_invoke()`

### 2.3 Transparency behavior
To keep behavior “transparent” without adding new features:
- In **verbose or debug** mode, print a single line indicating which agents file was applied, e.g.
  - `Using AGENTS.md system context from: <path>`
- In non-verbose mode, print nothing.

This mirrors existing patterns in the codebase (e.g., ground truth prompt loading uses verbose prints).

---

## Step 3: Ensure Determinism

Determinism checklist:
- Always use `Path.resolve()` for `cwd` and `repo_root` before comparisons
- Apply exactly one file maximum
- Clear precedence order:
  1) `cwd/.aye/AGENTS.md`
  2) first `AGENTS.md` found walking upward from `cwd`, stopping at `repo_root` or filesystem root

Avoid:
- Reading multiple AGENTS files
- Any content rewriting, trimming, or templating beyond the required delimiter wrapper

---

## Step 4: Testing Plan (Unit + Lightweight Integration)

### 4.1 Unit tests for discovery
Create tests that build temporary directory trees:

Cases:
1) Only `cwd/.aye/AGENTS.md` exists → selected
2) `cwd/.aye/AGENTS.md` and `repo_root/AGENTS.md` exist → `.aye/AGENTS.md` selected
3) No `.aye/AGENTS.md`, but `cwd/AGENTS.md` exists → selected
4) No `cwd/AGENTS.md`, but `parent/AGENTS.md` exists → selected
5) No files up to repo root → none
6) A file exists above repo root → must NOT be selected
7) Repo root == filesystem root-like scenario (stop condition correctness)

Assertions:
- Returned path is correct
- Only one path returned

### 4.2 Prompt integration test
Test `invoke_llm()` prompt assembly indirectly by:
- Injecting a fake `conf` with `ground_truth` unset and set
- Creating an AGENTS file and verifying the produced `system_prompt` passed to `cli_invoke()` or to the local plugin handler

(Use mocking around `cli_invoke` / plugin manager command handler to capture arguments.)

---

## Step 5: Documentation (Minimal, not for the initial implementation)

Add a short note in existing help/docs (only if there is already a docs location in-repo) explaining:
- Where to put `AGENTS.md`
- `.aye/AGENTS.md` precedence
- That it is appended verbatim to the system prompt

Keep this minimal and strictly within requirements.

---

## Implementation Checklist (Concrete)

1) Add discovery helper (new function/module):
   - Inputs: `cwd`, `repo_root`
   - Output: optional `(path, text)`

2) Update `aye/controller/llm_invoker.py::invoke_llm()`:
   - Build `base_system_prompt`
   - Call helper
   - Append delimited block if present

3) Add tests:
   - Discovery logic tests
   - Prompt integration smoke test

4) Manual verification:
   - Run `aye chat` in a repo with `AGENTS.md` at root; confirm LLM behavior shifts
   - Add `.aye/AGENTS.md` in a subdirectory and `cd` into it; confirm precedence

---

## Exact Delimiter Block (Must Match Requirement)

```text
--- SYSTEM CONTEXT - AGENTS.md (repo instructions)

<contents>

--- END AGENTS.md
```
