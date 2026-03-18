# Auto-attach failing shell output to next LLM prompt

## Goal
When the user runs a **shell command** in Aye Chat and it **fails**, automatically capture its output (stdout/stderr/return code) and **attach it to the very next LLM prompt**.

This supports workflows like:
1. User runs `pytest`
2. Tests fail
3. User types: "fix the failing tests"
4. The LLM receives the failing test output without the user copy/pasting


## Non-goals (v1)
- Capturing output from fully interactive TTY programs (vim/top/less/etc.). `os.system()` path cannot reliably capture terminal output.
- Persisting shell output across process restarts.
- Automatically attaching output to prompts that aren't LLM prompts (built-ins, shell commands).


## Current code paths (where to hook)
Shell execution happens in `controller/repl.py` in two places:

1) Normal shell attempt (the "try shell command first" branch):
- Calls plugin: `execute_shell_command`
- Prints `stdout`/`stderr`

2) Forced shell execution with `!` prefix:
- `_execute_forced_shell_command()`
- Calls plugin: `execute_shell_command` with `force=True`
- Prints `stdout`/`stderr` or `message`

The shell plugin already returns structured results for non-interactive commands:
- `plugins/shell_executor.py::_execute_non_interactive()` returns:
  - `stdout`, `stderr`, `returncode`
  - plus `error` on `CalledProcessError` / not found


## Desired behavior (spec)

### Capture rule (what counts as failure)
Arm auto-attach when the shell result indicates failure via **either** of these:
- `returncode` is present and `returncode != 0`
- `error` key is present in the `shell_response` **even if `returncode` is missing**

Rationale: some error paths may communicate failure via `error` without a trustworthy `returncode`.

**Interactive command guard**: If the shell response contains a `message` key but does
**not** contain `stdout`, treat it as an interactive command response and do **not**
arm the attach — even if `exit_code != 0`. Interactive commands cannot have their
output captured reliably.

```python
# Inside _capture_shell_result:
if "message" in shell_response and "stdout" not in shell_response:
    return  # Interactive command — don't capture
```


### Attach rule
- Only attach to the **next LLM invocation**.
- "Next LLM invocation" includes:
  - regular prompts that go down the LLM path
  - `with ...: ...` prompts (LLM)
  - `blog ...` prompts (LLM)
  - prompts using `@file` expansion (LLM)
- Do **not** attach to:
  - built-in commands (`diff`, `restore`, `model`, etc.)
  - subsequent shell commands


### One-shot semantics
- After the output is attached to the next LLM prompt, the pending attach state is **cleared immediately**.
- The pending state is **not** cleared by subsequent shell commands, built-ins, or successful commands.
- If the user wants to re-send the same failure output, they re-run the failing command.
- Rationale: simplest mental model. "Output attached once, then gone." Eliminates
  stale-attach edge cases without additional conditional logic.


### User-visible UX
- The user sees normal shell output printing exactly as today.
- Print a short, subtle note when attach is armed:
  - `"(Captured failing command output; will attach to next AI prompt)"`


## Data model (session state)
Store pending output in memory on the session `conf` object:

```python
conf._last_shell_result = {
  "cmd": "pytest -q",
  "cwd": "/abs/path",
  "returncode": 1,
  "stdout": "...",
  "stderr": "...",
  "timestamp": "...",
  "truncated": True/False,
}
conf._pending_shell_attach = True/False
```

Notes:
- Prefix underscore to signal internal/session-only.
- **Important**: Verify the `conf` object supports dynamic attribute assignment.
  If it uses `__slots__` or a frozen dataclass, this will raise `AttributeError`.
  Recommended: initialize both attributes in `commands.initialize_project_context()`
  (or wherever `conf` is first created) with default values:
  ```python
  conf._last_shell_result = None
  conf._pending_shell_attach = False
  ```
  Alternatively, use `hasattr()` guards in the helpers as a safety net.


## Truncation & size limits (hard requirement)
Shell output can be huge; enforce strict limits.

**v1 hard spec — two-tier truncation:**

### Tier 1: Line limit
- Truncate to **100 lines maximum**.
- Keep the **tail** (last 100 lines).
- Apply independently to stdout and stderr.
- Mark truncation clearly:
  - `...[truncated: showing last 100 lines]`

### Tier 2: Byte size limit
- After line truncation, enforce a **10 KB hard cap** on the combined
  stdout + stderr payload.
- If the combined size exceeds 10 KB, truncate further (tail-trim characters)
  until it fits.
- Rationale: a single line can be enormous (minified JS, binary garbage, long
  log lines). The byte cap protects LLM token budgets.

Implementation detail:
- Split on lines (preserve newlines), take the last 100.
- If the original had more than 100 lines, set `truncated = True`.
- After line truncation, check `len(stdout.encode('utf-8')) + len(stderr.encode('utf-8'))`.
  If > 10240, trim the longer of the two (from the front/top) until under budget.


