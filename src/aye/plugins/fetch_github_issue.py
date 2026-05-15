import os
import re
import httpx

from typing import Any, Dict, Optional
import rich

try:
    from aye.model.auth import get_user_config
except Exception:  # pragma: no cover
    try:
        from ..model.auth import get_user_config  # type: ignore
    except Exception:
        def get_user_config(key: str, default: Optional[str] = None) -> Optional[str]:
            return default

from aye.plugins.plugin_base import Plugin

DEFAULT_TIMEOUT = 30.0

GITHUB_ISSUE_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/issues/(\d+)/?$"
)

rprint = rich.print


def _get_config(env_key: str, cfg_key: str) -> Optional[str]:
    """Read a config value from environment variable first, then ~/.ayecfg.

    Args:
        env_key: Environment variable name (e.g. 'AYE_GITHUB_TOKEN').
        cfg_key: Key in ~/.ayecfg (e.g. 'github_token').

    Returns:
        The value as a stripped string, or None if not set.
    """
    val = os.environ.get(env_key)
    if val:
        return val.strip()
    cfg_val = get_user_config(cfg_key, None)
    if cfg_val:
        return str(cfg_val).strip()
    return None


class FetchGithubIssuePlugin(Plugin):
    name = "process_url"

    def init(self, cfg: Dict[str, Any]) -> None:
        super().init(cfg)

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if command_name == "process_url":
            url = params.get("url")
            verbose = params.get("verbose", False)

            if url and GITHUB_ISSUE_PATTERN.match(url):
                try:
                    data = fetch_github_issue(url, verbose)
                    return {"status": "success", "data": data}
                except ValueError:
                    return None
                except httpx.HTTPStatusError:
                    return None
                except httpx.RequestError:
                    return None

        return None


def fetch_github_issue(url: str, verbose: bool, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch a GitHub issue via the REST API.

    Authorization is optional. When a token is configured (via
    ``AYE_GITHUB_TOKEN`` environment variable or ``github_token`` in
    ``~/.ayecfg``), it is sent as ``Authorization: token <token>``.
    Requests go directly from your machine to the GitHub API.
    """
    match = GITHUB_ISSUE_PATTERN.match(url)
    if not match:
        raise ValueError("Not a GitHub issue URL")
    owner, repo, issue_num = match.groups()

    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_num}"
    timeline_url = f"{api_url}/timeline"

    token = _get_config("AYE_GITHUB_TOKEN", "github_token")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "aye-chat-github-fetcher/1.0",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    with httpx.Client(timeout=timeout) as client:
        response = client.get(api_url, headers=headers)
        response.raise_for_status()
        issue = response.json()

        if verbose:
            rprint(f"[green]\u2713 Fetched GitHub Issue #{issue_num} from {repo}[/]")

        timeline_headers = {
            "Accept": "application/vnd.github.mockingbird-preview+json",
            "User-Agent": "aye-chat-github-fetcher/1.0",
        }
        if token:
            timeline_headers["Authorization"] = f"token {token}"

        timeline_response = client.get(timeline_url, headers=timeline_headers)
        comments = []
        if timeline_response.status_code == 200:
            for event in timeline_response.json():
                if event.get("user", {}).get("login") and event.get("body"):
                    comments.append({
                        "author": event.get("user", {}).get("login"),
                        "body": event.get("body")
                    })

        return {
            "url": url,
            "number": issue.get("number"),
            "title": issue.get("title"),
            "author": issue.get("user", {}).get("login"),
            "state": issue.get("state"),
            "body": issue.get("body"),
            "labels": [lbl.get("name") for lbl in issue.get("labels", [])],
            "comments": comments,
        }
