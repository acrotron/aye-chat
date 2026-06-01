# Image Sharing — Implementation Phases

Companion to `issue.md`. Use this file to plan **which files to edit in each LLM turn** so a single generation never has to hold the whole change in context.

Each phase is self-contained and testable. Run tests between phases. Do not skip phases — later phases depend on earlier ones.

---

## Phase 1 — Foundation: attachment model and helpers

**Goal:** Add the data structures and pure helpers. No wiring yet.

**Files to create:**
- `src/aye/model/attachments.py`
  - `IMAGE_EXTENSIONS` set
  - `IMAGE_MAX_BYTES` constant
  - `ImageAttachment` dataclass
  - `_is_image_targeted_pattern(pattern: str) -> bool`
  - `detect_mime_type(path: Path) -> str`
  - `load_image_attachment(path: Path, root: Path) -> dict` (returns plain dict; raises on size violation)

**Files to modify:** none

**Tests to add:**
- `tests/test_attachments.py`
  - Extension detection (case-insensitive).
  - `_is_image_targeted_pattern` for `*.png`, `*.*`, `dir/`, `dir/**/*`, `screenshot.png`.
  - MIME detection for png/jpg/webp.
  - Size limit enforcement.
  - Base64 round-trip integrity.

**Verify before continuing:** `pytest tests/test_attachments.py -v` passes.

**Depends on:** nothing.

---

## Phase 2 — Plugin: detect images in `@` references

**Goal:** Make `parse_at_references` return image attachments as plain dicts alongside existing source file behavior.

**Files to modify:**
- `src/aye/plugins/at_file_completer.py`
  - Import `IMAGE_EXTENSIONS`, `_is_image_targeted_pattern`, `load_image_attachment` from `aye.model.attachments`.
  - During pattern expansion, apply the glob/directory image rule (Section 3.1 of `issue.md`).
  - In file-reading step, route image-extension files to `load_image_attachment` instead of text read.
  - Update plugin response shape to include:
    - `attachments: List[dict]`
    - `has_image_references: bool`
    - `has_source_references: bool`
  - Preserve existing `file_contents` and `cleaned_prompt` keys.
  - Preserve ignore-pattern handling.

**Files to create:** none.

**Tests to add:**
- `tests/test_at_file_completer_images.py`
  - `@photo.png` produces an attachment, not a text entry.
  - `@code.py @photo.png` produces both.
  - `@*.png` includes images.
  - `@src/` excludes images even if present in the directory.
  - `@dir/*.*` excludes images.
  - Direct `@dir/photo.png` always includes the image.
  - Image under `.gitignore` is not loaded.

**Verify before continuing:** new tests pass; existing `at_file_completer` tests still pass.

**Depends on:** Phase 1.

---

## Phase 3 — Config: model capability flag

**Goal:** Mark which models support images and expose a capability helper.

**Files to modify:**
- `src/aye/model/config.py`
  - Add `"supports_images": True` to verified multimodal entries in `MODELS` (initial set per `issue.md` Section 5.1 — coordinate with backend before flipping flags).
  - Default for missing key is `False` (handled by reader, not by editing every entry).

- `src/aye/controller/llm_invoker.py`
  - Add `_model_supports_images(model_id: str) -> bool` reusing existing `_get_model_config`.
  - Do **not** wire it into the call path yet — that happens in Phase 5.

**Files to create:** none.

**Tests to add:**
- `tests/test_model_capability.py`
  - `_model_supports_images` returns `True` for flagged models.
  - Returns `False` for unflagged models.
  - Returns `False` for unknown model IDs.

**Verify before continuing:** capability tests pass; existing model/config tests still pass.

**Depends on:** nothing (independent of Phases 1–2).

---

## Phase 4 — API: thread attachments through to the backend

**Goal:** Allow `cli_invoke` to send an optional `attachments` array. No client-side capability gating yet.

**Files to modify:**
- `src/aye/model/api.py`
  - Add optional `attachments: Optional[List[dict]] = None` parameter to `cli_invoke(...)`.
  - Include `attachments` in the JSON body **only when non-empty**.
  - Use field names exactly: `file_name`, `mime_type`, `data_b64`, `bytes_size`.
  - Ensure image bytes are not logged in debug output.

- `src/aye/controller/llm_invoker.py`
  - Add optional `attachments` parameter to `invoke_llm(...)`.
  - Forward to `cli_invoke(...)` unchanged.
  - Pass `attachments` in the `params` dict to local plugins so they can reject cleanly.

**Files to create:** none.

**Tests to add:**
- `tests/test_api_attachments.py`
  - Text-only request body is unchanged when `attachments` is empty/None.
  - Image request body includes `attachments` with correct field names.
  - Image bytes do not appear in debug log output.

