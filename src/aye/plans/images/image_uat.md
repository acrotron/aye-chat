# Image Sharing UAT Plan — CLI Client Side

## Goal

Validate that Aye Chat users can attach images from the terminal using existing `@filename` prompt syntax, send them to supported multimodal models, and receive useful responses without regressing existing text-only, source-only, shell, restore, or `with` command workflows.

This UAT plan covers the CLI client behavior after the image-sharing implementation phases are complete. The backend is assumed to be implemented and available.

## Scope

### In scope

- Image attachment through `@filename` prompt references.
- Direct image references such as `@screenshot.png`.
- Mixed source + image references such as `@src/ui.py @mockup.png`.
- Image-targeted glob references such as `@*.png`.
- Directory and generic glob behavior, ensuring images are not attached accidentally.
- Model capability gating for image-capable and non-image-capable models.
- API request behavior as observed through CLI outcomes and debug output.
- `.gitignore` and `.ayeignore` handling.
- `with` command rejection for image files.
- Local/offline model rejection for image attachments.
- No image bytes appearing in terminal debug output, logs, telemetry, or feedback payloads.

### Out of scope

- Image generation.
- Clipboard image input.
- Image URL input.
- `image` / `img` commands.
- `with ...:` image attachment support.
- OCR fallback for non-multimodal models.
- Client-side provider-specific multimodal payload shaping.
- Image resizing, compression, or format conversion.

## Preconditions

1. The image-sharing implementation is complete through Phase 6.
2. The backend accepts `attachments` and supports at least one verified multimodal model.
3. The CLI is authenticated:

   ```bash
   aye auth status
   ```

4. At least one model in `aye chat` is configured with image support.
5. At least one model is available that does not support images.
6. Optional but recommended: verbose and debug modes can be toggled during testing:

   ```text
   verbose on
   debug on
   ```

## Test Project Setup

Create or use a temporary project directory:

```bash
mkdir aye-image-uat
cd aye-image-uat
git init
mkdir -p src design screenshots ignored
```

Create source files:

```bash
cat > src/main.py <<'EOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"
EOF

cat > src/ui.py <<'EOF'
def render_button(label: str) -> str:
    return f"<button>{label}</button>"
EOF

cat > README.md <<'EOF'
# Image UAT Fixture

Small project for Aye Chat image attachment testing.
EOF
```

Create small image files. Any valid small PNG/JPG/WebP files are acceptable. For example, copy screenshots into the project:

```bash
cp /path/to/small-screenshot.png screenshot.png
cp /path/to/mockup.png design/mockup.png
cp /path/to/photo.jpg screenshots/photo.jpg
cp /path/to/icon.webp src/icon.webp
cp /path/to/ignored.png ignored/secret.png
```

Create ignored paths:

```bash
cat > .gitignore <<'EOF'
ignored/
EOF

cat > .ayeignore <<'EOF'
private.png
EOF
```

Create an ignored image if desired:

```bash
cp screenshot.png private.png
```

Start Aye Chat:

```bash
aye chat --root .
```

## Acceptance Criteria Summary

The feature is accepted when all of the following are true:

- `describe @screenshot.png` sends the image to a supported multimodal model and returns a coherent response.
- Image-only `@` prompts keep normal source/context search behavior.
- Source-only `@` prompts preserve existing behavior and skip automatic source search.
- Mixed source + image prompts include only explicitly referenced source files plus attached images.
- `@*.png` attaches matching images.
- `@src/`, `@dir/*.*`, and generic recursive globs do not attach images accidentally.
- Non-multimodal models reject image prompts clearly before sending the request.
- `with screenshot.png: describe this` is rejected with a clear message pointing users to `@screenshot.png` syntax.
- `.gitignore` and `.ayeignore` are respected for images and source files.
- Existing text-only and source-only flows continue to work.
- No image bytes appear in debug output, telemetry, feedback, or terminal logs.
- Image attachments use backend field names: `file_name`, `mime_type`, `data_b64`, `bytes_size`.

---

# UAT Scenarios

## UAT-001 — Startup and baseline text-only prompt

**Purpose:** Confirm normal chat behavior still works before testing images.

**Steps:**

1. Start the CLI:

   ```bash
   aye chat --root .
   ```

2. Send a normal text-only prompt:

   ```text
   explain this project briefly
   ```

**Expected result:**

- Aye Chat responds normally.
- No attachment summary is printed.
- No image-related error appears.
- Source/context behavior matches the existing CLI behavior.

---

## UAT-002 — Direct PNG image attachment with supported model

**Purpose:** Verify the core user flow: attaching an image with `@filename`.

**Steps:**