## Prompt format (how to attach)
When an LLM prompt is about to be invoked, rewrite the effective prompt:

```text
<User prompt>

---
Captured output from last failing command:
$ pytest -q
cwd: /path/to/project
exit_code: 1

STDOUT:
<...>

STDERR:
<...>
---
```

Guidelines:
- Use explicit delimiters (`---`) to separate logs from instructions.
- Include command + cwd + exit code for context.
- Include both stdout and stderr (many tools print failures to stderr).


## Module structure

All capture/attach logic lives in a **new dedicated module**:

```
controller/shell_capture.py
```

This module contains:
- `truncate_output(text: str, max_lines: int = 100) -> tuple[str, bool]`
- `enforce_byte_limit(stdout: str, stderr: str, max_bytes: int = 10240) -> tuple[str, str]`
- `capture_shell_result(conf, *, cmd: str, shell_response: dict) -> None`
- `maybe_attach_shell_result(conf, prompt: str) -> str`

Rationale: Both `repl.py` (standard + forced shell paths) and
`command_handlers.py` (`handle_with_command`, `handle_blog_command`)
need to call these helpers. A shared module avoids duplication and
cross-module import tangles.


## Implementation plan (step-by-step)

### 1) Create `controller/shell_capture.py`

Add small utilities:

- `def truncate_output(text: str, max_lines: int = 100) -> tuple[str, bool]:`
  - Returns `(truncated_text, was_truncated)`.
  - Keeps the tail (last `max_lines` lines).
  - Prepends `...[truncated: showing last 100 lines]` if truncated.

- `def enforce_byte_limit(stdout: str, stderr: str, max_bytes: int = 10240) -> tuple[str, str]:`
  - Returns `(stdout, stderr)` trimmed so their combined UTF-8 byte size ≤ `max_bytes`.
  - Trims the longer of the two from the front first.

- `def capture_shell_result(conf, *, cmd: str, shell_response: dict) -> None:`
  - **Interactive guard**: if `"message" in shell_response and "stdout" not in shell_response`, return immediately.
  - Extract `stdout`, `stderr`, `returncode`, `error`.
  - Determine `failed`:
    - `failed = (returncode is not None and returncode != 0) or ("error" in shell_response and shell_response["error"])`
  - If failed:
    - Truncate stdout/stderr using `truncate_output` (tier 1).
    - Enforce byte limit using `enforce_byte_limit` (tier 2).
    - Store in `conf._last_shell_result`.
    - Set `conf._pending_shell_attach = True`.
    - Print subtle UX note: `(Captured failing command output; will attach to next AI prompt)`
  - If not failed: do nothing (do **not** clear pending state here).

- `def maybe_attach_shell_result(conf, prompt: str) -> str:`
  - If `getattr(conf, '_pending_shell_attach', False)` and `getattr(conf, '_last_shell_result', None)`:
    - Append formatted block to prompt.
    - **Immediately clear** `conf._pending_shell_attach = False` (one-shot).
    - Return augmented prompt.
  - Else return original prompt.


### 2) Capture results in both shell execution paths in `repl.py`

Import from the new module:
```python
from aye.controller.shell_capture import capture_shell_result, maybe_attach_shell_result
```

#### 2a) Normal shell execution branch
In the existing section:

```python
shell_response = conf.plugin_manager.handle_command(...)
if shell_response is not None:
    # prints...
```

After printing, call:
- `capture_shell_result(conf, cmd=<full cmd string>, shell_response=shell_response)`

Build `cmd` as a human-readable string, e.g. join `original_first` + args as entered.


#### 2b) Forced `!` shell execution
In `_execute_forced_shell_command()`:
- After receiving `shell_response`, call the same capture helper.


### 3) Attach to the next LLM prompt in ALL LLM entry points

There are three LLM entry points. Each needs `maybe_attach_shell_result`.

#### 3a) Standard LLM path (`repl.py`)
Right before calling `invoke_llm(prompt=cleaned_prompt, ...)`:
- `cleaned_prompt = maybe_attach_shell_result(conf, cleaned_prompt)`

Important: do this **after** `@` reference parsing, so the logs are not mangled by `parse_at_references`.


#### 3b) `with ...: ...` command (`command_handlers.py`)
`handle_with_command()` extracts the prompt (part after `:`) and calls
`invoke_llm()` directly. The attach must happen **inside**
`handle_with_command()`, right before `invoke_llm()` is called:

```python
from aye.controller.shell_capture import maybe_attach_shell_result

# Inside handle_with_command, just before invoke_llm:
final_prompt = maybe_attach_shell_result(conf, new_prompt_str.strip())
llm_response = invoke_llm(prompt=final_prompt, ...)
```

