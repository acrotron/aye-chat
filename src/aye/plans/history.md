# Persistent Command History — Implementation Plan

## Goal Summary

Add command/prompt history persistence across Aye Chat restarts so users can press Up/Down in a new `aye chat` session and access prior REPL inputs.

Current behavior uses `prompt_toolkit.history.InMemoryHistory`, so history is lost when the process exits.

---

## Final Product Decisions

Accepted decisions:

1. **Storage location:** one global user-level history file under `~/.aye/`, not inside project `.aye/`.
2. **History grouping:** no grouping by project, session, or chat ID.
3. **Persisted inputs:** save only the exact main REPL input typed/submitted by the user, including:
   - shell commands
   - AI prompts
   - built-in commands
4. **Do not persist augmented prompt content:** command history must not store any additional context that Aye attaches internally after the user submits input, including:
   - source file contents
   - auto-detected RAG/code context
   - `@file` expanded file contents
   - `with ...:` expanded file contents
   - URL / website / GitHub / SonarQube / plugin-fetched context
   - captured shell output attached via `shellcap`
   - clipboard/image attachment data or metadata beyond the marker text the user actually typed
   - internal prompt wrappers such as the full `blog` preamble
5. **Excluded inputs:** do not save:
   - empty lines
   - `exit`
   - `quit`
   - `:q`
   - exact duplicates
6. **Max size:** keep the most recent **500 unique global entries**.
7. **Feedback prompt:** do not persist feedback prompt text.
8. **Format:** keep it simple; no chat metadata required.

---

## Recommendation Confirmation

One global history file is the best v1 design.

This matches normal shell history behavior:

- Bash/Zsh/Fish history is global by default.
- Users expect Up/Down to recall useful recent commands regardless of session.
- Shell commands, built-ins, and AI prompts are all part of the same terminal workflow.

Avoiding chat IDs is a good simplification because:

- Backend `chat_id` is not known before the first successful LLM response.
- Shell commands can happen before any chat exists.
- Local/offline model paths may not have backend chat IDs.
- `new` would otherwise require awkward history scope switching.
- Users usually want recall across sessions, not strict conversation scoping.

Final recommendation: **one global command history, max 500 entries, no metadata.**

---

## History UX Principle

History should behave like shell history: pressing Up should rotate through exactly what the user typed, not what Aye internally sent to the model.

For example, if the user types:

```text
review @src/main.py and this SonarQube issue
```

history should store that line only.

It should not store:

- the contents of `src/main.py`
- SonarQube API response JSON
- URL/plugin-expanded context
- RAG-selected source chunks
- captured shell output
- image base64 data
- hidden system or blog preambles

This keeps recall predictable, concise, and safe.

---

## Architecture Overview

Current REPL session creation happens in:

- `controller/repl.py::create_prompt_session(...)`

Current code:

```python
return PromptSession(
    history=InMemoryHistory(),
    ...
)
```

`prompt_toolkit.history.FileHistory` is available, but plain `FileHistory` does not provide all required behavior by itself:

- skip exit commands
- skip empty lines
- deduplicate exact duplicates
- prune to 500 entries

Therefore implement a small custom `prompt_toolkit.history.History` subclass.

Recommended new module:

```text
model/repl_history.py
```

---

## Proposed Storage

Default file:

```text
~/.aye/history
```

Rationale:

- Global across projects and chat sessions.
- Keeps prompt history out of project repos.
- Simple format is easy to inspect and debug.
- No metadata needed.

Suggested file permissions:

```text
0600
```

History is local user data, not a secret, but it may contain sensitive prompts or commands.

---

## Data Format

Use JSON-lines with one record per submitted user input.

Example:

```jsonl
{"text":"git status"}
{"text":"pytest tests/ -v"}
{"text":"explain this project"}
{"text":"review @src/main.py"}
```

Each record contains only:

| Field | Meaning |
|---|---|
| `text` | Exact REPL input text submitted by the user |

No timestamps, chat IDs, project IDs, session IDs, source contents, plugin results, or augmented prompt bodies.

### Why JSON-lines instead of raw text lines

JSON-lines keeps the format simple while safely supporting future multiline inputs:

```jsonl
{"text":"multi-line\nprompt"}
```

If current REPL input remains single-line, this still works cleanly.

---

## Implementation Plan

### 1. Add `model/repl_history.py`