**Verify before continuing:** new tests pass; existing API tests still pass.

**Depends on:** Phase 1 (for `ImageAttachment` shape reference, even if dicts are passed).

---

## Phase 5 — REPL wiring and capability gating

**Goal:** Connect the pieces. Extract attachments from the plugin response, apply context-selection rule, gate by model capability, send via API.

**Files to modify:**
- `src/aye/controller/repl.py`
  - After `parse_at_references` call:
    - `attachments = at_response.get("attachments", [])`
    - `used_at = bool(explicit_files)` (source refs only — images do not suppress search).
  - If `attachments` is non-empty:
    - Check `_model_supports_images(conf.selected_model)`.
    - If unsupported, print error and skip the API call.
    - Otherwise, forward `attachments` to `invoke_llm(...)`.
  - Print attachment summary line for each image.

- `src/aye/controller/llm_invoker.py`
  - Add capability check at top of image-bearing path (defensive, in case REPL didn't check).
  - Refuse the request with a clear error if model is unsupported.

- `src/aye/presenter/repl_ui.py`
  - Add `print_attachment_summary(name, mime, size_bytes)` helper.

**Files to create:** none.

**Tests to add:**
- `tests/test_repl_image_flow.py` (mock plugin manager + `invoke_llm`)
  - Image-only prompt: `explicit_source_files` stays `None`, attachments forwarded.
  - Source-only prompt: existing behavior preserved.
  - Mixed prompt: explicit sources passed, search skipped, attachments forwarded.
  - Unsupported model + image: error printed, no API call.

**Verify before continuing:** REPL flow tests pass; existing REPL tests still pass.

**Depends on:** Phases 1, 2, 3, 4.

---

## Phase 6 — Defensive guards and local/offline rejection

**Goal:** Cover edge cases so users get clean errors instead of crashes.

**Files to modify:**
- `src/aye/controller/command_handlers.py`
  - In `handle_with_command`, detect image extensions in the file list before reading.
  - Reject with a clear message (Section 1.5 of `issue.md`). Do not call the LLM.

- `src/aye/plugins/offline_llm.py`
  - In `local_model_invoke` handler, check for `attachments` in params.
  - If present and non-empty, return a clear unsupported-image error via `create_error_response`.

- `src/aye/plugins/local_model.py`
  - Same guard as offline_llm: return clear unsupported-image error if `attachments` non-empty.

**Files to create:** none.

**Tests to add:**
- `tests/test_with_image_guard.py`
  - `with screenshot.png: ...` is rejected with clear message.
  - `with main.py: ...` still works (no regression).

- `tests/test_local_offline_image_rejection.py`
  - `local_model_invoke` with attachments returns error, does not crash.
  - `offline_llm` invocation with attachments returns error, does not crash.

**Verify before continuing:** all guards pass; full test suite green.

**Depends on:** Phases 1, 4 (attachments must be passable through params).

---

## Optional Phase 7 — Docs and help text

**Goal:** Document only the `@image` UX. No mention of clipboard, URLs, `image` command, or `with` image support.

**Files to modify:**
- `README.md` — add a short "Attach images with `@`" section with one or two examples.
- `src/aye/controller/command_handlers.py` (or wherever `help` text lives) — add a single example line.

**Tests to add:** none (docs only).

**Depends on:** Phases 1–6 complete and verified.

---

## Phase Summary Table

| # | Title | Files Changed | Files Added | Risk |
|---|---|---|---|---|
| 1 | Foundation | 0 | 1 | Low — pure helpers |
| 2 | Plugin image detection | 1 | 0 | Medium — touches `@` parsing |
| 3 | Model capability flag | 2 | 0 | Low — config + helper |
| 4 | API plumbing | 2 | 0 | Low — additive parameters |
| 5 | REPL wiring + gating | 3 | 0 | Medium — connects all pieces |
| 6 | Defensive guards | 3 | 0 | Low — guard clauses only |
| 7 | Docs | 2 | 0 | None |

**Recommended order:** strictly 1 → 2 → 3 → 4 → 5 → 6 → 7.

Phases 1 and 3 are independent and could be parallelized if needed, but keeping them sequential keeps each LLM turn small and the diff easy to review.

---

## Per-phase generation tips

- When asking the assistant to implement a phase, attach **only** the files listed in that phase's "Files to modify" plus any directly-imported helpers.
- Reference `issue.md` for behavioral details rather than restating them in the prompt.
- After each phase, run the targeted tests before moving on. Catching a regression in Phase 2 is much cheaper than catching it in Phase 6.
