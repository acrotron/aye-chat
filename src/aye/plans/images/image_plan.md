# Plan: Image sharing / multimodal input (GitHub issue #265)

Reference: https://github.com/acrotron/aye-chat/issues/265

## Goal
Let users attach images using the existing `@filename` prompt syntax and have multimodal-capable LLMs analyze them alongside text and source code, while preserving Aye Chat's optimistic-edit workflow.

The first version is intentionally small: support image input only through `@` references.

Non-goals for the initial version:
- Generating images.
- OCR fallback for non-multimodal models.
- Editing or annotating images inside Aye Chat.
- Video or audio inputs.
- Clipboard image input.
- Image URLs.
- A separate `image` / `img` command.
- `with ...:` image support (v1 rejects image files in `with`).
- Client-side provider-specific multimodal payload shaping.
- Optional Pillow-based downscaling or format conversion.

---

## 1. User experience

### 1.1 Inline `@` image references only
Reuse the existing `@filename` UX from `at_file_completer` / `parse_at_references`.

Examples:

```text
explain @screenshot.png
what's wrong with this layout? @design/mockup.jpg
review @src/ui.py using @design/mockup.png
```

Detection rule:
- If the referenced file extension is in `IMAGE_EXTENSIONS`, it is treated as an image attachment.
- If the referenced file is not an image, existing source-file behavior is preserved.

Initial image extensions:

```python
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
```

### 1.2 Context-selection behavior
`@` references affect context selection as follows:

1. **Image `@` references only**
   - Example: `describe @screenshot.png`
   - Attach the image.
   - Keep the regular source search/context behavior exactly as today.
   - This means RAG, small-project full context, `/all`, and fallback source collection continue to work normally.

2. **Source-file `@` references only**
   - Example: `explain @src/main.py`
   - Preserve current behavior: skip automatic source search and include only the explicitly referenced source files.

3. **Mixed image and source-file `@` references**
   - Example: `review @src/ui.py using @design/mockup.png`
   - Attach the image.
   - Include only the explicitly referenced source files.
   - Skip automatic source search/RAG so the prompt context stays focused and predictable.

This keeps image-only prompts convenient while preserving the existing meaning of explicit source-file references.

### 1.3 Model capability behavior
If the selected model does not support images:
- Print a clear warning/error.
- Do not silently drop images.
- Refuse the image prompt before sending the request, unless a future config explicitly opts into text-only fallback.

Suggested message:

```text
The selected model does not support image input. Choose a multimodal model or remove the image reference.
```

### 1.4 Visual feedback
When an image attachment is detected, print a concise attachment summary:

```text
📎 attached: screenshot.png (image/png, 142 KB)
```

When `verbose on` is enabled, also print:
- Resolved relative path.
- Raw byte size.
- Whether automatic source search is being used or skipped because explicit source `@` references were present.

### 1.5 Defensive guard for `with` command and image files
The `with` command (`with file1.py, file2.py: prompt`) is out of scope for v1 image support. However, a user could still write `with screenshot.png: describe this`.

In v1, `handle_with_command` must detect image-extension files in the file list and reject them with a clear message:

```text
The 'with' command does not support image files yet. Use @screenshot.png in your prompt instead.
```

This prevents `with` from reading binary image data as UTF-8 text and producing garbled context.

---

## 2. Data model changes

The existing LLM pipeline passes `source_files: Dict[str, str]` for text source context. Keep that contract unchanged and add a parallel image-only attachments list.

### 2.1 New image attachment record
Add a small image-only attachment model.

```python
# src/aye/model/attachments.py
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageAttachment:
    file_name: str
    mime_type: str
    data_b64: str
    bytes_size: int
```

Notes:
- Do not introduce `kind="text"` in v1; text files already use `source_files`.
- Keep the client-side shape simple and let the backend handle provider-specific conversion.
- The `ImageAttachment` dataclass is used in the REPL and `llm_invoker` layers. The `at_file_completer` plugin returns **plain dicts** (see Section 3) to stay consistent with the existing plugin contract.

### 2.2 Backward-compatible plumbing
Add an optional `attachments` parameter to:
- `invoke_llm(...)` in `src/aye/controller/llm_invoker.py`
- `cli_invoke(...)` in `src/aye/model/api.py`
- the `local_model_invoke` plugin params dict, only so local plugins can reject unsupported image input cleanly.

Existing text-only flows should produce the same request body as today when `attachments` is empty.

### 2.3 LLMResponse
No changes required. Responses remain:
- `summary`
- `updated_files`
- `chat_id`

---

