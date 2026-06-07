# Ctrl+V Clipboard Image Paste — Detailed Implementation Plan

## Goal Summary

Add clipboard image input to the Aye Chat CLI.

The feature should let users attach an image from the system clipboard to the next LLM prompt, initially through a reliable explicit command:

```text
paste-image
```

Then, optionally, add experimental `Ctrl+V` support behind a config flag.

The feature must:

- Read images directly from the system clipboard.
- Stage clipboard images in memory only.
- Reuse the existing image attachment payload shape:
  - `file_name`
  - `mime_type`
  - `data_b64`
  - `bytes_size`
- Reuse existing model image capability gating.
- Record privacy-safe telemetry using distinct coarse event names for each image source.
- Never log, print, or persist image bytes or base64 outside the API request body.

Existing `@filename` image support is already implemented in `src/aye/plugins/at_file_completer.py` and is out of scope. Do not add or document `@filename` as a clipboard fallback.

---

## Architecture Overview

### Existing relevant files

#### `src/aye/model/attachments.py`

Already provides the existing image attachment model and file-based image helpers.

Important existing items:

- `IMAGE_MAX_BYTES`
- `ImageAttachment`
- `load_image_attachment(...)`
- Attachment wire keys:
  - `file_name`
  - `mime_type`
  - `data_b64`
  - `bytes_size`

Clipboard image support should reuse these conventions and size limits.

#### `src/aye/plugins/at_file_completer.py`

Already implements `@filename` image support.

This file should generally not be changed for clipboard support.

#### `src/aye/controller/repl.py`

Main integration point.

Relevant existing responsibilities:

- Defines `chat_repl(...)`.
- Defines `BUILTIN_COMMANDS` inside `chat_repl(...)`.
- Dispatches built-in commands.
- Creates prompt sessions through `create_prompt_session(...)`.
- Creates prompt key bindings through `create_key_bindings()`.
- Parses existing `@` references through plugin command `parse_at_references`.
- Holds local `attachments` before calling `invoke_llm(...)`.
- Records LLM telemetry before invocation.
- Handles model image capability gating through `_model_supports_images(...)`.

Clipboard images should be merged into the existing `attachments` list before model capability gating and before `invoke_llm(...)`.

Current telemetry kind selection in the normal AI prompt path:

```python
if attachments and used_at:
    telemetry.record_llm_prompt("LLM @")
elif attachments:
    telemetry.record_llm_prompt("LLM @")
elif used_at:
    telemetry.record_llm_prompt("LLM @")
else:
    telemetry.record_llm_prompt("LLM")
```

This will need to be replaced with the new four-way telemetry kind selection described in the Telemetry section below.

#### `src/aye/controller/command_handlers.py`

Best place to add a new handler:

```python
handle_paste_image_command(conf: Any) -> None
```

Optional later handler:

```python
handle_clear_attachments_command(conf: Any) -> None
```

#### `src/aye/controller/llm_invoker.py`

Already accepts `attachments` and checks image-capable models.

Usually no change needed.

#### `src/aye/model/api.py`

Already sends `attachments` through `cli_invoke(...)` and redacts `data_b64` in debug output through `_redact_payload_for_debug(...)`.

Usually no change needed except privacy tests if coverage is missing.

#### `src/aye/model/telemetry.py`

Telemetry is currently count-based event names.

Existing LLM event names include:

- `LLM`
- `LLM <with>`
- `LLM @`
- `LLM <blog>`

New event names for image support:

- `LLM @ attachment` — prompt used `@` image attachment(s)
- `LLM clipboard` — prompt used clipboard image attachment(s)

See the Telemetry section below for full rules.

#### `src/aye/presenter/repl_ui.py`

Relevant functions:

- `print_help_message()`
- `print_attachment_summary(...)`

Use `print_attachment_summary(...)` for safe clipboard attachment display.

---

## Proposed New Components

### `src/aye/model/clipboard_images.py`