1. Select a multimodal model using the `model` command.
2. Run:

   ```text
   describe @screenshot.png
   ```

**Expected result:**

- CLI prints an attachment summary similar to:

  ```text
  📎 attached: screenshot.png (image/png, <size>)
  ```

- The model returns a coherent description of the image.
- The image is not treated as a text source file.
- No file updates are applied unless the model intentionally returns file changes.

---

## UAT-003 — Case-insensitive image extension detection

**Purpose:** Verify image extensions are detected case-insensitively.

**Setup:**

```bash
cp screenshot.png Screenshot.PNG
```

**Steps:**

```text
describe @Screenshot.PNG
```

**Expected result:**

- `Screenshot.PNG` is attached as an image.
- MIME type is detected as `image/png`.
- The request succeeds on a supported multimodal model.

---

## UAT-004 — JPG and WebP MIME handling

**Purpose:** Verify supported non-PNG image formats work from the CLI.

**Steps:**

```text
describe @screenshots/photo.jpg
```

Then:

```text
describe @src/icon.webp
```

**Expected result:**

- `.jpg` is attached with MIME type `image/jpeg`.
- `.webp` is attached with MIME type `image/webp`.
- Both prompts work on a supported multimodal model.

---

## UAT-005 — Image-only prompt keeps normal source search

**Purpose:** Confirm image-only `@` references do not suppress normal source search/RAG behavior.

**Steps:**

1. Enable verbose mode:

   ```text
   verbose on
   ```

2. Run:

   ```text
   using this screenshot, explain what part of this project might be relevant @screenshot.png
   ```

**Expected result:**

- The image is attached.
- The CLI does not treat the image as an explicit source-file reference.
- Automatic source/context search remains active.
- In verbose output, source search should not be reported as skipped because of image-only references.

---

## UAT-006 — Source-only `@` reference preserves existing behavior

**Purpose:** Confirm existing source-only `@` behavior does not regress.

**Steps:**

```text
explain @src/main.py
```

**Expected result:**

- `src/main.py` is included as source text.
- No attachment summary is printed.
- Automatic source search is skipped, as in existing `@source` behavior.
- The model answer is based on `src/main.py`.

---

## UAT-007 — Mixed source + image prompt

**Purpose:** Verify mixed prompts include explicit source files and images, while skipping automatic source search.

**Steps:**

```text
review @src/ui.py against this mockup @design/mockup.png
```

**Expected result:**

- `src/ui.py` is included as source text.
- `design/mockup.png` is attached as an image.
- Attachment summary is printed for the image.
- Automatic source search/RAG is skipped because a source file was explicitly referenced.
- The model response references both the source file and the image.

---

## UAT-008 — Image-targeted glob includes images

**Purpose:** Verify image-targeted glob patterns attach matching images.

**Steps:**

```text
describe all root PNG images @*.png
```

**Expected result:**

- Matching `.png` files in the project root are attached.
- Attachment summary is printed for each attached image.
- Non-root images are not included unless matched by the glob.

---

## UAT-009 — Nested image-targeted glob includes images

**Purpose:** Verify image-targeted nested globs work.

**Steps:**

```text
compare screenshot images @screenshots/*.jpg
```

**Expected result:**

- Matching `.jpg` files under `screenshots/` are attached.
- MIME type is `image/jpeg`.
- The model receives and analyzes the image attachments.

---

## UAT-010 — Directory reference excludes images

**Purpose:** Verify directory references do not accidentally attach images.

**Steps:**

```text
explain @src/
```

**Expected result:**

- Text/source files in `src/` are included.
- `src/icon.webp` is not attached as an image.
- No attachment summary is printed for `src/icon.webp`.
- Existing directory source expansion behavior remains intact for text files.

---

## UAT-011 — Generic wildcard excludes images

**Purpose:** Verify generic glob patterns do not attach images accidentally.

**Steps:**

```text
summarize files @src/*.*
```

**Expected result:**

- Text/source files matching the glob are included.
- Image files matching the glob are excluded from attachments.
- No image attachment summary is printed for `src/icon.webp`.

---

## UAT-012 — Direct image reference always includes image

**Purpose:** Verify direct image references work even when the same image would be excluded by a directory or generic glob rule.

**Steps:**

```text
describe @src/icon.webp
```

**Expected result:**

- `src/icon.webp` is attached as an image.
- Attachment summary is printed.
- The model receives the image.

---

## UAT-013 — Unsupported hosted model rejects image prompt

**Purpose:** Verify client-side capability gating prevents silent text-only requests.

**Steps:**

1. Select a model that does not support images:

   ```text
   model
   ```

2. Run:

   ```text
   describe @screenshot.png
   ```

