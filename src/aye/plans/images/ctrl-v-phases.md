# Ctrl+V Clipboard Image Paste — Phase Overview

This is a lightweight implementation phase overview.

All file paths below are relative to `src/aye/` unless noted otherwise.

For detailed design, data flow, risks, telemetry rules, and testing, see:

```text
ctrl-v.md
```

Existing `@filename` image support is already implemented and is out of scope. Do not add or document `@filename` as a clipboard fallback.

---

## Phase 1 — Clipboard Image Loading Core

**Goal:** Add a model-level helper that reads an image from the system clipboard, normalizes it to PNG, enforces the existing image size limit, and returns the existing attachment dict shape. Include Linux subprocess fallbacks from the start.

**Create:**

- `model/clipboard_images.py`

**Modify:**

- `../../pyproject.toml` — add `Pillow>=9.0` to `dependencies`

**Inspect:**

- `model/attachments.py`

**Attach for context:**

```text
@model/attachments.py @../../pyproject.toml @../../ctrl-v.md
```

**Brief notes:**

- Pillow is a mandatory dependency.
- Use `PIL.ImageGrab.grabclipboard()` on macOS and Windows.
- Include Linux subprocess fallbacks: `wl-paste --type image/png` (Wayland), `xclip -selection clipboard -t image/png -o` (X11).
- If no clipboard tool is available, raise `ClipboardImageUnavailableError` with a platform-appropriate install suggestion.
- Reuse `IMAGE_MAX_BYTES` from `model/attachments.py`.
- Return `file_name`, `mime_type`, `data_b64`, `bytes_size`.
- Keep clipboard images in memory only.
- Do not touch REPL behavior in this phase.

---

## Phase 2 — Pending Clipboard State and `paste-image` Command

**Goal:** Add an explicit command that stages a clipboard image for the next normal AI prompt, without sending it yet. Multiple calls accumulate.

**Create:**

- `controller/clipboard_attachments.py`

**Modify:**

- `controller/command_handlers.py`
- `controller/repl.py`
- `presenter/repl_ui.py` only if adding help text now

**Attach for context:**

```text
@model/clipboard_images.py @model/attachments.py @controller/command_handlers.py @controller/repl.py @presenter/repl_ui.py @../../ctrl-v.md
```

**Brief notes:**

- Add `handle_paste_image_command(conf)` in `controller/command_handlers.py`.
- Add `paste-image` to `BUILTIN_COMMANDS` in `controller/repl.py`.
- Dispatch `paste-image` in `chat_repl(...)`.
- Store pending attachments on `conf._pending_clipboard_attachments`.
- Multiple `paste-image` calls accumulate images.
- Use `print_attachment_summary(...)` for safe metadata display.
- Do not invoke the LLM.
- Do not record image-used-with-LLM telemetry yet.

---

## Phase 3 — Attach Pending Clipboard Images to LLM Prompts and Telemetry

**Goal:** Merge staged clipboard images into the normal LLM prompt flow and record privacy-safe telemetry using four distinct kind names.

**Modify:**

- `controller/repl.py`
- `model/telemetry.py`

**Inspect but usually do not modify:**

- `controller/llm_invoker.py`
- `model/api.py`
- `controller/command_handlers.py` — note `with`/`blog` v1 limitation
- `plugins/local_model.py`
- `plugins/offline_llm.py`

**Attach for context:**

```text
@controller/repl.py @model/telemetry.py @controller/clipboard_attachments.py @controller/llm_invoker.py @model/api.py @controller/command_handlers.py @../../ctrl-v.md
```

**Brief notes:**

- Merge pending clipboard attachments into the existing `attachments` list before model capability gating.
- Reuse existing `_model_supports_images(...)` behavior.
- Keep pending attachments if unsupported-model gating rejects the prompt.
- Clear pending attachments only **after** `invoke_llm(...)` returns successfully. If it raises, keep pending for retry.
- Replace the existing telemetry block with four-way kind selection:
  - `LLM clipboard` when clipboard attachments are present.
  - `LLM @ attachment` when `@` image attachments are present (no clipboard).
  - `LLM @` when only `@` source files are present.
  - `LLM` for text-only prompts.
- Update allowed kinds in `model/telemetry.py` to include `LLM @ attachment` and `LLM clipboard`.
- Do not record filenames, MIME types, byte sizes, image counts, base64, raw bytes, or clipboard markers.
- **v1 limitation:** `with` and `blog` paths do not consume staged clipboard images. They remain pending for the next normal prompt.

---

## Phase 4 — UX, Privacy Hardening, Help, and UAT

**Goal:** Make the feature discoverable and verify debug, telemetry, feedback, and error output are privacy-safe.

**Modify:**

- `presenter/repl_ui.py`
- `controller/repl.py`
- `controller/command_handlers.py` only if adding optional `clear-attachments`
- `model/telemetry.py` only if needed

**Inspect but usually do not modify:**

- `model/api.py`
- `controller/llm_invoker.py`

**Attach for context:**

```text
@presenter/repl_ui.py @controller/repl.py @controller/command_handlers.py @model/telemetry.py @model/api.py @model/clipboard_images.py @../../ctrl-v.md
```

**Brief notes:**

- Add help entry for `paste-image` in `print_help_message()` in `presenter/repl_ui.py`.
- Update telemetry consent text in `_prompt_for_telemetry_consent_if_needed()` in `controller/repl.py` to mention `LLM clipboard` and `LLM @ attachment`.
- Confirm telemetry never includes image metadata or content.
- Confirm debug output redacts `data_b64`.
- Optional: add `clear-attachments` command in `controller/repl.py` and `controller/command_handlers.py`.
- Perform manual UAT for command-based clipboard image flow.
- Verify `with`/`blog` do not consume staged clipboard images (known v1 limitation).

---

## Phase 5 — Optional Experimental `Ctrl+V` Key Binding

**Goal:** Add optional `Ctrl+V` support that reads an image from the clipboard and stages it into the current prompt buffer. Multiple presses accumulate.

**Modify:**

- `controller/repl.py`
- `controller/clipboard_attachments.py`
- `presenter/repl_ui.py`

**Inspect / optionally modify:**

- `model/auth.py`
- `model/config.py`

**Attach for context:**

```text
@controller/repl.py @controller/clipboard_attachments.py @presenter/repl_ui.py @model/auth.py @model/config.py @model/clipboard_images.py @../../ctrl-v.md
```

**Brief notes:**

- Keep disabled by default.
- Use config key:

  ```text
  clipboard_image_paste=on|off
  ```

- Existing config behavior should also allow:

  ```text
  AYE_CLIPBOARD_IMAGE_PASTE=on
  ```

- Change `create_key_bindings()` and `create_prompt_session(...)` in `controller/repl.py` to accept `conf`.
- Register `c-v` only when enabled.
- On `Ctrl+V`, stage clipboard image (accumulates) and insert a safe marker like `[clipboard:image-001]`.
- Strip markers before LLM invocation.
- Do not emit telemetry until the prompt is actually submitted.
- Submitted prompt records `LLM clipboard`.

---

## Recommended Implementation Order

1. Phase 1 — Clipboard Image Loading Core
2. Phase 2 — Pending Clipboard State and `paste-image` Command
3. Phase 3 — Attach Pending Clipboard Images to LLM Prompts and Telemetry
4. Phase 4 — UX, Privacy Hardening, Help, and UAT
5. Phase 5 — Optional Experimental `Ctrl+V` Key Binding

`paste-image` should ship before `Ctrl+V`. Keep `Ctrl+V` disabled by default until manual UAT confirms it does not interfere with normal terminal behavior.
