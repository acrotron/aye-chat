# Ctrl+V Clipboard Image Paste — UAT Plan

## Goal

Validate that Aye Chat users can attach images from the system clipboard using the `paste-image` command and the optional `Ctrl+V` key binding, send them to supported multimodal models alongside text and source code, and receive useful responses — without regressing existing text-only, source-only, `@` image, shell, restore, `with`, or `blog` workflows.

This UAT plan covers CLI client behavior after all five clipboard image implementation phases are complete.

## Scope

### In scope

- `paste-image` command: staging, accumulation, error handling.
- `clear-attachments` command.
- Staged clipboard images merging into normal LLM prompt flow.
- Pending attachment lifecycle: clear on success, keep on failure, keep on model rejection.
- Model capability gating for clipboard images.
- Four-way telemetry kind selection (`LLM`, `LLM @`, `LLM @ attachment`, `LLM clipboard`).
- Privacy: no image bytes in debug output, telemetry, feedback, or error messages.
- Optional `Ctrl+V` key binding behind `clipboard_image_paste=on` config flag.
- `Ctrl+V` marker insertion and stripping.
- Interaction between clipboard images and `@` image references.
- Known v1 limitation: `with` and `blog` do not consume staged clipboard images.
- Cross-platform clipboard behavior (macOS, Windows, Linux).
- Help text discoverability.

### Out of scope

- Image generation.
- Image URLs.
- `@filename` image attachment (covered by `image_uat.md`).
- Client-side image resizing or format conversion.
- OCR fallback for non-multimodal models.

## Preconditions

1. All five clipboard image phases are implemented.
2. `Pillow>=9.0` is installed.
3. On Linux: at least one of `wl-paste` (Wayland) or `xclip` (X11) is installed for clipboard tests, **or** one UAT scenario explicitly tests the unavailable-tool path.
4. The backend accepts `attachments` and supports at least one verified multimodal model.
5. The CLI is authenticated:

   ```bash
   aye auth status
   ```

6. At least one model supports images (`supports_images: true`).
7. At least one model does **not** support images.
8. An image is available in the system clipboard (e.g. screenshot or copy from an image viewer).
9. For `Ctrl+V` scenarios, config is set:

   ```bash
   # In ~/.ayecfg or via environment
   clipboard_image_paste=on
   ```

## Test Project Setup

Create or reuse a temporary project:

```bash
mkdir aye-clipboard-uat
cd aye-clipboard-uat
git init
mkdir -p src
```

Create a source file:

```bash
cat > src/main.py <<'EOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"
EOF
```

Create a small image file for `@` reference comparison tests:

```bash
cp /path/to/small-screenshot.png screenshot.png
```

Start Aye Chat:

```bash
aye chat --root .
```

---

## Acceptance Criteria Summary

The feature is accepted when all of the following are true:

- `paste-image` reads an image from the clipboard and stages it for the next prompt.
- Multiple `paste-image` calls accumulate images.
- The next normal AI prompt sends all staged clipboard images to a supported multimodal model.
- Pending clipboard images are cleared after a successful LLM response.
- Pending clipboard images survive failed LLM invocations and unsupported-model rejections.
- `clear-attachments` removes all pending clipboard images.
- `Ctrl+V` (when enabled) stages a clipboard image and inserts a visible marker.
- Markers are stripped before LLM invocation.
- Telemetry records `LLM clipboard` for clipboard-image prompts without any image metadata.
- No image bytes, base64 data, filenames, MIME types, or sizes appear in debug output, telemetry, feedback, or error messages.
- `with` and `blog` commands do not consume staged clipboard images (known v1 limitation).
- Existing text-only, source-only, `@` image, shell, restore, and `with` workflows are unaffected.
- Help text includes `paste-image`, `clear-attachments`, and `Ctrl+V`.

---

# UAT Scenarios

## CUAT-001 — Baseline text-only prompt still works

**Purpose:** Confirm normal chat behavior is unaffected before testing clipboard features.

**Steps:**

1. Start the CLI:

   ```bash
   aye chat --root .
   ```

2. Send a text-only prompt:

   ```text
   explain this project briefly
   ```

**Expected result:**

