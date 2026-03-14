# `llm` command — local_model configuration (implementation plan)

This document describes how to add an interactive `llm` command to the Aye Chat REPL that configures the **OpenAI-compatible local_model** endpoint parameters.

Scope (for now):
- Add a new built-in REPL command: `llm`
- Running `llm` prompts the user for:
  - URL
  - API key
  - model
  Each prompt shows the current value (if any) and allows the user to press **Enter** to keep the existing value.
- Persist values into `~/.ayecfg` (the existing config file) under:
  - `llm_api_url`
  - `llm_api_key`
  - `llm_model`
- Add a way to clear these values from config.

Non-goals (we’ll do later): validation/test requests, presets (e.g., `--ollama`), session-only overrides, richer subcommands.

---

## Why this fits the current architecture

- The REPL already supports built-in commands and interactive prompting (see `model`, telemetry prompt, etc.).
- The config system already persists settings to `~/.ayecfg` via `set_user_config()` and reads them via `get_user_config()`.
- `get_user_config()` supports environment-variable override using `AYE_<KEY>`.
  - If we store `llm_api_url`, `llm_api_key`, `llm_model`, then env overrides become:
    - `AYE_LLM_API_URL`
    - `AYE_LLM_API_KEY`
    - `AYE_LLM_MODEL`
  - This matches the names that `plugins/local_model.py` already uses today.

---

## User experience

### 1) Configure/update: `llm`

When the user types `llm` (no args), Aye Chat prompts interactively:

1. **URL prompt**
   - Display the current value if configured.
   - If user presses Enter, keep existing value.

2. **Key prompt**
   - Same behavior, but:
     - Input should be hidden (password-style).
     - Do **not** print the key value to the terminal.
     - When showing “current value”, show a neutral marker such as `(set)` / `(not set)`.

3. **Model prompt**
   - Display current model value if configured.
   - Enter keeps existing.

After prompts, write the final values to `~/.ayecfg` under:
- `llm_api_url`
- `llm_api_key`
- `llm_model`

Finally, print a short confirmation:
- include URL and model
- for key, print `KEY: set (hidden)` or `KEY: not set`


#### Prompt text suggestion
- URL: `LLM API URL (current: <value>): `
- Key: `LLM API KEY (current: set/not set): ` (hidden input)
- Model: `LLM MODEL (current: <value>): `


### 2) Clear config: `llm clear`

Add a clearing path to remove the config values.

Syntax:
- `llm clear`

Behavior:
- Remove (or blank out) `llm_api_url`, `llm_api_key`, `llm_model` from `~/.ayecfg`.
- Print confirmation, e.g. `LLM config cleared.`

Implementation detail recommendation: prefer **removing keys** rather than setting them to empty strings.

---

## Implementation plan

### A) Add `llm` to the REPL built-in command list
File: `controller/repl.py`
- Extend `BUILTIN_COMMANDS` to include `"llm"`.
- In the main command dispatch `if/elif` chain, add a new branch:
  - `elif lowered_first == "llm": ...`

Telemetry:
- Record it like other built-ins:
  - `telemetry.record_command("llm", has_args=len(tokens) > 1, prefix=_AYE_PREFIX)`


### B) Implement a command handler
File: `controller/command_handlers.py`

Add a new function (patterned after existing handlers like `handle_model_command`):

- `handle_llm_command(session: Optional[PromptSession], tokens: list[str]) -> None`

Responsibilities:
1. If tokens include `clear`:
   - clear all 3 keys from config
   - print confirmation
   - return

2. Otherwise:
   - read current values via `get_user_config()`
   - prompt for new values
   - apply “Enter keeps old value” logic
   - persist with `set_user_config()`
   - print a short summary

Prompting approach:
- Use `PromptSession.prompt(...)` to keep REPL behavior consistent.
- Use hidden input for the key:
  - `session.prompt("...", is_password=True)` (prompt_toolkit supports password-style entry)

If `session` is not available (shouldn’t happen in the normal REPL flow), fall back to a simpler input method or just print usage; but in the current REPL architecture the handler can be passed the active `session`.


### C) Add config-key deletion helper
File: `model/auth.py`

`set_user_config()` can only set values today. For `llm clear`, add a small helper to delete keys cleanly.

Recommended addition:
- `delete_user_config(key: str) -> None`
  - parse config
  - `pop(key, None)`
  - write the updated config back
  - keep file mode `0o600`

This mirrors existing token deletion logic (`delete_token()`), but generalized.

Alternative (less clean): set keys to empty string. This works with truthiness checks but leaves empty values in the config file.


### D) Make `plugins/local_model.py` read from config (so `llm` actually affects behavior)

Currently `_handle_openai_compatible()` reads:
- `os.environ.get("AYE_LLM_API_URL")`
- `os.environ.get("AYE_LLM_API_KEY")`
- `os.environ.get("AYE_LLM_MODEL", ...)`

To make the new `llm` command effective without exporting environment variables, update it to read from the config system:
- `from aye.model.auth import get_user_config`
- `api_url = get_user_config("llm_api_url")`
- `api_key = get_user_config("llm_api_key")`
- `model_name = get_user_config("llm_model", "gpt-3.5-turbo")`

This preserves env override behavior automatically, because `get_user_config()` checks env vars first:
- `AYE_LLM_API_URL` overrides `llm_api_url`
- `AYE_LLM_API_KEY` overrides `llm_api_key`
- `AYE_LLM_MODEL` overrides `llm_model`


### E) Wire the handler into the REPL
File: `controller/repl.py`

- Import the new handler:
  - `from aye.controller.command_handlers import handle_llm_command`
- Add the `elif lowered_first == "llm":` branch to call:
  - `handle_llm_command(session, tokens)`

---

## Expected behavior after implementation

- User runs `aye chat`.
- User types `llm`.
- Aye prompts for URL, key, model; user can press Enter to keep each current value.
- Values are saved to `~/.ayecfg`.
- Subsequent LLM calls will route through `plugins/local_model.py` OpenAI-compatible handler (as long as URL and key are set), without requiring exported env vars.
- User can run `llm clear` to remove these values and revert to the default cloud API routing.

---

## Notes / edge cases (keep minimal for now)

- Do not echo secrets: never print the API key value.
- If URL or key is empty/unset after prompting, local_model should behave as disabled (return `None`) and the app should fall back to the cloud API path.
- No network validation is required in this iteration.
