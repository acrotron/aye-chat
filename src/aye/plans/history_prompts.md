# Persistent Command History — Aye Chat Implementation Prompts

Use these phases in order. Each phase is a single copy-paste block: it includes the file references and the prompt text together.

---

## Phase 1 — Implement persistent history backend

```text
@history.md @model/repl_history.py @model/auth.py

Implement Phase 1 of persistent REPL command history from history.md.

Create model/repl_history.py with:
- AyePersistentHistory
- get_repl_history_path()
- is_history_enabled()
- get_history_max_entries()

Requirements:
- Store one global history file at ~/.aye/history by default.
- Support history_file override via existing get_user_config().
- Support history=on|off, default on.
- Support history_max_entries, default 500.
- Use JSON-lines with one object per line: {"text": "..."}.
- Store only exact user-submitted REPL text.
- Do not store augmented prompt content, source contents, URL/plugin context, shellcap output, image data, or hidden prompt wrappers.
- Skip empty/whitespace-only input.
- Skip exact single-token exit commands: exit, quit, :q.
- Deduplicate exact duplicate text by moving re-entered text to most-recent position.
- Prune to latest 500 unique global entries by default.
- Save atomically where practical.
- Use file mode 0600 where supported.
- Handle corrupt/invalid history file gracefully without crashing.
- Do not modify controller/repl.py yet.
```

---

## Phase 2 — Wire persistent history into the main REPL

```text
@history.md @model/repl_history.py @controller/repl.py

Implement Phase 2 of persistent REPL command history from history.md.

Wire AyePersistentHistory into controller/repl.py for the main chat PromptSession.

Requirements:
- Replace main REPL InMemoryHistory() with AyePersistentHistory when history is enabled.
- Fall back to InMemoryHistory() when history=off.
- Keep feedback PromptSession using InMemoryHistory(); feedback must not persist.
- Preserve existing Ctrl+V key binding behavior.
- Preserve existing completion behavior.
- Ensure history stores only the original user-entered prompt returned by prompt_toolkit.
- Do not store cleaned_prompt, final_prompt, source file contents, URL/plugin context, shellcap output, image data, or blog preamble.
- Do not add or repurpose the existing history command; it must continue to mean snapshot history.
- Do not change command dispatch behavior except for the history backend integration.
```

---

## Phase 3 — Add unit tests for history backend

```text
@history.md @model/repl_history.py @tests/test_repl_history.py

Implement unit tests for persistent REPL command history from history.md.

Create or update tests/test_repl_history.py.

Cover:
- Default path resolves to ~/.aye/history.
- history_file config/env override works.
- history=off disables persistent history helper behavior.
- history_max_entries defaults to 500 and handles invalid values.
- Empty strings are not stored.
- Whitespace-only strings are not stored.
- exit, quit, and :q are not stored.
- Shell commands are stored.
- AI prompts are stored.
- Built-in commands other than exit commands are stored.
- Exact duplicates are not duplicated.
- Re-entered duplicate moves to most-recent position.
- More than 500 entries are pruned to 500.
- Corrupt history file does not crash loading.
- Multiline entries round-trip correctly.
- JSON-lines file contains only {"text": "..."} records and no metadata.

Use temp files and/or unittest MagicMock framework where needed. Do not depend on the real ~/.aye/history file.
```

---

## Phase 4 — Add REPL integration and regression tests

```text
@history.md @model/repl_history.py @controller/repl.py @controller/command_handlers.py @controller/shell_capture.py @plugins/at_file_completer.py @tests/test_repl_history_integration.py

Implement integration/regression tests for REPL history from history.md.

Create or update tests/test_repl_history_integration.py.

Cover:
- create_prompt_session() uses AyePersistentHistory when history=on.
- create_prompt_session() uses InMemoryHistory when history=off.
- Feedback PromptSession remains in-memory and is not persisted.
- Completion and Ctrl+V key bindings still exist after history integration.
- The existing snapshot history command is not repurposed or broken.
- Blog flow stores only the user-facing command text, not _BLOG_PROMPT_PREAMBLE.
- with/@/URL/shellcap flows do not write expanded source contents, fetched context, or attached shell output to REPL history.
- History stores the original user input exactly as submitted, before any internal prompt mutation.

Prefer focused tests with mocks. Do not perform real API calls, real clipboard access, or real plugin web fetches.
```

---

## Phase 5 — Optional polish: docs/help and manual UAT notes

```text
@history.md @model/repl_history.py @controller/repl.py @presenter/repl_ui.py @history_uat.md

Implement optional documentation and UAT polish for persistent REPL command history from history.md.

Create history_uat.md and update help text only if appropriate.

Requirements:
- Document that command history is stored locally at ~/.aye/history by default.
- Document config keys: history=on|off, history_file, history_max_entries.
- Document default max entries: 500 unique global entries.
- Document that only exact user-submitted REPL input is stored.
- Document that source contents, URL/plugin context, shellcap output, image data, and internal prompt wrappers are not stored.
- Do not add a new command unless explicitly needed.
- Do not repurpose the existing history command; it remains snapshot history.
- Keep help text concise if updated.
```