- Aye Chat responds normally.
- No attachment summary is printed.
- No clipboard-related output appears.

---

## CUAT-002 — `paste-image` stages a clipboard image

**Purpose:** Core happy path for the explicit clipboard command.

**Precondition:** Copy an image to the system clipboard (e.g. take a screenshot).

**Steps:**

1. Select a multimodal model.
2. Run:

   ```text
   paste-image
   ```

**Expected result:**

- CLI prints an attachment summary similar to:

  ```text
  📎 attached: clipboard-20250615-143022.png (image/png, <size>)
  Attached clipboard image to the next AI prompt.
  ```

- The filename is synthetic (`clipboard-YYYYMMDD-HHMMSS.png`).
- MIME type is `image/png`.
- No LLM call is made.
- No `LLM clipboard` telemetry is recorded yet.

---

## CUAT-003 — Multiple `paste-image` calls accumulate

**Purpose:** Verify accumulation behavior.

**Precondition:** An image is in the clipboard.

**Steps:**

1. Run `paste-image`.
2. Run `paste-image` again.

**Expected result:**

- Each call prints its own attachment summary.
- The second call shows a count:

  ```text
  Attached clipboard image to the next AI prompt (2 total staged).
  ```

- Both images are staged, not just the latest one.

---

## CUAT-004 — Staged clipboard image sent with next prompt

**Purpose:** Verify clipboard images are actually sent to the LLM.

**Precondition:** An image is in the clipboard. A multimodal model is selected.

**Steps:**

1. Run `paste-image`.
2. Type a normal prompt:

   ```text
   describe this screenshot
   ```

**Expected result:**

- The model returns a coherent description of the clipboard image.
- Pending clipboard images are cleared after successful response.
- Telemetry records `LLM clipboard` (verify via debug inspection if possible).

---

## CUAT-005 — Multiple staged images sent together

**Purpose:** Verify all accumulated images are sent, not just the last one.

**Precondition:** An image is in the clipboard. A multimodal model is selected.

**Steps:**

1. Run `paste-image`.
2. Copy a different image to the clipboard.
3. Run `paste-image`.
4. Type:

   ```text
   compare these two images
   ```

**Expected result:**

- Both images are sent.
- The model response references or compares two images.
- Pending clipboard images are cleared after success.

---

## CUAT-006 — Pending images cleared after successful send

**Purpose:** Verify cleared-on-success semantics.

**Steps:**

1. Run `paste-image`.
2. Submit a normal prompt (model responds successfully).
3. Submit another normal prompt without running `paste-image`:

   ```text
   explain this project
   ```

**Expected result:**

- The second prompt is text-only.
- No clipboard image is sent with the second prompt.
- No attachment-related output appears for the second prompt.
- Telemetry for the second prompt is `LLM` (not `LLM clipboard`).

---

## CUAT-007 — Unsupported model rejects clipboard image prompt

**Purpose:** Verify model capability gating for clipboard images.

**Steps:**

1. Select a model that does **not** support images.
2. Run `paste-image`.
3. Type a normal prompt:

   ```text
   describe the image
   ```

**Expected result:**

- CLI prints a clear error:

  ```text
  Error: The selected model '<model-name>' does not support image input. Choose a multimodal model or remove the image reference.
  ```

- No API request is sent.
- The staged clipboard image is **not** cleared (kept for retry).

---

## CUAT-008 — Switch model and retry after rejection

**Purpose:** Verify pending images survive model rejection and can be retried.

**Steps:**

1. Continue from CUAT-007 (pending image still staged).
2. Switch to a multimodal model:

   ```text
   model
   ```

3. Submit a prompt:

   ```text
   describe the image
   ```

**Expected result:**

- The previously staged clipboard image is sent.
- The model returns a coherent response.
- Pending images are cleared.

---

## CUAT-009 — Pending images survive failed LLM invocation

**Purpose:** Verify keep-on-failure semantics.

**Steps:**

1. Run `paste-image`.
2. Trigger a network failure or API error (e.g. disconnect network, or use a deliberately broken endpoint).
3. After the error, reconnect and submit the prompt again.

**Expected result:**

- The first attempt fails with an error.
- The pending clipboard image is **not** cleared.
- The retry attempt sends the clipboard image successfully.