New model-level helper for reading and encoding clipboard images.

Responsibilities:

- Read image from OS clipboard.
- Normalize clipboard image data to PNG.
- Enforce `IMAGE_MAX_BYTES`.
- Return attachment dict compatible with the existing backend contract.
- Raise clear, safe exceptions for unsupported clipboard, no image, oversized image, and unexpected failures.

Suggested public API:

```python
class ClipboardImageError(Exception): ...
class ClipboardImageUnavailableError(ClipboardImageError): ...
class ClipboardImageNotFoundError(ClipboardImageError): ...
class ClipboardImageTooLargeError(ClipboardImageError): ...


def clipboard_image_available() -> bool: ...
def load_clipboard_image_attachment(name_hint: str | None = None) -> dict[str, object]: ...
def read_clipboard_image_bytes() -> tuple[bytes, str]: ...
```

### `src/aye/controller/clipboard_attachments.py`

New controller-level helper for staged clipboard attachment state.

Responsibilities:

- Store pending clipboard attachments on the active REPL config object.
- Keep staging behavior reusable from command handler, REPL prompt flow, and optional key binding.
- Provide marker helpers for optional `Ctrl+V` UX.

Suggested public API:

```python
CLIPBOARD_MARKER_RE = ...


def get_pending_clipboard_attachments(conf: Any) -> list[dict[str, object]]: ...
def add_pending_clipboard_attachment(conf: Any, attachment: dict[str, object]) -> None: ...
def clear_pending_clipboard_attachments(conf: Any) -> None: ...
def pending_clipboard_attachment_count(conf: Any) -> int: ...
def make_clipboard_marker(conf: Any) -> str: ...
def strip_clipboard_markers(prompt: str) -> str: ...
```

Pending state should live on:

```python
conf._pending_clipboard_attachments
```

Clipboard images should stay in memory only.

Multiple calls to `paste-image` (or `Ctrl+V`) should accumulate. Each staged image is sent with the next normal AI prompt.

---

## Telemetry

The telemetry kind for LLM prompts should be determined using the following four-way logic.

The kind is selected based on what produced the image attachments, not on the merged `attachments` list, to avoid incorrectly attributing clipboard images to `@` or vice versa.

Variables available at the decision point:

- `used_at` — True when `@` resolved to explicit source file contents.
- `at_attachments` — image attachments produced by `@` reference parsing (from `at_response.get("attachments")`).
- `clipboard_attachments` — pending clipboard attachments merged in from `conf._pending_clipboard_attachments`.

Decision table:

| `used_at` or `at_attachments` | `clipboard_attachments` | `at_attachments` | Kind |
|-------------------------------|------------------------|-------------------|--------------------|
| no | no | no | `LLM` |
| yes | no | no | `LLM @` |
| yes or no | no | yes | `LLM @ attachment` |
| no | yes | no | `LLM clipboard` |
| yes | yes | any | `LLM clipboard` |

Simplified priority: clipboard > @ attachment > @ source > plain LLM.

Implementation sketch:

```python
if clipboard_attachments:
    kind = "LLM clipboard"
elif at_attachments:
    kind = "LLM @ attachment"
elif used_at:
    kind = "LLM @"
else:
    kind = "LLM"

telemetry.record_llm_prompt(kind)
```

Allowed event names for LLM prompts (complete list):

```text
LLM
LLM <with>
LLM @
LLM @ attachment
LLM <blog>
LLM clipboard
```

Update `src/aye/model/telemetry.py` to include the new kinds in the allowed set inside `record_llm_prompt(...)`.

Telemetry must not record:

- image filename
- MIME type
- byte size
- image count
- `data_b64`
- raw bytes
- clipboard marker text
- clipboard-vs-file source details beyond the coarse kind name

---

## Dependencies

### Existing dependencies

- `prompt_toolkit`
- `rich`

### New mandatory dependency

- `Pillow`

Add Pillow as a regular dependency in `pyproject.toml`:

```toml
dependencies = [
    ...,
    "Pillow>=9.0",
]
```

Pillow is used for `PIL.ImageGrab.grabclipboard()` on macOS and Windows.

### Linux subprocess fallbacks (included in Phase 1)

On Linux, `PIL.ImageGrab.grabclipboard()` is not reliably supported. The clipboard helper should include subprocess fallbacks from the start:

- Linux Wayland: `wl-paste --type image/png`
- Linux X11: `xclip -selection clipboard -t image/png -o`

If none of these tools are installed, the helper should raise `ClipboardImageUnavailableError` with a message suggesting the user install `wl-paste` (Wayland) or `xclip` (X11).

Subprocess calls must use:

- `shell=False`
- A timeout (e.g. 5 seconds)
- Captured output
- Graceful handling for missing executables (`FileNotFoundError`)

---

## Data Flow

### `paste-image` command flow

```text
User copies image to clipboard
  -> User types paste-image
  -> repl.py dispatches built-in command
  -> command_handlers.handle_paste_image_command(conf)
  -> clipboard_images.load_clipboard_image_attachment()
  -> clipboard_attachments.add_pending_clipboard_attachment(conf, attachment)
  -> print_attachment_summary(...) displays safe metadata
  -> No LLM call yet
  -> Multiple paste-image calls accumulate additional images
```

### Next prompt flow

```text
User types normal prompt
  -> repl.py parses existing @ references as it does today
  -> at_attachments and used_at are determined from @ parsing
  -> pending clipboard attachments are loaded from conf
  -> attachments = pending_clipboard_attachments + at_attachments
  -> model image capability gating runs
  -> telemetry kind is selected using the four-way logic
  -> invoke_llm(..., attachments=attachments)
  -> if invoke_llm returns successfully:
       clear pending clipboard attachments
  -> if invoke_llm raises:
       keep pending clipboard attachments for retry
```

### Optional `Ctrl+V` flow

```text
User copies image to clipboard
  -> User presses Ctrl+V when feature flag is enabled
  -> prompt_toolkit key binding runs
  -> clipboard image is loaded and staged (accumulates)
  -> safe marker is inserted into prompt buffer
  -> User submits prompt
  -> marker is stripped before LLM invocation
  -> attachment is sent through normal prompt flow
```

---

## Known v1 Limitations

### `with` and `blog` do not consume clipboard images

Pending clipboard images are only consumed by the normal AI prompt path in `chat_repl(...)`. The `with` and `blog` command paths call `invoke_llm(...)` directly from their own handler functions without merging clipboard attachments.

If a user runs `paste-image`, then uses `with file.py: do something`, the staged clipboard image is silently ignored for that prompt and remains staged for the next normal AI prompt.

This is a known v1 limitation. Options for future improvement:

- Warn the user when `with` or `blog` is submitted while clipboard images are staged.
- Consume clipboard attachments in those paths too.
- Allow the user to clear staged images explicitly.

---

## Step-by-Step Plan

# Phase 1 — Clipboard Image Loading Core

## Objective

Create the low-level clipboard image loader without changing REPL behavior.

## Files to create

- `src/aye/model/clipboard_images.py`
- `tests/test_clipboard_images.py`

## Files to modify

- `pyproject.toml`

## Files to inspect

- `src/aye/model/attachments.py`

## Implementation details

Add `Pillow>=9.0` to the `dependencies` list in `pyproject.toml`.

In `src/aye/model/clipboard_images.py`:

- Use `PIL.ImageGrab.grabclipboard()` as the primary implementation for macOS and Windows.
- Immediately add Linux subprocess fallbacks:
  - Wayland: `wl-paste --type image/png` (stdout is PNG bytes)
  - X11: `xclip -selection clipboard -t image/png -o` (stdout is PNG bytes)
  - Detection order: try Pillow first, then `wl-paste`, then `xclip`.
  - If none succeed, raise `ClipboardImageUnavailableError` with a message:

    ```text
    Clipboard image paste is not available. On Linux, install wl-paste (Wayland) or xclip (X11).
    ```

