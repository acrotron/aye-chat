# Plan: SonarQube Findings Fetch Plugin (`sq`)

## Goal
Add an Aye Chat plugin similar to `plugins/fetch_github_issue.py` that retrieves SonarQube/SonarCloud "findings" (issues) via the SonarQube Web API and returns structured JSON suitable for pasting into the chat context.

Non-goals (initial version):
- Writing/patching project files based on findings.
- Deep code snippet extraction beyond basic locations.
- Full parity with every SonarQube filter option.

---

## User experience
### New plugin command
- **Command name:** `sq`
- **Primary usage (project-level):**
  - `sq <projectKey>`
- **URL usage (optional):**
  - `sq <sonarqube-issues-url>`

### Example calls
- `sq my_project_key`
- `sq https://sonarcloud.io/project/issues?id=my_org_my_project`
- `sq my_project_key --severities=CRITICAL --types=BUG,VULNERABILITY --statuses=OPEN,CONFIRMED`

> **Flag parsing:** `--key=value` tokens on the command line are parsed by the REPL command dispatcher into the structured `params` dict described in *`on_command` contract* below. Flag names **must match** the `params` keys (e.g. `--severities`, not `--severity`). Comma-separated values are accepted for list-valued params.

### Returned data shape (plugin response)
Plugin `on_command` should return:
- `{"status": "success", "summary": "...", "data": <dict>}` on success
- `{"status": "error", "summary": "..."}` on error

`summary` is a short human-readable string (e.g. `"Fetched 123 SonarQube issues for my_project_key"`) for consistency with other Aye plugins.

Where `data` is a JSON-serializable dict like:
```json
{
  "server": "https://sonarcloud.io",
  "project": {"key": "my_project_key"},
  "query": {
    "severities": ["CRITICAL"],
    "types": ["BUG"],
    "resolved": false
  },
  "total": 123,
  "page": 1,
  "page_size": 50,
  "issues": [
    {
      "key": "AX...",
      "rule": "python:S1234",
      "severity": "CRITICAL",
      "type": "BUG",
      "status": "OPEN",
      "component": "my_project:src/foo.py",
      "file": "src/foo.py",
      "line": 42,
      "message": "...",
      "effort": "10min",
      "tags": ["..."],
      "creationDate": "...",
      "updateDate": "..."
    }
  ]
}
```

---

## Configuration and authentication
SonarQube uses **Basic Auth** with:
- username = token
- password = empty

### Proposed config sources (priority order)
1. Environment variables (recommended for secrets)
   - `AYE_SONARQUBE_URL`
   - `AYE_SONARQUBE_TOKEN` *(optional)*
2. `~/.ayecfg` via `aye.model.auth.get_user_config()`
   - `sonarqube_url`
   - `sonarqube_token` *(optional)*

### Server URL resolution (precedence)
1. If the user input is a full URL (e.g. `https://sonarcloud.io/project/issues?id=...`), the **URL's origin** (`scheme://host[:port]`) is used as the server URL, overriding env/config.
2. Otherwise, use `AYE_SONARQUBE_URL` (env) or `sonarqube_url` (config).
3. If neither is available, return a `status=error` response with a clear message.

### Token handling
- Token is **optional**. If provided, Basic Auth is used: `auth=(token, "")`.
- If not provided, the plugin performs an **anonymous** request. This works for public SonarCloud projects and self-hosted instances that allow anonymous read; private projects will typically return 401/403, and the plugin should surface a clear `status=error` summary in that case.

### SSL verification
Follow existing project practice (see `model/version_checker.py`):
- Respect `sslverify` config/env via `get_user_config("sslverify", "on")`.
- Pass `verify=<bool>` into `httpx`.

---

## URL detection / parsing
Support both:
1. **Direct project key** (simple)
2. **Issues URL** pasted from SonarQube UI

### SonarQube UI URL patterns (common)
Self-hosted SonarQube often uses:
- `https://sonar.example.com/project/issues?id=<projectKey>`

SonarCloud commonly uses:
- `https://sonarcloud.io/project/issues?id=<projectKey>`

Optional filters sometimes appear as query params:
- `severities=CRITICAL,BLOCKER`
- `types=BUG,VULNERABILITY,CODE_SMELL`
- `statuses=OPEN,CONFIRMED`
- `resolved=false`
- `pullRequest=<id>`
- `branch=<branchName>`
- `open=<issueKey>` *(ignored by the parser in v1)*

### Parsing plan
- Implement a regex for `https?://.../project/issues\?(.+)`.
- Parse query string using `urllib.parse.parse_qs`.
- Extract:
  - `id` as `projectKey`
  - optional `severities`, `types`, `statuses`
  - optional `resolved`
  - optional `pullRequest`, `branch`
- `open=<issueKey>` is recognized but **not used** in v1.
- If parsing fails, treat the input as a project key.

---

## SonarQube Web API endpoints to use
### Primary endpoint: search issues
- `GET /api/issues/search`

Key params:
- `componentKeys=<projectKey>`
- `severities=...` (comma-separated)
- `types=...` (comma-separated)
- `statuses=...` (comma-separated)
- `resolved=false` (default for "open" findings; always sent unless user overrides via `resolved` param)
- `p=<page>`
- `ps=<page_size>`
- `branch=<branch>` (optional)
- `pullRequest=<id>` (optional)