---

## CUAT-010 — `clear-attachments` removes all pending images

**Purpose:** Verify manual clearing.

**Steps:**

1. Run `paste-image`.
2. Run `paste-image` again.
3. Run:

   ```text
   clear-attachments
   ```

4. Submit a normal prompt.

**Expected result:**

- `clear-attachments` confirms:

  ```text
  Cleared 2 pending clipboard image attachment(s).
  ```

- The subsequent prompt is text-only.
- No clipboard image is sent.

---

## CUAT-011 — `clear-attachments` with nothing staged

**Purpose:** Verify harmless no-op.

**Steps:**

```text
clear-attachments
```

**Expected result:**

- CLI prints:

  ```text
  No pending clipboard image attachments to clear.
  ```

- No error or crash.

---

## CUAT-012 — No image in clipboard

**Purpose:** Verify clear error when clipboard has no image.

**Precondition:** Clear the clipboard or copy text (not an image).

**Steps:**

```text
paste-image
```

**Expected result:**

- CLI prints:

  ```text
  No image found in clipboard. Copy an image to the clipboard and try `paste-image` again.
  ```

- Nothing is staged.
- No crash.

---

## CUAT-013 — Clipboard unavailable (headless/SSH)

**Purpose:** Verify clear error when clipboard access is not supported.

**Precondition:** Run in an environment without clipboard access (e.g. SSH session, headless server).

**Steps:**

```text
paste-image
```

**Expected result:**

- CLI prints a clear message such as:

  ```text
  Clipboard image paste is not available on this system.
  ```

  On Linux without tools:

  ```text
  Clipboard image paste is not available. On Linux, install wl-paste (Wayland) or xclip (X11).
  ```

- Nothing is staged.
- No crash.

---

## CUAT-014 — Oversized clipboard image

**Purpose:** Verify size limit enforcement.

**Precondition:** Copy an image larger than `IMAGE_MAX_BYTES` (default 2 MB) to the clipboard.

**Steps:**

```text
paste-image
```

**Expected result:**

- CLI prints a clear size error:

  ```text
  Clipboard image is <N> bytes, which exceeds the limit of <MAX> bytes. Copy a smaller image to the clipboard and try again.
  ```

- Nothing is staged.
- The error message does **not** include base64 or raw bytes.

---

## CUAT-015 — Clipboard image combined with `@` source file

**Purpose:** Verify mixed clipboard + explicit source context.

**Precondition:** An image in clipboard. `screenshot.png` exists in project.

**Steps:**

1. Run `paste-image`.
2. Type:

   ```text
   review @src/main.py against the screenshot I just pasted
   ```

**Expected result:**

- `src/main.py` is included as explicit source context.
- The clipboard image is sent as an attachment.
- Automatic source search is skipped (because `@` source file was referenced).
- The model response references both the source file and the image.
- Telemetry kind is `LLM clipboard` (clipboard takes priority).

---

## CUAT-016 — Clipboard image combined with `@` image reference

**Purpose:** Verify clipboard + @ image attachments are both sent.

**Precondition:** An image in clipboard. `screenshot.png` exists in project.

**Steps:**

1. Run `paste-image`.
2. Type:

   ```text
   compare this clipboard image with @screenshot.png
   ```

**Expected result:**

- Both the clipboard image and the `@` referenced image are sent.
- Attachment summaries printed for both.
- The model response references both images.
- Telemetry kind is `LLM clipboard` (clipboard takes priority over `@` attachment).

---

## CUAT-017 — `with` command does NOT consume staged clipboard image

**Purpose:** Verify known v1 limitation.

**Steps:**

1. Run `paste-image`.
2. Run:

   ```text
   with src/main.py: explain this function
   ```

3. Then submit a normal prompt:

   ```text
   describe the image I previously pasted
   ```

**Expected result:**

- The `with` command executes normally (source file only, no clipboard image sent).
- The staged clipboard image is still pending after the `with` command.
- The subsequent normal prompt sends the clipboard image.
- The model responds about the image in step 3.

---

## CUAT-018 — `blog` command does NOT consume staged clipboard image

**Purpose:** Verify known v1 limitation for `blog` path.

