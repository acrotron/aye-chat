# User Acceptance Tests for Download Plugins Functionality in Aye

This document outlines user acceptance tests (UATs) for the plugin download functionality in Aye, implemented in `aye/model/download_plugins.py`. The `fetch_plugins` function fetches the plugin manifest from the server, downloads and installs plugins if hashes don't match, and updates the local manifest. This is triggered during authentication (e.g., `aye auth login`) and supports dry_run mode for testing. Tests focus on successful downloads, error handling (e.g., network issues, invalid tokens), hash verification, and plugin loading in the chat interface.

## Test Environment Setup
- Ensure Aye is installed and accessible via CLI.
- Use a test account or mock environment for authentication (set `AYE_TOKEN` env var if needed).
- Clear the `~/.aye/plugins/` directory between tests to avoid cached plugins.
- Simulate network conditions (e.g., offline) for error tests.
- Run tests in a terminal where user input can be prompted for login.
- Verify plugin loading by starting `aye chat` and checking for loaded plugins in the output (e.g., "Plugins loaded: completer, shell_executor").

## Test Cases

### 1. Successful Plugin Download and Installation

#### UAT-1.1: First-Time Plugin Download After Login
- **Given**: No existing token, empty `~/.aye/plugins/` directory, and valid server response with plugins.
- **When**: The user runs `aye auth login`, enters a valid token, and the system proceeds to download plugins.
- **Then**: The system stores the token, downloads plugins to `~/.aye/plugins/`, updates `manifest.json`, and displays success messages. Plugins are loaded in subsequent chat sessions (e.g., `aye chat` shows "Plugins loaded: completer, shell_executor").
- **Verification**: Check that plugin files exist in `~/.aye/plugins/`, manifest.json contains hash and timestamp data, and no errors occur. Confirm plugins are functional (e.g., tab completion in chat).

#### UAT-1.2: Plugin Download with Existing Plugins (Hash Match)
- **Given**: Valid token stored, plugins already in `~/.aye/plugins/` with matching hashes in manifest.json.
- **When**: The user runs `aye auth login` with a valid token.
- **Then**: The system skips downloading unchanged plugins, updates manifest timestamps, and proceeds without errors.
- **Verification**: Confirm plugins remain in place, manifest.json is updated with new timestamps, and chat session loads plugins without re-downloading.

#### UAT-1.3: Plugin Download with Hash Mismatch
- **Given**: Valid token stored, plugins in `~/.aye/plugins/` with outdated hashes in manifest.json.
- **When**: The user runs `aye auth login` with a valid token.
- **Then**: The system re-downloads mismatched plugins, overwrites files, and updates manifest with new hashes.
- **Verification**: Check that modified plugin files are replaced, manifest.json reflects new hashes, and no stale plugins are used in chat.

### 2. Error Handling and Edge Cases

#### UAT-2.1: No Token Available
- **Given**: No token stored (empty `~/.ayecfg` or no `AYE_TOKEN` env var).
- **When**: The system attempts to fetch plugins (e.g., during login or implicitly).
- **Then**: Plugin download is skipped gracefully, no error is raised, and the system continues (e.g., login succeeds without plugins).
- **Verification**: Confirm plugins directory remains empty or unchanged, and chat session starts without loaded plugins.

#### UAT-2.2: Network Failure During Manifest Fetch
- **Given**: Valid token stored, but network is offline or server is unreachable.
- **When**: The user runs `aye auth login`.
- **Then**: The system stores the token but fails plugin download, displaying an error (e.g., "Could not download plugins - Network error"), and exits gracefully.
- **Verification**: Check token is stored, plugins directory is not modified, and subsequent runs retry download on success.

#### UAT-2.3: Invalid Token or API Error
- **Given**: Invalid token stored (e.g., expired or fake).
- **When**: The user runs `aye auth login` or the system fetches plugins.
- **Then**: Plugin download fails with an API error (e.g., "Could not download plugins - [API error message]"), but token storage succeeds.
- **Verification**: Confirm plugins are not downloaded, manifest is not updated, and error is logged without crashing the command.

#### UAT-2.4: Corrupted or Missing Manifest
- **Given**: Valid token, but existing `manifest.json` is corrupted or missing.
- **When**: The user runs `aye auth login`.
- **Then**: The system fetches a new manifest, downloads all plugins, and creates/updates manifest.json.
- **Verification**: Check that all plugins are downloaded regardless of prior state, and manifest is regenerated correctly.

#### UAT-2.5: Dry Run Mode
- **Given**: Valid token, and fetch_plugins is called with dry_run=True (e.g., in tests or API calls).
- **When**: The system fetches plugins in dry run mode.
- **Then**: No actual downloads occur (files not written), but manifest is simulated/updated.
- **Verification**: Confirm plugins directory is unchanged, but API calls are made (for testing purposes).

### 3. Plugin Loading and Integration

#### UAT-3.1: Plugins Load in Chat Session
- **Given**: Plugins successfully downloaded and manifest updated.
- **When**: The user starts `aye chat`.
- **Then**: Plugins are discovered and loaded, with a message like "Plugins loaded: completer, shell_executor, auto_detect_mask".
- **Verification**: Confirm plugins are functional (e.g., auto-completion, shell commands in chat).

#### UAT-3.2: Fallback Without Plugins
- **Given**: Plugin download fails or no plugins available.
- **When**: The user starts `aye chat`.
- **Then**: Chat session starts normally without plugin features, no errors.
- **Verification**: Ensure core chat functionality works (e.g., model selection, history).

#### UAT-3.3: Plugin Updates on Re-Login
- **Given**: Existing plugins, but server has updated versions.
- **When**: The user logs out and logs in again with a valid token.
- **Then**: New plugin versions are downloaded based on hash changes.
- **Verification**: Check updated plugin files and manifest reflect server changes.

## Notes
- Tests rely on the API endpoint `/plugins` being available and returning valid JSON with plugin data.
- Error messages are based on code inspection (e.g., from exception handling in fetch_plugins); actual output may vary.
- Security: Ensure tokens are not logged in real tests; use placeholders.
- All downloads are to `~/.aye/plugins/`, which is wiped on full re-fetch to prevent conflicts.
- Tests should verify idempotency: multiple logins don't cause issues if plugins are up-to-date.
- For integration tests, run in a controlled environment to avoid affecting user data.
