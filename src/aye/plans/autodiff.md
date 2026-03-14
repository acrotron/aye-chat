# autodiff.md — Plan: Auto-diff for LLM-applied changes (config on/off, default off)

## Goal
Add an **`autodiff`** user-config parameter (**off by default**) that, when enabled, automatically prints a `diff` for **every file modified by an LLM response**, immediately after the optimistic write is applied.

This preserves Aye Chat’s optimistic workflow while making changes reviewable without requiring the user to manually run `diff <file>`.

---

## Desired UX

### Default behavior (autodiff=off)
- No change to current behavior.
- LLM response prints summary and “files updated” message.
- User can manually run `diff <file>`.

### When enabled (autodiff=on)
After an LLM response applies updates:
1. Print assistant summary (existing).
2. Apply snapshot + file writes (existing).
3. Print “files updated” (existing).
4. **Automatically display diffs** for each written file:
   - File A diff
   - File B diff
   - …

Notes:
- Only diff files that are actually written (post ignore/strict-mode filtering).
- Prefer a clear header separating diffs from normal output.

---

## Configuration spec

### Config key
- **Key:** `autodiff`
- **Values:** `on|off` (also accept truthy: `true|1|yes`)
- **Default:** `off`

### Where it can be set
- Config file: `~/.ayecfg`
  - `autodiff=on`
- Environment variable override (recommended to match other toggles):
  - `AYE_AUTODIFF=on`

(Implementation should follow the precedent used by `block_ignored_file_writes` in `model/write_validator.py`.)

---

## Where to hook auto-diff

### Primary hook point
**`controller/llm_handler.py::process_llm_response`**

Rationale:
- This is already the centralized place where LLM updates are filtered, path-normalized, ignore-validated, and written via `apply_updates()`.
- It already has a `console` and `conf` available.

Flow today:
- Print summary
- `filter_unchanged_files`
- `make_paths_relative`
- ignore checks
- `apply_updates(updated_files, prompt)`
- print updated files

Required change:
- Capture the snapshot id returned from `apply_updates(...)`.
- If `autodiff` enabled, run diff for each file against the snapshot created in that same `apply_updates` call.

---

## Design: How to diff “current” vs “snapshot” reliably

### Current diff capability
- `presenter/diff_presenter.py::show_diff(file1, file2, is_stash_ref=False)` supports:
  1. **Regular filesystem paths** (diff two files)
  2. **GitRefBackend snapshot references** when `is_stash_ref=True` and `file2` is formatted like:
     - `ref:path` (or `ref1:path|ref2:path`)

### Snapshot backends in play
- `FileBasedBackend` (active today): snapshot stored under `.aye/snapshots/<batch>/...` and metadata maps original -> snapshot copy.
- `GitRefBackend` (future/optional): snapshot stored as git commit/ref; diff_presenter can extract file contents from snapshot.

### Key need
After `apply_updates`, we have a snapshot identifier (`batch_ts` / `batch_id`). We need a backend-agnostic way to obtain “the snapshot-side reference for this file in that batch”.

---

## Implementation plan (recommended)

### 1) Add config helper (new)
Create a small helper function (patterned after `is_strict_mode_enabled`) to check if autodiff is enabled.

Option A (keep it near other write-time policies):
- New module: `model/autodiff_config.py`
  - `AUTODIFF_KEY = "autodiff"`
  - `is_autodiff_enabled() -> bool` using `get_user_config(AUTODIFF_KEY, "off")`

Option B (keep it local):
- Add `is_autodiff_enabled()` inside `controller/llm_handler.py`

Recommendation: **Option A** for reuse/testing and to mirror `write_validator` style.

Environment variable support:
- If `get_user_config` already honors env vars (likely, given docs), no extra work.
- If not, explicitly check `os.environ.get("AYE_AUTODIFF")` first.

Acceptance criteria:
- With no config, returns False.
- With `autodiff=on`, returns True.

---

### 2) Expose a backend-agnostic “snapshot reference for file” API
Add a small helper in `model/snapshot/__init__.py` that returns what `diff_presenter.show_diff` needs.