**Steps:**

1. Run `paste-image`.
2. Run:

   ```text
   blog write about what we discussed
   ```

3. Then submit a normal prompt:

   ```text
   describe the image
   ```

**Expected result:**

- The `blog` command executes without sending the clipboard image.
- The staged clipboard image persists.
- The normal prompt in step 3 sends it.

---

## CUAT-019 — Telemetry kind: `LLM clipboard`

**Purpose:** Verify telemetry records the correct kind for clipboard-image prompts.

**Steps:**

1. Enable debug mode:

   ```text
   debug on
   ```

2. Run `paste-image`.
3. Submit a normal prompt.
4. Inspect debug/telemetry output.

**Expected result:**

- Telemetry records `LLM clipboard`.
- Telemetry does **not** include image filename, MIME type, byte size, image count, base64, or raw bytes.

---

## CUAT-020 — Telemetry kind: `LLM @ attachment` for `@` images

**Purpose:** Verify telemetry distinguishes `@` image from clipboard.

**Steps:**

1. Without running `paste-image`, type:

   ```text
   describe @screenshot.png
   ```

2. Inspect telemetry.

**Expected result:**

- Telemetry records `LLM @ attachment`.

---

## CUAT-021 — Telemetry kind: `LLM @` for `@` source only

**Purpose:** Verify source-only `@` still records `LLM @`.

**Steps:**

```text
explain @src/main.py
```

**Expected result:**

- Telemetry records `LLM @`.

---

## CUAT-022 — Telemetry kind: `LLM` for text-only

**Purpose:** Verify text-only prompts record plain `LLM`.

**Steps:**

```text
explain this project
```

**Expected result:**

- Telemetry records `LLM`.

---

## CUAT-023 — Debug output does not expose image bytes

**Purpose:** Verify privacy in debug mode.

**Steps:**

1. Enable debug:

   ```text
   debug on
   ```

2. Run `paste-image`.
3. Submit a prompt.
4. Scan terminal output for long base64-like strings.

**Expected result:**

- Debug output does not include base64 image data or raw bytes.
- Safe metadata (filename, MIME type, byte size) may appear.
- `data_b64` is redacted in any debug payload inspection.

---

## CUAT-024 — Error messages do not expose image bytes

**Purpose:** Verify exception messages are privacy-safe.

**Steps:**

1. Trigger clipboard errors (no image, oversized, unavailable).
2. Inspect all error output.

**Expected result:**

- No error message includes base64 data, raw image bytes, or clipboard binary content.
- Error messages are human-readable and actionable.

---

## CUAT-025 — Exit feedback does not include image bytes

**Purpose:** Verify feedback path is privacy-safe.

**Steps:**

1. Run `paste-image` and submit a prompt.
2. Exit:

   ```text
   exit
   ```

3. If prompted for feedback, enter a message.

**Expected result:**

- Feedback submission does not include image bytes or base64.
- Telemetry payload contains only coarse kind names.
- CLI exits cleanly.

---

## CUAT-026 — Telemetry consent text mentions clipboard and attachment

**Purpose:** Verify updated consent text.

**Steps:**

1. Clear telemetry consent state (delete `telemetry_opt_in` from `~/.ayecfg`).
2. Start a new session.
3. Read the telemetry consent prompt.

**Expected result:**

- The consent text lists `LLM clipboard` and `LLM @ attachment` as examples.
- The privacy notice mentions that image contents, image names, MIME types, and image sizes are never collected.

---

## CUAT-027 — Help text includes clipboard commands

**Purpose:** Verify discoverability.

**Steps:**

```text
help
```

**Expected result:**

- Help output includes:
  - `paste-image` — with description about attaching clipboard image.
  - `clear-attachments` — with description about clearing staged images.
  - `Ctrl+V` — with note about config flag and macOS caveat.
- No mention of `@filename` as a clipboard fallback.

---

## CUAT-028 — `Ctrl+V` disabled by default

**Purpose:** Verify `Ctrl+V` does not intercept keystrokes when disabled.

**Precondition:** Config `clipboard_image_paste` is `off` (default) or absent.

**Steps:**

