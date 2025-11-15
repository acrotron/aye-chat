# User Acceptance Test Plan for Demo Token Functionality

## Introduction

The demo token functionality in the Aye CLI tool (ayechat package) is designed to provide a seamless experience for users who haven't yet obtained or configured a real personal access token from https://ayechat.ai. When no token is found in the environment variable (`AYE_TOKEN`) or the config file (`~/.ayecfg`), the tool automatically generates a temporary demo token (e.g., `aye_demo_abc123defg`) using a hash of the current timestamp. This token is persisted to the config file and allows limited or dry-run interactions with the API (e.g., via `dry_run=True` in API calls).

The purpose of this test plan is to validate that the demo token behaves correctly from a user's perspective, ensuring easy onboarding without requiring immediate authentication while gracefully handling transitions to real tokens.

## Scope

- **In Scope**: Token generation, storage/retrieval, API invocation with demo token, override by login, removal on logout.
- **Out of Scope**: Full end-to-end API responses (as they depend on the backend), security audits of token generation, or non-demo auth flows.

## Test Objectives

1. Verify automatic demo token generation on first use.
2. Confirm persistence across CLI sessions and commands.
3. Ensure API calls (e.g., chat invoke, plugin fetch) succeed or handle demo mode appropriately.
4. Validate that real token login overrides the demo token.
5. Test logout removes the token, triggering new demo generation on next use.
6. Check edge cases like env var overrides and config file corruption.

## Test Environment

- **OS**: Linux/macOS/Windows (tested on Ubuntu 22.04 and macOS Ventura).
- **Python**: 3.10+ with ayechat package installed (`pip install ayechat` or from source).
- **Setup**: Fresh user environment (no `~/.ayecfg` initially). Use `AYE_TOKEN` env var for override tests.
- **Tools**: Terminal for CLI execution; `cat ~/.ayecfg` to inspect config; mock backend if needed (but prefer real for acceptance).
- **Preconditions**: Backend API at https://api.ayechat.ai is reachable; no real token configured.

## Test Scenarios and Cases

### Scenario 1: Initial Demo Token Generation
**Description**: User runs Aye CLI without prior auth; demo token should be created and stored.

