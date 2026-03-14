# Plan: Streaming Markdown Rendering (Stable Prefix Markdown + Tail Text)

## Objective
During LLM streaming, render **Markdown progressively** without the current behavior of:
- streaming as plain `Text`, then
- re-rendering once at the end as `Markdown`.

Instead, render a **stable prefix** as Markdown (nice formatting early) and keep the **unstable tail** as plain `Text` (so incomplete Markdown constructs don’t visually break the entire panel).

This plan targets the existing Rich-based streaming UI implemented in:
- `presenter/streaming_ui.py`

## Why “stable prefix + tail” is best UX
Markdown streaming is hard mainly because **incomplete block structures** (especially fenced code blocks) cause large sections of content to render incorrectly until they close.

Typical failure mode:
- Model emits: 
  ```
  ```python
  print('hi')
  
  (no closing fence yet)
  ```
- Markdown renderer treats **everything after** as code block, so the whole stream looks wrong.

By splitting:
- render **only the part that is known safe** as Markdown
- show the remainder as raw `Text`

…we get correct Markdown most of the time, and the tail can be “a little ugly” without ruining the entire message.

---

## Scope of code changes
### Files to change
1. **`presenter/streaming_ui.py`**
   - Add streaming split logic
   - Update panel rendering to support “Markdown prefix + Text tail”
   - (Recommended) add throttling/debouncing so we don’t re-parse Markdown on every single character/word

### Files not required to change
- `presenter/repl_ui.py`: theme is already shared (`deep_ocean_theme` imported in `streaming_ui.py`).
- Controller/model code: streaming callback already provides partial text; rendering is purely presenter responsibility.

---

## Design overview
We will replace the binary decision:
- `use_markdown=True` => render whole content as `Markdown`
- `use_markdown=False` => render whole content as `Text`

…with a **composite renderable** during streaming:
- `Markdown(prefix)`
- `Text(tail)` (optional dim style)

### Rendering rule
- **Final render** (`is_final=True`) stays as full `Markdown(content)`.
- **Streaming render** uses a split:
  - `prefix = stable_markdown_prefix(content_so_far)`
  - `tail = remainder` (raw)

---

## Step-by-step implementation plan

### Step 1) Add a streaming Markdown splitter
Add helper(s) in `presenter/streaming_ui.py`:

#### 1.1 `_split_streaming_markdown(text: str) -> tuple[str, str]`
Goal: pick a safe cut position so `prefix` is unlikely to cause large-scale misrendering.

**Recommended heuristics (ordered):**

1) **Fenced code blocks safety**
- Scan lines for fenced markers that start at line beginning (allowing indentation):
  - ```
  - ~~~
- Track whether we are currently “inside a fence”.
- If inside a fence (odd number of fences opened minus closed):
  - choose cut position = **start index of the last opened fence line**
  - return:
    - prefix = text up to that fence start
    - tail = from that fence start to end

This ensures an unclosed fence does not swallow the rest of the rendered content.

2) **Otherwise, cut on a paragraph boundary**
If not inside a fence:
- Prefer cut position at the **last blank line** (`\n\n`) so we mostly render complete paragraphs/lists.
- If no blank line exists, cut at the **last newline** (`\n`) so we render complete lines.
- If no newline, prefix may be empty and everything becomes tail.

**Notes:**
- This is heuristic, not a full Markdown parser, but it fixes the most disruptive streaming artifact (code fences).


### Step 2) Render composite content in `_create_response_panel`
Currently `_create_response_panel(content: str, use_markdown: bool, show_stall_indicator: bool)` chooses one renderer.

We will extend it so that when streaming Markdown is desired, it can render:
- Markdown prefix
- Text tail

#### 2.1 Introduce a new “mode” concept (recommended)
Instead of overloading `use_markdown`, use an explicit mode:
- `render_mode = "text" | "markdown" | "markdown_stream"`

However, to keep the public surface small, we can keep `use_markdown` and implement:
- `use_markdown=True` + `streaming=True` => composite
- `use_markdown=True` + final => full Markdown

**Minimal change option** (lowest refactor):
- Keep current signature
- Add internal branching:
  - if `use_markdown` and `show_stall_indicator` etc. -> build composite

#### 2.2 Add helper `_render_streaming_markdown(text: str, show_stall_indicator: bool) -> rich.renderable`
Implementation idea:
- `(prefix, tail) = _split_streaming_markdown(text)`
- `md = Markdown(prefix)` if prefix else `Text("")`
- `tail_text = Text(tail)` with a style like `"dim"` or a theme key (optional)

Combine using a container that Rich can render as one object:
- `Table.grid(padding=0)`
  - row 1: md
  - row 2: tail_text (only if tail non-empty)
  - row 3: stall indicator (if enabled)

Alternative containers:
- `rich.console.Group(md, tail_text, stall_text)`

`Table.grid` is already used in this file and works consistently.


### Step 3) Update streaming refresh calls to use the composite renderer
In `StreamingResponseDisplay`:

- During `_animate_words`, after appending new content, the code currently calls:
  - `use_markdown=True` only on newline
  - otherwise `use_markdown=False`

Change behavior:
- For streaming updates, **always** render with the streaming markdown composite (prefix Markdown + tail Text).

That means:
- Newline, whitespace, word updates all call a unified refresh method that renders `markdown_stream`.