1. Start a session.
2. Copy text to clipboard.
3. Press `Ctrl+V` in the prompt.

**Expected result:**

- `Ctrl+V` behaves as the terminal's default paste (if any).
- No clipboard image is staged.
- No marker is inserted.

---

## CUAT-029 — `Ctrl+V` stages image when enabled

**Purpose:** Verify `Ctrl+V` key binding works when enabled.

**Precondition:** Set `clipboard_image_paste=on` in config. Copy an image to clipboard.

**Steps:**

1. Start a new session (so key bindings are registered with the flag).
2. Begin typing a prompt.
3. Press `Ctrl+V`.

**Expected result:**

- A marker is inserted into the prompt buffer:

  ```text
  [clipboard:image-001]
  ```

- An image is staged as a pending clipboard attachment.
- The marker is visible in the prompt text.

---

## CUAT-030 — Multiple `Ctrl+V` presses accumulate

**Purpose:** Verify `Ctrl+V` accumulation.

**Precondition:** `clipboard_image_paste=on`. An image in clipboard.

**Steps:**

1. Press `Ctrl+V` twice while typing a prompt.

**Expected result:**

- Two markers appear in the prompt buffer:

  ```text
  [clipboard:image-001] [clipboard:image-002]
  ```

- Two images are staged.

---

## CUAT-031 — `Ctrl+V` markers stripped before LLM invocation

**Purpose:** Verify markers do not pollute the LLM prompt.

**Precondition:** `clipboard_image_paste=on`. An image in clipboard.

**Steps:**

1. Press `Ctrl+V` and type:

   ```text
   [clipboard:image-001] describe this screenshot
   ```

2. Submit the prompt.

**Expected result:**

- The model receives the prompt **without** the `[clipboard:image-NNN]` markers.
- The model response is coherent (not confused by marker text).
- The clipboard image is sent as an attachment.

---

## CUAT-032 — `Ctrl+V` with no image is silent

**Purpose:** Verify `Ctrl+V` fails silently when clipboard has no image.

**Precondition:** `clipboard_image_paste=on`. Clear clipboard or copy text.

**Steps:**

1. Press `Ctrl+V` while typing.

**Expected result:**

- Nothing happens (no marker, no staging, no error message).
- The prompt buffer is unchanged.
- For diagnostic errors, the user should use `paste-image` instead.

---

## CUAT-033 — `Ctrl+V` image sent with `LLM clipboard` telemetry

**Purpose:** Verify telemetry for `Ctrl+V` path.

**Precondition:** `clipboard_image_paste=on`. An image in clipboard.

**Steps:**

1. Press `Ctrl+V`.
2. Submit the prompt.

**Expected result:**

- Telemetry records `LLM clipboard`.
- No telemetry recorded at the moment of `Ctrl+V` press (only on prompt submission).

---

## CUAT-034 — Shell commands still work after clipboard operations

**Purpose:** Confirm shell integration is unaffected.

**Steps:**

1. Run `paste-image`.
2. Run:

   ```text
   ls -la
   ```

3. Run:

   ```text
   git status
   ```

**Expected result:**

- Shell commands execute normally.
- They are not treated as LLM prompts.
- Pending clipboard images are not consumed by shell commands.
- Pending images remain staged for the next AI prompt.

---

## CUAT-035 — Restore/undo unaffected by clipboard input

**Purpose:** Confirm clipboard images are not snapshotted as outputs.

**Steps:**

1. Run `paste-image`.
2. Ask for a code edit:

   ```text
   update @src/main.py based on this screenshot
   ```

3. If the assistant edits `src/main.py`, run:

   ```text
   diff src/main.py
   ```

4. Run:

   ```text
   undo
   ```

**Expected result:**

- Only LLM-produced text file updates are snapshotted.
- Clipboard images are not stored in `.aye/` snapshots.
- `diff` and `undo` work normally for edited source files.

---

## CUAT-036 — New session clears pending clipboard images

**Purpose:** Verify session reset behavior.

**Steps:**

1. Run `paste-image`.
2. Run:

   ```text
   new
   ```

3. Submit a text-only prompt.

**Expected result:**