Rationale: `repl.py` delegates to `handle_with_command()` before it reaches
the standard LLM path, so attaching in `repl.py` would miss the `with` flow.


#### 3c) `blog ...` command (`command_handlers.py`)
`handle_blog_command()` builds `llm_prompt` and calls `invoke_llm()`.
Apply `maybe_attach_shell_result` to `llm_prompt` before `invoke_llm()`:

```python
from aye.controller.shell_capture import maybe_attach_shell_result

# Inside handle_blog_command, just before invoke_llm:
llm_prompt = maybe_attach_shell_result(conf, llm_prompt)
llm_response = invoke_llm(prompt=llm_prompt, ...)
```


### 4) Clear attach state at the correct time
- Clear `conf._pending_shell_attach` **immediately** inside `maybe_attach_shell_result()` after augmenting the prompt.
- One-shot: once attached, it's gone. User re-runs the command if they need it again.
- Do **not** clear on subsequent shell commands (whether they succeed or fail).
  A new failing command will simply overwrite `_last_shell_result` and re-arm.


### 5) Telemetry considerations
Do not change telemetry payloads.
- Telemetry currently records only command names and argument-presence.
- Ensure captured stdout/stderr is never sent in telemetry.


## Files affected

| File | Change |
|---|---|
| `controller/shell_capture.py` | **New file** — `truncate_output`, `enforce_byte_limit`, `capture_shell_result`, `maybe_attach_shell_result` |
| `controller/repl.py` | Import from `shell_capture`. Call `capture_shell_result` in both shell paths; call `maybe_attach_shell_result` in standard LLM path |
| `controller/command_handlers.py` | Import from `shell_capture`. Call `maybe_attach_shell_result` inside `handle_with_command()` and `handle_blog_command()` before `invoke_llm()` |

No changes needed to:
- `plugins/shell_executor.py` (already returns the needed data)
- `controller/llm_invoker.py`
- `presenter/` files
- `__main__.py`


## Edge cases & handling

### Interactive commands
`shell_executor` returns `{"message": ..., "exit_code": ...}` for interactive commands.
- The interactive guard in `capture_shell_result` skips these:
  `if "message" in shell_response and "stdout" not in shell_response: return`
- Even if `exit_code != 0`, interactive output is not captured.


### Command not found
Typically returned with `error` + `returncode=127`.
- This should arm attach; the user might ask "why didn't this run?".


### Multiple failures in a row
- Last failure wins. Each failing command overwrites `conf._last_shell_result`
  and re-arms `_pending_shell_attach = True`.


### Very long single lines
- Handled by tier-2 byte limit (10 KB cap). Minified JS, binary garbage,
  or extremely long log lines will be truncated to fit.


## Testing plan

### Unit tests (suggested)
- `truncate_output`:
  - Returns unchanged text when ≤ 100 lines
  - Keeps tail 100 lines when > 100 lines
  - Sets `was_truncated = True` when truncated
  - Prepends truncation marker

- `enforce_byte_limit`:
  - No-op when combined size ≤ 10 KB
  - Trims longer string from front when over budget
  - Handles edge case of single huge string

- `capture_shell_result`:
  - Arms attach on `returncode != 0`
  - Arms attach on `error` present even if `returncode` missing
  - Does not arm attach on `returncode == 0` and no `error`
  - Does not arm on interactive response (`message` key, no `stdout`)
  - Truncates to last 100 lines (tail)
  - Enforces 10 KB byte cap

- `maybe_attach_shell_result`:
  - Appends expected block when pending
  - Clears pending flag after attaching (one-shot)
  - No-op if no pending output
  - No-op if `conf` doesn't have the attributes (graceful with `getattr`)


### Integration tests (suggested)
- Simulate shell response followed by LLM call:
  - run `pytest` (mock plugin response with failing output)
  - then prompt "fix tests"
  - assert `invoke_llm` receives augmented prompt

- Ensure `with ...:` and `blog ...` also attach when pending.

- Verify one-shot: after attaching once, a second LLM prompt does **not** include the output.


## Acceptance criteria
- After a failing shell command, the next LLM prompt includes the **last 100 lines** (tail) of stdout/stderr, capped at **10 KB** combined.
- Attach happens exactly once (one-shot), then clears immediately.
- No attach occurs after successful commands.
- Interactive commands do not arm the attach.
- Existing printing behavior is unchanged.
- `error` in shell response arms attach even if `returncode` is missing.
- All capture/attach logic lives in `controller/shell_capture.py`.
- Both `with` and `blog` LLM paths also attach when pending.


## Optional future enhancements
- Config key to control behavior:
  - `shell_attach=off|failures|always`
- `showcapture` / `clearcapture` built-ins.
- Keep a ring buffer of last N failures and let user pick which to attach.
- Streaming shell capture (real-time) for long-running commands.