**Reason:**
- Even inline markdown like `**bold**` should show as soon as it becomes stable.
- The splitter ensures partial block issues don’t dominate.


### Step 4) Add debouncing / throttling (important)
Rendering `Markdown(prefix)` on every char/word can be expensive and can flicker.

Add a small throttle in `StreamingResponseDisplay`:
- Track `self._last_render_time`.
- Define `self._min_render_interval` default e.g. **0.05–0.12 seconds**.
- Only call `self._live.update(...)` if enough time passed.

Where to apply:
- Inside `_refresh_display(...)` (best centralization)

What not to do:
- Do not throttle the state updates (animated content) themselves—only throttle expensive UI refresh.

Optional: allow env var override
- `AYE_STREAM_MARKDOWN_MIN_INTERVAL=0.08`


### Step 5) Integrate stall indicator with composite rendering
Current stall indicator logic:
- If content is Markdown, it wraps markdown + stall text in a container.
- Else it appends to `Text`.

With the new composite renderer:
- Always treat streamed renderables as “container-like”.

Implementation:
- When `show_stall_indicator=True`, append a last row:
  - `Text("\n⋯ waiting for more", style="ui.stall_spinner")`

Important: stall monitor currently forces `use_markdown=False` when showing stall.
- Update stall monitor update path to call the same streaming render mode (so the visual style stays consistent).


### Step 6) Preserve final rendering semantics
No change required to user-visible final output:
- When `update(..., is_final=True)`:
  - set `_animated_content = content`
  - render full Markdown (as today)

Just ensure the final `_refresh_display` uses:
- full Markdown render, not split mode


---

## Detailed edits checklist (by function)

### `presenter/streaming_ui.py`

#### A) Add new helpers
- `_split_streaming_markdown(text: str) -> tuple[str, str]`
- `_render_streaming_markdown(text: str, show_stall_indicator: bool) -> "RenderableType"`

Heuristic details for `_split_streaming_markdown`:
- Work line-by-line to detect fences:
  - fence line matches something like: `^\s*```|^\s*~~~`
- Keep last fence start index.
- If currently inside a fence => cut at last fence start.
- Else cut at last `\n\n` then fallback to last `\n`.


#### B) Update `_create_response_panel`
- Replace the current `if use_markdown and content: rendered_content = Markdown(content)` branch with:
  - If `use_markdown` AND we are in streaming mode: `rendered_content = _render_streaming_markdown(content, show_stall_indicator)`
  - Else if `use_markdown` and final: `rendered_content = Markdown(content)`
  - Else: `rendered_content = Text(content)`

Decision on how to signal streaming mode:
- Easiest: add a new boolean parameter `streaming: bool = False`.
  - Streaming calls pass `streaming=True`
  - Final calls pass `streaming=False`

This avoids guessing based on other state.


#### C) Update `_refresh_display`
- Add parameters:
  - `render_markdown: bool`
  - `streaming: bool`
- Apply throttle before calling `self._live.update(...)`.


#### D) Update `_animate_words`
- After each append (word/whitespace/newline), call:
  - `_refresh_display(render_markdown=True, streaming=True, show_stall=False)`

We no longer need to switch to `use_markdown=False` between words.


#### E) Update `_monitor_stall`
- When it decides to redraw with the stall indicator, call:
  - streaming markdown render (`render_markdown=True, streaming=True`) so the content remains formatted.

Keep the timestamp logic unchanged (it is already correct about `_last_receive_time`).


---

## Performance considerations
- Markdown parsing cost grows with prefix size.
- Throttling is required to keep CPU usage sane.

Optional improvement (later):
- Only rebuild the Markdown renderable when `prefix` changed (not just tail).
  - Track `self._last_prefix_rendered`.
  - If splitter produces same prefix as last time, only update tail text.

This is an optimization and not necessary for the first implementation.


---

## Testing plan
Add/adjust unit tests if there is an existing test suite for presenters.
If not, implement a small manual test checklist.

### Manual test checklist
1) **Inline markdown**
- Stream: `Hello **wor` then `ld**` 
- Expect: after completion of `**bold**`, the prefix renders bold, tail remains raw while incomplete.

2) **Unclosed fenced code**
- Stream: 
  - `Here is code:\n```python\nprint('x')\n`
- Expect: content before fence renders as Markdown; fence block stays in tail as raw Text until closed.

3) **Closing fence arrives**
- Stream additional: `\n```\n`
- Expect: now the fence becomes stable and is included in Markdown prefix; code block renders correctly.

4) **Stall indicator**
- Let stream pause beyond `stall_threshold`.
- Expect: the “⋯ waiting for more” appears without breaking the formatted prefix.

5) **Final render snap**
- When final arrives, expect whole response rendered as Markdown (no tail).


---

## Rollout notes
- Implement behind no flag initially (it is only a presenter change).
- If you want a safety toggle, add env var:
  - `AYE_STREAM_MARKDOWN=on|off`
  - default `on`

(But this is optional; simplest is to just switch behavior.)


---

## Summary
This change will:
- keep streaming responsive
- render Markdown progressively
- avoid the most disruptive incomplete-Markdown failure mode (unclosed code fences)
- keep final output unchanged

Primary implementation touchpoints are in `presenter/streaming_ui.py`:
- add a splitter
- render composite Markdown+Text during streaming
- throttle refreshes
- ensure stall indicator uses the same composite rendering