## 3. `@` reference resolution updates

Primary file to change:
- `src/aye/plugins/at_file_completer.py`

Current behavior:
- `parse_at_references` extracts `@` paths.
- Expands direct paths, directories, and glob patterns.
- Reads matched files as UTF-8 text.
- Returns `file_contents` and `cleaned_prompt`.

New behavior:
1. Parse `@` references as today.
2. Expand patterns as today.
3. For each expanded file, classify it as image or source:
   - If it is an image extension, load it as bytes, base64 encode, and add it to the `attachments` list as a **plain dict** (not an `ImageAttachment` object — the plugin returns raw dicts consistent with the existing plugin contract).
   - Otherwise, read it as UTF-8 text and add it to `file_contents` as today.
4. Return both text files and image attachment dicts.

### 3.1 Glob and directory expansion rules for images

Glob patterns and directory references can expand to a large and potentially mixed set of files. To avoid accidentally sending unexpected images, apply the following rules:

**Rule: Only include images from patterns that explicitly target image extensions.**

| Pattern | Image-targeted? | Images included? | Examples |
|---|---|---|---|
| `@screenshot.png` | Direct file reference | Yes | Always included |
| `@*.png` | Yes — extension is an image type | Yes | All `.png` files in project root |
| `@screenshots/*.jpg` | Yes — extension is an image type | Yes | All `.jpg` in `screenshots/` |
| `@assets/**/*.webp` | Yes — extension is an image type | Yes | All `.webp` recursively |
| `@src/` | No — directory, no image extension | No — images skipped | Only text/source files from `src/` |
| `@dir/*.*` | No — generic wildcard extension | No — images skipped | Only text/source files matching `*.*` |
| `@src/**/*` | No — generic pattern | No — images skipped | Only text/source files |
| `@*.py` | No — not an image extension | N/A (no image files match `.py`) | Source files only |

**Detection logic:** A glob pattern is considered "image-targeted" if its **literal extension** (the suffix after the last `.` in the pattern, ignoring `*` and `?` wildcards in the filename stem) matches `IMAGE_EXTENSIONS`. Concretely:

```python
def _is_image_targeted_pattern(pattern: str) -> bool:
    """Check if a glob pattern explicitly targets image files.

    Returns True only when the pattern's extension (the part after the final dot)
    is a known image extension. Patterns like *.*, dir/*, or dir/**/* are NOT
    image-targeted.
    """
    # Strip trailing slash (directory reference — never image-targeted)
    if pattern.endswith('/'):
        return False
    suffix = Path(pattern).suffix.lower()
    return suffix in IMAGE_EXTENSIONS
```

Inside `_expand_file_patterns`, after expanding each pattern, apply:
- If the pattern is a **direct file reference** (no wildcards, no trailing slash):
  - Include the file regardless of whether it is an image or text.
- If the pattern is **image-targeted** (per the function above):
  - Include all expanded files, both image and non-image (though in practice only images will match).
- If the pattern is **not image-targeted** (generic glob or directory):
  - Exclude expanded files whose extension is in `IMAGE_EXTENSIONS`.
  - Include only text/source files.

This rule ensures that `@*.png` includes images (explicit intent), while `@src/` and `@dir/*.*` do not silently attach every image in the tree.

### 3.2 Plugin response shape

Suggested plugin response (plain dicts only — no model objects):

```python
{
    "references": references,
    "expanded_files": expanded_files,
    "file_contents": file_contents,           # Dict[str, str] — text source files only
    "attachments": [
        {
            "file_name": "screenshot.png",
            "mime_type": "image/png",
            "data_b64": "...",
            "bytes_size": 142000,
        }
    ],
    "has_image_references": bool(attachments),
    "has_source_references": bool(file_contents),
    "cleaned_prompt": cleaned_prompt,
}
```

The REPL or `llm_invoker` can wrap these dicts into `ImageAttachment` objects if needed for type safety, but the plugin itself should not import from `aye.model.attachments`.

### 3.3 Context behavior driven by plugin response

- If `has_image_references=True` and `has_source_references=False`, call `invoke_llm(...)` with `attachments`, but leave `explicit_source_files=None` so normal source search still runs.
- If `has_source_references=True`, call `invoke_llm(...)` with `explicit_source_files=file_contents` so automatic source search is skipped, regardless of whether images are also attached.

In `repl.py`, the existing `used_at = bool(explicit_files)` variable should be updated to key off source files only:

```python
explicit_files = at_response.get("file_contents", {})
attachments = at_response.get("attachments", [])
used_at = bool(explicit_files)  # Source refs only — images do not suppress search
```