- `new` starts a fresh session.
- Pending clipboard images from the previous session context **may** persist or be cleared (implementation-defined). Document actual behavior.
- If images persist, this is acceptable as a v1 behavior — `clear-attachments` is available.

---

## CUAT-037 — `paste-image` after `Ctrl+V` accumulates

**Purpose:** Verify interop between the two staging methods.

**Precondition:** `clipboard_image_paste=on`. An image in clipboard.

**Steps:**

1. Press `Ctrl+V` (stages one image, inserts marker).
2. Run `paste-image` (stages another image).
3. Submit the prompt.

**Expected result:**

- Both images are sent.
- Markers from `Ctrl+V` are stripped.
- Total staged count reflects both methods.
- Telemetry records `LLM clipboard`.

---

## CUAT-038 — macOS `Cmd+V` note

**Purpose:** Verify macOS terminal paste behavior.

**Precondition:** macOS with `clipboard_image_paste=on`.

**Steps:**

1. Copy text to clipboard.
2. Press `Cmd+V` (terminal paste).
3. Press `Ctrl+V`.

**Expected result:**

- `Cmd+V` pastes text as usual (handled by terminal emulator).
- `Ctrl+V` stages a clipboard image (if an image is in the clipboard).
- The two shortcuts behave independently.
- If `Ctrl+V` does not work as expected in a terminal, `paste-image` remains the reliable fallback.

---

# Regression Checklist

Run these existing workflows after clipboard testing to confirm no regressions:

- `help`
- `model`
- `verbose on` / `verbose off`
- `debug on` / `debug off`
- `cd <directory>`
- `history`
- `diff <file>`
- `undo` / `restore`
- `with src/main.py: explain`
- `@src/main.py` source-only prompt
- `@screenshot.png` image-only prompt (existing `@` image flow)
- `blog write a summary`
- Normal shell commands: `ls`, `git status`, `pytest`
- `new` session reset
- `exit` with feedback

Expected result: all behave identically to pre-clipboard-support behavior.

---

# Pass / Fail Criteria

## Pass

The release passes UAT when:

- All critical scenarios CUAT-001 through CUAT-025 pass.
- Help and telemetry consent scenarios CUAT-026 through CUAT-027 pass.
- No privacy failure occurs: no image bytes, base64, filenames, MIME types, or sizes appear in debug output, telemetry, feedback, or error messages.
- No existing workflow regresses.
- `paste-image` is the reliable primary path for clipboard image attachment.

## Conditional pass

The release may conditionally pass if:

- `Ctrl+V` scenarios (CUAT-028 through CUAT-033, CUAT-037, CUAT-038) have minor UI issues in specific terminals, provided `paste-image` works correctly as a fallback and `Ctrl+V` remains disabled by default.
- Platform-specific clipboard access fails on an unusual Linux configuration, but the error message is clear and `paste-image` provides a helpful install suggestion.

## Fail

The release fails UAT if any of the following occur:

- `paste-image` crashes or silently drops image data.
- Staged clipboard images are silently cleared without being sent.
- Unsupported-model rejection silently drops pending images.
- Image bytes or base64 data appear in debug output, telemetry, feedback, or error messages.
- `with` or `blog` commands silently consume staged clipboard images (this is acceptable as a v1 limitation only if they **ignore** them — silently consuming would be a bug).
- Existing `@` image attachment behavior regresses.
- Text-only prompts regress.
- Shell commands or restore/undo workflows regress.
- `Ctrl+V` is active when `clipboard_image_paste` config is off or absent.

---

# Notes for Testers

- Use small images under `IMAGE_MAX_BYTES` (default 2 MB) for positive tests.
- Keep one oversized image for the size-limit negative test.
- For `Ctrl+V` tests, remember to set `clipboard_image_paste=on` and restart the session.
- On macOS, `Cmd+V` is terminal paste; `Ctrl+V` is a separate keystroke.
- On Linux, ensure either `wl-paste` or `xclip` is installed for clipboard access.
- Over SSH, clipboard paste is not available — test the error path explicitly.
- If debug output is enabled, actively scan for long base64-like strings. None should appear.
- The `clear-attachments` command is the escape hatch for stale staged images.
- `paste-image` provides diagnostic error messages; `Ctrl+V` fails silently by design.
