# Streaming output tailing (Option B)

## Goal
When the streaming viewport fills up, don’t stop updating with a cropped view + ellipsis (`…`). Instead, keep the **bottom of the response visible** and continuously update it, similar to `tail -f`.

Constraints:
- Keep the current architecture: **prompt_toolkit input** + **Rich output**.
- Preserve existing behavior and usability:
  - completions/menu behavior
  - stall indicator / pulsing cursor
  - word-by-word animation (if enabled)
  - final response formatting (Markdown panel)
- Avoid major refactors (no Textual migration).

## Why the current UI shows `…`
`aye.presenter.streaming_ui.StreamingResponseDisplay` uses `rich.live.Live`, which repeatedly renders into a **fixed terminal region**.

When the renderable becomes taller than the available height, Rich crops it using a vertical overflow strategy (often showing an ellipsis marker). Live itself does not implement scroll-follow.

## Option B: “Tail rendering” (keep Live, render only the bottom)
### High-level idea
Maintain the full streaming content in memory as you do now (e.g., `self._current_content` / `self._animated_content`).

But **when rendering into the Live panel**, compute the terminal height budget and show only the **last N visible terminal lines**.

This makes the streaming output appear “natively scrollable” because the visible portion always follows the bottom.

### Recommended behavior (pragmatic)
To reduce complexity and avoid Markdown edge cases while streaming:

- **During streaming** (`is_final=False`):
  - render the tail as **plain wrapped text** inside your existing Panel.
  - keep the existing stall indicator line.
- **On final** (`is_final=True`):
  - stop Live and print the **full final response** using the existing Markdown formatting (full panel).

This keeps “tail-follow” during streaming and still gives users full formatted output in terminal scrollback after completion.

## Where changes should go
This should be localized to `aye/presenter/streaming_ui.py`.

Key integration points:
1. `StreamingResponseDisplay.start()`
   - Live initialization parameters (optional overflow tweaks; see below)
2. `StreamingResponseDisplay.update(content, is_final=...)`
   - on final: stop animation immediately and render final
3. `StreamingResponseDisplay._refresh_display()`
   - compute a tail version of the content before calling `_create_response_panel(...)`

No changes are required in:
- `controller/repl.py` (prompt_toolkit stays)
- `controller/llm_invoker.py` (stream callback wiring stays)

## Implementation plan

### Step 1: Add a tailing mode switch (optional but recommended)
Add a config flag so you can test/roll out safely.

Examples:
- Env var: `AYE_STREAM_TAIL=on|off`
- Or config key via `get_user_config(...)`

Default recommendation: **on**, once verified.

### Step 2: Compute available render height
In `_refresh_display()` (or right before building the panel), compute how many terminal rows are available for the panel contents.

Inputs:
- `self._console.size.height`
- If prompt_toolkit reserves menu space, remember that the *actual visible height* might be effectively lower while menu is visible.
  - In your REPL you call `session.prompt(... reserve_space_for_menu=6)`.
  - You cannot reliably detect menu visibility from Rich.
  - Best approach: be conservative and subtract a small constant buffer (e.g., 2–8 rows).

Suggested calculation (tune empirically):
- `terminal_height = self._console.size.height`
- `reserved = 6`  (match prompt reserve_space_for_menu) or make this configurable
- `panel_chrome = 2` (borders) + padding you use
- `stall_line = 1 if stall indicator is shown else 0`
- `available = max(1, terminal_height - reserved - panel_chrome - stall_line)`

This `available` is the maximum number of wrapped lines that can be shown inside the panel.

### Step 3: Wrap and tail the content
You want “last N visible lines”, not “last N newline-delimited lines”. That means you must account for wrapping at the current terminal width.

Recommended approach (streaming mode):
- Use Rich `Text` wrapping at the console width.

Pseudo-steps:
1. `text = Text(stream_content)`
2. Wrap to current width:
   - `wrapped_lines: List[Text] = text.wrap(self._console, width=inner_width)`
   - `inner_width` should roughly match panel inner width (console width minus panel borders/padding).
3. Keep only the last `available` wrapped lines:
   - `tail_lines = wrapped_lines[-available:]`
4. Convert back to a renderable:
   - Either join `tail_lines` into one `Text` (preserving style)
   - Or render a `Group(*tail_lines)`

Notes:
- If you don’t rely on style during streaming, simplest is to join plain strings.
- If you already use colored/styled stall markers, keep those as separate lines below.

### Step 4: Render tail inside existing panel
In streaming mode, feed the “tailed renderable” to your existing `_create_response_panel(...)`.

Two viable patterns:

**Pattern A (minimal intrusive): keep `_create_response_panel` signature**
- Before calling `_create_response_panel(self._animated_content, ...)`, replace the string content with `tailed_text_as_string`.
- `_create_response_panel` continues to build Panel/Markdown/Text as it does.

**Pattern B (more control): allow renderable override**
- Teach `_create_response_panel` to accept either `str` or a Rich renderable.
- In streaming: pass a `Text` renderable.
- In final: pass full markdown string.

Pattern A is usually easier if `_create_response_panel` currently assumes strings.

### Step 5: Final render should print full output to scrollback
To preserve usability:
- On `is_final=True`, do not leave the last state in Live (which is a fixed region).
- Instead:
  1. Stop Live (`live.stop()`)
  2. Print a normal Rich panel containing the **full** final response (Markdown), so it becomes part of the terminal scrollback.

This ensures users can scroll back through the entire answer.

### Step 6: Keep stall indicator and animation behavior
You likely have logic similar to:
- word-by-word animation (sleep/delay)
- a stall monitor thread that forces refresh/pulse

In tail mode:
- Keep all that behavior.
- Just apply tailing at the final stage (right before rendering).

Important: tailing should be applied to the *content you actually render*.
- If you keep `self._animated_content`, tail that.
- If you keep `self._current_content` and derive animated content, tail whichever is used for display.

### Step 7: (Optional) Try Rich’s `vertical_overflow` as a quick fallback
Depending on the Rich version, `Live(..., vertical_overflow="visible")` may reduce ellipsis behavior.

This is not a complete solution by itself (and varies by terminal), but can be:
- a fallback option
- or combined with tailing

## Edge cases and design decisions

### 1) Markdown streaming correctness
If you tail the Markdown source and render it as Markdown mid-stream, you can cut:
- a fenced code block opener/closer
- list indentation context
- tables

This is why the recommended approach is:
- **stream as plain text** (tail-safe)
- **final as full Markdown** (correct formatting)

If you strongly want Markdown formatting while streaming:
- you must tail at “safe boundaries” (e.g., never start inside an open fenced block)
- you already have logic that detects fenced code blocks (`_split_streaming_markdown` or similar). You can expand it to pick a tail start that respects fence parity.

### 2) prompt_toolkit reserved space
prompt_toolkit may reserve lines for its completion menu. You can’t reliably query that from Rich, so:
- subtract a conservative constant (e.g. 6)
- make it configurable

### 3) Performance
Wrapping the entire response on every token can become expensive for very long outputs.

Mitigations:
- Only recompute wrapping when:
  - content length changed, and
  - either (a) enough time has passed (throttle), or (b) number of new chars exceeds a threshold
- Cache previous wrap results and only wrap the appended delta (harder)

A simple throttle (e.g., refresh at most every 30–60ms) is usually sufficient.

### 4) Resizes
If the terminal resizes, `console.size` changes and your wrap/tail changes automatically on the next refresh.

## Testing plan

### Manual test cases
1. Short response (< terminal height)
   - should behave exactly like current behavior
2. Long response (> terminal height)
   - should keep updating and always show the bottom of the content
   - no `…` cropping
3. Stall indicator
   - should still pulse/show and not consume the last content line unexpectedly
4. Completion menu open
   - tailing should still look okay (may reduce visible lines, but should not freeze)
5. Final output
   - after completion, full response should be printed to scrollback in Markdown panel

### Automated / unit-level checks (optional)
- Extract tailing logic into a pure function:
  - inputs: `content`, `width`, `max_lines`
  - output: `tailed_content`
- Unit-test wrapping/tailing boundaries.

## Rollout suggestion
1. Implement tailing behind `AYE_STREAM_TAIL=on`.
2. Verify on common terminals:
   - macOS Terminal / iTerm2
   - Linux terminals
   - Windows Terminal
3. Make it default once stable.

---

## Minimal pseudo-code sketch

```python
def _refresh_display(self):
    content = self._animated_content

    if not self._is_final and self._tail_enabled:
        available = self._compute_available_lines()
        inner_width = self._compute_inner_width()
        content = tail_wrap_text(content, console=self._console, width=inner_width, max_lines=available)

    panel = self._create_response_panel(content, is_final=self._is_final)
    self._live.update(panel)


def tail_wrap_text(content: str, console: Console, width: int, max_lines: int) -> str:
    text = Text(content)
    wrapped = text.wrap(console, width=width)
    tail = wrapped[-max_lines:]
    # simplest: join plain text (streaming mode)
    return "\n".join([t.plain for t in tail])
```

(Exact names depend on your existing `StreamingResponseDisplay` implementation.)