- Subprocess calls:
  - `shell=False`
  - `timeout=5`
  - Capture stdout as bytes.
  - Handle `FileNotFoundError` for missing executables.
  - Validate returned bytes start with PNG signature before accepting.
- Normalize image data to PNG bytes using Pillow.
- Set MIME type to `image/png`.
- Generate file names like:

  ```text
  clipboard-YYYYMMDD-HHMMSS.png
  ```

- Enforce `IMAGE_MAX_BYTES` from `src/aye/model/attachments.py`.
- Return attachment dict:

  ```python
  {
      "file_name": "clipboard-YYYYMMDD-HHMMSS.png",
      "mime_type": "image/png",
      "data_b64": "...",
      "bytes_size": 12345,
  }
  ```

- Keep image data in memory only.
- Do not write clipboard images to `.aye/` or any temp file for v1.
- Do not include raw bytes or base64 in exception messages.

## Testing

In `tests/test_clipboard_images.py`, use mocks only.

Test cases:

- Attachment dict includes required keys.
- MIME type is `image/png`.
- `data_b64` decodes to expected PNG bytes.
- `IMAGE_MAX_BYTES` is enforced.
- Pillow `grabclipboard()` success path.
- Pillow `grabclipboard()` returns `None` (no image) — falls through to subprocess on Linux or raises on macOS/Windows.
- `wl-paste` subprocess success path (mocked).
- `xclip` subprocess success path (mocked).
- Missing subprocess executables raise `ClipboardImageUnavailableError`.
- Subprocess timeout produces clear error.
- Clipboard without image raises `ClipboardImageNotFoundError`.
- Generated filename is safe and predictable enough to test with time mocking.
- Error strings do not contain base64 or raw image bytes.

---

# Phase 2 — Pending Clipboard State and `paste-image` Command

## Objective

Add a stable explicit command that stages a clipboard image for the next normal AI prompt. Multiple calls accumulate.

## Files to create

- `src/aye/controller/clipboard_attachments.py`
- `tests/test_clipboard_attachments.py`
- `tests/test_command_handlers_clipboard.py`

## Files to modify

- `src/aye/controller/command_handlers.py`
- `src/aye/controller/repl.py`
- `src/aye/presenter/repl_ui.py` only if help is updated in this phase

## `src/aye/controller/clipboard_attachments.py`

Add helpers for pending state:

```python
get_pending_clipboard_attachments(conf)
add_pending_clipboard_attachment(conf, attachment)
clear_pending_clipboard_attachments(conf)
pending_clipboard_attachment_count(conf)
```

Also add marker helpers for the later key binding phase:

```python
make_clipboard_marker(conf)
strip_clipboard_markers(prompt)
```

The implementation should store staged attachments on:

```python
conf._pending_clipboard_attachments
```

`add_pending_clipboard_attachment(...)` should append to the existing list. Multiple calls accumulate images.

## `src/aye/controller/command_handlers.py`

Add:

```python
handle_paste_image_command(conf: Any) -> None
```

Behavior:

1. Call `load_clipboard_image_attachment()`.
2. Stage the returned dict with `add_pending_clipboard_attachment(...)`. This appends (accumulates).
3. Print safe metadata through `print_attachment_summary(...)`.
4. Print a short confirmation:

   ```text
   Attached clipboard image to the next AI prompt.
   ```

   If multiple images are staged, consider:

   ```text
   Attached clipboard image to the next AI prompt (2 total staged).
   ```

Error messages:

- No image:

  ```text
  No image found in clipboard. Copy an image to the clipboard and try `paste-image` again.
  ```

- Clipboard unsupported:

  ```text
  Clipboard image paste is not available on this system.
  ```

  On Linux, additionally suggest:

  ```text
  On Linux, install wl-paste (Wayland) or xclip (X11).
  ```