### 3.4 Ignore handling
- Reuse `load_ignore_patterns(root)` when expanding/resolving references.
- Images under `.gitignore` or `.ayeignore` paths must not be sent.
- Hidden files/directories should continue to be skipped consistently with existing source scanning behavior.

---

## 4. API / backend contract

The client should send a uniform `attachments` array to the Aye Chat backend. Provider-specific multimodal formatting should happen server-side in v1.

Add an optional `attachments` field to the request body in `cli_invoke(...)`:

```json
{
  "message": "describe this screenshot",
  "chat_id": 123,
  "model": "anthropic/claude-sonnet-4.6",
  "source_files": {},
  "attachments": [
    {
      "file_name": "screenshot.png",
      "mime_type": "image/png",
      "data_b64": "...",
      "bytes_size": 142000
    }
  ]
}
```

Note: JSON field names match the `ImageAttachment` dataclass exactly (`file_name`, `mime_type`, `data_b64`, `bytes_size`). No aliasing or renaming during serialization.

Server responsibilities:
- Reject attachments for models that do not support images.
- Translate attachments into the upstream provider's multimodal format.
- Enforce server-side size limits.
- Ensure image bytes are never logged or included in telemetry.

Client responsibilities:
- Detect image `@` references.
- Base64 encode image bytes.
- Enforce a basic local size cap.
- Send attachments only for image-capable models.
- Keep text-only request bodies unchanged.

---

## 5. Model capability flag

Extend model config entries in `src/aye/model/config.py::MODELS` with:

```python
"supports_images": True | False
```

Default behavior:
- Missing `supports_images` means `False`.
- Only mark models as `True` after verifying backend support.

### 5.1 Initial model list (backend-sync task)
The exact set of `supports_images: True` models depends on which providers the backend supports for multimodal payloads at launch. Recommended candidates to verify and enable first:

- `anthropic/claude-sonnet-*` (Claude 3.5+ supports images)
- `anthropic/claude-opus-*`
- `openai/gpt-4o*` or equivalent vision-capable models
- `google/gemini-*-pro` (if available)

This is a backend-sync task: before marking a model as supported on the client, confirm the backend can route image attachments to that provider successfully.

Keep offline/local models as unsupported unless explicitly implemented later.

### 5.2 Capability check helper
`llm_invoker.py` already has a `_get_model_config(model_id)` helper that returns the model dict by ID. Reuse it for the capability check:

```python
def _model_supports_images(model_id: str) -> bool:
    """Check if the given model supports image input."""
    config = _get_model_config(model_id)
    if config is None:
        return False
    return config.get("supports_images", False)
```

If `_get_model_config` is needed elsewhere (e.g. `repl.py`), consider extracting it to `config.py` as a shared utility. For v1, keeping it in `llm_invoker.py` is acceptable.

### 5.3 Capability check behavior
If a prompt includes image attachments and the selected model does not support images:
- Print a clear error.
- Do not call the API.
- Do not silently send a text-only request.

---

## 6. Size, encoding, and limits

Keep v1 simple.

### 6.1 Encoding
- Read image files as bytes.
- Detect MIME type using `mimetypes.guess_type(...)` with extension fallback.
- Base64 encode the original bytes.

### 6.2 Local size cap
Add a hard per-image cap before sending.

Suggested default:

```python
IMAGE_MAX_BYTES = 2_000_000
```

This should be configurable later, but v1 can use a constant or simple config key.

If an image exceeds the cap:
- Reject the image prompt with a clear message.
- Do not silently skip the image.

### 6.3 Deferred from v1
Do not implement in v1:
- Pillow dependency.
- Auto-downscaling.
- Format conversion.
- Animated GIF handling beyond passing bytes as-is if under size limit.
- Image byte accounting against RAG prompt budget.

These can be added after the basic path is working end-to-end.

---

## 7. Local/offline/provider-specific behavior

For v1, avoid client-side provider-specific payload shaping.

### 7.1 Hosted API path
The hosted API path is the primary v1 path:
- `invoke_llm(...)` forwards `attachments` to `cli_invoke(...)`.
- `cli_invoke(...)` sends `attachments` to the backend.
- Backend handles provider-specific multimodal payloads.

### 7.2 Local/offline plugins
If `attachments` are present and a local/offline plugin is selected:
- Return a clear unsupported message.
- Do not crash.
- Do not silently drop images.

Example:

```text
The selected local/offline model does not support image input.
```

Do not implement OpenAI-compatible, Gemini, Anthropic, Databricks, or offline multimodal payload shaping in the client for v1.

