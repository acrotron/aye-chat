# User Acceptance Tests for Configuration Management in Aye

This document outlines user acceptance tests (UATs) for configuration management in Aye, implemented in `aye/model/config.py`. The module handles loading/saving user config from `.aye/config.json`, with functions for get/set/delete/list. Configurations include file masks, root directories, selected models, and other settings. Tests cover CLI config commands (`aye config list/get/set/delete`), persistence, and defaults. Emphasize user interactions via CLI to set/get configs that affect behavior (e.g., model selection in chat).

## Test Environment Setup
- Ensure `.aye/config.json` exists or can be created (clean slate for tests).
- Run CLI commands in a terminal.
- For chat-related configs (e.g., selected_model), test in `aye chat` session.
- Back up existing config if needed; restore after tests.

## Test Cases

### 1. Config List Command

#### UAT-1.1: List All Configurations When Config Exists
- **Given**: `.aye/config.json` has settings like {"file_mask": "*.py", "selected_model": "x-ai/grok"}.
- **When**: The user runs `aye config list`.
- **Then**: Displays "Current Configuration:" followed by key-value pairs.
- **Verification**: Check output includes all keys; empty if no config.

#### UAT-1.2: List Empty Configuration
- **Given**: No `.aye/config.json` or empty file.
- **When**: Running `aye config list`.
- **Then**: Displays "No configuration values set.".
- **Verification**: Confirm no crash; correct message.

### 2. Config Get Command

#### UAT-2.1: Get Existing Configuration Key
- **Given**: Config has "selected_model": "openai/gpt".
- **When**: The user runs `aye config get selected_model`.
- **Then**: Displays "selected_model: openai/gpt".
- **Verification**: Ensure correct value; case-sensitive.

#### UAT-2.2: Get Non-Existing Key
- **Given**: Config exists but key not present.
- **When**: Running `aye config get nonexistent`.
- **Then**: Displays "Configuration key 'nonexistent' not found.".
- **Verification**: No error, just message.

### 3. Config Set Command

#### UAT-3.1: Set New Configuration Key
- **Given**: Config may exist.
- **When**: The user runs `aye config set file_mask "*.py,*.js"`.
- **Then**: Sets value, saves to file, displays "Configuration 'file_mask' set to '*.py,*.js'.".
- **Verification**: Check file updated; permissions 0o600.

#### UAT-3.2: Set Existing Key (Overwrite)
- **Given**: Key exists with old value.
- **When**: Setting new value.
- **Then**: Overwrites, saves, confirms.
- **Verification**: Old value replaced in file.

#### UAT-3.3: Set Invalid Key Type
- **Given**: Code validates string keys.
- **When**: Attempting non-string key (though CLI passes strings).
- **Then**: Raises TypeError.
- **Verification**: Ensure validation; but CLI typically passes strings.

### 4. Config Delete Command

#### UAT-4.1: Delete Existing Key
- **Given**: Config has key.
- **When**: The user runs `aye config delete file_mask`.
- **Then**: Removes key, saves, displays "Configuration 'file_mask' deleted.".
- **Verification**: Key absent from file.

#### UAT-4.2: Delete Non-Existing Key
- **Given**: Key not in config.
- **When**: Running delete.
- **Then**: Displays "Configuration key 'key' not found.".
- **Verification**: No change to file.

### 5. Config Persistence and Loading

#### UAT-5.1: Config Loads on Startup
- **Given**: Existing `.aye/config.json`.
- **When**: Starting Aye (e.g., `aye chat`).
- **Then**: Loads config into memory.
- **Verification**: Config values affect behavior (e.g., selected model in chat).

#### UAT-5.2: Config Saves Changes
- **Given**: Config modified via set/delete.
- **When**: Running another command.
- **Then**: Changes persist across sessions.
- **Verification**: Restart Aye, confirm values loaded.

#### UAT-5.3: Config with JSON Values
- **Given**: CLI allows JSON-like values.
- **When**: Setting value like "{\"key\": \"value\"}" (escaped).
- **Then**: Parses as dict if code supports.
- **Verification**: Confirm complex values stored/retrieved.

### 6. Config in Chat Context

#### UAT-6.1: Model Config Affects Chat
- **Given**: selected_model set via config.
- **When**: Starting `aye chat`.
- **Then**: Uses configured model for API calls.
- **Verification**: Check model in session prompt or via `model` command.

## Notes
- Config file is `.aye/config.json`, created on first set.
- Permissions: File set to 0o600 for security.
- Defaults: Functions use default param if key missing.
- JSON parsing: Values can be strings or parsed JSON.
- Tests should clean up config file between runs.