**Expected result:**

- CLI prints a clear error similar to:

  ```text
  The selected model does not support image input. Choose a multimodal model or remove the image reference.
  ```

- No API request is sent for the prompt.
- The image is not silently dropped.
- No text-only fallback request is made.

---

## UAT-014 — Local/offline model rejects image prompt cleanly

**Purpose:** Verify local/offline image handling fails gracefully.

**Steps:**

1. Select a local or offline model if available.
2. Run:

   ```text
   describe @screenshot.png
   ```

**Expected result:**

- CLI returns a clear unsupported-image message.
- The client does not crash.
- The image is not silently dropped.
- No malformed local/offline payload is attempted.

---

## UAT-015 — `with` command rejects direct image file

**Purpose:** Verify v1 explicitly rejects image files in `with` commands.

**Steps:**

```text
with screenshot.png: describe this
```

**Expected result:**

- CLI prints a clear error similar to:

  ```text
  The 'with' command does not support image files yet. Use @screenshot.png in your prompt instead.
  ```

- The LLM is not called.
- The image file is not read as UTF-8 text.

---

## UAT-016 — `with` command still works for text files

**Purpose:** Confirm the image guard does not regress existing `with` behavior.

**Steps:**

```text
with src/main.py: explain this function
```

**Expected result:**

- `src/main.py` is included as explicit source context.
- The LLM is called normally.
- No image-related error appears.

---

## UAT-017 — `with` command rejects image-targeted glob

**Purpose:** Verify wildcard image references in `with` are rejected before LLM invocation.

**Steps:**

```text
with *.png: describe these
```

**Expected result:**

- CLI rejects the command with a clear message pointing to `@` syntax.
- The LLM is not called.
- No binary image data is read as text.

---

## UAT-018 — Ignored image is not loaded or sent

**Purpose:** Verify `.gitignore` and `.ayeignore` are respected for images.

**Steps:**

Run each prompt:

```text
describe @ignored/secret.png
```

```text
describe @private.png
```

**Expected result:**

- Ignored images are not attached.
- No image bytes are loaded or sent.
- CLI behavior is consistent with existing ignored source-file behavior.
- If an error or warning is shown, it should be clear and should not expose image content.

---

## UAT-019 — Ignored image excluded from image glob

**Purpose:** Verify globs do not bypass ignore rules.

**Steps:**

```text
describe @**/*.png
```

**Expected result:**

- Non-ignored matching PNG files may be attached if the glob is image-targeted.
- `ignored/secret.png` and `private.png` are not attached.
- No attachment summary is printed for ignored images.

---

## UAT-020 — Oversized image is rejected

**Purpose:** Verify local image size enforcement.

**Setup:**

Create or copy an image larger than the configured client limit, expected default `2_000_000` bytes:

```bash
cp /path/to/large-image.png large.png
ls -lh large.png
```

**Steps:**

```text
describe @large.png
```

**Expected result:**

- CLI rejects the image with a clear size-limit error.
- The LLM/API request is not sent with the oversized image.
- The image is not silently skipped.

---

## UAT-021 — Debug output does not expose image bytes

**Purpose:** Verify privacy and logging behavior.

**Steps:**

1. Enable debug mode:

   ```text
   debug on
   ```

2. Run:

   ```text
   describe @screenshot.png
   ```

3. Inspect terminal output.

**Expected result:**

- Debug output does not include base64 image data.
- Debug output does not include raw image bytes.
- It is acceptable to show safe metadata such as MIME type or byte size.
- The prompt succeeds on a supported model.

---

## UAT-022 — Verbose output provides useful image metadata

**Purpose:** Verify verbose output is informative without leaking image bytes.

**Steps:**

1. Enable verbose mode:

   ```text
   verbose on
   ```

2. Run:

   ```text
   review @src/ui.py using @design/mockup.png
   ```

**Expected result:**

- CLI prints attachment summary.
- Verbose output may include resolved relative path and raw byte size.
- Verbose output must not include base64 image data.
- Verbose output should indicate source search is skipped because explicit source files were provided.

---

## UAT-023 — Text-only API body behavior remains unchanged

**Purpose:** Confirm text-only requests are not changed by the attachment plumbing.

**Steps:**

1. Enable debug mode if it shows request metadata safely:

   ```text
   debug on
   ```

2. Run:

   ```text
   explain src/main.py conceptually without editing files
   ```

**Expected result:**

- No `attachments` field is sent for a text-only prompt.
- No image-related output appears.
- The response behavior matches pre-image text-only behavior.

---

## UAT-024 — Attachment request field names are accepted by backend

**Purpose:** Confirm client/backend contract compatibility from the CLI.

**Steps:**