- Oversized image:

  ```text
  Clipboard image exceeds the image size limit. Copy a smaller image to the clipboard and try again.
  ```

Do not mention file-based image fallback.

## `src/aye/controller/repl.py`

Update command handler imports to include:

```python
handle_paste_image_command
```

Update `BUILTIN_COMMANDS` inside `chat_repl(...)` to include:

```python
"paste-image"
```

Add command dispatch:

```python
elif lowered_first == "paste-image":
    telemetry.record_command("paste-image", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)
    handle_paste_image_command(conf)
```

Important constraints:

- Do not invoke the LLM in this branch.
- Do not emit `LLM clipboard` telemetry in this branch.
- Do not clear pending clipboard attachments in this branch.

## Testing

`tests/test_clipboard_attachments.py`:

- Missing pending attribute returns empty list.
- Adding first attachment initializes pending list.
- Adding multiple attachments preserves order (accumulation).
- Third call adds a third image.
- Clearing removes all pending attachments.
- Count returns correct number.
- Marker stripping removes only known clipboard markers.

`tests/test_command_handlers_clipboard.py`:

- `handle_paste_image_command(...)` stages attachment on success.
- Second call to `handle_paste_image_command(...)` stages a second attachment (accumulates).
- Success output includes safe metadata only.
- Success output does not include `data_b64`.
- No-image clipboard stages nothing.
- Unsupported clipboard stages nothing.
- Oversized image stages nothing.
- Command does not invoke LLM.
- Command does not record image-used-with-LLM telemetry.

---

# Phase 3 — Attach Pending Clipboard Images to LLM Prompts and Telemetry

## Objective

Send staged clipboard images with the next normal AI prompt and record privacy-safe telemetry using the four-way kind selection.

## Files to modify

- `src/aye/controller/repl.py`
- `src/aye/model/telemetry.py`

## Files to inspect but usually not modify

- `src/aye/controller/llm_invoker.py`
- `src/aye/model/api.py`
- `src/aye/plugins/local_model.py`
- `src/aye/plugins/offline_llm.py`

## `src/aye/model/telemetry.py`

Keep the current count-based telemetry payload shape.

Update `record_llm_prompt(...)` allowed kinds:

```python
{"LLM", "LLM <with>", "LLM @", "LLM @ attachment", "LLM <blog>", "LLM clipboard"}
```

No new parameters are needed. The caller in `repl.py` selects the kind.

## `src/aye/controller/repl.py`

Import from `src/aye/controller/clipboard_attachments.py`:

```python
clear_pending_clipboard_attachments
get_pending_clipboard_attachments
strip_clipboard_markers
```

In the normal AI prompt path, after existing `@` parsing has produced local `attachments` and `used_at`:

1. Capture `at_attachments` from the result of `@` parsing (before merge).
2. Load pending clipboard attachments:

   ```python
   clipboard_attachments = get_pending_clipboard_attachments(conf)
   ```

3. Merge:

   ```python
   attachments = clipboard_attachments + at_attachments
   ```

4. Run existing capability gating:

   ```python
   if attachments and not _model_supports_images(conf.selected_model):
       ...
       continue
   ```

5. Determine telemetry kind before gating blocks the prompt (kind is computed from original variables, not the merged list):

   ```python
   if clipboard_attachments:
       kind = "LLM clipboard"
   elif at_attachments:
       kind = "LLM @ attachment"
   elif used_at:
       kind = "LLM @"
   else:
       kind = "LLM"
   ```

   Record the kind after gating passes, before invocation:

   ```python
   telemetry.record_llm_prompt(kind)
   ```

   This replaces the existing four-branch telemetry block.

6. Strip clipboard markers before invocation:

   ```python
   cleaned_prompt = strip_clipboard_markers(cleaned_prompt)
   ```

7. Call `invoke_llm(...)`.

