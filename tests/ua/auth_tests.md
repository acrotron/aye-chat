# User Acceptance Tests for Auth Commands in Aye

This document outlines user acceptance tests (UATs) for authentication-related commands in Aye. These commands are executed from the CLI: `aye auth login` and `aye auth logout`. Tests are based on the functionality implemented in `aye/__main__.py`, `aye/controller/commands.py`, and `aye/model/auth.py`, focusing on token management, plugin downloads, and error handling.

## Test Environment Setup
- Ensure Aye is installed and accessible via CLI.
- Use a clean environment (e.g., no existing `~/.ayecfg` file or `AYE_TOKEN` env var).
- For testing with real tokens, obtain a valid token from https://ayechat.ai (use a test account if possible).
- Mock or simulate network conditions for plugin download tests (e.g., using offline mode or network tools).
- Run tests in a terminal environment where user input can be simulated.

## Test Cases

### 1. Login Command

#### UAT-1.1: Successful Login with Valid Token
- **Given**: No existing token is stored, and the user has a valid personal access token.
- **When**: The user runs `aye auth login` and enters a valid token when prompted (input hidden).
- **Then**: The system stores the token securely in `~/.ayecfg`, displays a success message (e.g., "✅ Token saved."), and attempts to download plugins, showing success (e.g., "Plugins fetched and updated." if plugins are available).
- **Verification**: Check that `~/.ayecfg` contains the token under `[default]`, and the file has correct permissions (e.g., 0o600). If plugins are downloaded, verify they exist in `~/.aye/plugins/`.

#### UAT-1.2: Login with Invalid Token
- **Given**: No existing token is stored.
- **When**: The user runs `aye auth login` and enters an invalid token.
- **Then**: The system stores the token anyway (as it's just stored without validation), displays a success message, but fails to download plugins, showing an error (e.g., "Error: Could not download plugins - [API error message]").
- **Verification**: Confirm token is stored, but plugin download fails gracefully without crashing.

#### UAT-1.3: Login When Token Already Exists
- **Given**: A valid token is already stored (e.g., via previous login).
- **When**: The user runs `aye auth login` and enters a new token.
- **Then**: The system overwrites the existing token with the new one, displays success, and attempts plugin download.
- **Verification**: Check that the old token is replaced in `~/.ayecfg`.

#### UAT-1.4: Login with Network Failure During Plugin Download
- **Given**: No existing token, but network connectivity issues (e.g., simulate offline).
- **When**: The user runs `aye auth login` and enters a valid token.
- **Then**: The system stores the token and displays success for token saving, but shows an error for plugin download (e.g., "Error: Could not download plugins - Network error").
- **Verification**: Ensure the command exits gracefully without deleting the stored token.

#### UAT-1.5: Login Cancelled by User
- **Given**: No existing token.
- **When**: The user runs `aye auth login` but cancels the prompt (e.g., Ctrl+C or empty input).
- **Then**: The system does not store any token and exits without error.
- **Verification**: Confirm no changes to `~/.ayecfg`.

#### UAT-1.6: Login with Environment Variable Override
- **Given**: `AYE_TOKEN` environment variable is set to a valid token.
- **When**: The user runs `aye auth login`.
- **Then**: The system prioritizes the env var and skips prompting, proceeding to plugin download.
- **Verification**: Confirm no prompt appears, and token from env var is used (plugins download if token is valid).

### 2. Logout Command

#### UAT-2.1: Successful Logout When Token Exists
- **Given**: A token is stored in `~/.ayecfg`.
- **When**: The user runs `aye auth logout`.
- **Then**: The system removes the token from `~/.ayecfg`, displays "🔐 Token removed.", and if the file becomes empty, deletes it.
- **Verification**: Check that `~/.ayecfg` no longer contains the token; file may be deleted if no other config exists.

#### UAT-2.2: Logout When No Token Exists
- **Given**: No token is stored (empty or missing `~/.ayecfg`).
- **When**: The user runs `aye auth logout`.
- **Then**: The system displays "🔐 Token removed." (idempotent behavior).
- **Verification**: Confirm no errors and no changes to files.

#### UAT-2.3: Logout Preserves Other Config
- **Given**: `~/.ayecfg` contains token and other settings (e.g., selected_model).
- **When**: The user runs `aye auth logout`.
- **Then**: The system removes only the token, preserves other settings, and keeps the file.
- **Verification**: Check that non-token settings remain in `~/.ayecfg`.

#### UAT-2.4: Logout with Environment Variable Set
- **Given**: `AYE_TOKEN` is set, but no file-based token.
- **When**: The user runs `aye auth logout`.
- **Then**: The system displays "🔐 Token removed." but notes that env var remains (since it doesn't control env vars).
- **Verification**: Env var is unchanged; no file modifications.

## Notes
- Tests assume the API endpoints for plugin download are functional; use dry_run modes or mocks for isolated testing.
- Error messages are based on code inspection (e.g., from exception handling in `login_and_fetch_plugins` and `fetch_plugins`); actual output may vary.
- Security: Ensure tokens are handled securely; tests should not log or expose real tokens.
- All commands should handle exceptions gracefully, providing user-friendly messages.
- Tests should be run in a controlled environment to avoid affecting real authentication state.