| Test Case ID | Steps | Expected Result | Pass/Fail Criteria |
|--------------|-------|-----------------|---------------------|
| TC-DEM-001 | 1. Delete `~/.ayecfg` if exists.<br>2. Run `aye chat` or `aye auth login` (but don't enter token).<br>3. Check if command proceeds (may show demo mode).<br>4. Inspect `~/.ayecfg`. | - Demo token generated (format: `aye_demo_[10-char-hash]`).<br>- Stored under `[default]` section as `token=aye_demo_...`.<br>- File permissions: 0600.<br>- CLI starts REPL or shows no auth error. | Token present and unique; no errors in stderr. |
| TC-DEM-002 | 1. Run `python -c "from aye.model.auth import get_token; print(get_token())"`. | Prints a demo token; subsequent runs print the same (persisted). | Token starts with `aye_demo_`; consistent across runs. |

### Scenario 2: Persistence and Usage in API Calls
**Description**: Demo token enables basic API interactions (e.g., dry-run mode).

| Test Case ID | Steps | Expected Result | Pass/Fail Criteria |
|--------------|-------|-----------------|---------------------|
| TC-DEM-003 | 1. With demo token active.<br>2. Run `aye chat` and send a message (e.g., "Hello").<br>3. Or run `python aye/model/api.py` (with dry_run). | - API call to `/invoke_cli` or `/plugins` uses demo token in `Authorization: Bearer aye_demo_...`.<br>- Response handled (e.g., spinner shows, summary printed; may indicate demo limits).<br>- No 401/403 auth errors. | HTTP headers include demo token; response JSON parsed without auth exceptions. Check via debug logs if needed. |
| TC-DEM-004 | 1. Run multiple commands: `aye snap history`, `aye config list`.<br>2. Restart terminal/session. | Demo token reused; no regeneration unless deleted. | Token unchanged in `~/.ayecfg`; API calls succeed consistently. |

### Scenario 3: Override with Real Token via Login
**Description**: User logs in with real token; demo should be replaced.

| Test Case ID | Steps | Expected Result | Pass/Fail Criteria |
|--------------|-------|-----------------|---------------------|
| TC-DEM-005 | 1. Demo token active.<br>2. Run `aye auth login`.<br>3. Enter a mock/real token (e.g., from https://ayechat.ai).<br>4. Run `python -c "from aye.model.auth import get_token; print(get_token())"`. | - Token updated to real one in `~/.ayecfg`.<br>- Demo token no longer used.<br>- CLI echoes "✅ Token saved." | New token stored; starts with user-provided value, not `aye_demo_`. |
| TC-DEM-006 | 1. After login.<br>2. Run API call (e.g., `aye chat`). | Uses real token; full features if valid. | No demo prefix; backend responds accordingly (e.g., no dry_run forced). |

### Scenario 4: Logout and Demo Regeneration
**Description**: Logout removes token; next use generates fresh demo.

| Test Case ID | Steps | Expected Result | Pass/Fail Criteria |
|--------------|-------|-----------------|---------------------|
| TC-DEM-007 | 1. Real or demo token active.<br>2. Run `aye auth logout`.<br>3. Inspect `~/.ayecfg` (token key removed).<br>4. Run CLI command (e.g., `aye chat`). | - Token deleted from config (file may persist with other keys or be removed if empty).<br>- New demo token generated on next use.<br>- Echoes "🔐 Token removed." | `token` absent from config; new demo token created and different from previous. |
| TC-DEM-008 | 1. After logout.<br>2. Set `export AYE_TOKEN=override_demo`.<br>3. Run CLI. | Env var overrides; no file change or demo generation. | Uses `override_demo`; config unchanged. |

### Scenario 5: Edge Cases and Error Handling
**Description**: Handle invalid/missing configs or env vars.

| Test Case ID | Steps | Expected Result | Pass/Fail Criteria |
|--------------|-------|-----------------|---------------------|
| TC-DEM-009 | 1. Corrupt `~/.ayecfg` (e.g., invalid JSON/INI).<br>2. Run CLI. | Parses gracefully (ignores bad lines); generates demo if no valid token. | No crash; demo token created. Check stderr for warnings. |
| TC-DEM-010 | 1. Set `AYE_TOKEN=` (empty).<br>2. Run CLI. | Treats as no token; generates demo and stores it (unless env takes precedence). | Demo generated; stored in file. |
| TC-DEM-011 | 1. Run in read-only home dir (simulate via chmod).<br>2. Attempt CLI run. | Generates demo in memory (not stored); API uses it. Warns on save failure. | No crash; token used for session. |

## Acceptance Criteria

- All test cases pass without critical errors (auth failures, crashes).
- Demo token enables basic CLI usage (chat, plugins) in dry-run/demo mode.
- Transitions to/from real tokens are seamless.
- Coverage: 100% of core paths in `aye/model/auth.py#get_token()`.

## Risks and Mitigations

- **Risk**: Backend rejects demo tokens harshly. **Mitigation**: Use `dry_run=True` in tests; monitor API responses.
- **Risk**: Hash collisions or predictability. **Mitigation**: Time-based MD5 is fine for demo; not security-critical.
- **Risk**: Cross-platform config parsing. **Mitigation**: Test on multiple OS.

## Execution and Reporting

- **Executor**: QA/Dev team.
- **Timeline**: 1-2 hours per scenario.
- **Tools**: Terminal, `pytest` for automation if extended.
- **Report**: Log results in table format; attach screenshots of config file and API headers (via `curl -v` simulation).

This plan ensures the demo token feature delights new users by reducing friction in initial interactions.
