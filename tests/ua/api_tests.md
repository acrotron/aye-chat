# User Acceptance Tests for API Functionality in Aye

This document outlines user acceptance tests (UATs) for API-related functionality in Aye, implemented in `aye/model/api.py`. The `api.py` module handles HTTP requests to the Aye API for chat invokes, plugin manifests, and server time. (Note: api_driver.py has been refactored or removed; parallel workflows are now handled differently.) Tests focus on successful API calls, error handling (e.g., no token, network failures), polling for responses, and workflow execution. These are exercised via CLI commands or programmatic calls that depend on API endpoints.

## Test Environment Setup
- Ensure Aye is installed and configured with a valid API URL (via `AYE_CHAT_API_URL` env var or default).
- Use a test token (set `AYE_TOKEN` env var) or simulate login via `aye auth login`.
- Mock or use a test server for API calls to avoid real network dependencies in isolated tests.
- For parallel tests, ensure no real data is affected (use dry_run modes).
- Run tests in a terminal, observing prints and exceptions.

## Test Cases

### 1. Chat Invoke API (cli_invoke)

#### UAT-1.1: Successful Chat Invoke with Immediate Response
- **Given**: Valid token is set, and API returns response immediately (no polling).
- **When**: The user runs a generate or chat command that calls `cli_invoke` with `message`, `chat_id`, `source_files`, and `model`.
- **Then**: The API call succeeds, returns JSON with `assistant_response` and `chat_id`, and no polling occurs.
- **Verification**: Check that the response contains expected keys and no exceptions are raised. In chat, verify the assistant response is displayed.

#### UAT-1.2: Chat Invoke with Polling for Response
- **Given**: Valid token, API returns a `response_url` for polling.
- **When**: The user initiates a long-running chat message.
- **Then**: The system polls the URL until a 200 response is received, then returns the JSON payload.
- **Verification**: Ensure polling respects intervals, times out after 900s, and handles 404/403 interim statuses gracefully.

#### UAT-1.3: Chat Invoke Network Failure
- **Given**: Valid token, but network is offline or server unreachable.
- **When**: The user attempts a generate command.
- **Then**: The API call raises an exception with a descriptive error message.
- **Verification**: Confirm the error is caught and displayed (e.g., in CLI or chat), and the command fails without crashing.

#### UAT-1.4: Chat Invoke No Token
- **Given**: No token available (not logged in).
- **When**: The user runs `aye generate "Test"`.
- **Then**: The API call raises `RuntimeError` with "No auth token".
- **Verification**: Ensure the error propagates to user as an authorization error in the UI.

### 2. Plugin Manifest API (fetch_plugin_manifest)

#### UAT-2.1: Successful Plugin Manifest Fetch
- **Given**: Valid token, server returns plugin data JSON.
- **When**: The system fetches plugins during login.
- **Then**: The API returns a dict of plugins with content and hashes.
- **Verification**: Confirm manifest is used in `fetch_plugins` to download/update plugins, and success message is printed.

#### UAT-2.2: Plugin Manifest API Error
- **Given**: Valid token, but API returns error (e.g., 500).
- **When**: The login process calls `fetch_plugin_manifest`.
- **Then**: Raises exception with error message from API.
- **Verification**: Ensure plugin download is skipped, and error is handled/displayed during login.

### 3. Server Time API (fetch_server_time)

#### UAT-3.1: Successful Server Time Fetch
- **Given**: Valid token, server responds with timestamp.
- **When**: The API driver or manual call fetches server time.
- **Then**: Returns an integer Unix timestamp.
- **Verification**: Use in manifest expiry checks; verify timestamp is reasonable (within seconds of current time).

#### UAT-3.2: Server Time API Failure
- **Given**: Valid token, but API fails.
- **When**: Fetching server time in dry_run.
- **Then**: Raises exception with API error.
- **Verification**: Ensure fallback or error handling in calling code.

### 4. API Workflow (Refactored from Parallel Driver)

#### UAT-4.1: Successful Login, Fetch, Chat, Logout
- **Given**: Valid token provided, server responsive.
- **When**: The user runs login, chat, and logout commands sequentially.
- **Then**: Executes login (stores token), fetches plugins, processes chat, then logout, printing success messages.
- **Verification**: Confirm all steps complete successfully, chat response is returned, and token is removed at end. No race conditions or exceptions.

#### UAT-4.2: Workflow with One Failure
- **Given**: Valid token, but one API (e.g., chat) fails.
- **When**: Running workflow.
- **Then**: Other tasks continue, failed task raises exception, but workflow attempts to complete.
- **Verification**: Check that logout still runs, and errors are printed for failed tasks.

#### UAT-4.3: Workflow No Token
- **Given**: No token provided or invalid.
- **When**: Running workflow.
- **Then**: Login raises RuntimeError, workflow fails early.
- **Verification**: Ensure no partial execution; all dependent calls check token.

## Notes
- Tests rely on API endpoints being available; use dry_run=True for non-destructive calls.
- Error messages are based on code inspection (e.g., from _check_response and exceptions in api.py).
- Security: Avoid logging real tokens; use placeholders.
- Parallel tests may need threading/multiprocessing setup to simulate real concurrency.
- All API calls use HTTPS and verify SSL by default.