```text
describe @screenshot.png
```

**Expected result:**

- Backend accepts the request.
- No schema or validation error occurs for attachment fields.
- The successful response implies the client sent expected attachment field names:
  - `file_name`
  - `mime_type`
  - `data_b64`
  - `bytes_size`

If backend debug tools are available, confirm the request contains exactly those field names and does not log image bytes.

---

## UAT-025 — Multiple images in one prompt

**Purpose:** Verify multiple attachments can be sent together.

**Steps:**

```text
compare these images @screenshot.png @design/mockup.png
```

**Expected result:**

- Attachment summary is printed for each image.
- Both images are sent to the backend.
- The model response compares or references both images.
- Normal source search remains active because no source files were explicitly referenced.

---

## UAT-026 — Multiple images plus source file

**Purpose:** Verify mixed multi-image and source context behavior.

**Steps:**

```text
compare @src/ui.py with these visuals @screenshot.png @design/mockup.png
```

**Expected result:**

- `src/ui.py` is included as explicit source context.
- Both images are attached.
- Automatic source search is skipped.
- The model response uses the explicit source file and the images.

---

## UAT-027 — Existing shell command behavior still works

**Purpose:** Confirm shell integration is unaffected.

**Steps:**

```text
ls -la
```

Then:

```text
git status
```

**Expected result:**

- Commands execute as shell commands.
- They are not treated as LLM prompts.
- Image attachment code is not involved.

---

## UAT-028 — Restore/undo behavior unaffected by image input

**Purpose:** Confirm image inputs are not snapshotted as outputs and optimistic edit rollback still works.

**Steps:**

1. Ask for a small source edit using an image and source file, for example:

   ```text
   update @src/ui.py to better match this mockup @design/mockup.png
   ```

2. If the assistant edits `src/ui.py`, run:

   ```text
   diff src/ui.py
   ```

3. Then run:

   ```text
   undo
   ```

**Expected result:**

- Only LLM-produced text file updates are snapshotted.
- The image file is not modified or snapshotted as an output.
- `diff` works for updated source files.
- `undo` restores the source file.

---

## UAT-029 — New chat session works after image prompt

**Purpose:** Confirm session control remains stable after image prompts.

**Steps:**

```text
describe @screenshot.png
```

Then:

```text
new
```

Then:

```text
what project am I in?
```

**Expected result:**

- Image prompt succeeds.
- `new` starts a fresh chat session.
- Subsequent text prompt works normally.

---

## UAT-030 — Exit feedback does not include image bytes

**Purpose:** Confirm feedback path does not leak image data.

**Steps:**

1. Run an image prompt:

   ```text
   describe @screenshot.png
   ```

2. Exit:

   ```text
   exit
   ```

3. If prompted for feedback, enter a short feedback message.

**Expected result:**

- Feedback submission does not include image bytes or base64 data.
- No image content is printed during exit.
- CLI exits cleanly.

---

# Regression Checklist

Run these existing workflows after image testing:

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
- Normal shell commands such as `ls`, `git status`, and `pytest`

Expected result: all behave as they did before image support.

# Pass / Fail Criteria

## Pass

The release passes UAT when:

- All critical scenarios UAT-001 through UAT-024 pass.
- No privacy failure occurs: no image bytes or base64 strings appear in CLI output, debug output, telemetry, or feedback.
- No existing text-only, source-only, shell, restore, or `with` text workflow regresses.
- Unsupported models reject image prompts clearly before sending requests.

## Conditional pass

The release may conditionally pass if:

- A provider-specific backend failure occurs for one multimodal model, but at least one verified supported model works and the failing model is not marked `supports_images=True` on the client.
- Cosmetic formatting of attachment summaries differs, but the summary remains clear and includes file name, MIME type, and size.

## Fail

The release fails UAT if any of the following occur:

- Image prompts are silently sent as text-only prompts.
- Unsupported models send image requests instead of rejecting them client-side.
- Image bytes or base64 data appear in logs, debug output, telemetry, or feedback.
- Ignored images are loaded or sent.
- `with screenshot.png: ...` reads binary image data or calls the LLM.
- Source-only `@` behavior regresses.
- Mixed source + image prompts include unintended RAG-selected source files.
- `@src/` or `@dir/*.*` accidentally attaches images.

# Notes for Testers

- Use small images under the configured client size limit for normal positive tests.
- Keep at least one oversized image for the size-limit negative test.
- Prefer a temporary repository so restore/history tests do not affect real work.
- If debug output is enabled, actively scan for long base64-like strings. None should appear.
- If the backend exposes request inspection in a safe test environment, verify attachment metadata and field names there, but do not log or store image bytes.