8. Clear pending clipboard attachments only **after** `invoke_llm(...)` returns successfully:

   ```python
   llm_response = invoke_llm(...)
   clear_pending_clipboard_attachments(conf)
   ```

   If `invoke_llm(...)` raises an exception, the outer `except` in the REPL loop catches it and continues. Pending clipboard attachments survive for retry.

Clearing behavior summary:

- Do not clear pending attachments for unsupported-model rejection.
- Do not clear pending attachments for empty prompts.
- Do not clear pending attachments if `invoke_llm(...)` raises.
- Clear only after successful return from `invoke_llm(...)`.

### `with` and `blog` limitation

The `with` and `blog` paths in `src/aye/controller/command_handlers.py` call `invoke_llm(...)` directly without merging clipboard attachments. Pending clipboard images are silently ignored for those prompts and remain staged for the next normal AI prompt.

This is a known v1 limitation. Do not modify `handle_with_command(...)` or `handle_blog_command(...)` in this phase.

## Testing

Create or update:

- `tests/test_repl_clipboard_prompt_flow.py`
- `tests/test_telemetry_image_indicator.py`

Telemetry tests:

- `record_llm_prompt("LLM clipboard")` increments `LLM clipboard`.
- `record_llm_prompt("LLM @ attachment")` increments `LLM @ attachment`.
- `record_llm_prompt("LLM @")` increments `LLM @`.
- `record_llm_prompt("LLM")` increments `LLM`.
- Unknown kinds default to `LLM`.
- Telemetry payload contains no filenames, MIME types, sizes, counts, base64, raw bytes, or markers.

Prompt-flow tests:

- Pending clipboard image is passed to `invoke_llm(..., attachments=[...])`.
- Multiple pending images are all passed.
- Existing `@` non-clipboard attachments remain in the final list after clipboard attachments.
- Unsupported selected model blocks send and keeps pending clipboard attachments.
- Successful `invoke_llm(...)` return clears pending clipboard attachments.
- Failed `invoke_llm(...)` (raises exception) keeps pending clipboard attachments.
- Empty prompt does not invoke LLM and does not send pending attachments.
- Text-only prompt with no pending attachments remains unchanged.
- Telemetry kind is `LLM clipboard` when only clipboard images are present.
- Telemetry kind is `LLM @ attachment` when only `@` image attachments are present.
- Telemetry kind is `LLM @` when only `@` source files are present.
- Telemetry kind is `LLM` for text-only prompts.
- Telemetry kind is `LLM clipboard` when both clipboard and `@` images are present (clipboard wins).

---

# Phase 4 — UX, Privacy Hardening, Help, and UAT

## Objective

Make clipboard image input discoverable and verify privacy-sensitive output paths.

## Files to modify

- `src/aye/presenter/repl_ui.py`
- `src/aye/controller/repl.py`
- `src/aye/controller/command_handlers.py` only if adding optional clearing command
- `src/aye/model/telemetry.py` only if telemetry tests reveal gaps

## Files to inspect but usually not modify

- `src/aye/model/api.py`
- `src/aye/controller/llm_invoker.py`

## Help text

In `src/aye/presenter/repl_ui.py`, update `print_help_message()` with:

```text
paste-image       Attach image from clipboard to next AI prompt (accumulates)
```

Do not add file-based image fallback text to clipboard help or clipboard errors.

Keep existing `@filename` help unchanged unless a separate issue changes it.

## Telemetry consent text

In `src/aye/controller/repl.py`, update `_prompt_for_telemetry_consent_if_needed()`.

Add examples:

```text
- LLM clipboard
- LLM @ attachment
```

Strengthen privacy text:

```text
We never collect command arguments, prompt text, filenames, file contents, image contents, image names, MIME types, or image sizes in telemetry.
```

## Optional stale attachment clearing

If product wants an explicit clear path, add:

```text
clear-attachments
```

Files to modify if included:

- `src/aye/controller/repl.py`
- `src/aye/controller/command_handlers.py`
- `src/aye/presenter/repl_ui.py`

Suggested behavior:

```text
Cleared pending clipboard image attachments.
```