---

## 8. Telemetry & privacy

- Never include image bytes in telemetry.
- Never include image bytes in feedback payloads.
- If adding telemetry, record only a coarse event such as `LLM image <MIME type>`, with no filename, path, size, or image content.
- Images are inputs, not outputs, so they are not snapshotted.
- Only LLM-produced text file updates continue to flow through `apply_updates`.
- Respect `.gitignore` and `.ayeignore` before loading/sending image data. (Relevant for globs/wildcard inclusions.)

---

## 9. Files to add / change

New:
- `src/aye/model/attachments.py`
  - `ImageAttachment` dataclass.
  - `IMAGE_EXTENSIONS` set.
  - `IMAGE_MAX_BYTES` constant.
  - `_is_image_targeted_pattern(pattern)` helper.
  - `load_image_attachment(path, root)` helper — reads bytes, base64 encodes, detects MIME, validates size, returns a plain dict.
  - MIME detection helper.
  - Size validation helper.

Change:
- `src/aye/plugins/at_file_completer.py`
  - Import `IMAGE_EXTENSIONS` and `_is_image_targeted_pattern` from `aye.model.attachments`.
  - Detect image extensions during `_expand_file_patterns` and `_read_files`.
  - Apply the glob/directory image expansion rule (Section 3.1).
  - Return `attachments` (plain dicts) alongside existing `file_contents`.
  - Preserve existing source-file behavior.
  - Preserve ignore-pattern behavior.

- `src/aye/controller/repl.py`
  - Extract `attachments` from the `parse_at_references` plugin response.
  - Set `used_at = bool(explicit_files)` based on source files only (not images).
  - Apply the context-selection rule:
    - Image-only refs: normal source search.
    - Source refs present: explicit source files only.
  - Wrap attachment dicts into `ImageAttachment` objects if needed.
  - Check model capability before calling `invoke_llm`.
  - Print attachment summary (Section 1.4).

- `src/aye/controller/llm_invoker.py`
  - Accept optional `attachments: List[ImageAttachment]` parameter.
  - Add `_model_supports_images(model_id)` helper reusing `_get_model_config`.
  - Forward attachments to local plugins and hosted API.
  - Keep existing source search behavior unchanged unless explicit source files are provided.

- `src/aye/model/api.py`
  - Add optional `attachments` parameter to `cli_invoke(...)`.
  - Serialize attachments into the request body only when non-empty.
  - Use field names matching `ImageAttachment` dataclass: `file_name`, `mime_type`, `data_b64`, `bytes_size`.

- `src/aye/model/config.py`
  - Add `supports_images` to verified model entries.
  - Default missing capability to `False`.
  - Initial model list is a backend-sync task (see Section 5.1).

- `src/aye/controller/command_handlers.py`
  - In `handle_with_command`, add a guard that rejects image-extension files with a clear message (Section 1.5).

- `src/aye/presenter/repl_ui.py`
  - Add a small helper to print attachment summary lines.

Optional v1 change:
- `src/aye/plugins/offline_llm.py`
- `src/aye/plugins/local_model.py`
  - Return a clear unsupported-attachments error if images are passed.

Docs:
- Update `help` and README with only `@image` usage examples.
- Do not document `image`, clipboard, URLs, or `with` image support until implemented.

---

## 10. Test plan

All tests use `pytest` with `tmp_path` and `unittest.mock`. No real network or real model calls.

### 10.1 Image extension detection
- `@photo.png` produces an image attachment, not a text file entry.
- `@photo.JPG` is detected case-insensitively.
- Non-image `@main.py` continues to produce text source content.

### 10.2 Mixed references
- `@code.py @photo.png` produces:
  - `file_contents` containing `code.py`.
  - `attachments` containing `photo.png`.
  - `has_source_references=True`.
  - `has_image_references=True`.

### 10.3 Context-selection behavior
- Image-only prompt:
  - `explicit_source_files` remains `None`.
  - Normal source search/RAG path is used.
- Source-only prompt:
  - `explicit_source_files` is populated.
  - Automatic source search is skipped.
- Mixed source + image prompt:
  - `explicit_source_files` is populated with only referenced source files.
  - Image attachments are forwarded.
  - Automatic source search is skipped.

### 10.4 Glob/directory image expansion
- `@*.png` expands and includes `.png` image files.
- `@screenshots/*.jpg` expands and includes `.jpg` image files.
- `@src/` expands to source files only; images in `src/` are excluded.
- `@dir/*.*` expands to source files only; images in `dir/` are excluded.
- `@src/**/*` expands to source files only; images are excluded.
- Direct reference `@dir/photo.png` includes the image regardless of directory context.