Create a custom history backend:

```python
class AyePersistentHistory(History):
    def __init__(self, path: Path, max_entries: int = 500):
        ...

    def load_history_strings(self) -> Iterable[str]:
        ...

    def store_string(self, string: str) -> None:
        ...
```

Responsibilities:

- Load history from `~/.aye/history`.
- Store only the exact user-submitted REPL input.
- Skip empty lines.
- Skip `exit`, `quit`, `:q`.
- Skip exact duplicates.
- Prune to 500 global entries.
- Save atomically where practical.
- Use file permissions `0600` where supported.

### 2. Add helper functions

In `model/repl_history.py`:

```python
def get_repl_history_path() -> Path:
    """Return the persistent REPL history file path."""
```

Recommended behavior:

- Use config key `history_file` if set.
- Otherwise use `Path.home() / ".aye" / "history"`.
- Ensure parent directory exists.

Also add:

```python
def is_history_enabled() -> bool:
    """Return True unless history=off is configured."""
```

Default:

```text
history=on
```

And:

```python
def get_history_max_entries() -> int:
    """Return configured history max entries, defaulting to 500."""
```

### 3. Replace main REPL history backend

In `controller/repl.py`:

```python
from prompt_toolkit.history import InMemoryHistory
from aye.model.repl_history import (
    AyePersistentHistory,
    get_repl_history_path,
    is_history_enabled,
    get_history_max_entries,
)
```

Update `create_prompt_session(...)`.

Current:

```python
history=InMemoryHistory()
```

New behavior:

```python
if is_history_enabled():
    history = AyePersistentHistory(
        path=get_repl_history_path(),
        max_entries=get_history_max_entries(),
    )
else:
    history = InMemoryHistory()
```

Then pass:

```python
history=history
```

### 4. Store only the original user prompt/input

The history backend must receive the prompt_toolkit input string before Aye mutates it.

This means history stores values like:

```text
explain this project
review @src/main.py
with src/main.py: explain this function
blog write about this feature
sq my-project-key
```

It must not store later internal variables such as:

- `cleaned_prompt` after URL context is appended
- `final_prompt` after shell output is attached
- expanded source file dictionaries
- plugin response data
- blog command preamble
- LLM payloads

This preserves intuitive Up-arrow behavior.

### 5. Filtering rules

Do not store input if:

```python
not text.strip()
```

or normalized command is one of:

```python
{"exit", "quit", ":q"}
```

Suggested normalization for exit filtering:

```python
first_token = text.strip().split(maxsplit=1)[0].lower()
```

This means these are excluded:

```text
exit
quit
:q
```

But these are not excluded unless intentionally added later:

```text
exit now
quit please
```

Recommended v1: only exclude exact single-token exit commands.

### 6. Duplicate handling

Exact duplicate rule:

- If the same text already exists anywhere in history, remove the older entry and append the new one.

This keeps entries unique while preserving recency.

Example:

Before:

```text
git status
pytest
explain this project
```

User enters:

```text
git status
```

After:

```text
pytest
explain this project
git status
```

### 7. Pruning rules

After each stored input:

- Keep only the latest 500 entries globally.
- Save the pruned result.

Default config:

```text
history_max_entries=500
```

If config is missing or invalid, use `500`.

### 8. Keep feedback prompt in-memory

Do not change:

```python
feedback_session = PromptSession(history=InMemoryHistory())
```

Feedback text should not become part of command history.

### 9. Avoid conflict with existing `history` command

There is already a built-in `history` command for snapshots.

Do **not** repurpose `history` for command history settings.

If a command is needed later, use one of:

```text
history-clear
command-history-clear
repl-history-clear
```

Recommended v1: no new command; use config only.

---

## Config Keys

Recommended config keys in `~/.ayecfg`:

```text
history=on
history_file=/custom/path/history
history_max_entries=500
```

### `history`

Controls persistent REPL history:

| Value | Meaning |
|---|---|
| `on` | Persist main REPL history |
| `off` | Use in-memory history only |

Default:

```text
history=on
```

### `history_file`

Optional override for the history file location.

Default:

```text
~/.aye/history
```

### `history_max_entries`

Maximum number of global unique entries to keep.

Default:

```text
500
```

---

## Dependencies

- Existing dependency: `prompt_toolkit`
- Existing config helpers:
  - `get_user_config()`
  - `set_user_config()` only if adding commands later