This is optional. The required behavior is that successful send clears pending attachments, unsupported-model rejection keeps them, and failed invocation keeps them.

## Privacy audit

Verify:

- `src/aye/model/api.py::_redact_payload_for_debug(...)` redacts `data_b64`.
- `src/aye/controller/repl.py` does not print attachment dicts.
- `src/aye/model/telemetry.py` stores only coarse event names.
- `src/aye/model/clipboard_images.py` exceptions do not include image bytes or base64.
- `src/aye/presenter/repl_ui.py::print_attachment_summary(...)` prints only safe metadata.

Safe local display metadata:

- synthetic filename
- MIME type
- byte size

Telemetry may include only coarse kind names.

## Testing

Create or update:

- `tests/test_clipboard_privacy.py`
- `tests/test_repl_ui_help_clipboard.py` if help output is tested separately
- Existing telemetry/debug/feedback tests if present

Test cases:

- Help output includes `paste-image`.
- Help output does not present file-based image input as clipboard fallback.
- Telemetry consent text mentions clipboard and attachment telemetry without implying image metadata collection.
- Debug payload redaction never prints real `data_b64`.
- Clipboard errors do not include raw bytes or base64.
- Telemetry payload contains only coarse kind names.
- Feedback telemetry uses the same privacy-safe payload from `telemetry.build_payload(...)`.
- Optional `clear-attachments` clears pending images if implemented.

## Manual UAT

- Text-only prompt still works.
- `paste-image` stages a clipboard image.
- Second `paste-image` stages a second image (accumulates).
- `paste-image`, followed by a normal prompt, sends all staged clipboard images.
- Clipboard image plus unsupported model is rejected clearly.
- Unsupported-model rejection does not silently drop pending image.
- Successful prompt clears pending image.
- Failed LLM invocation keeps pending image for retry.
- Oversized clipboard image is rejected.
- No-image clipboard gives clear error.
- Clipboard-unavailable environment gives clear error (with Linux tool suggestion on Linux).
- Debug output does not include base64.
- Telemetry/debug inspection shows `LLM clipboard` for clipboard-image prompts.
- `@` image-only prompt shows `LLM @ attachment`.
- `@` source-only prompt shows `LLM @`.
- Text-only prompt shows `LLM`.
- Shell commands still work normally.
- `with` and `blog` paths do not consume staged clipboard images (known v1 limitation).

---

# Phase 5 — Optional Experimental `Ctrl+V` Key Binding

## Objective

Add optional `Ctrl+V` support that stages a clipboard image into the current prompt buffer.

This should remain disabled by default until UAT confirms it does not interfere with normal terminal paste behavior.

## Files to modify

- `src/aye/controller/repl.py`
- `src/aye/controller/clipboard_attachments.py`
- `src/aye/presenter/repl_ui.py`

## Files to inspect / optionally modify

- `src/aye/model/auth.py`
- `src/aye/model/config.py`

No `src/aye/model/auth.py` change is needed if using existing `get_user_config(...)` directly.

## Configuration

Suggested config key:

```text
clipboard_image_paste=on|off
```

With existing environment override behavior, this should also allow:

```text
AYE_CLIPBOARD_IMAGE_PASTE=on
```

Recommended beta default:

```text
off
```

## `src/aye/controller/repl.py`

Change:

```python
def create_key_bindings() -> KeyBindings:
```

to:

```python
def create_key_bindings(conf: Any | None = None) -> KeyBindings:
```

Change:

```python
def create_prompt_session(completer: Any, completion_style: str = "readline") -> PromptSession:
```

to:

```python
def create_prompt_session(
    completer: Any,
    completion_style: str = "readline",
    conf: Any | None = None,
) -> PromptSession:
```

Update call sites:

```python
session = create_prompt_session(completer, completion_style, conf)
```

and when completion style changes:

```python
session = create_prompt_session(completer, new_style, conf)
```

Register `c-v` only when enabled:

