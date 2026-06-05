# SonarQube Plugin (`sq`) \u2014 User Guide

The SonarQube plugin lets you pull findings (issues) from a SonarQube or
SonarCloud instance directly into your Aye Chat session, so the AI has
concrete, structured context about code quality problems in your project.

It mirrors the style of the built-in `fetch_github_issue` plugin but targets
the SonarQube Web API (`/api/issues/search`).

---

## 1. What it does

- Fetches open issues for a SonarQube project.
- Accepts either a **project key** or a full **SonarQube issues URL**.
- Supports filtering by severity, type, status, branch, and pull request.
- Returns structured JSON (project, server, query echo, list of issues)
  that can be fed back into the LLM or inspected directly.

Typical use cases:

- \u201c@sq my_project \u2014 triage the top BUG findings and propose fixes.\u201d
- \u201cFetch all CRITICAL vulnerabilities from SonarCloud and summarize them.\u201d
- \u201cUse SonarQube findings as a starting point for a cleanup PR.\u201d

---

## 2. Installation

The plugin ships as part of Aye Chat (once merged). No extra install step is
required \u2014 it is auto-discovered by the plugin manager from
`src/aye/plugins/sonarqube.py`.

To confirm it\u2019s loaded, start a chat with verbose on:

```
verbose on
```

On startup you should see a line like:

```
Plugins loaded: ..., sq, ...
```

---

## 3. Configuration

The plugin reads configuration from **environment variables** first, then
from the user config file `~/.ayecfg`.

### Required

| Setting        | Env var                | Config key        | Notes |
|----------------|------------------------|-------------------|-------|
| Server URL     | `AYE_SONARQUBE_URL`    | `sonarqube_url`   | Base URL of your SonarQube / SonarCloud instance (e.g. `https://sonarcloud.io` or `https://sonar.example.com`). |

> If you pass a full SonarQube **issues URL** to the command, its origin
> (`scheme://host[:port]`) is used as the server URL and overrides the
> env/config value for that call.

### Optional

| Setting     | Env var                | Config key          | Notes |
|-------------|------------------------|---------------------|-------|
| API token   | `AYE_SONARQUBE_TOKEN`  | `sonarqube_token`   | Required for private projects. Public SonarCloud projects may work anonymously. |
| SSL verify  | `AYE_SSLVERIFY`        | `sslverify`         | `on` (default) or `off`. Shared with the rest of Aye Chat. |

### How to set env vars

macOS / Linux:

```bash
export AYE_SONARQUBE_URL="https://sonarcloud.io"
export AYE_SONARQUBE_TOKEN="squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Windows (PowerShell):

```powershell
$env:AYE_SONARQUBE_URL = "https://sonarcloud.io"
$env:AYE_SONARQUBE_TOKEN = "squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### How to set via `~/.ayecfg`

Edit `~/.ayecfg` (flat `key=value` file) and add:

```
sonarqube_url=https://sonarcloud.io
sonarqube_token=squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Getting a SonarQube token

- **SonarCloud:** *My Account \u2192 Security \u2192 Generate Tokens*.
- **Self-hosted SonarQube:** *User menu \u2192 My Account \u2192 Security \u2192 Generate Tokens*.

The plugin authenticates via HTTP Basic Auth with `(token, "")`, which is
SonarQube\u2019s documented approach.

---

## 4. Using the `sq` command

The plugin registers a single command:

```
sq <projectKey-or-URL> [--flag=value ...]
```

### 4.1 By project key

```
sq my_project_key
```

Uses the server URL from env/config.

### 4.2 By SonarQube issues URL

Paste a URL copied from the SonarQube UI \u201cIssues\u201d page:

```
sq https://sonarcloud.io/project/issues?id=my_org_my_project
```

The plugin extracts:

- `id` \u2192 project key
- `severities`, `types`, `statuses`, `resolved`, `branch`, `pullRequest`
  if present in the URL query string

Any `open=<issueKey>` parameter in the URL is **ignored** in v1.

### 4.3 With filters

```
sq my_project_key --severities=CRITICAL,BLOCKER --types=BUG,VULNERABILITY --statuses=OPEN,CONFIRMED
```

Flag names must match the parameter names (note the plural forms).

---

## 5. Supported parameters

All parameters are optional unless noted.

| Parameter      | Values / type                    | Default | Meaning |
|----------------|----------------------------------|---------|---------|
| `project_key`  | string                           | \u2014       | Project key (if not given as positional input). |
| `url`          | string                           | \u2014       | Alternative to positional input. |
| `severities`   | comma-list / list                | \u2014       | `INFO,MINOR,MAJOR,CRITICAL,BLOCKER` |
| `types`        | comma-list / list                | \u2014       | `BUG,VULNERABILITY,CODE_SMELL` |
| `statuses`     | comma-list / list                | \u2014       | `OPEN,CONFIRMED,REOPENED,RESOLVED,CLOSED` |
| `resolved`     | bool                             | `false` | Send `resolved=false` to fetch unresolved findings only. Set `true` to include resolved ones. |
| `branch`       | string                           | \u2014       | Branch name. |
| `pull_request` | string / int                     | \u2014       | Pull request ID. |
| `page_size`    | int (1\u2013500)                      | `50`    | Page size for the API call. |
| `max_pages`    | int                              | `1`     | Maximum number of pages to fetch. |
| `max_total`    | int                              | `500`   | Hard cap on total issues returned across pages. |
| `verbose`      | bool                             | `false` | Extra logging to the terminal. |

Pagination stops as soon as either `max_pages` or `max_total` is reached,
or when the server runs out of results.

---

## 6. Example output

A successful call returns something like:

```json
{
  "server": "https://sonarcloud.io",
  "project": {"key": "my_project_key"},
  "query": {
    "resolved": false,
    "severities": ["CRITICAL"],
    "types": ["BUG"]
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
      "message": "Do not ignore the return value of ...",
      "effort": "10min",
      "tags": ["cwe", "error-handling"],
      "creationDate": "2024-05-12T10:15:00+0000",
      "updateDate": "2024-06-01T08:42:11+0000"
    }
  ]
}
```

Along with a short human-readable summary, e.g.:

```
Fetched 50 SonarQube issue(s) for 'my_project_key' (total available: 123).
```

---

## 7. Combining with the AI

Because the plugin returns structured JSON, it\u2019s easy to chain into AI
prompts. Common workflows:

1. Run `sq my_project_key --severities=CRITICAL` to fetch the worst issues.
2. Ask the AI to prioritize, group, or fix them:
   - \u201cTriage these findings and propose fixes for the top 5.\u201d
   - \u201cGroup these issues by file and suggest a refactor order.\u201d
   - \u201cFor each BUG, propose a minimal code change and explain why.\u201d

You can also include specific project files alongside the findings:

```
with src/foo.py, src/bar.py: fix the SonarQube CRITICAL bugs reported in these files
```

---

## 8. Troubleshooting

### \u201cSonarQube server URL is not configured\u201d
- Set `AYE_SONARQUBE_URL` or `sonarqube_url`, **or** pass a full issues URL.

### \u201cSonarQube project key is required\u201d
- Provide a positional argument: `sq <projectKey>` or `sq <url>`.

### `HTTP 401` / `HTTP 403`
- The token is missing, invalid, or lacks permission for the project.
- Set `AYE_SONARQUBE_TOKEN` (or `sonarqube_token`) to a token that has
  **Browse** permission on the project.
- For private projects, anonymous access will always fail.

### `HTTP 404`
- Check the project key spelling, and that the server URL points at the
  correct SonarQube / SonarCloud instance.

### SSL / certificate errors
- If you use a self-signed certificate internally, set `AYE_SSLVERIFY=off`
  (or `sslverify=off` in `~/.ayecfg`). Only do this on trusted networks.

### Too many / too few issues returned
- Increase `max_pages` and/or `max_total` to fetch more.
- Narrow the query with `--severities`, `--types`, `--statuses`.

### Verbose diagnostics
- Pass `--verbose=true` on the command, or toggle `verbose on` in chat.
- The token is **never** logged.

---

## 9. Privacy & security

- The plugin never prints or logs your SonarQube token or the
  `Authorization` header.
- Requests go directly from your machine to your configured SonarQube /
  SonarCloud server \u2014 Aye Chat does not proxy them.
- Respect your organization\u2019s policy for API tokens; prefer env vars over
  committing tokens into config files.

---

## 10. Quick reference

```
# One-time setup
export AYE_SONARQUBE_URL="https://sonarcloud.io"
export AYE_SONARQUBE_TOKEN="squ_..."

# Basic usage
sq my_project_key

# From a pasted SonarQube UI URL
sq https://sonarcloud.io/project/issues?id=my_org_my_project

# With filters
sq my_project_key --severities=CRITICAL,BLOCKER --types=BUG,VULNERABILITY

# Pull more issues
sq my_project_key --max_pages=5 --max_total=1000
```