No new package dependency required.

---

## Risks

### Privacy

Persistent history may contain prompts, shell commands, filenames, pasted text, or secrets accidentally typed by users.

Mitigations:

- Store locally only.
- Store only user-submitted REPL text, never expanded source/plugin/URL/shell/image context.
- Use file mode `0600` where possible.
- Add `history=off` config.
- Consider a `history-clear` command later.

### Existing command name conflict

`history` already means snapshot history.

Mitigation:

- Do not add `history on/off` as a command.
- Use config or a clearly named future command.

### File corruption

History file could be manually edited or partially written.

Mitigation:

- On load failure, ignore bad history and start empty.
- Optionally rename corrupt file to `history.bak`.
- Save atomically via temp file + replace.

### Multiline entries

Plain line-per-entry history cannot safely preserve multiline prompts.

Mitigation:

- Use JSON-lines with a single `text` field.
- This keeps the format simple while supporting multiline input.

---

## Testing Plan

### Unit tests

1. `get_repl_history_path()` returns `~/.aye/history` by default.
2. Config/env override works for `history_file`.
3. Parent directory is created.
4. `history=off` uses `InMemoryHistory`.
5. `history=on` uses `AyePersistentHistory`.
6. Empty strings are not stored.
7. Whitespace-only strings are not stored.
8. `exit`, `quit`, and `:q` are not stored.
9. Shell commands are stored.
10. AI prompts are stored.
11. Built-in commands other than exit commands are stored.
12. Exact duplicates are not duplicated.
13. Re-entered duplicate moves to most-recent position.
14. More than 500 entries are pruned to 500.
15. Invalid/corrupt history does not crash startup.
16. Multiline entries round-trip correctly.
17. Only original user input is stored, not augmented prompt data.
18. URL/plugin-expanded context is not written to history.
19. Shell capture output appended by `shellcap` is not written to history.
20. Source file contents from `@` or `with` are not written to history.
21. `blog` stores the user-facing command text, not the internal blog preamble.

### Integration/manual tests

1. Start `aye chat`.
2. Type several shell commands and AI prompts.
3. Use `@src/main.py` and confirm history stores only the typed `@` prompt, not file contents.
4. Use URL/GitHub/SonarQube/plugin context and confirm history stores only the typed command/prompt, not fetched content.
5. Enable `shellcap`, run a captured shell command, then submit an AI prompt and confirm history stores only the typed AI prompt, not attached shell output.
6. Exit.
7. Start `aye chat` again.
8. Press Up.
9. Confirm previous user-entered inputs appear exactly as typed.
10. Confirm `exit` / `quit` are not recalled.
11. Confirm duplicate commands appear only once.
12. Confirm feedback prompt input is not recalled.
13. Confirm history file exists at `~/.aye/history`.
14. Confirm history file has no more than 500 entries.

### Privacy test

1. Set:

   ```text
   history=off
   ```

2. Start chat and type inputs.
3. Exit and restart.
4. Confirm prior inputs do not persist.

---

## Rollout Strategy

### Phase 1 — Safe global persistence

- Add `AyePersistentHistory`.
- Store at `~/.aye/history`.
- Save only original user-submitted main REPL inputs.
- Exclude empty, duplicate, and exit commands.
- Keep max 500 entries globally.
- Keep feedback prompt in-memory.
- Add `history=on|off` config support.

### Phase 2 — Maintenance command

Optional future command:

```text
history-clear
```

Behavior:

- Deletes `~/.aye/history`.
- Clears current in-memory history if possible.

### Phase 3 — Optional filtering improvements

Possible future additions:

- Do not save commands matching configured patterns.
- Do not save lines containing obvious secret assignments.
- Add `history_ignore_patterns` config.

Do not include these in v1 unless explicitly requested.

---

## Final Recommendation

Implement one global persistent REPL history:

- `~/.aye/history`
- enabled by default
- config toggle: `history=on|off`
- max 500 unique entries globally
- save only exact user-submitted shell commands, AI prompts, and built-ins
- do not save internally augmented prompt content, source contents, plugin/web context, shell capture output, image data, or hidden prompt wrappers
- exclude empty lines, `exit`, `quit`, `:q`, and exact duplicates
- keep feedback prompt in-memory
- no chat IDs
- no metadata

This gives the expected shell-like UX with the lowest implementation complexity.