### 10.5 Capability gating
- With `supports_images=False`, an image prompt returns a clear error/warning and does not call the API.
- With `supports_images=True`, attachments are forwarded to `cli_invoke(...)`.

### 10.6 Size enforcement
- Image over the local max size is rejected with a clear error.
- Image under the local max size is base64 encoded and attached.

### 10.7 Ignore handling
- An image under an ignored path is not loaded or sent.
- A source file under an ignored path is not included.

### 10.8 API request body
- Text-only prompts produce the same request body as today.
- Image prompts include `attachments` only when non-empty.
- Attachment JSON field names match the `ImageAttachment` dataclass (`file_name`, `mime_type`, `data_b64`, `bytes_size`).
- Image bytes are not printed or logged in debug/telemetry output.

### 10.9 Local/offline behavior
- If local/offline model handling receives attachments, it returns a clear unsupported message without crashing.

### 10.10 `with` command guard
- `with screenshot.png: describe this` prints an error and does not send the request.
- `with main.py: explain` continues to work as before (no regression).

### 10.11 Plugin response shape
- Plugin returns `attachments` as plain dicts, not `ImageAttachment` objects.
- REPL correctly wraps dicts into `ImageAttachment` for downstream use.

---

## 11. Phased rollout

### Phase 1 — `@image` references only
Client:
- Add `ImageAttachment` model and helpers in `attachments.py`.
- Add `_is_image_targeted_pattern` helper.
- Add image detection to `parse_at_references` in `at_file_completer.py`.
- Apply glob/directory image expansion rule (only image-targeted patterns include images).
- Return both `file_contents` and `attachments` (as plain dicts) from `parse_at_references`.
- Apply the context-selection rule:
  - Image-only refs keep normal source search.
  - Any source-file refs skip normal source search and use only explicit sources.
- Thread `attachments` through `repl -> invoke_llm -> cli_invoke`.
- Add basic image size limit and MIME detection.
- Add `supports_images` capability check using `_get_model_config`.
- Refuse image prompts for unsupported models.
- Keep local/offline image support unsupported with a clear message.
- Add `with` command defensive guard for image files.

Backend:
- Accept the optional `attachments` array.
- Implement provider routing for at least one verified multimodal model.
- Enforce backend-side size limits.
- Confirm which models support images and sync with client `MODELS` config.

### Phase 2 — More hosted models and polish
- Enable additional verified multimodal hosted models server-side.
- Add backend-provided model capability metadata if available.
- Improve user-facing errors for provider-specific image failures.
- Add verbose output for image payload sizes.

### Phase 3 — Optional enhancements
- Clipboard image input.
- Image URL input.
- `with file.png, code.py: ...` image support.
- Optional Pillow downscaling/format normalization.
- Prompt budget accounting for image bytes.
- Client-side provider-specific adapters if ever needed.

---

## 12. Risks & open questions

- **Backend support**: v1 assumes the backend handles provider-specific multimodal payloads. If not, Phase 1 grows significantly. — YES: backend will handle.
- **Model capability drift**: hard-coded `supports_images` can become stale. Backend-provided capability metadata may be better long-term.
- **Payload size**: even small images become larger after base64 encoding (~33% overhead). Keep a conservative client-side size cap and enforce server-side caps too.
- **Privacy**: images may contain sensitive screenshots. Respect ignore files and never log image bytes.

---

## 13. Acceptance criteria

- A user can run `describe @screenshot.png` with a supported multimodal model and receive a coherent response.
- If the prompt contains only image `@` references, regular source search/context behavior remains unchanged.
- If the prompt contains source-file `@` references, automatic source search is skipped and only explicitly referenced source files are included.
- Mixed prompts such as `review @src/ui.py using @mockup.png` include the image and the specified source file, without additional RAG-selected source files.
- `@*.png` includes matching images; `@src/` and `@dir/*.*` do not include images.
- A user attaching an image to a non-multimodal model gets a clear error and the request is not silently sent text-only.
- `with screenshot.png: describe this` is rejected with a clear message pointing to `@` syntax.
- `.gitignore` and `.ayeignore` are respected for images and source files.
- Existing text-only and source-only `@` flows do not regress.
- No image bytes appear in telemetry, debug logs, or feedback payloads.
- API request body field names match the `ImageAttachment` dataclass (`file_name`, `mime_type`, `data_b64`, `bytes_size`).
- Plugin returns attachment data as plain dicts; REPL wraps them into typed objects.