Docs reference (for implementer):
- SonarQube Web API \u2192 Issues \u2192 `api/issues/search`

### Optional (nice-to-have) endpoint: component info
- `GET /api/components/show?component=<projectKey>`
Used to validate project exists and to include display name.

### Optional (nice-to-have) endpoint: rule details
- `GET /api/rules/show?key=<ruleKey>`
Only if we want to enrich each issue (may be slow; consider an opt-in flag).

---

## Plugin interface design
### File
- Add `plugins/sonarqube.py`.

### Class
- `class FetchSonarQubeFindingsPlugin(Plugin):`
  - `name = "sq"`
  - `version = "1.0.0"` (optional but consistent with other plugins)
  - `premium = "free"`

### `on_command` contract
Accept `params` keys:
- `input`: the raw string user provided (projectKey or URL)
- `url`: optional explicit URL (alternate)
- `project_key`: optional explicit project key
- `severities`: optional list or comma-string
- `types`: optional list or comma-string
- `statuses`: optional list or comma-string
- `resolved`: optional bool (default `False` \u2192 sends `resolved=false`, i.e. unresolved findings only)
- `branch`: optional string
- `pull_request`: optional string/int
- `page_size`: optional int (default 50; cap at 500 per SonarQube)
- `max_pages`: optional int (default 1) to avoid huge payloads
- `max_total`: optional int (default 500) \u2014 hard cap on total issues returned across pages
- `verbose`: bool

Return:
- Success: `{"status":"success", "summary": "...", "data": data}`
- Error: `{"status":"error", "summary": "..."}`

### Output limiting
To prevent overly large context payloads:
- Default `max_pages=1`, `page_size=50`, `max_total=500`.
- The plugin stops paginating as soon as either `max_pages` or `max_total` is reached.

---

## Implementation details (parallels with `fetch_github_issue.py`)
### HTTP client
- Use `httpx.Client(timeout=DEFAULT_TIMEOUT, verify=_ssl_verify())`.
- Add basic auth:
  - `auth=(token, "")` if token is set
  - if token missing, perform anonymous request (see *Token handling*)

### Error handling
Mirror GitHub plugin patterns:
- `ValueError` for invalid inputs / missing required config (e.g. no server URL resolvable)
- `httpx.HTTPStatusError` for non-2xx (401/403 should yield a clear summary about auth)
- `httpx.RequestError` for network problems

### Verbose logging
- Use module-level `rprint = rich.print` (patchable like GitHub plugin).
- Print:
  - server URL
  - project key
  - query filters
  - number of issues fetched
- **Never** print the token or `Authorization` header.

### Data shaping
Normalize SonarQube issue entries:
- Split `component` to derive a relative `file` if possible.
  - Many SonarQube components look like `<projectKey>:path/to/file`.
- Keep important fields only; do not dump entire API response by default.

---

## Changes in other parts of the codebase (optional)
### Auto-fetch URLs in prompts
If you want SonarQube URLs to be auto-fetched when pasted in a normal prompt (similar to GitHub issues), extend `controller/util.py`:
- Add a `SONARQUBE_ISSUES_URL_PATTERN`.
- In `handle_url()`, detect SonarQube URLs and call the plugin manager.

This is optional; the first version can rely on explicit command usage. Before implementing, confirm `controller/util.handle_url()` exists in the current codebase (that's the reference point from the GitHub plugin integration).

---

## Test plan
Add unit tests similar to existing plugin tests (if present in repo; otherwise create new ones).

### Suggested tests
1. **Parses project key input**
   - Input: `my_project`
   - Asserts request includes `componentKeys=my_project` and `resolved=false`.

2. **Parses SonarQube URL input**
   - Input: `https://sonarcloud.io/project/issues?id=my_project&severities=CRITICAL`
   - Asserts project key + severity parsed correctly and URL origin used as server URL.

3. **Authentication applied**
   - With token configured, assert Basic Auth header is used.
   - With no token, assert no `Authorization` header is sent.

4. **HTTP error handling**
   - 401/403 returns `status=error` with a clear summary.

5. **Pagination limiting**
   - With `max_pages=2`, fetches two pages and concatenates issues.
   - With `max_total=N`, stops early and returns no more than `N` issues.

6. **Missing server URL**
   - Project-key input with no `AYE_SONARQUBE_URL` / `sonarqube_url` configured returns `status=error`.

Mocking approach:
- Use `httpx.MockTransport` to avoid real network and avoid adding new dependencies.

---

## Security / privacy considerations
- Never print or log the Sonar token or `Authorization` header.
- Encourage env var usage for tokens.
- Respect `sslverify` config.
- Ensure returned JSON does not accidentally include server-side secrets (it shouldn't, but keep only needed fields).

---

## Step-by-step execution checklist
1. Create `plugins/sonarqube.py` with:
   - regex + query parsing helpers
   - `_ssl_verify()` helper (copy pattern from `model/version_checker.py`)
   - `fetch_sonarqube_findings(...)` function
   - `FetchSonarQubeFindingsPlugin` class (command name `sq`)

2. Validate plugin discovery works (it will, via `PluginManager.discover()` scanning `src/aye/plugins`).

3. Add tests under `tests/` for parsing + `httpx.MockTransport` mocking.

4. (Optional) Confirm `controller/util.handle_url()` exists, then extend it to auto-fetch SonarQube URLs.

5. Document usage in README or help text (optional).