Proposed API:
```python
def get_diff_base_for_file(batch_id: str, file_path: Path) -> tuple[str, bool]:
    """Return (snapshot_ref, is_git_ref).

    - If FileBasedBackend: snapshot_ref is a filesystem path to the snapshotted file.
    - If GitRefBackend: snapshot_ref is a 'ref:path' string.
    """
```

Implementation details per backend:

#### FileBasedBackend
- Use `.aye/snapshots/<batch_id>/metadata.json` to map `original` -> `snapshot`.
- Find the entry matching `file_path.resolve()`.
- Return:
  - `snapshot_ref = entry["snapshot"]`
  - `is_git_ref = False`

#### GitRefBackend
- Determine the refname for `batch_id` (e.g. `refs/aye/snapshots/<batch_id>`).
- Convert `file_path` to repo-relative posix path (GitRefBackend already has `_path_to_repo_rel_posix`, but it’s private).
- Return:
  - `snapshot_ref = f"{refname}:{repo_rel_path}"`
  - `is_git_ref = True`  (even though the parameter is currently called `is_stash_ref`)

Notes:
- This keeps `diff_presenter` unchanged.
- It also avoids duplicating metadata parsing logic in the controller.

Edge cases:
- If snapshot metadata is missing for a file, skip autodiff for that file and print a warning in verbose/debug mode.

---

### 3) Wire autodiff into `process_llm_response`
Update `controller/llm_handler.py::process_llm_response`:

1. Capture the returned batch id:
   - `batch_id = apply_updates(updated_files, prompt)`

2. If `is_autodiff_enabled()`:
   - Print a header, e.g. `"Auto-diff (autodiff=on):"`
   - For each updated file (in the final `updated_files` list):
     - Resolve current file path (relative to `conf.root` if needed).
     - Use `snapshot.get_diff_base_for_file(batch_id, Path(file_name))` to get `(base_ref, is_git_ref)`.
     - Call:
       - `diff_presenter.show_diff(file1=current_path, file2=base_ref, is_stash_ref=is_git_ref)`

Output formatting suggestions:
- Separate each file diff with a blank line.
- Optionally print the filename as a mini-header before each diff.

Important ordering:
- Autodiff should occur **after** apply_updates writes have completed.
- Only diff the files that were actually written (after strict ignore filtering).

---

### 4) Add a user-facing command to toggle autodiff
- Add a built-in `autodiff [on|off]` command similar to `verbose`.
- This would call `set_user_config("autodiff", value)`.

If omitted, config/env-only still satisfies the requirement.

---

## Testing plan

### Unit tests

1) Config parsing
- `autodiff` defaults to off.
- `autodiff=on` enables.
- Accept truthy variants.

2) FileBasedBackend diff base resolution
- Create a snapshot via `apply_updates`.
- Verify `get_diff_base_for_file(batch_id, file_path)` returns an on-disk snapshot file path that exists.

3) LLM handler integration (autodiff on)
- Patch:
  - `apply_updates` to return a known batch id
  - `snapshot.get_diff_base_for_file` to return a known ref
  - `diff_presenter.show_diff` to track calls
- Verify `show_diff` called once per updated file.

4) Strict ignore interaction
- If strict mode blocks some files, ensure autodiff only runs for allowed/written files.

### Manual tests

- Set `autodiff=on` in `~/.ayecfg`.
- Make an LLM prompt that updates 2+ files.
- Confirm:
  - “Files updated” shows
  - Then diffs display for each file
  - `restore` still works.

---

## Performance & safety considerations

- Diffing many large files can be noisy/slow.
  - Keep default off.
  - Consider a future safeguard like `autodiff_max_files` or only show diffs under a size threshold.

- Diff presenter uses Rich with `force_terminal=True`; output in non-TTY contexts may be verbose.
  - Acceptable given the feature is opt-in.

---

## Summary of code touch points

- **New:** `model/autodiff_config.py` for `is_autodiff_enabled()`
- **Update:** `model/snapshot/__init__.py` to add `get_diff_base_for_file(batch_id, file_path)`
- **Update:** `controller/llm_handler.py` to:
  - store `batch_id = apply_updates(...)`
  - run autodiff loop when enabled
- **No change required:** `presenter/diff_presenter.py` (reuse existing API)
