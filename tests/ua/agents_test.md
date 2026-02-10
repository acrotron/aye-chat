# User Acceptance Tests for AGENTS.md Inclusion in Aye Chat

This document outlines user acceptance tests (UATs) for the automatic inclusion of **`AGENTS.md`** files as extra system context in Aye Chat prompts. Implemented in `aye/controller/llm_invoker.py` (or related context builders), this feature discovers `AGENTS.md` files for repo-specific instructions (e.g., team conventions, architecture notes). Discovery follows **first-match-wins** priority: `./.aye/AGENTS.md` (highest), then walking up from project root: `.aye/AGENTS.md` or `AGENTS.md`. Content is prepended to the system prompt as actionable instructions. Tests verify discovery, inclusion in prompts, and graceful handling of missing/invalid files.

## Test Environment Setup
- Create test project directories with varied `AGENTS.md` placements (e.g., `./.aye/AGENTS.md`, `AGENTS.md`, parent `.aye/AGENTS.md`).
- Use `aye chat --verbose` or `debug on` to observe inclusion (e.g., "[cyan]Using AGENTS.md: path/to/AGENTS.md[/]").
- Verify via `verbose on` output showing system context or test prompts including AGENTS content.
- Test with chat prompts to confirm context injection.
- Ensure no pre-existing `.aye/` interferes; use temp directories.

## Test Cases

### 1. Discovery Priority (First Match Wins)

#### UAT-1.1: Highest Precedence - `./.aye/AGENTS.md`
- **Given**: Project root with `./.aye/AGENTS.md` containing "Always use TypeScript."
- **When**: Run `aye chat` or `aye generate "test"`.
- **Then**: Uses `./.aye/AGENTS.md`; verbose shows "Using AGENTS.md: ./.aye/AGENTS.md".
- **Verification**: System prompt includes "Always use TypeScript." (check verbose/debug output or mock LLM input).

#### UAT-1.2: Fallback to Project Root `AGENTS.md`
- **Given**: No `./.aye/AGENTS.md`, but `AGENTS.md` in project root with "Prefer functional components."
- **When**: Run `aye chat`.
- **Then**: Uses project `AGENTS.md`.
- **Verification**: Context includes content; verbose confirms path.

#### UAT-1.3: Fallback to `.aye/AGENTS.md` in Project Root
- **Given**: No `./.aye/` or root `AGENTS.md`, but `./.aye/AGENTS.md` with instructions.
- **When**: Run `aye chat`.
- **Then**: Uses `./.aye/AGENTS.md`.
- **Verification**: Path shown; content injected.

#### UAT-1.4: Walk Up to Parent `.aye/AGENTS.md`
- **Given**: Nested dir `./src/project`, parent `.aye/AGENTS.md` with "Monorepo: check shared libs."
- **When**: `cd src/project && aye chat`.
- **Then**: Discovers parent `.aye/AGENTS.md` (walks up).
- **Verification**: Verbose shows parent path; content included.

#### UAT-1.5: Walk Up to Parent `AGENTS.md`
- **Given**: Nested dir, grandparent `AGENTS.md` with instructions.
- **When**: Run `aye chat` from nested dir.
- **Then**: Uses closest parent `AGENTS.md`.
- **Verification**: Correct parent path used (first match).

### 2. Content Inclusion and Processing

#### UAT-2.1: Short Actionable Content Included
- **Given**: `AGENTS.md` with "1. Use async/await. 2. Add tests. 3. Lint before commit."
- **When**: Chat prompt sent to LLM.
- **Then**: Prepended to system prompt verbatim.
- **Verification**: LLM responses follow instructions (or verbose shows full context).

#### UAT-2.2: Long Content Truncated Gracefully
- **Given**: Large `AGENTS.md` (> context limit).
- **When**: Run chat.
- **Then**: Truncated or summarized; warning in verbose.
- **Verification**: No crash; partial instructions included.

#### UAT-2.3: Markdown Formatting Preserved
- **Given**: `AGENTS.md` with headers/lists: "## Rules\n- Rule 1"
- **When**: Included in prompt.
- **Then**: Raw Markdown passed to LLM.
- **Verification**: Context shows formatted text.

### 3. Edge Cases and No-Inclusion

#### UAT-3.1: No AGENTS.md Files Found
- **Given**: Empty project, no `AGENTS.md` anywhere.
- **When**: Run `aye chat`.
- **Then**: No inclusion; verbose: "No AGENTS.md found".
- **Verification**: Standard system prompt only.

#### UAT-3.2: Multiple Matches (First Wins)
- **Given**: `./.aye/AGENTS.md` (A), root `AGENTS.md` (B), parent `.aye/AGENTS.md` (C).
- **When**: Run chat.
- **Then**: Only (A) used.
- **Verification**: Verbose shows `./.aye/AGENTS.md`; ignores B/C.

#### UAT-3.3: Invalid/Empty AGENTS.md
- **Given**: `AGENTS.md` empty or binary/non-UTF8.
- **When**: Run chat.
- **Then**: Skipped; warning: "Skipping invalid AGENTS.md".
- **Verification**: No crash; no content included.

#### UAT-3.4: Ignored via .gitignore/.ayeignore
- **Given**: `AGENTS.md` in ignored path (e.g., `node_modules/AGENTS.md`).
- **When**: Run chat.
- **Then**: Skipped (respects ignores).
- **Verification**: Not discovered/included.

#### UAT-3.5: Non-Repo Context (Home Dir)
- **Given**: Run `aye chat` from `~`.
- **When**: No AGENTS.md.
- **Then**: No scan/inclusion.
- **Verification**: "Skipping AGENTS.md scan in home dir".

### 4. CLI and Chat Integration

#### UAT-4.1: Works with `aye chat --root`
- **Given**: `--root ./subproject` with local `AGENTS.md`.
- **When**: Run command.
- **Then**: Uses subproject's AGENTS.md.
- **Verification**: Context from specified root.

#### UAT-4.2: Works with `with <files>`
- **Given**: `with main.py: follow AGENTS.md` + AGENTS.md.
- **When**: Prompt executed.
- **Then**: AGENTS + explicit files in context.
- **Verification**: Both included.

## Notes
- Feature delegates to filesystem walker (similar to source collection); tests assume `collect_sources`-like exclusion (.gitignore/.ayeignore).
- Content treated as system instructions; LLM sees "Follow these repo rules: [content]".
- Verbose/debug output confirms path/content; no direct API inspection needed.
- Edge cases (permissions, symlinks) handled by Pathlib (UnicodeDecodeError skips).
- Tests run in temp dirs; verify via `--verbose` or LLM mock responses.
