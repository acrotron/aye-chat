# `printraw` / `raw` command — implementation plan

## Goal
Add built-in REPL commands `printraw` and `raw` (alias) that reprint the
**most recent assistant response** as **plain text** (no Rich Panel/box/Markdown
rendering), wrapped with **simple delimiter lines before and after** the content
so the user can easily select/copy it from the terminal.

This directly addresses the UX regression where "boxed" output is harder
(or impossible) to copy in some terminals.

---

## Command behavior (spec)

### Syntax
- `printraw`
- `raw` (alias — same behavior)

(Optionally later: `printraw <n>` or `printraw last|prev`, but not required for v1.)

### Output format
When there is a stored last assistant response:

1) Print a blank line
2) Print a **start delimiter line**
3) Print the stored response **exactly as text**
4) Print an **end delimiter line**
5) Print a blank line

Delimiter lines (short, stable, ASCII, searchable):

- `--- RAW BEGIN ---`
- `--- RAW END ---`

Notes:
- Do **not** include any Rich box/panel styling.
- Do **not** render Markdown.
- Use Python's built-in `print()` — **not** `console.print()` or Rich
  markup — so content containing Rich-like tokens (e.g. `[bold]`,
  `[link=...]`) is printed literally rather than rendered.
- Preserve the assistant's raw string content as much as possible.
- Ensure the output always ends with a newline so copying doesn't
  "stick" to the shell prompt.

### Content scope
- **Summary only** — `printraw` outputs only the assistant's text summary,
  not the list of written files or file contents.
- Users who want to see file-level changes should use `diff <file>`.

### When no last response exists
If the user runs `printraw` / `raw` before any assistant response is available,
or if the last response was whitespace-only:
- Print a small warning: `No assistant response available yet.`

### Mid-stream behavior
`printraw` is only available after the response completes.
`last_assistant_text` is set only after streaming finishes, never mid-stream.

---

## Data source: where the command gets its text

### Requirement
`printraw` must print the underlying assistant response text, not a
re-rendered/boxed version.

### Plan (Option A — recommended)
Store the last assistant response string in-memory near the REPL loop.

- Add `last_assistant_text: Optional[str] = None` in `controller/repl.py`.
- After each successful LLM response is processed/displayed, update:
  `last_assistant_text = llm_response.summary`
  (or the string passed to `print_assistant_response`).

### What to store?
For v1, store the same string currently passed to
`print_assistant_response(summary: str)` — the text summary only.

---

## UI printing helper

New module: **`presenter/raw_output.py`**

Function: `print_assistant_response_raw(text: Optional[str]) -> None`

Responsibilities:
- Print delimiters and the raw text via plain `print()`.
- Avoid Rich Markdown rendering and avoid Panels.
- Treat `None` or whitespace-only text as "no response" and show warning.

Output shape:

```text
--- RAW BEGIN ---
<raw response text>
--- RAW END ---
```

---

## Wire-up: REPL command handling

### 1) Register as built-in commands
In `controller/repl.py`, extend `BUILTIN_COMMANDS`:
- Add `"printraw"` and `"raw"`

This ensures:
- They show up in completions (via the `get_completer` plugin).
- They are treated as built-ins rather than shell commands.

### 2) Command handler
In `controller/command_handlers.py`:
- `handle_printraw_command(last_assistant_text: Optional[str]) -> None`

### 3) Command dispatch
In the main command dispatch (`if lowered_first == ...` chain), add:

```python
elif lowered_first in ("printraw", "raw"):
    handle_printraw_command(last_assistant_text)
    telemetry.record_command("printraw", has_args=False, prefix=_AYE_PREFIX)
```

### 4) Help text update
In `presenter/repl_ui.py` → `print_help_message()` add an entry:
- `("raw / printraw", "Reprint last assistant response as plain text (copy-friendly)")`

---

## When to update `last_assistant_text`

Update `last_assistant_text` any time the tool prints a new assistant response.

Typical integration point:
- Right after a successful `invoke_llm(...)` + `process_llm_response(...)` path,
  when you already have the assistant's text.

Important: keep it updated even if:
- The assistant response is truncated.
- The assistant returns no file updates.
- The assistant returns an error message that is still shown to the user.

---

## Delimiter lines: rationale

Delimiter lines solve 3 problems:
1. Make it obvious where copyable output begins/ends.
2. Make it searchable in scrollback (`/RAW BEGIN` in `less`, etc.).
3. Avoid accidentally copying the prompt or preceding UI lines.

They must be:
- Plain ASCII
- Short (avoid wrapping on narrow terminals)
- Stable across versions (users build muscle memory)

---

## Testing plan

1. **Unit test: normal output**
   - Call `print_assistant_response_raw("hello")` and assert delimiters +
     content are present.

2. **Unit test: Rich markup leak**
   - Call with text containing `[bold]something[/bold]` or `[link=http://x]`.
   - Verify output contains the literal markup strings, not rendered versions.

3. **Unit test: empty / whitespace**
   - `print_assistant_response_raw(None)` → warning.
   - `print_assistant_response_raw("   ")` → warning.

4. **Manual REPL test**
   - Start `aye chat`.
   - Ask any prompt.
   - Run `raw` and verify:
     - Output is unboxed.
     - Delimiters appear before/after.
     - Text is selectable/copyable in the terminal.
   - Run `printraw` and verify same behavior.

5. **No-response case**
   - Start REPL and immediately run `raw`.
   - Verify a friendly warning is printed.