```python
clipboard_paste_enabled = str(
    get_user_config("clipboard_image_paste", "off")
).lower() in {"on", "true", "1", "yes"}
```

On `Ctrl+V`:

1. Call `load_clipboard_image_attachment()`.
2. Stage attachment with `add_pending_clipboard_attachment(...)`. This accumulates.
3. Insert safe marker into prompt buffer:

   ```text
   [clipboard:image-001]
   ```

4. Do not submit the prompt.
5. Do not record telemetry yet.
6. Do not insert base64 into the prompt buffer.

Phase 3 should already strip markers before LLM invocation:

```python
cleaned_prompt = strip_clipboard_markers(cleaned_prompt)
```

## UX notes

- `Cmd+V` on macOS is usually handled by the terminal emulator and may not reach prompt_toolkit as a distinct key event.
- `Ctrl+V` can have existing terminal semantics.
- `paste-image` remains the reliable explicit clipboard command.

## Help text

Only if Phase 5 is implemented, add:

```text
Ctrl+V            Attach image from clipboard when enabled and supported
```

Optional macOS note:

```text
On macOS, Cmd+V is usually handled by the terminal. If image paste does not attach, use paste-image.
```

Do not document file-based image input as clipboard fallback.

## Testing

Create or update:

- `tests/test_repl_key_bindings.py`
- `tests/test_repl_clipboard_prompt_flow.py`

Test cases:

- `Ctrl+V` binding is not registered when config is off.
- `Ctrl+V` binding is registered when config is on.
- `Ctrl+V` with an image stages attachment.
- Second `Ctrl+V` stages another image (accumulates).
- `Ctrl+V` inserts only a safe marker.
- Marker is stripped before LLM invocation.
- `Ctrl+V` with no image does not stage attachment.
- `Ctrl+V` staging does not record telemetry before prompt submission.
- Submitted prompt with `Ctrl+V` staged image records `LLM clipboard`.
- Handler does not print, store, or insert base64 data.
- Existing Enter completion behavior still works.

---

## Risks

### Terminal paste limitations

`Cmd+V` on macOS is usually handled by the terminal emulator, not delivered as a CLI key event.

Mitigation:

- Ship `paste-image` first.
- Keep `Ctrl+V` optional and disabled by default.

### `Ctrl+V` may conflict with terminal behavior

In some terminals, `Ctrl+V` means quoted insert or has other behavior.

Mitigation:

- Config-gate the binding.
- Preserve `paste-image` as the stable path.

### Clipboard APIs vary by platform

Pillow clipboard behavior is platform-dependent. Linux support requires external tools.

Mitigation:

- Pillow is a mandatory dependency (macOS/Windows coverage).
- Linux subprocess fallbacks (`wl-paste`, `xclip`) are included from Phase 1.
- Clear error messages when no clipboard tool is available.

### Privacy leakage

Clipboard images may be sensitive.

Mitigation:

- In-memory staging only.
- No base64 in logs, debug output, telemetry, prompt markers, or errors.
- Telemetry records only coarse kind names.

### Stale staged attachments

A user may stage an image and forget before sending a later prompt.

Mitigation:

- Print clear confirmation after `paste-image` (with count when >1).
- Insert visible marker for `Ctrl+V`.
- Clear on successful send.
- Keep on failed send for retry.
- Optionally add `clear-attachments`.

### `with`/`blog` silently ignore staged images

See the Known v1 Limitations section above.

---

## Rollout Strategy

1. Implement command-only clipboard image staging with Linux fallbacks.
2. Integrate staged attachments into normal LLM prompt flow.
3. Add four-way privacy-safe telemetry indicator.
4. Add help text and privacy tests.
5. Manually UAT command flow.
6. Add optional `Ctrl+V` behind config.
7. UAT `Ctrl+V` across common terminals before enabling by default.

Recommended default for first release:

```text
paste-image: enabled
Ctrl+V: disabled unless clipboard_image_paste=on
```
